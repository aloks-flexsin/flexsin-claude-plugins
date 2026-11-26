"""The Supervisor's plan step (bin/autosupervisor.py) driven through the real Flask test client: journeys J1 and J2."""
import json

import pytest

from central import accounts, audit, personal, projects, runners, tasks
from tests.auto_runner.test_runner import ar

from .test_account_admin import new_client
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)
from .test_tasks import PW, START, add, api, clock, decide, take, team  # noqa: F401  (fixtures)

sup_mod = ar._load('autorunner_supervisor', 'autosupervisor.py')

ITEMS = "- [ ] **X1 Add thing.** Add auto-runner/bin/thing.py.\n  *Acceptance:* tests/test_thing.py passes.\n  *Slice:* 1 Thing\n"
GOOD = "# Plan\n\n## Queue items\n" + ITEMS + "\n## Needs a person\nNone.\n"
BAD = GOOD.replace('auto-runner/bin/thing.py', 'migrations/0001.sql')
POLICY = json.dumps(dict(base='main', restricted_paths=['migrations/**', '.github/']))


@pytest.fixture
def sup(conn, team):
    account = accounts.create(conn, 'sup@example.com', PW, role='supervisor')
    projects.add_member(conn, team['alpha']['id'], 'sup@example.com', 'manager', START)
    return personal.create_token(conn, account['id'], 'sup', START)


def http_for(app, token):
    calls = []

    def http(method, path, body=None):
        calls.append((method, path))
        resp = new_client(app).open(path, method=method, json=body, headers={'Authorization': 'Bearer ' + token})
        return resp.status_code, resp.get_json()
    http.calls = calls
    return http


def reader(text=POLICY, sha='sha-1', autonomy='manual'):
    """The base's files: the policy (`text`, None = missing) and `.claude/auto.json` with `autonomy` (None = no file).
    The tests above the auto-mode section run in manual mode, today's behaviour."""
    def read(ref, path):
        if path == '.claude/auto.json':
            return None if autonomy is None else (json.dumps({'autonomy': autonomy}), 'cfg-sha')
        return None if text is None else (text, sha)
    return read


def post_plan(app, team, n, text, runner='alok/bhawish-claude-auto-runner'):
    claim = take(app, team, runner=runner)['claim']
    resp = api(app, 'POST', '/api/v1/tasks/%d/plan' % n, team['token']['dev'], {'claim': claim, 'plan': text, 'items': ITEMS})
    assert resp.status_code == 200
    return claim


def status(conn, team, n):
    return tasks.get(conn, team['alpha']['id'], n)['status']


def test_plan_approved_or_sent_back(app, team, conn, sup):
    http = http_for(app, sup)
    # J1: a passing plan is approved, by the supervisor account, with the policy SHA in the audit row
    n = add(team)
    post_plan(app, team, n, GOOD)
    [done] = sup_mod.plan_step('alpha', http, reader(), 'origin/main')
    assert (done['task'], done['verdict'], done['decision']) == (n, 'approve', 'approve')
    assert status(conn, team, n) == 'approved'
    [row] = audit.rows(conn, 'supervisor.plan.approve')
    assert row['detail']['policy_sha'] == 'sha-1'
    assert tasks.get(conn, team['alpha']['id'], n)['decided_by'] == accounts.find_by_email(conn, 'sup@example.com')['id']
    # J2: a plan naming a restricted path is sent back twice, then left for a manager
    m = add(team, title='Second')
    runner = 'alok/second-claude-auto-runner'
    post_plan(app, team, m, BAD, runner)
    [sent] = sup_mod.plan_step('alpha', http, reader(), 'origin/main')
    assert sent['decision'] == 'changes' and status(conn, team, m) == 'changes_requested'
    assert 'migrations/0001.sql' in tasks.get(conn, team['alpha']['id'], m)['decision_comment']
    assert sup_mod.plan_step('alpha', http, reader(), 'origin/main') == []                    # nothing waits
    post_plan(app, team, m, BAD, runner)
    assert sup_mod.plan_step('alpha', http, reader(), 'origin/main')[0]['decision'] == 'changes'
    post_plan(app, team, m, BAD, runner)
    [third] = sup_mod.plan_step('alpha', http, reader(), 'origin/main')
    assert third['verdict'] == 'changes' and third['decision'] == 'escalate'
    assert status(conn, team, m) == 'plan_review'
    assert len(audit.rows(conn, 'supervisor.plan.changes')) == 2 and len(audit.rows(conn, 'supervisor.plan.escalate')) == 1
    page = team['clients']['manager'].get('/projects/alpha/tasks?state=approval').get_data(as_text=True)
    assert 'Second' in page
    posts = len(http.calls)
    assert sup_mod.plan_step('alpha', http, reader(), 'origin/main') == []                    # a further pass does nothing
    assert [c[0] for c in http.calls[posts:]] == ['GET'] and len(audit.rows(conn, 'supervisor.plan.escalate')) == 1


def test_a_missing_policy_leaves_the_plan_for_a_person_once(app, team, conn, sup):
    http = http_for(app, sup)
    n = add(team)
    post_plan(app, team, n, GOOD)
    [done] = sup_mod.plan_step('alpha', http, reader(None), 'origin/main')
    assert done['verdict'] == 'person' and done['decision'] == 'escalate' and status(conn, team, n) == 'plan_review'
    assert sup_mod.plan_step('alpha', http, reader(None), 'origin/main') == []
    [row] = audit.rows(conn, 'supervisor.plan.escalate')
    assert row['detail']['policy_sha'] is None and any('missing' in r for r in row['detail']['reasons'])


def test_an_unreadable_state_is_reported_and_decides_nothing(app, team, conn, sup):
    out = sup_mod.plan_step('alpha', lambda m, p, b=None: (403, {'error': 'no'}), reader(), 'origin/main')
    assert out[0]['task'] is None and out[0]['status'] == 403
    assert audit.rows(conn, 'supervisor.plan.approve') == []


# ---------- the merge step: journeys J3, J4 and J5 ----------

from tests.auto_runner.fake_forge import FakeForge  # noqa: E402

MERGER = 'sup-bot'


def finish(app, team, n, pr, runner='alok/bhawish-claude-auto-runner'):
    """Take the task, plan it, approve it as a person, build it and report it done with pull request `pr`."""
    post_plan(app, team, n, GOOD, runner)
    assert decide(team, n, 'approve').status_code == 302
    got = take(app, team, runner=runner)
    assert got['action'] == 'build'
    resp = api(app, 'POST', '/api/v1/tasks/%d/report' % n, team['token']['dev'],
               dict(claim=got['claim'], result='done', branch='b/%d' % n, pr_url='https://github.com/o/r/pull/%d' % pr))
    assert resp.get_json()['task']['status'] == 'done'


def pr_entry(sha='s1', author='runner', files=('auto-runner/bin/thing.py',), checks='success', approve=True, base='main'):
    return dict(pr=dict(number=0, state='open', draft=False, merged=False, head_sha=sha, base=base, author=author),
                files=list(files), checks=[dict(name='tests', state=checks)],
                reviews=[dict(user='reviewer', state='APPROVED', commit_id=sha)] if approve else [])


def audit_for(conn, action):
    return list(audit.rows(conn, action))


def test_done_when_one_pass(app, team, conn, sup):
    # J5 (and J3): one pass approves a passing plan and merges a green, approved PR at its exact SHA
    http = http_for(app, sup)
    merged = add(team, title='Merged one')
    finish(app, team, merged, 11)
    waiting = add(team, title='Plan one')
    post_plan(app, team, waiting, GOOD, 'alok/second-claude-auto-runner')
    forge = FakeForge({11: pr_entry(sha='abc')})
    out = sup_mod.run_pass('alpha', http, reader(), 'origin/main', forge, MERGER)
    assert [(p['task'], p['decision']) for p in out['plans']] == [(waiting, 'approve')]
    assert [(m['task'], m['outcome']) for m in out['merges']] == [(merged, 'merged')]
    assert forge.merges == [(11, 'abc', 'squash')]
    assert tasks.get(conn, team['alpha']['id'], merged)['merged_at']
    assert len(audit_for(conn, 'supervisor.plan.approve')) == 1
    [decided] = audit_for(conn, 'supervisor.merge.decided')
    [done] = audit_for(conn, 'supervisor.merge.done')
    assert decided['at'] <= done['at'] and decided['detail']['sha'] == 'abc' == done['detail']['sha']
    assert done['detail']['evidence']['approvals'] == ['reviewer'] and done['detail']['merge_sha'] == 'merge-abc'
    # J3: both pages say when it was merged
    for url in ('/projects/alpha/tasks/%d' % merged, '/projects/alpha/tasks?state=done&view=list'):
        assert 'Merged' in team['clients']['dev'].get(url).get_data(as_text=True)
    # a second pass does nothing: the task is merged and the plan is decided
    again = sup_mod.run_pass('alpha', http, reader(), 'origin/main', forge, MERGER)
    assert again['plans'] == [] and again['merges'] == [] and again['orders'] == [] and len(forge.merges) == 1
    assert len(audit_for(conn, 'supervisor.merge.decided')) == 1


def test_unsafe_prs_refused(app, team, conn, sup):
    # J4: red CI, a restricted path, no approval and a PR authored by the merger are all refused with their reasons
    http = http_for(app, sup)
    cases = {
        'red': (pr_entry(checks='failure'), 'not green'),
        'path': (pr_entry(files=['migrations/0001.sql']), 'restricted'),
        'review': (pr_entry(approve=False), 'approving review'),
        'self': (pr_entry(author=MERGER), 'different identity'),
    }
    numbers = {}
    for i, name in enumerate(cases):
        n = add(team, title=name)
        finish(app, team, n, 20 + i, 'alok/r%d-claude-auto-runner' % i)
        numbers[name] = n
    forge = FakeForge({20 + i: cases[name][0] for i, name in enumerate(cases)})
    out = sup_mod.merge_step('alpha', http, reader(), 'origin/main', forge, MERGER)
    assert sorted(m['outcome'] for m in out) == ['refused'] * 4 and forge.merges == []
    rows = {r['detail']['number']: r for r in audit_for(conn, 'supervisor.merge.refused')}
    assert len(rows) == 4
    for name, (_entry, word) in cases.items():
        assert any(word in reason for reason in rows[numbers[name]]['detail']['reasons']), name
        assert tasks.get(conn, team['alpha']['id'], numbers[name])['merged_at'] is None
    assert audit_for(conn, 'supervisor.merge.decided') == []
    # a further pass finds the same state and writes no second refusal
    sup_mod.merge_step('alpha', http, reader(), 'origin/main', forge, MERGER)
    assert len(audit_for(conn, 'supervisor.merge.refused')) == 4


def test_a_missing_policy_merges_nothing(app, team, conn, sup):
    http = http_for(app, sup)
    n = add(team)
    finish(app, team, n, 31)
    forge = FakeForge({31: pr_entry()})
    [out] = sup_mod.merge_step('alpha', http, reader(None), 'origin/main', forge, MERGER)
    assert out['outcome'] == 'refused' and forge.merges == []


def test_a_head_that_moved_is_recorded_as_failed(app, team, conn, sup):
    http = http_for(app, sup)
    n = add(team)
    finish(app, team, n, 41)
    forge = FakeForge({41: pr_entry(sha='old')})
    real = forge.merge

    def moved(pr, sha, method):
        forge.prs[pr]['pr']['head_sha'] = 'new'
        return real(pr, sha, method)
    forge.merge = moved
    [out] = sup_mod.merge_step('alpha', http, reader(), 'origin/main', forge, MERGER)
    assert out['outcome'] == 'failed' and tasks.get(conn, team['alpha']['id'], n)['merged_at'] is None
    assert len(audit_for(conn, 'supervisor.merge.decided')) == 1 and len(audit_for(conn, 'supervisor.merge.failed')) == 1
    assert audit_for(conn, 'supervisor.merge.done') == []


def test_the_merge_route_is_supervisor_only_and_validates(app, team, conn, sup):
    n = add(team)
    finish(app, team, n, 51)
    path = '/api/v1/supervisor/tasks/%d/merge' % n
    body = dict(phase='decided', sha='s')
    assert api(app, 'POST', path, team['token']['dev'], body).status_code == 403
    assert api(app, 'POST', path, 'nope', body).status_code == 401
    assert api(app, 'POST', path, sup, dict(phase='merged', sha='s')).status_code == 400
    assert api(app, 'POST', path, sup, dict(phase='decided')).status_code == 400
    assert api(app, 'POST', path, sup, dict(phase='done', sha='s')).status_code == 409           # nothing was decided
    assert api(app, 'POST', '/api/v1/supervisor/tasks/99/merge', sup, body).status_code == 404
    assert api(app, 'POST', path, sup, body).status_code == 200
    assert api(app, 'POST', path, sup, dict(phase='done', sha='other')).status_code == 409       # a different SHA
    assert api(app, 'POST', path, sup, dict(phase='done', sha='s')).status_code == 200
    again = api(app, 'POST', path, sup, dict(phase='done', sha='s')).get_json()
    assert again['already'] is True and len(audit_for(conn, 'supervisor.merge.done')) == 1
    assert api(app, 'POST', path, sup, body).status_code == 409                                  # already merged


STATE_URL = '/api/v1/supervisor/state?project=alpha'
ORDER_URL = '/api/v1/supervisor/order?project=alpha'


def test_queue_order_j6(app, team, conn, sup, clock):
    # J6: one pass ranks the open tasks (priority, then who unblocks others, then age) and writes only what changed
    from datetime import timedelta
    http = http_for(app, sup)
    n = {}
    for key, form in (('p2', dict(priority='2')), ('p1', dict(priority='1')), ('old', {}), ('young', {})):
        clock.now = clock.now + timedelta(minutes=1)
        n[key] = add(team, title=key, **form)
    clock.now = clock.now + timedelta(minutes=1)
    n['waits'] = add(team, title='waits', depends_on=str(n['young']))
    want = [n[k] for k in ('p1', 'p2', 'young', 'old', 'waits')]
    out = sup_mod.order_step('alpha', http)
    assert [(o['task'], o['rank']) for o in out] == list(zip(want, range(1, 6)))
    assert 'unblocks 1 task' in out[2]['reason']
    ranks = {t['number']: t['rank'] for t in http('GET', STATE_URL)[1]['tasks']}
    assert [ranks[x] for x in want] == [1, 2, 3, 4, 5]
    rows = audit_for(conn, 'supervisor.order')
    assert len(rows) == 5 and all(r['detail']['reason'] and r['detail']['from'] is None for r in rows)
    # a second pass writes nothing; when a task is finished only the ranks that moved are written
    assert sup_mod.order_step('alpha', http) == [] and len(audit_for(conn, 'supervisor.order')) == 5
    conn.execute("UPDATE project_task SET status = 'done' WHERE number = ?", (n['p1'],))
    conn.commit()
    assert [o['task'] for o in sup_mod.order_step('alpha', http)] == want[1:]
    assert len(audit_for(conn, 'supervisor.order')) == 9


def test_the_order_route_is_supervisor_only_and_validates(app, team, conn, sup):
    n = add(team, title='one')
    dev = api(app, 'PUT', ORDER_URL, team['token']['dev'], dict(orders=[dict(number=n, rank=1)]))
    assert dev.status_code == 403 and tasks.get(conn, team['alpha']['id'], n)['rank'] is None
    head = {'Authorization': 'Bearer ' + sup}
    for bad in (None, 'x', [dict(number=n, rank=0)], [dict(number=n, rank='1')], [dict(number=n, rank=True)],
                [dict(number=n, rank=1), dict(number=n, rank=2)]):
        assert new_client(app).put(ORDER_URL, json=dict(orders=bad), headers=head).status_code == 400
    two = dict(orders=[dict(number=n, rank=1), dict(number=99, rank=2)])
    assert new_client(app).put(ORDER_URL, json=two, headers=head).status_code == 404
    assert tasks.get(conn, team['alpha']['id'], n)['rank'] is None
    ok = new_client(app).put(ORDER_URL, json=dict(orders=[dict(number=n, rank=3, reason='why')]), headers=head)
    assert ok.status_code == 200 and ok.get_json() == dict(changed=[n])
    assert tasks.get(conn, team['alpha']['id'], n)['rank'] == 3
    [row] = audit_for(conn, 'supervisor.order')
    assert row['detail'] == {'number': n, 'from': None, 'to': 3, 'reason': 'why'}


# ---------- the control step: journey J7 ----------

RED = [dict(name='tests', state='failure')]
GREEN = [dict(name='tests', state='success')]


def control(http, forge):
    return sup_mod.control_step('alpha', http, reader(), 'origin/main', forge)


def controls(conn, team):
    return {r['label']: (r['control'], r['control_role']) for r in runners.for_project(conn, team['alpha']['id'])}


def test_pause_when_main_red_j7(app, team, conn, sup, clock):
    from datetime import timedelta
    from .test_runners import beat
    http = http_for(app, sup)
    forge = FakeForge(base=RED)
    beat(app, team, label='dev-old')                                                 # will go silent
    clock.now = clock.now + timedelta(hours=3)
    beat(app, team, label='dev-1')
    beat(app, team, label='dev-2')
    beat(app, team, label='dev-busy', state='building #4', task=4)
    beat(app, team, who='manager', label='mgr-1')
    conn.execute("UPDATE runner SET control = 'pause', control_by = ? WHERE label = 'dev-2'",
                 (team['accounts']['dev']['id'],))                                   # a person paused this one
    conn.commit()
    # main is red: every live runner without a control is paused; the person's pause and the silent runner are left
    out = {c['runner']: c['action'] for c in control(http, forge)}
    assert out == {'dev-old': 'silent', 'dev-1': 'pause', 'dev-busy': 'pause', 'mgr-1': 'pause'}
    state = controls(conn, team)
    assert state['dev-1'] == ('pause', 'supervisor') and state['dev-2'] == ('pause', 'user')
    assert state['dev-old'] == (None, None)
    assert len(audit_for(conn, 'supervisor.runner.pause')) == 3
    assert beat(app, team, label='dev-1').get_json()['control'] == 'pause'
    again = control(http, forge)                                                      # a second pass pauses nothing more
    assert [c['action'] for c in again] == ['silent'] and len(audit_for(conn, 'supervisor.runner.pause')) == 3
    # CI still running, or no checks yet: nothing is resumed
    for checks in ([], [dict(name='tests', state='pending')]):
        assert [c['action'] for c in control(http, FakeForge(base=checks))] == ['silent']
    # main is green: only the runners the supervisor paused are resumed
    out = {c['runner']: c['action'] for c in control(http, FakeForge(base=GREEN))}
    assert out == {'dev-old': 'silent', 'dev-1': 'resume', 'dev-busy': 'resume', 'mgr-1': 'resume'}
    state = controls(conn, team)
    assert state['dev-2'] == ('pause', 'user') and state['dev-1'][0] is None and state['dev-old'][0] is None
    assert len(audit_for(conn, 'supervisor.runner.resume')) == 3


def test_wake_an_idle_watcher_when_a_task_is_ready(app, team, conn, sup, clock):
    from datetime import timedelta
    from .test_runners import beat
    http = http_for(app, sup)
    beat(app, team, label='dev-1')
    beat(app, team, label='dev-busy', state='building #9', task=9)
    beat(app, team, who='manager', label='mgr-1')
    assert [c for c in control(http, FakeForge(base=GREEN)) if c['action']] == []     # nothing is ready
    ready = add(team, title='Ready')
    waits = add(team, title='Waits', depends_on=str(ready))
    out = control(http, FakeForge(base=GREEN))
    assert [(c['runner'], c['action'], c['error']) for c in out] == [('dev-1', 'wake', None)]   # not busy, not another person's
    assert len(audit_for(conn, 'supervisor.runner.wake')) == 1
    assert beat(app, team, label='dev-1').get_json()['control'] == 'wake'
    assert control(http, FakeForge(base=GREEN))[0]['action'] == 'wake'                # said once, woken again while still ready
    # once the ready task is claimed, the one that waits on it is not ready, so nobody is woken
    assert take(app, team, runner='dev-busy')['task']['number'] == ready
    assert [c for c in control(http, FakeForge(base=GREEN)) if c['action'] == 'wake'] == [] and waits
    # red main: the runners are paused, never woken
    assert sorted((c['runner'], c['action']) for c in control(http, FakeForge(base=RED))) == [
        ('dev-1', 'pause'), ('dev-busy', 'pause'), ('mgr-1', 'pause')]


def test_the_control_step_reports_failures_and_runs_in_the_pass(app, team, conn, sup):
    from tests.auto_runner.fake_forge import FakeForge as F

    class Broken(F):
        def base_checks(self, branch):
            raise sup_mod.autoforge.ForgeError('boom', 500)
    http = http_for(app, sup)
    [bad] = control(http, Broken())
    assert bad['action'] is None and 'boom' in bad['error']
    result = sup_mod.run_pass('alpha', http, reader(), 'origin/main', F(base=RED), MERGER)
    assert result['controls'] == [] and 'control' not in ' '.join(sup_mod.summary(result))
    assert sup_mod.control_step('alpha', lambda m, p, b=None: (403, {}), reader(), 'origin/main', F())[0]['error']


# ---------- auto mode (the default): the supervisor decides instead of waiting for a person ----------

def test_auto_mode_approves_a_plan_with_open_points_and_records_them(app, team, conn, sup):
    http = http_for(app, sup)
    n = add(team)
    post_plan(app, team, n, GOOD.replace('None.', '- Which colour should the button be?'))
    [done] = sup_mod.plan_step('alpha', http, reader(autonomy='auto'), 'origin/main')
    assert done['verdict'] == 'approve' and done['decision'] == 'approve' and status(conn, team, n) == 'approved'
    [row] = audit.rows(conn, 'supervisor.plan.approve')
    assert any('decided by the supervisor in auto mode: Which colour should the button be?' in r
               for r in row['detail']['reasons'])


def test_no_auto_json_means_auto_mode(app, team, conn, sup):
    http = http_for(app, sup)
    n = add(team)
    finish(app, team, n, 51)
    forge = FakeForge({51: pr_entry(author=MERGER)})            # the runner's PR, opened by the merging identity
    [out] = sup_mod.merge_step('alpha', http, reader(autonomy=None), 'origin/main', forge, MERGER)
    assert out['outcome'] == 'merged' and forge.merges


def test_auto_mode_without_a_policy_file_uses_the_default_policy(app, team, conn, sup):
    http = http_for(app, sup)
    n = add(team)
    finish(app, team, n, 52)
    forge = FakeForge({52: pr_entry(author=MERGER)})
    [out] = sup_mod.merge_step('alpha', http, reader(None, autonomy='auto'), 'origin/main', forge, MERGER)
    assert out['outcome'] == 'merged'


def test_auto_mode_still_refuses_red_ci_and_secrets(app, team, conn, sup):
    http = http_for(app, sup)
    a, b = add(team, title='red'), add(team, title='secret')
    finish(app, team, a, 53, 'alok/r1-claude-auto-runner')
    finish(app, team, b, 54, 'alok/r2-claude-auto-runner')
    forge = FakeForge({53: pr_entry(checks='failure'), 54: pr_entry(files=['config/prod.pem'])})
    outs = sup_mod.merge_step('alpha', http, reader(None, autonomy='auto'), 'origin/main', forge, MERGER)
    assert [o['outcome'] for o in outs] == ['refused', 'refused'] and forge.merges == []



# ---------- autonomy (decision record 0012, Phase 3b) ----------

def test_in_auto_mode_the_third_round_approves_and_a_manual_task_waits(app, team, conn, sup):
    http = http_for(app, sup)
    m = add(team, title='Second')
    runner = 'alok/second-claude-auto-runner'
    for _ in (1, 2):
        post_plan(app, team, m, BAD, runner)
        assert sup_mod.plan_step('alpha', http, reader(autonomy='auto'), 'origin/main')[0]['decision'] == 'changes'
    post_plan(app, team, m, BAD, runner)
    [third] = sup_mod.plan_step('alpha', http, reader(autonomy='auto'), 'origin/main')
    assert third['decision'] == 'approve' and status(conn, team, m) == 'approved'
    n = add(team)
    tasks.set_manual(conn, tasks.get(conn, team['alpha']['id'], n), True)
    conn.commit()
    post_plan(app, team, n, GOOD)
    [held] = sup_mod.plan_step('alpha', http, reader(autonomy='auto'), 'origin/main')
    assert held['decision'] == 'escalate' and status(conn, team, n) == 'plan_review'


def test_a_central_project_in_manual_mode_overrides_an_auto_repository(app, team, conn, sup):
    projects.set_autonomy(conn, team['alpha']['id'], 'manual')
    conn.commit()
    http = http_for(app, sup)
    m = add(team, title='Second')
    for _ in (1, 2, 3):
        post_plan(app, team, m, BAD, 'alok/second-claude-auto-runner')
        out = sup_mod.plan_step('alpha', http, reader(autonomy='auto'), 'origin/main')
    assert out[0]['decision'] == 'escalate' and status(conn, team, m) == 'plan_review'


def test_task_autonomy_needs_all_three_to_say_auto():
    assert sup_mod.task_autonomy({'autonomy': 'auto'}, {'autonomy': 'auto'}, {'manual': False}) == 'auto'
    assert sup_mod.task_autonomy({'autonomy': 'auto'}, {}, {}) == 'auto'
    assert sup_mod.task_autonomy({'autonomy': 'manual'}, {'autonomy': 'auto'}, {}) == 'manual'
    assert sup_mod.task_autonomy({'autonomy': 'auto'}, {'autonomy': 'manual'}, {}) == 'manual'
    assert sup_mod.task_autonomy({'autonomy': 'auto'}, {'autonomy': 'auto'}, {'manual': True}) == 'manual'
