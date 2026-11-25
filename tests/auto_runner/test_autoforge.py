import io
import json
import logging
import urllib.error

import pytest

from tests.auto_runner import fake_forge
from tests.auto_runner.fake_forge import Forge, ForgeError  # same classes the module exports
from autoforge import GithubForge

TOKEN = 'ghp_SECRET_TOKEN_123'


class Resp:
    def __init__(self, body, status=200):
        self.status, self._b = status, json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    """Recorded fake opener: answers by (method, path); records every request."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, req, timeout=None):
        path = req.full_url.split('api.github.com', 1)[1]
        self.calls.append((req.get_method(), path, dict(req.header_items()), req.data))
        r = self.routes.get((req.get_method(), path.split('?')[0]))
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise urllib.error.HTTPError(req.full_url, 404, 'nf', {}, io.BytesIO(b''))
        return Resp(r)


PR = {'number': 7, 'state': 'open', 'draft': False, 'merged': False, 'html_url': 'https://x/pull/7',
      'user': {'login': 'dev'}, 'head': {'sha': 'abc', 'ref': 'u/a/feat/x'}, 'base': {'ref': 'main'}}


def forge(routes):
    op = Opener(routes)
    return GithubForge('o/r', token=TOKEN, opener=op), op


def test_pr_files_checks_reviews():
    runs = [{'name': 't', 'status': 'completed', 'conclusion': 'success'},
            {'name': 'l', 'status': 'completed', 'conclusion': 'failure'},
            {'name': 'p', 'status': 'in_progress', 'conclusion': None}]
    f, op = forge({
        ('GET', '/repos/o/r/pulls/7'): PR,
        ('GET', '/repos/o/r/pulls/7/files'): [{'filename': 'a.py'}, {'filename': 'b/c.md'}],
        ('GET', '/repos/o/r/commits/abc/check-runs'): {'check_runs': runs},
        ('GET', '/repos/o/r/pulls/7/reviews'): [{'user': {'login': 'r'}, 'state': 'APPROVED', 'commit_id': 'abc'}],
    })
    assert f.pr(7) == {'number': 7, 'state': 'open', 'draft': False, 'merged': False, 'head_sha': 'abc',
                       'base': 'main', 'head_ref': 'u/a/feat/x', 'url': 'https://x/pull/7', 'author': 'dev'}
    assert f.changed_files(7) == ['a.py', 'b/c.md']
    assert [c['state'] for c in f.checks(7)] == ['success', 'failure', 'pending']
    assert f.reviews(7) == [{'user': 'r', 'state': 'APPROVED', 'commit_id': 'abc'}]
    assert all(h['Authorization'] == 'Bearer ' + TOKEN for _, _, h, _ in op.calls)


def test_pagination_follows_full_pages():
    calls = []
    pages = {'1': [{'filename': 'f%d' % i} for i in range(100)], '2': [{'filename': 'last'}]}

    def opener(req, timeout=None):
        calls.append(req.full_url)
        return Resp(pages[req.full_url.rsplit('&page=', 1)[1]])
    files = GithubForge('o/r', token=TOKEN, opener=opener).changed_files(7)
    assert len(files) == 101 and files[-1] == 'last' and len(calls) == 2


def test_merge_sends_sha_and_method():
    f, op = forge({('PUT', '/repos/o/r/pulls/7/merge'): {'merged': True, 'sha': 'm1'}})
    assert f.merge(7, 'abc', 'squash') == {'merged': True, 'sha': 'm1'}
    assert json.loads(op.calls[0][3]) == {'sha': 'abc', 'merge_method': 'squash'}
    with pytest.raises(ForgeError):
        f.merge(7, 'abc', 'force')


def test_refused_merge_raises_with_status():
    err = urllib.error.HTTPError('u', 409, 'conflict', {}, io.BytesIO(b'{}'))
    f, _ = forge({('PUT', '/repos/o/r/pulls/7/merge'): err})
    with pytest.raises(ForgeError) as e:
        f.merge(7, 'abc', 'merge')
    assert e.value.status == 409


def test_missing_token_refused_without_calling():
    op = Opener({})
    with pytest.raises(ForgeError):
        GithubForge('o/r', token='', opener=op).pr(1)
    assert op.calls == []


def test_interface_exposes_no_write_to_branch_method():
    public = {n for n in dir(Forge) if not n.startswith('_')}
    assert public == {'pr', 'changed_files', 'checks', 'base_checks', 'reviews', 'merge'}
    for cls in (GithubForge, fake_forge.FakeForge):
        extra = {n for n in dir(cls) if not n.startswith('_')} - public
        assert not [n for n in extra if any(w in n for w in ('push', 'commit', 'write', 'update', 'branch', 'ref'))]


def test_token_never_in_logged_line_or_error(caplog):
    caplog.set_level(logging.DEBUG, logger='autoforge')
    err = urllib.error.HTTPError('u', 401, 'bad', {}, io.BytesIO(b''))
    f, _ = forge({('GET', '/repos/o/r/pulls/7'): err, ('GET', '/repos/o/r/pulls/8'): PR,
                  ('PUT', '/repos/o/r/pulls/8/merge'): {'merged': True, 'sha': 'm'}})
    with pytest.raises(ForgeError) as e:
        f.pr(7)
    f.pr(8)
    f.merge(8, 'abc', 'merge')
    assert caplog.records
    assert TOKEN not in caplog.text and TOKEN not in str(e.value) and TOKEN not in repr(f)


def test_fake_forge_merges_at_exact_sha():
    ff = fake_forge.FakeForge({3: {'pr': {'number': 3, 'head_sha': 's1', 'merged': False, 'state': 'open'}}})
    with pytest.raises(ForgeError):
        ff.merge(3, 'other', 'squash')
    assert ff.merge(3, 's1', 'squash')['merged'] and ff.merges == [(3, 's1', 'squash')]
