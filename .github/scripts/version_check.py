"""Version control for every pull request into main (the `version` check). Standard library only.

    python .github/scripts/version_check.py --base origin/main [--no-changelog]

1. The plugin's version agrees everywhere: `auto-runner/.claude-plugin/plugin.json`, the auto-runner entry in
   `.claude-plugin/marketplace.json` and the newest `## [x.y.z]` heading of `CHANGELOG.md`.
2. A pull request that changes the plugin (anything under `auto-runner/` except its README and docs) adds a line to
   `CHANGELOG.md` (normally under `## [Unreleased]`), so every merge says what it changed. A pull request labelled
   `no-changelog` (`--no-changelog`) skips this; nothing else does.
3. A pull request that changes the version moves it forward (never back, never sideways).
Exit 0 when every rule holds, 1 with one line per problem otherwise.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = 'auto-runner/.claude-plugin/plugin.json'
MARKETPLACE = '.claude-plugin/marketplace.json'
CHANGELOG = 'CHANGELOG.md'
HEADING = re.compile(r'^## \[(\d+)\.(\d+)\.(\d+)\]', re.M)
NO_ENTRY_NEEDED = re.compile(r'^auto-runner/(README\.md|MIGRATING-[^/]*\.md|docs/)')


def plugin_version(text):
    return json.loads(text)['version']


def marketplace_version(text):
    data = json.loads(text)
    return next(p['version'] for p in data['plugins'] if p.get('name') == 'auto-runner')


def changelog_version(text):
    found = [tuple(map(int, m.groups())) for m in HEADING.finditer(text)]
    return '.'.join(map(str, max(found))) if found else None


def parse(version):
    return tuple(int(x) for x in version.split('.'))


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', check=True).stdout


def at(ref, path):
    return git('show', f'{ref}:{path}')


def problems(read, changed, added_changelog_lines, base_read, no_changelog=False):
    """The rules, on injected readers (`read(path)` for this branch, `base_read(path)` for the base) -> [problem]."""
    out = []
    versions = {PLUGIN: plugin_version(read(PLUGIN)), MARKETPLACE: marketplace_version(read(MARKETPLACE)),
                CHANGELOG: changelog_version(read(CHANGELOG))}
    if len(set(versions.values())) != 1:
        out.append('the version differs: ' + ', '.join(f'{k} says {v}' for k, v in versions.items())
                   + '. Release with `python auto-runner/bin/autochange.py release --apply`, which updates all three.')
    plugin_changed = [f for f in changed if f.startswith('auto-runner/') and not NO_ENTRY_NEEDED.match(f)]
    if plugin_changed and not added_changelog_lines and not no_changelog:
        out.append(f'{len(plugin_changed)} plugin file(s) changed (e.g. {plugin_changed[0]}) but {CHANGELOG} gained no '
                   'entry: add a line under "## [Unreleased]" saying what changed (or label the pull request '
                   '"no-changelog" when nothing a user notices changed).')
    before, now = base_read(PLUGIN), versions[PLUGIN]
    if before is not None and parse(now) < parse(plugin_version(before)):
        out.append(f'the version goes back from {plugin_version(before)} to {now}')
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default='origin/main')
    ap.add_argument('--no-changelog', action='store_true')
    a = ap.parse_args(argv)
    changed = [f for f in git('diff', '--name-only', f'{a.base}...HEAD').splitlines() if f]
    diff = git('diff', '--unified=0', f'{a.base}...HEAD', '--', CHANGELOG)
    added = [ln for ln in diff.splitlines() if ln.startswith('+') and not ln.startswith('+++') and ln[1:].strip()]

    def base_read(path):
        try:
            return at(a.base, path)
        except subprocess.CalledProcessError:
            return None
    found = problems(lambda p: (ROOT / p).read_text(encoding='utf-8'), changed, added, base_read, a.no_changelog)
    for p in found:
        print(f'version check: {p}')
    if not found:
        print(f'version check: ok ({plugin_version((ROOT / PLUGIN).read_text(encoding="utf-8"))}, '
              f'{len(changed)} file(s) changed)')
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
