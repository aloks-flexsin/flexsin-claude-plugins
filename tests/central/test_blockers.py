"""Blocker pages: members see what the runner waits for; owners, managers and admins answer it (the answer becomes a
pending delivery through deliveries.record_decision)."""
import datetime
import json
import re
import uuid

import pytest

from central import accounts, db, projects, tokens

PW = 'violet-anchor-58-tide'
START = datetime.datetime(2026, 10, 31, 9, 0, tzinfo=datetime.timezone.utc)
AT = '2026-10-31T09:00:00Z'
ROLES = ('viewer', 'developer', 'manager', 'owner')
CONTENT = {'question': 'Which colour should the <b>button</b> be?', 'why_needed': 'The page depends on it.',
           'prevents': 'Finishing A1.', 'impact_if_delayed': 'A1 waits.', 'impact_if_wrong': 'Repainting later.',
           'class': 'B2', 'owner_role': 'product owner',
           'options': [{'label': 'Orange', 'tradeoffs': 'Matches the brand.'}, {'label': 'Blue', 'tradeoffs': 'Calmer.'}],
           'recommendation': 'Orange', 'assumptions': ['The brand guide is current.'], 'evidence': [], 'due': None}


def new_client(app):
    c = app.test_client()
    c.environ_base['wsgi.url_scheme'] = 'https'
    return c


def csrf(client, path):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def signed_in(app, email):
    c = new_client(app)
    r = c.post('/signin', data={'email': email, 'password': PW, 'csrf_token': csrf(c, '/signin')})
    assert r.status_code == 302
    return c


def post_event(app, raw, project, item, detail):
    e = {'schema': 'event.v1', 'id': uuid.uuid4().hex, 'time': '2026-10-31T09:00:00+00:00', 'run': '20261031-0900',
         'project': project, 'type': 'blocker', 'item': item, 'detail': detail, 'evidence_class': 'agent_reported'}
    r = new_client(app).post('/api/v1/events', data=json.dumps(e),
                             headers={'Authorization': 'Bearer ' + raw, 'Content-Type': 'application/json'})
    assert r.status_code == 200, r.get_json()


@pytest.fixture
def conn(app, db_url):
    c = db.connect(db_url)
    yield c
    c.close()


@pytest.fixture
def w(app, conn):
    app.config['CLOCK'] = lambda: START
    for who in ROLES + ('stranger',):
        accounts.create(conn, '%s@example.com' % who, PW)
    accounts.create(conn, 'admin@example.com', PW, role='admin')
    alpha = projects.create(conn, 'alpha', 'Alpha', AT, 'https://github.com/example/repo.git')
    beta = projects.create(conn, 'beta', 'Beta', AT, 'https://github.com/example/repo.git')
    for role in ROLES:
        projects.add_member(conn, alpha['id'], '%s@example.com' % role, role, AT)
    projects.add_member(conn, beta['id'], 'stranger@example.com', 'owner', AT)
    raw, _ = tokens.create(conn, alpha['id'], ['ingest'], None, AT)
    post_event(app, raw, 'alpha', 'A1', {'action': 'raised', 'id': 'B-colour', 'blocker': CONTENT})
    post_event(app, raw, 'alpha', 'A2', {'action': 'raised', 'id': 'B-old'})      # an older runner: id only
    clients = {who: signed_in(app, '%s@example.com' % who) for who in ROLES + ('stranger', 'admin')}
    clients['anonymous'] = new_client(app)
    return {'app': app, 'conn': conn, 'clients': clients, 'ingest': raw, 'alpha': alpha}


def answer(w, who, blocker='B-colour', **data):
    c = w['clients'][who]
    if 'csrf_token' not in data:              # a valid token for this client: the home page form carries the session's
        data['csrf_token'] = csrf(c, '/signin' if who == 'anonymous' else '/')
    return c.post('/projects/alpha/blockers/%s/answer' % blocker, data=data)


def blocker_row(w, blocker='B-colour'):
    return w['conn'].query_one('SELECT * FROM blocker WHERE project_id = ? AND blocker_id = ?', (w['alpha']['id'], blocker))


def test_ingest_keeps_the_question_the_runner_sent(w):
    assert blocker_row(w)['question'] == CONTENT['question']
    assert blocker_row(w, 'B-old')['question'] is None


@pytest.mark.parametrize('who', ROLES + ('admin',))
def test_every_member_sees_the_list_waiting_first_with_the_question(w, who):
    page = w['clients'][who].get('/projects/alpha/blockers')
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert 'B-colour' in text and 'B-old' in text and 'waiting for an answer' in text
    assert 'Which colour should the &lt;b&gt;button&lt;/b&gt; be?' in text and '<b>button</b>' not in text


@pytest.mark.parametrize('who', ('stranger', 'anonymous'))
def test_non_members_get_404_on_every_blocker_page(w, who):
    c = w['clients'][who]
    assert c.get('/projects/alpha/blockers').status_code == 404
    assert c.get('/projects/alpha/blockers/B-colour').status_code == 404
    assert answer(w, who, option='Orange').status_code in (400, 404)    # anonymous has no valid form token either
    assert blocker_row(w)['status'] == 'OPEN'


def test_unknown_blocker_is_404(w):
    assert w['clients']['owner'].get('/projects/alpha/blockers/B-nope').status_code == 404


def test_the_detail_page_shows_the_content(w):
    text = w['clients']['viewer'].get('/projects/alpha/blockers/B-colour').get_data(as_text=True)
    for part in ('The page depends on it.', 'Finishing A1.', 'Matches the brand.', 'Calmer.', 'Repainting later.',
                 'The brand guide is current.'):
        assert part in text
    assert 'Recommendation' in text


@pytest.mark.parametrize('who,form', [('viewer', False), ('developer', False), ('manager', True), ('owner', True),
                                      ('admin', True)])
def test_only_deciders_get_the_answer_form(w, who, form):
    text = w['clients'][who].get('/projects/alpha/blockers/B-colour').get_data(as_text=True)
    assert ('Send answer' in text) is form


@pytest.mark.parametrize('who', ('viewer', 'developer'))
def test_readers_cannot_answer(w, who):
    assert answer(w, who, option='Orange').status_code == 404
    assert blocker_row(w)['status'] == 'OPEN'


@pytest.mark.parametrize('who', ('manager', 'owner', 'admin'))
def test_a_decider_answers_and_the_delivery_is_queued(w, who):
    r = answer(w, who, option='Orange', text='Use the brand orange.')
    assert r.status_code == 302 and r.headers['Location'].endswith('/projects/alpha/blockers/B-colour')
    row = blocker_row(w)
    assert row['status'] == 'PENDING_DELIVERY'
    d = w['conn'].query_one('SELECT * FROM decision WHERE blocker_ref = ?', (row['id'],))
    assert (d['version'], d['option_label'], d['text']) == (1, 'Orange', 'Use the brand orange.')
    assert json.loads(d['body'])['responder'] == '%s@example.com' % who
    dl = w['conn'].query_one('SELECT * FROM delivery WHERE decision_id = ?', (d['id'],))
    assert (dl['delivery_id'], dl['status']) == ('B-colour.v1', 'pending')
    assert w['conn'].query_one("SELECT COUNT(*) AS n FROM audit WHERE action = 'decision.record'")['n'] == 1
    text = w['clients']['viewer'].get('/projects/alpha/blockers/B-colour').get_data(as_text=True)
    assert 'Use the brand orange.' in text and 'answered, waiting for the runner' in text and 'Send answer' not in text


def test_an_answered_blocker_cannot_be_answered_again(w):
    assert answer(w, 'owner', option='Orange').status_code == 302
    r = answer(w, 'manager', option='Blue')
    assert r.status_code == 409
    assert w['conn'].query_one('SELECT COUNT(*) AS n FROM decision')['n'] == 1


@pytest.mark.parametrize('data,message', [({'option': 'Purple'}, 'Choose one of the listed options.'),
                                          ({}, 'Choose an option or write an answer.'),
                                          ({'text': 'x' * 5001}, 'longer than 5000')])
def test_bad_answers_are_refused_and_nothing_is_stored(w, data, message):
    r = answer(w, 'owner', **data)
    assert r.status_code == 400 and message in r.get_data(as_text=True)
    assert w['conn'].query_one('SELECT COUNT(*) AS n FROM decision')['n'] == 0


def test_answer_without_the_form_token_is_refused(w):
    r = w['clients']['owner'].post('/projects/alpha/blockers/B-colour/answer', data={'option': 'Orange'})
    assert r.status_code == 400 and blocker_row(w)['status'] == 'OPEN'


def test_a_blocker_without_content_can_be_answered_with_text(w):
    text = w['clients']['owner'].get('/projects/alpha/blockers/B-old').get_data(as_text=True)
    assert 'sent no question' in text and 'Send answer' in text
    assert answer(w, 'owner', blocker='B-old', option='Orange').status_code == 400     # it has no options
    assert answer(w, 'owner', blocker='B-old', text='Go ahead with blue.').status_code == 302
    assert blocker_row(w, 'B-old')['status'] == 'PENDING_DELIVERY'


def test_a_resumed_event_moves_the_blocker_to_resumed(w):
    post_event(w['app'], w['ingest'], 'alpha', 'A1', {'action': 'resumed', 'id': 'B-colour'})
    assert blocker_row(w)['status'] == 'RESUMED'
    text = w['clients']['owner'].get('/projects/alpha/blockers/B-colour').get_data(as_text=True)
    assert 'the runner resumed the item' in text and 'Send answer' not in text


def test_secrets_in_the_content_are_redacted_before_they_are_shown(w):
    secret = 'ghp_' + 'A' * 36
    post_event(w['app'], w['ingest'], 'alpha', 'A3',
               {'action': 'raised', 'id': 'B-key', 'blocker': dict(CONTENT, question='Is %s still valid?' % secret)})
    text = w['clients']['owner'].get('/projects/alpha/blockers/B-key').get_data(as_text=True)
    assert secret not in text and '[redacted]' in text


def test_the_project_page_links_to_its_blockers(w):
    assert '/projects/alpha/blockers' in w['clients']['viewer'].get('/projects/alpha').get_data(as_text=True)
