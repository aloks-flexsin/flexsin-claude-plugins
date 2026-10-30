"""
Staleness and impact analysis for artifacts (roadmap K5): which documents were built from something that has since changed.

    python autostale.py scan FILE... [--root DIR]         # re-hash, mark what was built from a changed file stale
    python autostale.py status FILE... [--root DIR]       # print path, status, version, built_from
    python autostale.py revalidate FILE [--root DIR] [--from PATHS]   # a person has checked it: status valid, hash refreshed

An artifact is a text file that starts with a small front-matter block (anything else is not tracked and is never stale):

    ---
    artifact: v1
    version: 3
    hash: <sha256 of the body below the block, line endings folded to \\n>
    built_from: docs/spec.md, docs/plan.md
    status: valid            (valid | stale | superseded)
    sources_hash: <sha256>   (optional: what the built_from files held when the doc was last checked; see below)
    ---

Rules:
- The hash covers the body only, so rewriting the header or saving the same text again changes nothing.
- An artifact whose body no longer matches its recorded hash has changed: `scan` records the new hash, bumps the version and
  marks every artifact built from it, directly or through others, `stale` (`mark_descendants_stale`); siblings and parents are
  untouched. A stale artifact stays stale until a person revalidates it; nothing here decides whether it is still right.
- A cycle in `built_from` is reported (`CycleError` naming it) and nothing is marked.
- `stale(root, paths)` lists the required artifacts that are stale; `autodeliver.resume` refuses to resume an item while one is.
- Code watched by a doc (doc map entries with `stamp: true`, Q11): `built_from` lists plain files and `sources_hash`
  (`sources_hash()`) records their content when a person last checked the doc. `revalidate FILE --from a.py,b.py` stamps a
  doc that has no header yet, or replaces its sources; `revalidate` refreshes `sources_hash` whenever the header has one.
  The runner's docs gate (autodocs.stale_docs) compares it at the item's head commit; a header without the line is unchanged.
Paths are relative to the project root and must stay inside it. No gate logic and no AI. Stdlib only.
"""
import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path

SCHEMA = 'v1'
STATUSES = ('valid', 'stale', 'superseded')
FENCE = '---'
KEYS = ('artifact', 'version', 'hash', 'built_from', 'status', 'sources_hash')


class CycleError(ValueError):
    def __init__(self, cycle):
        self.cycle = cycle
        super().__init__('built_from has a cycle: ' + ' -> '.join(cycle))


class StaleArtifact(Exception):
    def __init__(self, paths):
        self.paths = list(paths)
        super().__init__('stale artifact(s): ' + ', '.join(self.paths) + '; revalidate before resuming')


def _fold(text):
    return text.replace('\r\n', '\n').replace('\r', '\n')


def content_hash(body):
    return hashlib.sha256(_fold(body).encode('utf-8')).hexdigest()


def parse(text):
    """-> (header dict or None, body). A block without `artifact:` is someone else's front matter: not tracked."""
    text = _fold(text)
    lines = text.split('\n')
    if not lines or lines[0].strip() != FENCE:
        return None, text
    for end in range(1, len(lines)):
        if lines[end].strip() == FENCE:
            break
    else:
        return None, text
    fields = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(':')
        if sep:
            fields[key.strip()] = value.strip()
    if fields.get('artifact') != SCHEMA:
        return None, text
    body = '\n'.join(lines[end + 1:])
    try:
        version = int(fields.get('version') or 0)
    except ValueError:
        raise ValueError(f'version: not a number ({fields.get("version")!r})')
    status = fields.get('status') or 'valid'
    if status not in STATUSES:
        raise ValueError(f'status: {status!r} is not one of {", ".join(STATUSES)}')
    built = [p.strip() for p in (fields.get('built_from') or '').split(',') if p.strip()]
    header = {'version': version, 'hash': fields.get('hash') or '', 'built_from': built, 'status': status}
    if fields.get('sources_hash'):
        header['sources_hash'] = fields['sources_hash']
    return header, body


def render(header, body):
    extra = [f'sources_hash: {header["sources_hash"]}'] if header.get('sources_hash') else []
    return '\n'.join([FENCE, f'artifact: {SCHEMA}', f'version: {header["version"]}', f'hash: {header["hash"]}',
                      f'built_from: {", ".join(header["built_from"])}', f'status: {header["status"]}', *extra,
                      FENCE]) + '\n' + body


def sources_hash(paths, read_text):
    """One sha256 over the files in `paths` (sorted, each by its content hash; a tracked artifact counts by its body only, so
    re-stamping its header changes nothing; a missing file counts as missing). `read_text(path)` -> text or None, so the
    same rule works on the working tree and on a git commit."""
    h = hashlib.sha256()
    for rel in sorted(set(paths)):
        text = read_text(rel)
        if text is None:
            digest = 'missing'
        else:
            try:
                header, body = parse(text)
            except ValueError:
                header, body = None, text
            digest = content_hash(body if header is not None else text)
        h.update(f'{rel}\0{digest}\n'.encode('utf-8'))
    return h.hexdigest()


def _disk_reader(root):
    def read_text(rel):
        try:
            p = _inside(root, rel)
        except ValueError:
            return None
        return p.read_text(encoding='utf-8') if p.is_file() else None
    return read_text


def _inside(root, rel):
    root = Path(root).resolve()
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise ValueError(f'{rel}: outside the project')
    return p


def read(root, rel):
    """-> (header or None, body); a missing file is (None, None)."""
    p = _inside(root, rel)
    if not p.is_file():
        return None, None
    return parse(p.read_text(encoding='utf-8'))


def write(root, rel, header, body):
    p = _inside(root, rel)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix='.tmp-', suffix=p.suffix)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(render(header, body))
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def stamp(root, rel, built_from=None):
    """Give `rel` a header (or refresh its hash). -> True if the file was written; same body and same sources = no write."""
    header, body = read(root, rel)
    if body is None:
        raise FileNotFoundError(rel)
    h = content_hash(body)
    sources = list(built_from) if built_from is not None else (header['built_from'] if header else [])
    if header and header['hash'] == h and header['built_from'] == sources:
        return False
    new = {'version': (header['version'] if header else 0) + (0 if header and header['hash'] == h else 1), 'hash': h,
           'built_from': sources, 'status': header['status'] if header else 'valid'}
    if header and header.get('sources_hash'):
        new['sources_hash'] = header['sources_hash']
    write(root, rel, new, body)
    return True


def changed(root, rel):
    header, body = read(root, rel)
    return header is not None and header['hash'] != content_hash(body)


def graph_of(root, paths):
    """-> {path: [built_from...]} for the tracked artifacts among `paths`."""
    out = {}
    for rel in paths:
        header, _ = read(root, rel)
        if header is not None:
            out[rel] = list(header['built_from'])
    return out


def find_cycle(graph):
    """-> one cycle as a list of paths (first repeated at the end), or None."""
    state = {}

    def visit(node, trail):
        state[node] = 'open'
        trail.append(node)
        for src in graph.get(node, ()):
            if state.get(src) == 'open':
                return trail[trail.index(src):] + [src]
            if src not in state:
                found = visit(src, trail)
                if found:
                    return found
        trail.pop()
        state[node] = 'done'
        return None
    for node in sorted(graph):
        if node not in state:
            found = visit(node, [])
            if found:
                return found
    return None


def mark_descendants_stale(changed_paths, graph):
    """Everything built, directly or through others, from `changed_paths` -> sorted list (the changed ones excluded).
    Pure: `graph` maps an artifact to the artifacts it was built from. Raises CycleError on a cycle."""
    cycle = find_cycle(graph)
    if cycle:
        raise CycleError(cycle)
    children = {}
    for node, sources in graph.items():
        for src in sources:
            children.setdefault(src, set()).add(node)
    seen, todo = set(), list(changed_paths)
    while todo:
        for child in children.get(todo.pop(), ()):
            if child not in seen:
                seen.add(child)
                todo.append(child)
    return sorted(seen - set(changed_paths))


def scan(root, paths):
    """Re-hash `paths`; for each changed one record the new hash and mark its descendants stale.
    -> {'changed': [...], 'stale': [...newly marked...]}. Unchanged input = nothing written."""
    graph = graph_of(root, paths)
    moved = sorted(p for p in graph if changed(root, p))
    if not moved:
        return {'changed': [], 'stale': []}
    descendants = mark_descendants_stale(moved, graph)      # raises before anything is written
    for p in moved:
        stamp(root, p)
    marked = []
    for p in descendants:
        header, body = read(root, p)
        if header['status'] != 'stale':
            header['status'] = 'stale'
            write(root, p, header, body)
            marked.append(p)
    return {'changed': moved, 'stale': marked}


def revalidate(root, rel, sources=None):
    """A person has checked `rel` against its sources: status valid and hash refreshed. `sources` (a list of files) stamps a
    doc without a header, or replaces its built_from; with sources, or a header that already has `sources_hash`, that
    hash is recomputed from the files as they are now."""
    header, body = read(root, rel)
    if body is None and sources is not None:
        raise FileNotFoundError(rel)
    if header is None:
        if sources is None:
            raise ValueError(f'{rel}: not a tracked artifact (give --from to stamp it)')
        header = {'version': 0, 'hash': '', 'built_from': [], 'status': 'valid'}
    h = content_hash(body)
    new = {**header, 'status': 'valid', 'hash': h, 'version': header['version'] + (0 if header['hash'] == h else 1)}
    if sources is not None:
        new['built_from'] = list(sources)
    if sources is not None or header.get('sources_hash'):
        new['sources_hash'] = sources_hash(new['built_from'], _disk_reader(root))
    if new == header:
        return False
    write(root, rel, new, body)
    return True


def stale(root, paths):
    """The tracked artifacts among `paths` whose status is stale (missing or untracked files are not)."""
    out = []
    for rel in paths:
        try:
            header, _ = read(root, rel)
        except (ValueError, OSError):                       # outside the project, unreadable or not a path at all
            continue
        if header is not None and header['status'] == 'stale':
            out.append(rel)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=['scan', 'status', 'revalidate'])
    ap.add_argument('files', nargs='+')
    ap.add_argument('--root', default=os.environ.get('CLAUDE_PROJECT_DIR') or '.')
    ap.add_argument('--from', dest='sources', default=None,
                    help='revalidate only: comma-separated files the doc describes (stamps a doc without a header)')
    a = ap.parse_args(argv)
    sources = [s.strip() for s in a.sources.split(',') if s.strip()] if a.sources is not None else None
    try:
        if a.command == 'scan':
            r = scan(a.root, a.files)
            print(f'changed: {", ".join(r["changed"]) or "(none)"}')
            print(f'marked stale: {", ".join(r["stale"]) or "(none)"}')
        elif a.command == 'status':
            for rel in a.files:
                header, body = read(a.root, rel)
                if body is None:
                    print(f'{rel}\tmissing')
                elif header is None:
                    print(f'{rel}\tuntracked')
                else:
                    print(f'{rel}\t{header["status"]}\tv{header["version"]}\t{", ".join(header["built_from"])}')
        else:
            for rel in a.files:
                print(f'{rel}: ' + ('revalidated' if revalidate(a.root, rel, sources) else 'already valid'))
    except (CycleError, ValueError, FileNotFoundError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
