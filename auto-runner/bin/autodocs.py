"""
Documentation checks for a commit range (stdlib only, read-only: it runs only `git diff`, `git show`, `git ls-tree` and
`git merge-base`, and writes nothing).

    python autodocs.py check [--project DIR] [--base REF] [--head REF] [--map PATH]

The command runs `gate()` (the checks below plus stale docs). Exit 0 = nothing to fix, 1 = failures (printed one per line in plain words), 2 = the doc map itself is broken.
The range is `merge-base(base, head)..head`; base defaults to the project's base branch (autocfg.detect_base_branch), head to HEAD.
The runner calls `gate()` (these checks plus the stale-doc scan) on each green item's own commits when a map exists and
`docs.check` is not false; a failure stops the run with reason `docs` (auto_run.py).

The doc map (`docs.map` in .claude/auto.json, default docs/doc-map.json) says which docs describe which code:

    {
      "entries": [
        {"name": "schema",  "paths": ["db/schema.sql"],        "docs": ["docs/data-model.md"], "pattern": "sql_table"},
        {"name": "routes",  "paths": ["webapp/**/*.py"],       "docs": ["README.md"],           "pattern": "route"},
        {"name": "settings","paths": ["app/settings.py"],      "docs": ["docs/env.md"],         "pattern": "\\b(APP_[A-Z_]+)\\b"},
        {"name": "runner",  "paths": ["bin/auto_run.py"],      "docs": ["README.md"],           "stamp": true}
      ]
    }

    paths    globs relative to the project, `/` separated: `*` and `?` stay inside one folder, `**/` spans folders
    docs     the files that must change whenever a matching path changes
    pattern  optional: a preset (PRESETS: sql_table, route) or a regular expression; group 1 (else the whole match) is a
             symbol. A symbol found in an added line of a matching file, absent from every matching file before the range,
             must appear somewhere in one of the entry's docs at head
    stamp    optional true/false: each doc carries an autostale header whose `built_from` lists the watched files and
             whose `sources_hash` records them (`autostale.py revalidate DOC --from a.py,b.py`); see stale docs below
    name     optional label used in messages (default: "entry N")

Failures, in plain words:
  1. a changed file matches an entry and none of that entry's docs changed in the same range (names the files and the docs);
     a changed file that is itself one of the entry's docs never triggers its own entry;
  2. a changed Markdown file holds a relative link (outside code) whose target is not in the head tree (names the link);
  3. a newly added symbol is in none of the entry's docs (names the symbol and where it was added).
No map file = no failures and a note saying so.

Stale docs (`stale_docs`, only for `stamp: true` entries; read from git at head, never written): a changed file that matches
the entry makes each of its docs stale, and every tracked artifact built from that doc, directly or through others
(autostale.mark_descendants_stale over the head versions; a cycle is a failure). A stale doc stays blocking until it was
revalidated in the same range, i.e. at head it has a header with status valid, a `sources_hash` equal to what its
`built_from` files hold at head, and (for a stamped doc) every changed watched file listed in `built_from`. Each failure
gives the revalidate command to run.
"""
import argparse
import importlib.util
import json
import posixpath
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

PRESETS = {
    'sql_table': (r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`\[]?(\w+)', re.I, 'table'),
    'route': (r'\.(?:route|get|post|put|patch|delete)\(\s*[\'"](/[^\'"]*)[\'"]', 0, 'route'),
}
ENTRY_KEYS = {'name', 'paths', 'docs', 'pattern', 'stamp'}
MARKDOWN = ('.md', '.markdown')
LINK = re.compile(r'!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+["\'][^)]*["\'])?\s*\)')
FENCE = re.compile(r'^(```|~~~).*?^\1[^\n]*$', re.M | re.S)
INLINE_CODE = re.compile(r'`[^`\n]*`')
SCHEME = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*:')


class DocMapError(ValueError):
    pass


def glob_regex(pattern):
    """`*`/`?` match inside one folder, `**/` any number of folders (also none), a trailing `**` everything below."""
    out, i = '', 0
    while i < len(pattern):
        if pattern.startswith('**/', i):
            out, i = out + '(?:[^/]+/)*', i + 3
        elif pattern.startswith('**', i):
            out, i = out + '.*', i + 2
        elif pattern[i] == '*':
            out, i = out + '[^/]*', i + 1
        elif pattern[i] == '?':
            out, i = out + '[^/]', i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r'\Z')


def _texts(value):
    return isinstance(value, list) and value and all(isinstance(x, str) and x for x in value)


def parse_map(raw, where='doc map'):
    """JSON text or object -> list of entries {name, paths, docs, regex, label, stamp}. Errors name the entry and key."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError as e:
        raise DocMapError(f'{where} is not valid JSON: {e}')
    if not isinstance(data, dict) or not isinstance(data.get('entries'), list):
        raise DocMapError(f'{where} must be an object with an "entries" list')
    unknown = set(data) - {'entries', 'version'}
    if unknown:
        raise DocMapError(f'{where}: unknown key "{sorted(unknown)[0]}" (known: entries, version)')
    entries = []
    for n, e in enumerate(data['entries'], 1):
        label = f'{where} entry {n}'
        if not isinstance(e, dict):
            raise DocMapError(f'{label} must be an object')
        extra = set(e) - ENTRY_KEYS
        if extra:
            raise DocMapError(f'{label}: unknown key "{sorted(extra)[0]}" (known: {", ".join(sorted(ENTRY_KEYS))})')
        for key in ('paths', 'docs'):
            if not _texts(e.get(key)):
                raise DocMapError(f'{label}: "{key}" must be a non-empty list of text')
        name = e.get('name', f'entry {n}')
        if not isinstance(name, str) or not name:
            raise DocMapError(f'{label}: "name" must be text')
        if not isinstance(e.get('stamp', False), bool):
            raise DocMapError(f'{label}: "stamp" must be true/false')
        regex, kind = None, 'symbol'
        if 'pattern' in e:
            pat = e['pattern']
            if not isinstance(pat, str) or not pat:
                raise DocMapError(f'{label}: "pattern" must be text')
            source, flags, kind = PRESETS.get(pat, (pat, 0, 'symbol'))
            try:
                regex = re.compile(source, flags)
            except re.error as err:
                raise DocMapError(f'{label}: "pattern" is not a valid regular expression ({err})')
        entries.append({'name': name, 'paths': [glob_regex(p) for p in e['paths']], 'docs': list(e['docs']),
                        'regex': regex, 'kind': kind, 'stamp': e.get('stamp', False)})
    return entries


def _git(project, *args):
    r = subprocess.run(['git', '-C', str(project), *args], capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    if r.returncode != 0:
        raise RuntimeError(f'git {args[0]} failed: {r.stderr.strip()}')
    return r.stdout


def _read_at(project, ref, path):
    r = subprocess.run(['git', '-C', str(project), 'show', f'{ref}:{path}'], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return r.stdout if r.returncode == 0 else None


def _tree(project, ref):
    return set(_git(project, 'ls-tree', '-r', '--name-only', ref).splitlines())


def _matches(entry, path):
    return any(rx.match(path) for rx in entry['paths'])


def _symbols(regex, text):
    return {(m.group(1) if regex.groups else m.group(0)) for m in regex.finditer(text or '')}


def _added_lines(project, base, head, path):
    diff = _git(project, 'diff', '-U0', '--no-renames', base, head, '--', path)
    return '\n'.join(l[1:] for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++'))


def _strip_code(text):
    return INLINE_CODE.sub('', FENCE.sub('', text))


def dead_links(doc, text, tree):
    """-> [(link, resolved)] for relative links in `text` (outside code) whose target is not in `tree`."""
    dirs = {posixpath.dirname(p) for p in tree}
    for p in list(dirs):
        while p:
            p = posixpath.dirname(p)
            dirs.add(p)
    out = []
    for link in LINK.findall(_strip_code(text)):
        if link.startswith('#') or SCHEME.match(link) or link.startswith('//'):
            continue
        target = unquote(link.split('#', 1)[0].split('?', 1)[0])
        if not target:
            continue
        joined = target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(doc), target)
        resolved = posixpath.normpath(joined)
        if resolved == '.':
            continue
        if resolved.startswith('..') or (resolved not in tree and resolved.rstrip('/') not in dirs):
            out.append((link, resolved))
    return out


def _cfg_module():
    mod = sys.modules.get('autorunner_cfg')
    if mod is None:
        spec = importlib.util.spec_from_file_location('autorunner_cfg', Path(__file__).resolve().parent / 'autocfg.py')
        mod = importlib.util.module_from_spec(spec)
        sys.modules['autorunner_cfg'] = mod
        spec.loader.exec_module(mod)
    return mod


def _stale_module():
    mod = sys.modules.get('autorunner_stale')
    if mod is None:
        spec = importlib.util.spec_from_file_location('autorunner_stale', Path(__file__).resolve().parent / 'autostale.py')
        mod = importlib.util.module_from_spec(spec)
        sys.modules['autorunner_stale'] = mod
        spec.loader.exec_module(mod)
    return mod


def stale_docs(project, base_ref, head_ref, entries):
    """-> [failure text] for docs of `stamp: true` entries made stale by the range and not revalidated in it. Read-only."""
    st = _stale_module()
    base = _git(project, 'merge-base', base_ref, head_ref).strip()
    changed = [p for p in _git(project, 'diff', '--name-only', '--no-renames', base, head_ref).splitlines() if p]
    cache = {}

    def at_head(rel):
        if rel not in cache:
            cache[rel] = _read_at(project, head_ref, rel)
        return cache[rel]

    def header_of(rel):
        try:
            return st.parse(at_head(rel) or '')[0]
        except ValueError:
            return None

    watched, triggers = {}, {}                  # stamped doc -> changed watched files; changed file -> docs it stales
    for e in entries:
        if not e['stamp']:
            continue
        own = set(e['docs'])
        hits = [p for p in changed if p not in own and _matches(e, p)]
        for d in e['docs'] if hits else ():
            watched.setdefault(d, set()).update(hits)
            for p in hits:
                triggers.setdefault(p, set()).add(d)
    if not watched:
        return []
    # Graph over the head versions: a stamped doc is built from the files that changed, every tracked artifact from its
    # built_from. Only Markdown/text artifacts in the head tree can be descendants.
    graph = {d: sorted(hits) for d, hits in watched.items()}
    for rel in _tree(project, head_ref):
        if rel in graph or not rel.lower().endswith(MARKDOWN + ('.txt',)):
            continue
        h = header_of(rel)
        if h is not None and h['built_from']:
            graph[rel] = list(h['built_from'])
    for d in watched:
        h = header_of(d)
        if h is not None:
            graph[d] = sorted(set(graph[d]) | set(h['built_from']))
    try:
        candidates = st.mark_descendants_stale(sorted(triggers), graph)
    except st.CycleError as err:
        return [f'Stale-doc check cannot run: {err}.']
    failures = []
    for d in candidates:
        h = header_of(d)
        needed = sorted(watched.get(d, ()))
        sources = sorted(set(needed) | set(h['built_from'] if h else []))
        fix = f'run `python autostale.py revalidate {d}' + (f' --from {",".join(sources)}`' if sources else '`')
        why = None
        if at_head(d) is None:
            why = 'is missing'
        elif h is None:
            why = 'has no artifact header'
        elif h['status'] != 'valid':
            why = f'is marked {h["status"]}'
        elif not h.get('sources_hash'):
            why = 'has no sources_hash'
        elif set(needed) - set(h['built_from']):
            why = 'does not list ' + ', '.join(sorted(set(needed) - set(h['built_from']))) + ' in built_from'
        elif h['sources_hash'] != st.sources_hash(h['built_from'], at_head):
            why = 'was not revalidated after its sources changed'
        if why:
            src = ', '.join(needed) if needed else 'a doc it is built from'
            failures.append(f'{d} is stale: {src} changed and it {why}; check it against the code, then {fix} '
                            f'in the same commit range.')
    return failures


def gate(project, base_ref, head_ref='HEAD', map_path=None):
    """The runner's docs gate: check() plus stale_docs() on the same range. -> same shape as check(), plus 'stale'."""
    result = check(project, base_ref, head_ref, map_path)
    result['stale'] = []
    if result['map'] is None:
        return result
    entries = parse_map((Path(project) / result['map']).read_text(encoding='utf-8'), result['map'])
    result['stale'] = stale_docs(project, base_ref, head_ref, entries)
    result['failures'] = result['failures'] + result['stale']
    return result


def check(project, base_ref, head_ref='HEAD', map_path=None):
    """-> {'failures': [text], 'notes': [text], 'map': path or None}. Reads git only; never writes."""
    project = Path(project)
    if map_path is None:
        cfg = _cfg_module()
        map_path = cfg.docs_map(cfg.load(project))
    full = project / map_path
    if not full.is_file():
        return {'failures': [], 'notes': [f'No doc map at {Path(map_path).as_posix()}: nothing to check.'], 'map': None}
    entries = parse_map(full.read_text(encoding='utf-8'), Path(map_path).as_posix())
    base = _git(project, 'merge-base', base_ref, head_ref).strip()
    changed = [p for p in _git(project, 'diff', '--name-only', '--no-renames', base, head_ref).splitlines() if p]
    changed_set = set(changed)
    head_tree = _tree(project, head_ref)
    base_tree = None
    failures = []

    for e in entries:
        own_docs = set(e['docs'])
        hits = [p for p in changed if p not in own_docs and _matches(e, p)]
        if hits and not own_docs & changed_set:
            failures.append(f'{", ".join(hits)} changed but {", ".join(e["docs"])} was not updated in the same range '
                            f'(doc map entry "{e["name"]}").')
        if not hits or e['regex'] is None:
            continue
        if base_tree is None:
            base_tree = _tree(project, base)
        before = set()
        for p in base_tree:
            if _matches(e, p):
                before |= _symbols(e['regex'], _read_at(project, base, p))
        docs_text = '\n'.join(_read_at(project, head_ref, d) or '' for d in e['docs'])
        seen = set()
        for p in hits:
            for sym in sorted(_symbols(e['regex'], _added_lines(project, base, head_ref, p)) - before - seen):
                seen.add(sym)
                if sym not in docs_text:
                    failures.append(f'New {e["kind"]} "{sym}" (added in {p}) is not mentioned in '
                                    f'{" or ".join(e["docs"])} (doc map entry "{e["name"]}").')

    for doc in changed:
        if doc.lower().endswith(MARKDOWN) and doc in head_tree:
            for link, resolved in dead_links(doc, _read_at(project, head_ref, doc) or '', head_tree):
                failures.append(f'{doc} links to "{link}", which does not exist ({resolved}).')
    return {'failures': failures, 'notes': [], 'map': Path(map_path).as_posix()}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Check that docs changed with the code they describe (read-only).')
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check', help='check a commit range against the doc map')
    c.add_argument('--project', default='.')
    c.add_argument('--base', default='', help='base ref (default: the project base branch)')
    c.add_argument('--head', default='HEAD')
    c.add_argument('--map', default=None, help='doc map path relative to the project (default: docs.map or docs/doc-map.json)')
    args = ap.parse_args(argv)
    base = args.base
    try:
        if not base:
            cfg = _cfg_module()
            base = cfg.detect_base_branch(args.project)
        result = gate(args.project, base, args.head, args.map)
    except DocMapError as e:
        print(f'doc map error: {e}')
        return 2
    except Exception as e:  # git or base-branch problems: say what, never a traceback
        print(f'cannot check: {e}')
        return 2
    for note in result['notes']:
        print(note)
    for failure in result['failures']:
        print(f'FAIL {failure}')
    if not result['failures']:
        print('docs ok' + (f' ({result["map"]})' if result['map'] else ''))
    return 1 if result['failures'] else 0


if __name__ == '__main__':
    sys.exit(main())
