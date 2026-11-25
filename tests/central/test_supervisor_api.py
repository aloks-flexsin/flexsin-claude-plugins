import pytest

from central import accounts, audit, personal

from .test_tasks import PW, START, add, clock, decide, plan, take, team  # noqa: F401  (fixtures)
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)
from .test_account_admin import new_client


def get(app, path, token=None):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    return new_client(app).get(path, headers=headers)


@pytest.fixture
def sup_token(conn, team):
    sup = accounts.create(conn, 'sup@example.com', PW, role='supervisor')
    return personal.create_token(conn, sup['id'], 'sup', START)


def test_state_is_read_only_and_supervisor_only(app, team, conn, sup_token):
    n = add(team)
    take(app, team)
    url = '/api/v1/supervisor/state?project=alpha'
    before = ([dict(r) for r in conn.query('SELECT * FROM project_task')], len(audit.rows(conn)))
    ok = get(app, url, sup_token)
    assert ok.status_code == 200
    body = ok.get_json()
    assert [t['number'] for t in body['tasks']] == [n] and body['project'] == 'alpha'
    assert 'claim' not in ok.get_data(as_text=True)
    assert {'plan', 'items', 'status', 'branch', 'pr_url', 'plan_rounds', 'merged_at'} <= set(body['tasks'][0])
    assert body['runners'] == [] and body['blockers'] == []
    assert get(app, url, team['token']['dev']).status_code == 403
    admin_token = personal.create_token(conn, team['accounts']['admin']['id'], 'adm', START)
    assert get(app, url, admin_token).status_code == 403
    assert get(app, url).status_code == 401
    assert get(app, '/api/v1/supervisor/state?project=nope', sup_token).status_code == 404
    assert get(app, '/api/v1/supervisor/state?project=nope', team['token']['dev']).status_code == 403
    assert new_client(app).post(url, headers={'Authorization': 'Bearer ' + sup_token}).status_code == 405
    assert ([dict(r) for r in conn.query('SELECT * FROM project_task')], len(audit.rows(conn))) == before


def test_whoami_returns_role(app, team, sup_token):
    assert get(app, '/api/v1/supervisor/whoami', sup_token).get_json()['role'] == 'supervisor'
    assert get(app, '/api/v1/supervisor/whoami', team['token']['dev']).get_json()['role'] == 'user'
    assert get(app, '/api/v1/supervisor/whoami').status_code == 401
    assert get(app, '/api/v1/supervisor/whoami', 'bogus').status_code == 401


def test_state_plan_rounds_counts_changes_rows(app, team, conn, sup_token):
    n, other = add(team), add(team, title='Other')
    for _ in range(2):
        claim = take(app, team)['claim']
        assert plan(app, team, n, claim).status_code == 200
        assert decide(team, n, 'changes', comment='again').status_code == 302
    rows = {t['number']: t for t in get(app, '/api/v1/supervisor/state?project=alpha', sup_token).get_json()['tasks']}
    assert rows[n]['plan_rounds'] == 2 and rows[other]['plan_rounds'] == 0
    assert rows[n]['status'] == 'changes_requested' and rows[n]['merged_at'] is None


def test_merged_at_column_exists_and_is_migrated(conn):
    from central import db as centraldb
    assert ('project_task', 'merged_at', 'VARCHAR(32)') in centraldb.COLUMNS_ADDED
    assert 'merged_at' in conn.columns('project_task')
