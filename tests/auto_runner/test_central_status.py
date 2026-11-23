"""auto-runner plugin: `/auto-runner:central` (`autocentral.py status`) says whether a checkout is connected to the central
service and what to do next, read-only. Requests go to the real central app through its Flask test client (no socket)."""
import json

import pytest

from tests.auto_runner.test_init_central import BASE, ENV, REPO, SLUG, ac, ai, central, repo  # noqa: F401  (central is a fixture)
from tests.auto_runner.test_runner import BLOCKER

ME = {'AUTO_RUNNER_USER': 'alok', 'AUTO_RUNNER_ACCOUNT': 'bhawish'}
ab = ac._load('autorunner_block', 'autoblock.py')


def connect(p, enabled=True, **extra):
    section = {'enabled': enabled, 'url': BASE, 'project': SLUG, 'token_env': ENV, **extra}
    (p / '.claude').mkdir(exist_ok=True)
    (p / '.claude' / 'auto.json').write_text(json.dumps({'test_command': 'pytest -q', 'central': section}), encoding='utf-8')


def run(p, central, env):
    lines, ok = ac.status(p, env=env, send=central.send)
    return '\n'.join(lines), ok


@pytest.fixture
def connected(tmp_path, central):
    p = repo(tmp_path, REPO)
    connect(p)
    return p, {**ME, ENV: central.token}


# ---------- not connected ----------

def test_no_section_names_both_routes_and_calls_nothing(tmp_path, central):
    p = repo(tmp_path, REPO)
    text, ok = run(p, central, ME)
    assert not ok and 'NOT CONNECTED' in text
    assert '/auto-runner:init --central-url URL (the project, "acme-web", is read from origin)' in text
    assert '/auto-runner:join SLUG' in text and '--central-project' not in text
    assert 'auto_init.py' in text and 'auto_join.py' in text and 'https://auto-runner-central.vercel.app' in text
    assert central.calls == []


def test_off_says_answers_stay_local(tmp_path, central):
    p = repo(tmp_path, REPO)
    connect(p, enabled=False)
    text, ok = run(p, central, {**ME, ENV: central.token})
    assert not ok and 'OFF' in text and central.calls == []


def test_unset_token_gives_the_steps_and_calls_nothing(tmp_path, central):
    p = repo(tmp_path, REPO)
    connect(p)
    text, ok = run(p, central, ME)
    assert not ok and 'Token: NOT SET' in text and 'login start' in text and 'login wait' in text
    assert f'project token in {ENV}' in text and f'{BASE}/projects/{SLUG}/tokens' in text and central.calls == []


def test_a_token_in_the_config_is_refused_without_calling(tmp_path, central):
    p = repo(tmp_path, REPO)
    connect(p, token_env='art_abc')
    text, ok = run(p, central, ME)
    assert not ok and 'holds a token' in text and central.calls == []


# ---------- the service's answer ----------

def test_connected(connected, central):
    p, env = connected
    text, ok = run(p, central, env)
    assert ok and 'Service: CONNECTED' in text and 'Runner identity: alok/bhawish-claude-auto-runner' in text
    assert f'{BASE}/projects/{SLUG}/blockers' in text and 'owners, managers and admins answer' in text
    assert central.token not in text
    assert [(m, u) for m, u, _ in central.calls] == [('GET', f'{BASE}/api/v1/project')]   # never deliveries, never a POST


def test_refused_token(connected, central):
    p, env = connected
    text, ok = run(p, central, {**env, ENV: 'art_' + 'x' * 43})
    assert not ok and 'REFUSED the token' in text and f'{BASE}/projects/{SLUG}/tokens' in text


def test_unreachable(connected, central):
    p, env = connected
    central.down = True
    text, ok = run(p, central, env)
    assert not ok and 'NOT REACHABLE' in text


def test_another_projects_token(connected, central):
    p, env = connected
    text, ok = run(p, central, {**env, ENV: central.other_token})
    assert not ok and 'the token belongs to project "other"' in text


def test_origin_is_another_repository(tmp_path, central):
    p = repo(tmp_path, 'https://github.com/example/something-else.git')
    connect(p)
    text, ok = run(p, central, {**ME, ENV: central.token})
    assert not ok and 'is not project "acme-web"\'s repository' in text


# ---------- local facts ----------

def test_missing_identity_keeps_it_not_ok(connected, central):
    p, env = connected
    text, ok = run(p, central, {ENV: env[ENV]})
    assert not ok and 'Service: CONNECTED' in text and 'Runner identity: MISSING' in text
    assert "SetEnvironmentVariable('AUTO_RUNNER_USER'" in text and "SetEnvironmentVariable('AUTO_RUNNER_ACCOUNT'" in text


def test_waiting_blockers_are_listed_with_their_page(connected, central):
    p, env = connected
    b = ab.new_blocker(**BLOCKER, project=SLUG, task='A1', run='r', agent_role='implementer', stage='A1')
    ab.Store(p / '.auto').create(b)
    text, _ = run(p, central, env)
    assert 'Blockers waiting for an answer: 1' in text
    assert f'{b["id"]} (A1): Which colour should it be? -> {BASE}/projects/{SLUG}/blockers/{b["id"]}' in text


def test_unsent_events_are_counted(connected, central):
    p, env = connected
    auto = p / '.auto'
    auto.mkdir(exist_ok=True)
    (auto / 'events.jsonl').write_text('{}\n' * 5, encoding='utf-8')
    (auto / 'central-push.json').write_text(json.dumps({'schema': 'push.v1', 'lines': 3, 'skipped': ['e1']}), encoding='utf-8')
    text, _ = run(p, central, env)
    assert 'Events: 5 recorded here, 2 not sent yet' in text and '1 refused by the service for good' in text


def test_status_writes_nothing(connected, central):
    p, env = connected
    before = sorted(x.relative_to(p).as_posix() for x in p.rglob('*') if '.git' not in x.parts)
    run(p, central, env)
    assert sorted(x.relative_to(p).as_posix() for x in p.rglob('*') if '.git' not in x.parts) == before


def test_init_apply_then_status_says_connected(tmp_path, central):
    p = repo(tmp_path, REPO)
    env = {**ME, ENV: central.token}
    assert 'NOT CONNECTED' in run(p, central, env)[0]
    plan = ai.make_plan(p, {'url': BASE, 'project': SLUG}, env=env, send=central.send)
    assert not plan['problems'] and not plan['central_problems']
    ai.apply(plan)
    text, ok = run(p, central, env)
    assert ok and 'Service: CONNECTED' in text


# ---------- command line ----------

def test_cli_exit_codes(tmp_path, monkeypatch, capsys):
    p = repo(tmp_path, REPO)
    for var in ('AUTO_RUNNER_USER', 'AUTO_RUNNER_ACCOUNT'):
        monkeypatch.setenv(var, ME[var])
    assert ac.main(['status', '--project', str(p)]) == 1
    assert 'NOT CONNECTED' in capsys.readouterr().out


def test_the_guide_and_command_exist_and_say_the_essentials():
    root = ai.HERE.parent
    guide = (root / 'docs' / 'central-guide.md').read_text(encoding='utf-8')
    for needle in ('/auto-runner:join', '/auto-runner:central', 'viewer, developer', 'manager', 'owner', 'admin',
                   'AUTO_RUNNER_USER', 'AUTO_RUNNER_ACCOUNT', 'ingest', 'pull', 'unverified'):
        assert needle in guide
    command = (root / 'commands' / 'central.md').read_text(encoding='utf-8')
    assert 'autocentral.py" status' in command and 'Never ask for a token' in command and '--apply' in command
    assert 'login start' in command and 'login wait' in command
    assert ac.GUIDE == root / 'docs' / 'central-guide.md'
