"""
Update the installed auto-runner plugin from its marketplace (`/auto-runner:update`).

    python autoupdate.py check     # refresh the marketplace; print installed and available versions and the changelog between
    python autoupdate.py apply     # install the newest version (`claude plugin update`); a restart loads it

Both steps go through the `claude` command line, never by editing Claude's plugin files: `check` runs
`claude plugin marketplace update <marketplace> --json` (it downloads the marketplace's catalogue, nothing is installed) and then
reads the plugin version and CHANGELOG.md from the refreshed marketplace copy (`installLocation` in
`~/.claude/plugins/known_marketplaces.json`); `apply` runs `claude plugin update auto-runner@<marketplace> --json` and reports the
old and new version from its reply. The installed version is this copy's own `.claude-plugin/plugin.json`. A missing `claude`
command, a failed refresh or an unreadable reply is reported with what to do by hand; nothing is retried. Stdlib only.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = 'auto-runner'
MARKETPLACE = 'flexsin-claude-plugins'
PLUGIN_ID = f'{PLUGIN}@{MARKETPLACE}'
TIMEOUT = 300
VERSION = re.compile(r'^(\d+)\.(\d+)\.(\d+)$')
HEADING = re.compile(r'^## \[(\d+\.\d+\.\d+)\]')
BY_HAND = (f'claude plugin marketplace update {MARKETPLACE}', f'claude plugin update {PLUGIN_ID}')


class UpdateError(RuntimeError):
    """The update could not be checked or applied; the message says what to do instead."""


def parse_version(text):
    m = VERSION.match((text or '').strip())
    return tuple(int(x) for x in m.groups()) if m else None


def installed_version(plugin_dir=HERE.parent):
    return json.loads((Path(plugin_dir) / '.claude-plugin' / 'plugin.json').read_text(encoding='utf-8'))['version']


def config_dir(env=None):
    env = os.environ if env is None else env
    return Path(env.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')


def marketplace_dir(env=None):
    """-> the folder of the refreshed marketplace copy, or None when Claude does not know the marketplace."""
    try:
        known = json.loads((config_dir(env) / 'plugins' / 'known_marketplaces.json').read_text(encoding='utf-8'))
        location = known[MARKETPLACE]['installLocation']
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return Path(location) if location else None


def changes_between(changelog, old, new):
    """-> the CHANGELOG.md sections of versions above `old` up to and including `new`, newest first, as text."""
    lo, hi = parse_version(old), parse_version(new)
    out, keep = [], False
    for line in changelog.splitlines():
        m = HEADING.match(line)
        if m or line.startswith('## '):
            v = parse_version(m.group(1)) if m else None
            keep = v is not None and lo is not None and hi is not None and lo < v <= hi
        if keep:
            out.append(line)
    return '\n'.join(out).strip()


def claude(args, run=subprocess.run, which=shutil.which):
    """Run `claude <args> --json` -> the parsed last line. Raises UpdateError naming the command to run by hand."""
    exe = which('claude')
    if not exe:
        raise UpdateError('the `claude` command was not found on PATH. Run by hand: ' + ' ; '.join(BY_HAND))
    try:
        r = run([exe, *args, '--json'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpdateError(f'`claude {" ".join(args)}` did not finish ({type(e).__name__}). Run by hand: ' + ' ; '.join(BY_HAND))
    lines = [ln for ln in (r.stdout or '').splitlines() if ln.strip()]
    try:
        reply = json.loads(lines[-1])
    except (IndexError, ValueError):
        reply = None
    if r.returncode != 0 or not isinstance(reply, dict) or reply.get('outcome') != 'ok':
        message = reply.get('message') if isinstance(reply, dict) else (r.stderr or r.stdout or '').strip()
        raise UpdateError(f'`claude {" ".join(args)}` failed: {str(message)[:300]}')
    return reply


def check(run=subprocess.run, which=shutil.which, env=None, plugin_dir=HERE.parent):
    """Refresh the marketplace -> {installed, available, newer, changes}."""
    claude(['plugin', 'marketplace', 'update', MARKETPLACE], run, which)
    installed = installed_version(plugin_dir)
    where = marketplace_dir(env)
    if where is None:
        raise UpdateError(f'Claude does not list the marketplace {MARKETPLACE}; add it with '
                          f'`claude plugin marketplace add aloks-flexsin/{MARKETPLACE}`')
    try:
        available = installed_version(where / PLUGIN)
    except (OSError, ValueError, KeyError) as e:
        raise UpdateError(f'the marketplace copy has no readable {PLUGIN} version ({type(e).__name__})')
    newer = (parse_version(available) or ()) > (parse_version(installed) or ())
    log = where / 'CHANGELOG.md'
    changes = changes_between(log.read_text(encoding='utf-8'), installed, available) if newer and log.exists() else ''
    return {'installed': installed, 'available': available, 'newer': newer, 'changes': changes}


def apply(run=subprocess.run, which=shutil.which):
    """Install the newest version -> {old, new, outcome} from claude's reply."""
    reply = claude(['plugin', 'update', PLUGIN_ID], run, which)
    return {'old': reply.get('oldVersion'), 'new': reply.get('newVersion'), 'outcome': reply.get('updateOutcome')}


def main(argv=None, out=sys.stdout, **deps):
    ap = argparse.ArgumentParser(prog='autoupdate.py', description=__doc__.strip().splitlines()[0])
    ap.add_argument('action', choices=('check', 'apply'))
    args = ap.parse_args(argv)
    try:
        if args.action == 'check':
            r = check(**deps)
            print(f"installed: {r['installed']}", file=out)
            print(f"available: {r['available']}", file=out)
            if not r['newer']:
                print('auto-runner is up to date.', file=out)
            else:
                print(f"an update is available: {r['installed']} -> {r['available']}", file=out)
                print(r['changes'] or '(the changelog has no entries for these versions)', file=out)
        else:
            r = apply(**{k: v for k, v in deps.items() if k in ('run', 'which')})
            if r['outcome'] == 'up_to_date':
                print(f"auto-runner is already at the latest version ({r['new']}).", file=out)
            else:
                print(f"auto-runner updated: {r['old']} -> {r['new']}. Restart Claude to load it.", file=out)
    except UpdateError as e:
        print(f'update not done: {e}', file=out)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
