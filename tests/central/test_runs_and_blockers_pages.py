"""Central service: the Runs and Blockers pages (UX audit, PR 4). Runs: live cards, then a history with outcome chips
(`?outcome=`), reasons in words and the next step for a run that stopped. Blockers: question first, waiting ones on
top; the answer page offers the options as cards with the recommended one marked and pre-chosen, folds the context
away and says which task and runner wait."""
import datetime

from .test_blockers import answer, w  # noqa: F401  (fixtures)
from .test_runs import _member, ev
from .test_ingest import START, clock, conn, send, world  # noqa: F401,F811  (fixtures)


def _runs(world, clock):
    clock.now = datetime.datetime(2026, 11, 23, 12, 0, tzinfo=datetime.timezone.utc)
    sent = [ev('r1', 'run_start', '2026-11-23T11:40:00+00:00', user='alok', account='bhawish', branch='b1', task=3),
            ev('r1', 'item_state', '2026-11-23T11:50:00+00:00', 'A1', **{'from': 'queued', 'to': 'running'}),
            ev('r2', 'run_start', '2026-11-23T08:00:00+00:00', user='ravi', branch='b2'),
            ev('r2', 'run_end', '2026-11-23T08:30:00+00:00', exit=1, reason='red'),
            ev('r3', 'run_start', '2026-11-23T07:00:00+00:00', user='mia', branch='b3'),
            ev('r3', 'run_end', '2026-11-23T07:45:00+00:00', exit=0, reason='queue_empty'),
            ev('r4', 'run_start', '2026-11-23T06:00:00+00:00', user='zed', branch='b4')]
    assert send(world['app'], world['token'], sent).status_code == 200


def test_runs_page_live_cards_history_and_next_steps(world, conn, clock):
    _runs(world, clock)
    page = _member(world['app'], conn, world['alpha'], 'viewer').get('/projects/alpha/runs').get_data(as_text=True)
    assert 'Task #3</a>' in page and 'running for 20 min' in page and 'item A1' in page
    assert 'tests failing' in page and 'Next: Fix the failing test, then run again.' in page
    assert 'took 45 min' in page and 'the runner stopped without saying so' in page
    assert 'Stopped 1</a>' in page and 'Silent 1</a>' in page and 'Finished 1</a>' in page
    assert 'data-refresh="30"' in page


def test_runs_outcome_filter(world, conn, clock):
    _runs(world, clock)
    c = _member(world['app'], conn, world['alpha'], 'viewer')
    page = c.get('/projects/alpha/runs?outcome=stopped').get_data(as_text=True)
    assert 'tests failing' in page and 'took 45 min' not in page and 'aria-current="page">Stopped 1' in page
    page = c.get('/projects/alpha/runs?outcome=nonsense').get_data(as_text=True)
    assert 'aria-current="page">All</a>' in page and 'took 45 min' in page


def test_blockers_list_is_question_first(w):
    page = w['clients']['viewer'].get('/projects/alpha/blockers').get_data(as_text=True)
    assert page.index('Which colour should the &lt;b&gt;button&lt;/b&gt; be?') < page.index('<span>B-colour</span>')
    assert 'Waiting for an answer <span class="pill attention">2</span>' in page and 'item A1' in page


def test_the_answer_page_marks_and_preselects_the_recommendation(w):
    page = w['clients']['owner'].get('/projects/alpha/blockers/B-colour').get_data(as_text=True)
    assert 'class="option-card recommended"' in page and 'value="Orange" checked' in page
    assert 'value="Blue" checked' not in page and 'Recommended</span>' in page
    assert '<details class="card context" open>' in page and 'If the answer is wrong' in page
    assert '<strong>Holding up:</strong> Finishing A1.' in page
    viewer = w['clients']['viewer'].get('/projects/alpha/blockers/B-colour').get_data(as_text=True)
    assert 'option-card' not in viewer and '(recommended)' in viewer and 'An owner, manager or admin answers it.' in viewer


def test_a_refused_answer_keeps_the_choice(w):
    resp = answer(w, 'owner', option='Purple', text='keep me')
    page = resp.get_data(as_text=True)
    assert resp.status_code == 400 and 'keep me' in page and 'value="Orange" checked' not in page


def test_answers_read_as_sentences(w):
    answer(w, 'owner', option='Orange', text='Use the brand orange.')
    page = w['clients']['viewer'].get('/projects/alpha/blockers/B-colour').get_data(as_text=True)
    assert '<strong>Owner</strong> chose <strong>Orange</strong>: Use the brand orange.' in page
