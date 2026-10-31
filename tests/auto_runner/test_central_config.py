"""auto-runner plugin: the runner talks to the central service when `.claude/auto.json` turns it on (roadmap C10).
No network: HttpTransport's default `send` is replaced by a fake that routes to the in-memory FakeCentral."""
import json
import subprocess
import sys
from urllib.parse import unquote

import pytest

from tests.auto_runner.fake_central import FakeCentral
from tests.auto_runner.test_runner import BLOCKER, ar, blocking_agent, events, fake_agent, picking_agent, project, \
    project_statuses, set_cfg, worktree  # noqa: F401  (project is a fixture)

ab = ar.autoblock
ad = ar.autodeliver
ENV = ar.autosafe.CENTRAL_TOKEN_ENV
TOKEN = 'art_' + 'S3cretTokenValue' * 3
URL = 'https://central.example.test'
SLUG = 'acme-web'                                 # differs from the project folder name on purpose
CENTRAL = {'enabled': True, 'url': URL, 'project': SLUG}


def fake_send(central, seen):
    """The HTTP binding of docs/delivery-contract.md, answered from `central` (a FakeCentral)."""
    def send(method, url, headers, body, timeout):
        seen.append((method, url, dict(headers)))
        path = url[len(URL):]
        if method == 'GET' and path == '/api/v1/deliveries/pending':
            return 200, json.dumps({'deliveries': central.pull_pending(SLUG)}).encode()
        if method == 'POST' and path.startswith('/api/v1/deliveries/') and path.endswith('/ack'):
            try:
                return 200, json.dumps({'delivery': central.ack(unquote(path[len('/api/v1/deliveries/'):-len('/ack')]))}).encode()
            except KeyError:
                return 404, b'{"error": "not found"}'
        return 404, b'{"error": "not found"}'
    return send


# --- transport choice ---------------------------------------------------------------------------------------------------

def test_central_on_gives_http_transport_with_the_token_from_the_environment():
    t = ar.central_transport({'central': dict(CENTRAL)}, env={ENV: TOKEN})
    assert isinstance(t, ad.HttpTransport) and t.base_url == URL
    assert TOKEN not in repr(t)


@pytest.mark.parametrize('cfg', [{}, {'central': {}}, {'central': {'enabled': False, 'url': URL, 'project': SLUG}}])
def test_central_off_or_absent_means_no_central_transport(cfg):
    assert ar.central_transport(cfg, env={ENV: TOKEN}) is None
    assert ar.project_slug('/x/proj', cfg) == 'proj'


def test_project_slug_comes_from_the_config_when_central_is_on():
    assert ar.project_slug('/x/proj', {'central': dict(CENTRAL)}) == SLUG


def test_runner_without_central_keeps_using_local_transport(project, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setattr(ad, 'urllib_send', lambda *a: pytest.fail('no HTTP call without central'))
    pulled = []
    real = ad.LocalTransport.pull_pending
    monkeypatch.setattr(ad.LocalTransport, 'pull_pending', lambda self, p: pulled.append(p) or real(self, p))
    assert ar.run(project, max_iterations=1, claude=fake_agent()) == 0
    assert pulled and set(pulled) == {project.name}


# --- preflight ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('central,env,named', [
    (CENTRAL, {}, ENV),
    ({'enabled': True, 'project': SLUG}, {ENV: TOKEN}, '"central.url"'),
    ({'enabled': True, 'url': URL}, {ENV: TOKEN}, '"central.project"'),
    ({'enabled': True, 'url': ' ', 'project': SLUG}, {ENV: '  '}, '"central.url"'),
])
def test_preflight_names_what_central_is_missing(central, env, named):
    problems = ar.autosafe.central_problems({'central': dict(central)}, env=env)
    assert len(problems) == 1 and named in problems[0]
    assert TOKEN not in problems[0]


def test_preflight_accepts_complete_central_and_ignores_it_when_off():
    assert ar.autosafe.central_problems({'central': dict(CENTRAL)}, env={ENV: TOKEN}) == []
    assert ar.autosafe.central_problems({'central': {'enabled': False}}, env={}) == []
    assert ar.autosafe.central_problems({}, env={}) == []


def test_run_is_refused_without_the_token(project, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    set_cfg(project, central=CENTRAL)
    calls = []
    with pytest.raises(ar.RunnerError, match=ENV):
        ar.run(project, max_iterations=1, claude=fake_agent(calls=calls))
    assert calls == [] and not worktree(project).exists()


def test_run_is_refused_without_the_url_even_with_a_token(project, monkeypatch):
    monkeypatch.setenv(ENV, TOKEN)
    set_cfg(project, central={'enabled': True, 'project': SLUG})
    with pytest.raises(ar.RunnerError, match='central.url') as e:
        ar.run(project, max_iterations=1, claude=fake_agent(), dry_run=True)
    assert TOKEN not in str(e.value) and not worktree(project).exists()


# --- the token is never read from the config ----------------------------------------------------------------------------

def test_a_token_in_the_config_file_is_refused(project):
    set_cfg(project, central={**CENTRAL, 'token': TOKEN})
    with pytest.raises(ar.autocfg.ConfigError, match='central.token'):
        ar.run(project, max_iterations=1, claude=fake_agent())


def test_the_agent_and_the_suite_do_not_inherit_the_token(project, monkeypatch):
    monkeypatch.setenv(ENV, TOKEN)
    probe = [sys.executable, '-c', f'import os; print(repr(os.environ.get("{ENV}")))']
    assert ar.run_claude(probe, project) == (0, 'None\n')
    code, out = ar.run_tests(subprocess.list2cmdline(probe), project)
    assert (code, out) == (0, 'None\n')
    assert ENV not in ar.child_env()


# --- a whole run against the (fake) central service ---------------------------------------------------------------------

def every_file_text(*roots):
    for root in roots:
        for p in root.rglob('*'):
            if p.is_file():
                yield p, p.read_bytes()


def test_run_pulls_from_central_with_the_slug_and_never_writes_the_token(project, monkeypatch, capsys):
    monkeypatch.setenv(ENV, TOKEN)
    central, seen = FakeCentral(), []
    monkeypatch.setattr(ad, 'urllib_send', fake_send(central, seen))
    set_cfg(project, central=CENTRAL)

    assert ar.run(project, max_iterations=4, claude=blocking_agent('A1', BLOCKER)) == 0
    auto = project / '.auto'
    b = ab.Store(auto).all()[0][0]
    assert b['project'] == SLUG and project_statuses(project)['A1'][0] == 'blocked'
    assert {e['project'] for e in events(project)} == {SLUG}

    central.publish(SLUG, 'A1', b['id'], option='Orange', text='The brand orange.')
    calls = []
    assert ar.run(project, max_iterations=2, claude=picking_agent(calls), allow_dirty=True) == 0
    assert calls == ['A1'] and project_statuses(project)['A1'][0] == 'done'
    assert central.rows[f'{b["id"]}.v1']['state'] == 'acknowledged'
    assert ab.Store(auto).load(b['id'])['state'] == 'RESUMED'

    gets = [s for s in seen if s[0] == 'GET']
    assert gets and all(u == URL + '/api/v1/deliveries/pending' for _, u, _ in gets)
    assert all(h['Authorization'] == f'Bearer {TOKEN}' for _, _, h in seen)
    assert all(TOKEN not in u for _, u, _ in seen)
    out = capsys.readouterr()
    assert TOKEN not in out.out and TOKEN not in out.err
    for path, data in every_file_text(project, worktree(project)):
        assert TOKEN.encode() not in data, path


def test_central_outage_during_a_run_changes_nothing_and_is_retried(project, monkeypatch, capsys):
    monkeypatch.setenv(ENV, TOKEN)

    def down(method, url, headers, body, timeout):
        raise ConnectionRefusedError('no route')
    monkeypatch.setattr(ad, 'urllib_send', down)
    set_cfg(project, central=CENTRAL)
    assert ar.run(project, max_iterations=1, claude=fake_agent()) == 0
    out = capsys.readouterr().out
    assert 'answer service unavailable' in out and TOKEN not in out
