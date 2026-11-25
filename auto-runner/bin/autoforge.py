"""Forge adapter for the Supervisor (decision 0010): the few calls it needs on a pull request. Standard library only.

`Forge` is the interface: `pr`, `changed_files`, `checks`, `reviews` (all read-only) and `merge(pr, sha, method)`.
It has NO push, commit or branch-write method on purpose: the Supervisor can read and merge a pull request at an exact
head SHA, and nothing else. `GithubForge` implements it over `urllib` with the token from
`AUTO_SUPERVISOR_GITHUB_TOKEN`; the token is only ever put in the Authorization header, never in a log line, an error
message or an exception. The opener is injectable (`opener(request, timeout) -> response`) so tests use a recording fake.
`FakeForge` for tests lives in `tests/auto_runner/fake_forge.py`.
"""
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger('autoforge')

TOKEN_ENV = 'AUTO_SUPERVISOR_GITHUB_TOKEN'
API = 'https://api.github.com'
MERGE_METHODS = ('merge', 'squash', 'rebase')
MAX_PAGES = 10


class ForgeError(Exception):
    """A forge call failed. `status` is the HTTP status (0 when none). The message never holds the token."""

    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class Forge:
    """What the Supervisor may do on a forge. Read-only except `merge`; there is deliberately no way to write a branch."""

    def pr(self, number):
        """-> {number, state, draft, merged, head_sha, base, head_ref, url, author}"""
        raise NotImplementedError

    def changed_files(self, number):
        """-> list of changed file paths"""
        raise NotImplementedError

    def checks(self, number):
        """-> list of {name, state} for the PR head, state one of success, failure, pending"""
        raise NotImplementedError

    def reviews(self, number):
        """-> list of {user, state, commit_id} (state: APPROVED, CHANGES_REQUESTED, COMMENTED, ...)"""
        raise NotImplementedError

    def merge(self, pr, sha, method):
        """Merge pull request `pr` only if its head is still `sha`. -> {merged: bool, sha}. Raises ForgeError if refused."""
        raise NotImplementedError


def _check_state(run):
    if run.get('status') != 'completed':
        return 'pending'
    return 'success' if run.get('conclusion') in ('success', 'neutral', 'skipped') else 'failure'


class GithubForge(Forge):
    def __init__(self, repo, token=None, opener=None, api=API, timeout=30):
        self.repo = repo
        self._token = token if token is not None else os.environ.get(TOKEN_ENV, '')
        self._open = opener or urllib.request.urlopen
        self.api = api.rstrip('/')
        self.timeout = timeout

    def __repr__(self):
        return f'GithubForge({self.repo!r})'

    def _call(self, method, path, body=None):
        if not self._token:
            raise ForgeError(f'{TOKEN_ENV} is not set')
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            'Authorization': 'Bearer ' + self._token,
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'auto-runner-supervisor',
        }
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.api + path, data=data, method=method, headers=headers)
        try:
            with self._open(req, timeout=self.timeout) as resp:
                status, raw = getattr(resp, 'status', 200), resp.read()
        except urllib.error.HTTPError as e:
            log.warning('%s %s -> %s', method, path, e.code)
            raise ForgeError(f'{method} {path} failed with HTTP {e.code}', e.code) from None
        except (urllib.error.URLError, OSError) as e:
            log.warning('%s %s -> network error', method, path)
            raise ForgeError(f'{method} {path} failed: {type(e).__name__}') from None
        log.info('%s %s -> %s', method, path, status)
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            raise ForgeError(f'{method} {path} returned invalid JSON', status) from None

    def _pages(self, path):
        sep = '&' if '?' in path else '?'
        out = []
        for page in range(1, MAX_PAGES + 1):
            chunk = self._call('GET', f'{path}{sep}per_page=100&page={page}')
            if isinstance(chunk, dict):  # check-runs wraps its list
                chunk = chunk.get('check_runs', [])
            chunk = chunk or []
            out.extend(chunk)
            if len(chunk) < 100:
                break
        return out

    def pr(self, number):
        d = self._call('GET', f'/repos/{self.repo}/pulls/{int(number)}')
        return {'number': d['number'], 'state': d['state'], 'draft': bool(d.get('draft')),
                'merged': bool(d.get('merged')), 'head_sha': d['head']['sha'], 'base': d['base']['ref'],
                'head_ref': d['head']['ref'], 'url': d.get('html_url', ''),
                'author': (d.get('user') or {}).get('login', '')}

    def changed_files(self, number):
        return [f['filename'] for f in self._pages(f'/repos/{self.repo}/pulls/{int(number)}/files')]

    def checks(self, number):
        sha = self.pr(number)['head_sha']
        runs = self._pages(f'/repos/{self.repo}/commits/{sha}/check-runs')
        return [{'name': r.get('name', ''), 'state': _check_state(r)} for r in runs]

    def reviews(self, number):
        return [{'user': (r.get('user') or {}).get('login', ''), 'state': r.get('state', ''),
                 'commit_id': r.get('commit_id', '')}
                for r in self._pages(f'/repos/{self.repo}/pulls/{int(number)}/reviews')]

    def merge(self, pr, sha, method):
        if method not in MERGE_METHODS:
            raise ForgeError(f'unknown merge method {method!r}')
        d = self._call('PUT', f'/repos/{self.repo}/pulls/{int(pr)}/merge', {'sha': sha, 'merge_method': method}) or {}
        return {'merged': bool(d.get('merged')), 'sha': d.get('sha', '')}
