"""
Join a project that is on the central service: read its repository URL there, clone it with your own git credentials, then
show init's preview in the new folder. Shows a preview; nothing is cloned without --apply.

    python auto_join.py acme-web --central-url https://central.example.com              # preview
    python auto_join.py acme-web --central-url https://central.example.com --apply      # clone, then init's preview
    python auto_join.py acme-web --central-url ... --dest D:/work/acme-web --apply      # clone into a folder you chose

- The token comes only from the environment variable --token-env names (default AUTO_RUNNER_TOKEN_<SLUG>, the one init
  writes into `central.token_env`), and must be that project's. Without it, join says how to make and store one.
- The folder is --dest, else the repository's name in the current folder; join refuses one that exists.
- `git clone -- <repo_url> <folder>` runs with your own git setup (credential manager, ssh keys). The central service never
  holds git credentials, and only https://, ssh:// and user@host:owner/repo URLs are cloned.
- After cloning it prints init's preview there (with the same central options); `auto_init.py --apply` writes it.
"""
import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


autocentral = _load('autorunner_central', 'autocentral.py')
auto_init = _load('autorunner_init', 'auto_init.py')
autodeliver = autocentral.autodeliver


def clonable(url):
    """True for https://, ssh:// and user@host:owner/repo URLs that name a repository (never an option or a local path)."""
    url = (url or '').strip()
    if autocentral.repo_key(url) is None or url.startswith('-'):
        return False
    return url.startswith(('https://', 'ssh://')) or ('://' not in url and autocentral.SCP.match(url) is not None)


def _name(repo_url):
    return autocentral.repo_key(repo_url).rsplit('/', 1)[1]


def make_plan(slug, central_url, token_env='', dest='', cwd='.', env=None, send=None):
    """-> {'slug', 'central_url', 'token_env', 'repo_url', 'dest', 'problems', 'steps'}. Reads only (one GET)."""
    env = os.environ if env is None else env
    slug, central_url = (slug or '').strip().lower(), (central_url or '').strip().rstrip('/')
    name = (token_env or '').strip() or autocentral.default_token_env(slug)
    plan = {'slug': slug, 'central_url': central_url, 'token_env': name, 'repo_url': None, 'dest': None,
            'problems': [], 'steps': []}
    if not autocentral.SLUG.match(slug):
        plan['problems'].append('the project slug must be lower-case letters, digits and dashes.')
    if not auto_init._url_ok(central_url):
        plan['problems'].append('--central-url must be an https:// address (http only for localhost).')
    if name.startswith(auto_init.autosafe.CENTRAL_TOKEN_PREFIX) or not auto_init.autosafe.ENV_NAME.match(name):
        plan['problems'].append('--token-env must be an environment variable name (letters, digits and _), not a token.')
    if plan['problems']:
        return plan
    token = (env.get(name) or '').strip()
    if not token:
        plan['problems'].append(f'the token is not in {name} yet.')
        plan['steps'] = autocentral.token_steps(central_url, slug, name)
        return plan
    try:
        info = autocentral.lookup(central_url, token, send=send)
    except autodeliver.TransportRefused as e:
        plan['problems'].append(f'the central service refused the token in {name} ({e}).')
        return plan
    except autodeliver.TransportUnavailable as e:
        plan['problems'].append(f'the central service at {central_url} could not be reached ({e}). Try again.')
        return plan
    if info.get('slug') != slug:
        plan['problems'].append(f'the token in {name} belongs to project "{info.get("slug")}", not "{slug}".')
        return plan
    repo_url = info.get('repo_url')
    if not repo_url:
        plan['problems'].append(f'project "{slug}" has no repository URL yet: an owner or admin sets it on '
                                f'{central_url}/projects/{slug}.')
        return plan
    if not clonable(repo_url):
        plan['problems'].append(f'the repository URL {autocentral.clean_url(repo_url)} is not one join clones '
                                '(https://, ssh:// or user@host:owner/repo).')
        return plan
    plan['repo_url'] = repo_url
    target = Path(dest) if dest else Path(cwd) / _name(repo_url)
    plan['dest'] = target.resolve()
    if plan['dest'].exists():
        plan['problems'].append(f'{plan["dest"]} already exists; choose another folder with --dest.')
    return plan


def describe(plan):
    if plan['problems']:
        lines = [f'REFUSED: {p}' for p in plan['problems']]
        if plan['steps']:
            lines.append('To get a token:')
            lines += ['  ' + s for s in plan['steps']]
        return lines + ['Nothing was cloned.']
    return [f'Project "{plan["slug"]}" on {plan["central_url"]}: repository {plan["repo_url"]}',
            f'Would run: git clone -- {plan["repo_url"]} "{plan["dest"]}"  (with your own git credentials)',
            'Then init\'s preview runs in that folder, with the central service already chosen.']


def init_args(plan):
    return ['--project', str(plan['dest']), '--central-url', plan['central_url'], '--central-project', plan['slug'],
            '--token-env', plan['token_env']]


def clone(plan, run=subprocess.run):
    """Run git clone for an accepted plan -> the exit code (git prints its own progress and errors)."""
    if plan['problems']:
        raise SystemExit('Not cloned: ' + '; '.join(plan['problems']))
    return run(['git', 'clone', '--', plan['repo_url'], str(plan['dest'])]).returncode


def main(argv=None, env=None, send=None, run=subprocess.run):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('slug', help="the project's slug on the central service")
    ap.add_argument('--central-url', required=True, help='the central service, e.g. https://auto-runner-central.vercel.app')
    ap.add_argument('--token-env', default='', help='the variable that holds the token (default AUTO_RUNNER_TOKEN_<SLUG>)')
    ap.add_argument('--dest', default='', help='the folder to clone into (default: the repository name, here)')
    ap.add_argument('--apply', action='store_true', help='clone (default: preview only)')
    a = ap.parse_args(argv)
    env = os.environ if env is None else env
    plan = make_plan(a.slug, a.central_url, a.token_env, a.dest, os.getcwd(), env, send)
    for line in describe(plan):
        print(line)
    if plan['problems']:
        return 1
    if not a.apply:
        print('Preview only. Add --apply to clone.')
        return 0
    code = clone(plan, run)
    if code != 0:
        print(f'git clone failed (exit {code}); nothing else was done.')
        return code
    print(f'Cloned into {plan["dest"]}. Init\'s preview there:')
    init_plan = auto_init.make_plan(plan['dest'], {'url': plan['central_url'], 'project': plan['slug'],
                                                   'token_env': plan['token_env']}, env=env, send=send)
    for line in auto_init.describe(init_plan):
        print(f'  {line}')
    print('To write it: python auto_init.py ' + ' '.join(f'"{x}"' if ' ' in x else x for x in init_args(plan)) + ' --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())
