"""
The changelog the runner keeps for a project with `changelog.enabled` (see autocfg.py). Stdlib only.

    python autochange.py check --sentence-file .auto/changes/A1.txt      # validate one change sentence, change nothing
    python autochange.py release [--project DIR] [--file CHANGELOG.md]    # propose the next version, write .auto/release/<v>.md

The agent writes one sentence per item to `.auto/changes/<ITEM>.txt` (the prompt asks for it). After a green item with no
leftovers the runner validates it (`validate_sentence`), adds it (`append_entry`) and commits only the changelog file
(`commit_entry`) on the auto branch, never on the base branch. A missing or invalid sentence stops the run (reason `changelog`).

Sentence rules: one line, one sentence, 10 to 200 characters, no item id, no URL, no generated secret (the error never repeats it).

File format (Keep a Changelog): a `## [Unreleased]` section with one `###` heading per kind, in the order of HEADINGS. An entry is
    - <sentence> _(A1 Title; commit abc1234; acceptance: <the item's acceptance line>; tests: pass)_
The same item never appears twice anywhere in the file, so a second call on the same item changes nothing.

Release (`release`): the last version is the highest `## [x.y.z]` heading, else the highest of the project's version files,
else 0.0.0.
Version files (`version_files`): `package.json`, `pyproject.toml` (`version = "..."`), `.claude-plugin/plugin.json` and `VERSION`
when they exist, plus `release.version_files` from the config (a `.json` file's top-level "version", a `.toml` file's
`version = "..."`, any other file's whole text). The bump follows the
Unreleased kinds: breaking -> major, else feature -> minor, else fix -> patch; only internal entries (or none) -> no release and
nothing written. It writes `.auto/release/<version>.md` with the notes and the commands a person runs to publish (including the
tag). It never runs git: no tag, no push, no remote, no change to the changelog itself.

`release --apply` (decision record 0012, Phase 5) makes the release on the working tree instead: the Unreleased entries move
under `## [x.y.z] - <date>` with a new empty Unreleased above, and every version file gets the new version (only its
version text changes). It still runs no git: the runner or a person commits it on a branch and it lands through a pull
request like any change; the tag (`release.tag_format`, default `v{version}`) is made after that merge.
"""
import argparse
import importlib.util
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

MIN_LEN, MAX_LEN = 10, 200
# kind -> heading, in the order the headings appear under Unreleased (autochange `release` reads them back).
HEADINGS = {'breaking': 'Breaking', 'feature': 'Added', 'fix': 'Fixed', 'internal': 'Internal'}
UNRELEASED = '## [Unreleased]'
PREAMBLE = ('# Changelog\n\nAll notable changes to this project are recorded here. The format follows Keep a Changelog '
            '(keepachangelog.com) and the project uses semantic versioning.\n')
ITEM_PATTERN = re.compile(r'\b[A-Z]{1,3}\d+\b')
URL = re.compile(r'[a-z][a-z0-9+.-]*://|\bwww\.', re.I)
SENTENCE_BREAK = re.compile(r'[.!?]["\')\]]*\s+["\'(]?[A-Z0-9]')
TITLE = re.compile(r'^- \[[ x]\] \*\*[A-Z]{1,3}\d+\s+(.*?)\.?\*\*')
ACCEPTANCE = re.compile(r'^\s*\*Acceptance:\*\s*(.*)$')
VERSION_HEADING = re.compile(r'^## \[v?(\d+)\.(\d+)\.(\d+)\]', re.M)
VERSION_TEXT = re.compile(r'v?(\d+)\.(\d+)\.(\d+)')
FACTS = re.compile(r' _\(.*\)_$')
RELEASE_DIR = Path('.auto') / 'release'


class ChangelogError(RuntimeError):
    pass


def _values(secrets):
    vals = secrets.values() if isinstance(secrets, dict) else (secrets or ())
    return [v for v in vals if isinstance(v, str) and v]


def validate_sentence(text, item_ids=None, secrets=None):
    """-> list of reasons (empty = valid). `item_ids` are the queue's ids (None: anything shaped like one counts)."""
    if text is None:
        return ['no sentence was written']
    s = text.strip()
    errors = []
    if '\n' in s or '\r' in s:
        errors.append('it must be one line')
    if len(s) < MIN_LEN or len(s) > MAX_LEN:
        errors.append(f'it must be {MIN_LEN} to {MAX_LEN} characters (it has {len(s)})')
    if SENTENCE_BREAK.search(s):
        errors.append('it must be one sentence')
    ids = ITEM_PATTERN.findall(s) if item_ids is None else [i for i in item_ids if re.search(rf'\b{re.escape(i)}\b', s)]
    if ids:
        errors.append(f'it must not name item ids ({", ".join(sorted(set(ids)))})')
    if URL.search(s):
        errors.append('it must not contain a link')
    if any(v in s for v in _values(secrets)):
        errors.append('it contains a generated test credential')
    return errors


def item_facts(block):
    """-> (title, acceptance) from a queue item's block; each '' when absent. Whitespace collapsed to one line."""
    lines = block.splitlines()
    m = TITLE.match(lines[0]) if lines else None
    acceptance = next((a.group(1) for a in map(ACCEPTANCE.match, lines) if a), '')
    one = lambda t: ' '.join(t.split())
    return one(m.group(1) if m else ''), one(acceptance).rstrip('.')


def format_entry(item, title, commit, acceptance, tests, sentence):
    facts = '; '.join(x for x in (f'{item} {title}'.strip(), f'commit {commit}' if commit else '',
                                  f'acceptance: {acceptance}' if acceptance else '', f'tests: {tests}' if tests else '') if x)
    return f'- {sentence.strip()} _({facts})_'


def has_entry(text, item):
    return re.search(rf'^- .*_\({re.escape(item)}\b', text, re.M) is not None


def _unreleased_bounds(lines):
    """-> (start, end) of the Unreleased section (heading line, first line after it), or None."""
    for n, line in enumerate(lines):
        if line.strip().lower() == UNRELEASED.lower():
            end = next((k for k in range(n + 1, len(lines)) if lines[k].startswith('## ')), len(lines))
            return n, end
    return None


def add_entry(text, kind, entry):
    """-> `text` with `entry` at the end of its kind's heading under Unreleased (both created when missing)."""
    if kind not in HEADINGS:
        raise ChangelogError(f'unknown kind "{kind}" (one of {", ".join(HEADINGS)})')
    lines = (text or PREAMBLE).rstrip('\n').split('\n')
    bounds = _unreleased_bounds(lines)
    if bounds is None:
        at = next((k for k, line in enumerate(lines) if line.startswith('## ')), len(lines))
        lines[at:at] = ([''] if at and lines[at - 1].strip() else []) + [UNRELEASED, '']
        bounds = _unreleased_bounds(lines)
    start, end = bounds
    body = lines[start + 1:end]
    lead, sections, order, cur = [], {}, [], None
    for line in body:
        if line.startswith('### '):
            cur = line[4:].strip()
            sections.setdefault(cur, [])
            order.append(cur)
        elif cur is None:
            lead.append(line)
        elif line.strip():
            sections[cur].append(line)
    heading = HEADINGS[kind]
    sections.setdefault(heading, []).append(entry)
    known = [h for h in HEADINGS.values() if h in sections]
    rebuilt = [x for x in lead if x.strip()]
    for h in known + [h for h in order if h not in HEADINGS.values()]:
        rebuilt += ['', f'### {h}', *sections[h]]
    rest = lines[end:]
    out = lines[:start + 1] + rebuilt + ([''] if rest else []) + rest
    return '\n'.join(out) + '\n'


def append_entry(path, kind, item, title, commit, acceptance, tests, sentence):
    """Add the item's entry to the changelog at `path`. -> True when written, False when the item is already there."""
    path = Path(path)
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    if has_entry(text, item):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(add_entry(text, kind, format_entry(item, title, commit, acceptance, tests, sentence)), encoding='utf-8')
    return True


def _git(cwd, *args):
    r = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise ChangelogError(f'git {" ".join(args)} failed: {r.stderr.strip() or r.stdout.strip()}')
    return r.stdout.strip()


def commit_entry(cwd, rel_file, item, title='', trailer='', forbid_branch=None):
    """Commit only `rel_file` in `cwd`. -> short commit id, or None when the file has nothing to commit.
    Refuses on `forbid_branch` (the base branch) and on a detached HEAD."""
    branch = _git(cwd, 'branch', '--show-current')
    if not branch or branch == forbid_branch:
        raise ChangelogError(f'refusing to commit the changelog on {branch or "a detached HEAD"}')
    if not _git(cwd, 'status', '--porcelain', '--', rel_file):
        return None
    msg = f'Changelog: {item}' + (f' {title}' if title else '') + (f'\n\n{trailer}' if trailer else '')
    _git(cwd, 'add', '--', rel_file)
    _git(cwd, 'commit', '-q', '-m', msg, '--', rel_file)
    return _git(cwd, 'rev-parse', '--short', 'HEAD')


# ---- release preparation (never tags, never pushes) ----------------------------------------------------------------------

def unreleased_entries(text):
    """-> {kind: [entry line, ...]} for the entries under Unreleased; headings not in HEADINGS are ignored."""
    lines = (text or '').split('\n')
    bounds = _unreleased_bounds(lines)
    if bounds is None:
        return {}
    kinds = {h.lower(): k for k, h in HEADINGS.items()}
    out, cur = {}, None
    for line in lines[bounds[0] + 1:bounds[1]]:
        if line.startswith('### '):
            cur = kinds.get(line[4:].strip().lower())
        elif cur and line.startswith('- '):
            out.setdefault(cur, []).append(line)
    return out


def _parse_version(text, where):
    m = VERSION_TEXT.fullmatch(text.strip())
    if not m:
        raise ChangelogError(f'{where} does not hold a version like 1.2.3')
    return tuple(int(x) for x in m.groups())


KNOWN_VERSION_FILES = ('package.json', 'pyproject.toml', '.claude-plugin/plugin.json', 'VERSION')
JSON_VERSION = re.compile(r'("version"\s*:\s*")(v?\d+\.\d+\.\d+)(")')
TOML_VERSION = re.compile(r'^(version\s*=\s*")(v?\d+\.\d+\.\d+)(")', re.M)
TAG_FORMAT = 'v{version}'


def version_files(project, configured=()):
    """-> the project's version files (relative paths that exist): the known ones, then the configured ones, no repeats."""
    project = Path(project)
    out = []
    for rel in (*KNOWN_VERSION_FILES, *configured):
        if rel not in out and (project / rel).is_file():
            out.append(rel)
    return out


def _version_pattern(rel):
    return JSON_VERSION if rel.endswith('.json') else TOML_VERSION if rel.endswith('.toml') else None


def read_version(path):
    """-> (major, minor, patch) from a version file, or None when it holds no version."""
    text = Path(path).read_text(encoding='utf-8')
    pattern = _version_pattern(Path(path).name)
    if pattern is None:
        m = VERSION_TEXT.fullmatch(text.strip())
        return tuple(int(x) for x in m.groups()) if m else None
    m = pattern.search(text)
    return tuple(int(x) for x in VERSION_TEXT.fullmatch(m.group(2)).groups()) if m else None


def write_version(path, version):
    """Put `version` (x.y.z) into a version file, changing only the version text. -> True when it changed."""
    path = Path(path)
    text = path.read_text(encoding='utf-8')
    pattern = _version_pattern(path.name)
    if pattern is None:
        new = version + '\n'
    else:
        new, n = pattern.subn(lambda m: m.group(1) + ('v' if m.group(2).startswith('v') else '') + version + m.group(3),
                              text, count=1)
        if n == 0:
            return False
    if new == text:
        return False
    path.write_bytes(new.encode('utf-8'))
    return True


def last_version(changelog_text, version_file=None, files=()):
    """-> ((major, minor, patch), source). The highest `## [x.y.z]` heading; with none, the highest of the VERSION file
    (`version_file`) and the version `files`; else 0.0.0."""
    headings = [tuple(int(x) for x in m.groups()) for m in VERSION_HEADING.finditer(changelog_text or '')]
    if headings:
        return max(headings), 'changelog heading'
    found = []
    if version_file is not None and Path(version_file).exists():
        found.append((_parse_version(Path(version_file).read_text(encoding='utf-8'), 'VERSION'), 'VERSION file'))
    for f in files:
        v = read_version(f)
        if v is not None:
            found.append((v, Path(f).name))
    if found:
        return max(found)
    return (0, 0, 0), 'none (0.0.0)'


def propose_bump(entries):
    """breaking -> major, feature -> minor, fix -> patch; internal only (or nothing) -> None (no release)."""
    for kind, part in (('breaking', 'major'), ('feature', 'minor'), ('fix', 'patch')):
        if entries.get(kind):
            return part
    return None


def bump(version, part):
    major, minor, patch = version
    return {'major': (major + 1, 0, 0), 'minor': (major, minor + 1, 0), 'patch': (major, minor, patch + 1)}[part]


def release_notes(version, day, entries):
    """Markdown notes: one section per kind present, each entry's sentence without the runner's facts."""
    lines = [f'## [{version}] - {day}']
    for kind, heading in HEADINGS.items():
        if entries.get(kind):
            lines += ['', f'### {heading}'] + [FACTS.sub('', e) for e in entries[kind]]
    return '\n'.join(lines)


def tag_name(version, tag_format=TAG_FORMAT):
    """The tag for `version`: `tag_format` with `{version}` replaced (default v1.2.3)."""
    return (tag_format or TAG_FORMAT).replace('{version}', version)


def release_commands(version, changelog_rel, has_version_file, files=(), tag_format=TAG_FORMAT):
    """The steps a person runs to publish `version`; the runner runs none of them. `files` are the version files."""
    steps = [f'Run `autochange.py release --apply` (or edit {changelog_rel}: rename "{UNRELEASED}" to "## [{version}] - <date>" '
             f'and add a new empty "{UNRELEASED}" above it).']
    files = [f for f in files if f != 'VERSION'] + (['VERSION'] if has_version_file else [])
    if files:
        steps.append(f'Write {version} into ' + ', '.join(files) + '.')
    tag = tag_name(version, tag_format)
    return steps, [f'git add -- {" ".join([changelog_rel, *files])}', f'git commit -m "Release {version}"',
                   f'git tag -a {tag} -m "Release {version}"', f'git push origin {tag}']


def release(project, changelog_file='CHANGELOG.md', today=None, configured_files=(), tag_format=TAG_FORMAT,
            apply=False):
    """Propose the next version from the Unreleased entries and write `.auto/release/<version>.md`.
    -> {'bump', 'previous', 'version', 'path', 'source', 'files', 'tag', 'applied'}; with no releasable entry
    bump/version/path are None and nothing is written. With `apply` the release is also made on the working tree
    (apply_release). Never runs git."""
    project = Path(project)
    path = project / changelog_file
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    vfile = project / 'VERSION'
    files = [f for f in version_files(project, configured_files) if f != 'VERSION']
    previous, source = last_version(text, vfile, [project / f for f in files])
    entries = unreleased_entries(text)
    part = propose_bump(entries)
    result = {'bump': part, 'previous': '.'.join(map(str, previous)), 'version': None, 'path': None, 'source': source,
              'files': files + (['VERSION'] if vfile.exists() else []), 'tag': None, 'applied': []}
    if part is None:
        return result
    version = '.'.join(map(str, bump(previous, part)))
    day = (today or date.today()).isoformat()
    steps, commands = release_commands(version, Path(changelog_file).as_posix(), vfile.exists(), files, tag_format)
    body = [f'# Release {version} (proposed)', '',
            f'Previous version: {result["previous"]} (from {source}). Proposed bump: {part}. '
            'Prepared by the auto-runner; nothing has been tagged or pushed.', '',
            release_notes(version, day, entries), '', '## To publish (a person runs these)', '']
    body += [f'{n}. {s}' for n, s in enumerate(steps, 1)]
    body += ['', '```', *commands, '```', '']
    out = project / RELEASE_DIR / f'{version}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(body), encoding='utf-8')
    result.update(version=version, path=out, tag=tag_name(version, tag_format))
    if apply:
        result['applied'] = apply_release(project, changelog_file, version, day, result['files'])
    return result


def apply_release(project, changelog_file, version, day, files):
    """Make the release on the working tree: the Unreleased section becomes `## [version] - day` under a new empty
    Unreleased, and each version file gets `version`. -> the relative paths changed. Never runs git."""
    project = Path(project)
    path = project / changelog_file
    text = path.read_text(encoding='utf-8')
    if UNRELEASED not in text:
        raise ChangelogError(f'{changelog_file} has no "{UNRELEASED}" section')
    if re.search(r'^## \[v?' + re.escape(version) + r'\]', text, re.M):
        raise ChangelogError(f'{changelog_file} already has a section for {version}')
    path.write_bytes(text.replace(UNRELEASED, f'{UNRELEASED}\n\n## [{version}] - {day}', 1).encode('utf-8'))
    changed = [Path(changelog_file).as_posix()]
    for rel in files:
        if write_version(project / rel, version):
            changed.append(rel)
    return changed


def _changelog_file(project):
    """The configured changelog file, or the default when the config cannot be read."""
    try:
        spec = importlib.util.spec_from_file_location('autorunner_cfg', Path(__file__).resolve().parent / 'autocfg.py')
        cfg_mod = sys.modules.get('autorunner_cfg')
        if cfg_mod is None:
            cfg_mod = importlib.util.module_from_spec(spec)
            sys.modules['autorunner_cfg'] = cfg_mod
            spec.loader.exec_module(cfg_mod)
        return cfg_mod.changelog(cfg_mod.load(project))['file']
    except Exception:
        return 'CHANGELOG.md'


def _release_config(project):
    """The `release` section (version_files, tag_format) with defaults, or the defaults when the config cannot be read."""
    try:
        cfg_mod = _load('autorunner_cfg', 'autocfg.py')
        return cfg_mod.release(cfg_mod.load(project))
    except Exception:
        return {'version_files': [], 'tag_format': TAG_FORMAT}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check')
    c.add_argument('--sentence-file', required=True)
    r = sub.add_parser('release')
    r.add_argument('--project', default='.')
    r.add_argument('--file', help='changelog file (default: changelog.file in .claude/auto.json, else CHANGELOG.md)')
    r.add_argument('--apply', action='store_true',
                   help='also make the release on the working tree: changelog section and version files (no git)')
    a = ap.parse_args(argv)
    if a.cmd == 'release':
        try:
            rel_cfg = _release_config(a.project)
            res = release(a.project, a.file or _changelog_file(a.project), configured_files=rel_cfg['version_files'],
                          tag_format=rel_cfg['tag_format'], apply=a.apply)
        except ChangelogError as e:
            print(f'refused: {e}')
            return 1
        if res['bump'] is None:
            print(f'no release: nothing under Unreleased needs one (only internal changes or none); current {res["previous"]}')
        else:
            print(f'proposed {res["version"]} ({res["bump"]} bump from {res["previous"]}); notes and commands in {res["path"]}')
            if a.apply:
                print(f'applied: {", ".join(res["applied"])} now say {res["version"]}; commit them on a branch, merge the '
                      f'pull request, then tag {res["tag"]}')
        return 0
    p = Path(a.sentence_file)
    errors = validate_sentence(p.read_text(encoding='utf-8') if p.exists() else None)
    print('ok' if not errors else 'invalid: ' + '; '.join(errors))
    return 1 if errors else 0


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


if __name__ == '__main__':
    _load('autorunner_safe', 'autosafe.py').utf8_console()
    sys.exit(main())
