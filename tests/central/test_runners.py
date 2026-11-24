"""Central service: runner presence, `POST /api/v1/runners/beat` (decision record 0009)."""
import datetime

from central import audit, db, runners, tokens

from .test_repo_url import AT, OLD_PROJECT, conn, world  # noqa: F401  (fixtures)
from .test_tasks import START, api, clock, team  # noqa: F401  (fixtures)

LABEL = 'alok/bhawish-claude-auto-runner@laptop'


def beat(app, team, who='dev', label=LABEL, **body):
    payload = {'label': label, 'state': 'watching', 'task': None, 'spend_usd': 0}
    payload.update(body)
    return api(app, 'POST', '/api/v1/runners/beat', team['token'][who], payload)


def test_first_beat_creates_the_runner_and_later_beats_update_it(app, team, conn, clock):
    resp = beat(app, team)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body['control'] is None
    assert body['runner'] == {'label': LABEL, 'person': 'dev@example.com', 'state': 'watching', 'task': None,
                              'spend_usd': 0.0, 'beat_at': '2026-11-23T09:00:00Z', 'silent': False, 'control': None}

    clock.now = START + datetime.timedelta(minutes=5)
    body = beat(app, team, state='building #3', task=3, spend_usd=1.25).get_json()
    assert (body['runner']['state'], body['runner']['task'], body['runner']['spend_usd']) == ('building #3', 3, 1.25)
    assert body['runner']['beat_at'] == '2026-11-23T09:05:00Z'

    rows = runners.for_project(conn, team['alpha']['id'])
    assert len(rows) == 1 and rows[0]['state'] == 'building #3' and rows[0]['account_id'] == team['accounts']['dev']['id']
    starts = audit.rows(conn, 'runner.start')                         # arrivals are audited, ordinary beats are not
    assert len(starts) == 1 and starts[0]['detail'] == {'label': LABEL, 'state': 'watching'}
    assert starts[0]['account_id'] == team['accounts']['dev']['id']
    assert audit.rows(conn, 'runner.back') == []


def test_a_token_cannot_beat_another_persons_runner(app, team, conn, clock):
    assert beat(app, team, who='dev').status_code == 200
    resp = beat(app, team, who='manager', state='paused')
    assert resp.status_code == 403 and 'another person' in resp.get_json()['error']
    row = runners.get(conn, team['alpha']['id'], LABEL)
    assert (row['state'], row['account_id']) == ('watching', team['accounts']['dev']['id'])
    assert beat(app, team, who='manager', label='manager-runner').status_code == 200   # their own label is fine
    assert len(runners.for_project(conn, team['alpha']['id'])) == 2


def test_only_personal_tokens_of_developers_beat(app, team, conn):
    raw, _ = tokens.create(conn, team['alpha']['id'], ['ingest'], None, AT)       # a project token is no one's
    assert api(app, 'POST', '/api/v1/runners/beat', raw, {'label': LABEL, 'state': 'watching'}).status_code == 403
    assert beat(app, team, who='viewer').status_code == 403                         # viewers run no runners
    assert runners.for_project(conn, team['alpha']['id']) == []


def test_bad_beats_are_refused(app, team, conn):
    assert beat(app, team, label='').status_code == 400
    assert beat(app, team, state='  ').status_code == 400
    assert beat(app, team, task='3').status_code == 400
    assert beat(app, team, task=True).status_code == 400
    assert beat(app, team, spend_usd=-1).status_code == 400
    assert beat(app, team, label='x' * 201).status_code == 400
    assert runners.for_project(conn, team['alpha']['id']) == []


def test_silent_after_two_hours_and_back_on_the_next_beat(app, team, conn, clock):
    beat(app, team)
    row = runners.get(conn, team['alpha']['id'], LABEL)
    assert not runners.silent(row, START + datetime.timedelta(hours=1, minutes=59))
    assert runners.silent(row, START + datetime.timedelta(hours=2))
    assert runners.public(row, START + datetime.timedelta(hours=3))['silent'] is True

    clock.now = START + datetime.timedelta(hours=3)
    body = beat(app, team).get_json()
    assert body['runner']['silent'] is False
    back = audit.rows(conn, 'runner.back')
    assert len(back) == 1 and back[0]['detail']['label'] == LABEL


def test_pause_and_stop_are_returned_to_the_runner(app, team, conn):
    beat(app, team)
    conn.execute("UPDATE runner SET control = 'pause' WHERE label = ?", (LABEL,))
    conn.commit()
    body = beat(app, team, state='paused').get_json()
    assert body['control'] == 'pause' and body['runner']['control'] == 'pause'
    conn.execute("UPDATE runner SET control = 'stop' WHERE label = ?", (LABEL,))
    conn.commit()
    assert beat(app, team).get_json()['control'] == 'stop'
    conn.execute("UPDATE runner SET control = NULL WHERE label = ?", (LABEL,))
    conn.commit()
    assert beat(app, team).get_json()['control'] is None


def test_the_task_api_is_unchanged_by_beats(app, team):
    beat(app, team)
    assert api(app, 'GET', '/api/v1/tasks', team['token']['dev']).get_json() == {'tasks': []}


def test_init_db_adds_the_runner_table_to_an_existing_database(db_url):
    old = db.connect(db_url)
    old.execute(OLD_PROJECT)
    old.execute("INSERT INTO project (id, slug, name, created_at) VALUES ('p1', 'old', 'Old', ?)", (AT,))
    old.commit()
    old.close()
    db.init_db(db_url)
    conn = db.connect(db_url)
    try:
        assert {'label', 'state', 'task_number', 'spend_usd', 'control', 'beat_at'} <= conn.columns('runner')
        assert conn.query_one("SELECT slug FROM project WHERE id = 'p1'")['slug'] == 'old'   # existing rows kept
        assert runners.for_project(conn, 'p1') == []
    finally:
        conn.close()
    db.init_db(db_url)                                               # a second run changes nothing
