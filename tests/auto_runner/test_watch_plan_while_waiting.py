"""auto-runner plugin: while a resumed task request waits for an earlier branch, the watch step claims the next task in
the same step (queue E3): a plan answer is planned, a build answer only gets its pending folder. Fake builds, fake
planner and the in-memory task fake (no network, no CLI)."""
import json
import shutil
from pathlib import Path

from tests.auto_runner.fake_central import FakeRunners
from tests.auto_runner.test_runner import project  # noqa: F401  (project is a fixture)
from tests.auto_runner.test_watch_resume_waiting import (ENV, POLL, Clock, aw, waiting, waiting_build,  # noqa: F401
                                                         watched)


class Next:
    """take_next answers `reply` once (then no task); start, reopen_build and plan are recorded."""

    def __init__(self, monkeypatch, reply=None, start=None, plan='posted'):
        self.taken, self.started, self.reopened, self.planned = [], [], [], []
        replies = [reply or {'task': None}]

        def take_next(*a):
            self.taken.append(a)
            return replies.pop(0) if replies else {'task': None}

        def start_(project, number, *a):
            self.started.append(number)
            return start(project, number) if start else f'task-{number}-x'

        def plan_(project, number, claude, *a):
            self.planned.append((number, claude))
            if isinstance(plan, Exception):
                raise plan
            return {'status': plan, 'reason': '' if plan == 'posted' else 'the planner gave up'}
        monkeypatch.setattr(aw.autotasks, 'take_next', take_next)
        monkeypatch.setattr(aw.autotasks, 'start', start_)
        monkeypatch.setattr(aw.autotasks, 'reopen_build', lambda p, n, *a: self.reopened.append(n))
        monkeypatch.setattr(aw.autoplan, 'plan', plan_)


def plan_answer(n=8):
    return {'task': {'number': n, 'title': f'Task {n}'}, 'action': 'plan'}


def build_answer(n=8):
    return {'task': {'number': n, 'title': f'Task {n}'}, 'action': 'build'}


def test_a_waiting_build_then_a_plan_answer_plans_it_and_stays_waiting(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 7)
    calls, slugs, started = Next(monkeypatch, plan_answer()), [], []
    out = aw.step(p, None, ENV, svc.send, build=waiting_build(slugs), on_start=lambda n, a: started.append((n, a)))
    assert len(slugs) == 1 and len(calls.taken) == 1 and [n for n, _ in calls.planned] == [8]
    assert started == [(7, 'build'), (8, 'plan')] and calls.started == []
    assert out == {'task': 8, 'action': 'plan', 'title': 'Task 8', 'ok': True, 'detail': 'posted', 'waiting': True}


def test_a_waiting_build_then_a_build_answer_only_starts_it(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 7)

    def start(project, number):              # what autotasks.start leaves: the task's own pending folder
        d = Path(project) / '.auto' / 'pending' / f'task-{number}-x'
        d.mkdir(parents=True)
        return d.name
    calls, slugs, started = Next(monkeypatch, build_answer(), start=start), [], []
    out = aw.step(p, None, ENV, svc.send, build=waiting_build(slugs), on_start=lambda n, a: started.append((n, a)))
    assert calls.started == [8] and (p / '.auto' / 'pending' / 'task-8-x').is_dir()
    assert slugs == ['task-7-task-7'] and started == [(7, 'build')] and calls.planned == [] and calls.reopened == []
    assert out['task'] == 7 and out['action'] == 'build' and out['waiting'] is True
    assert out['detail'] == aw.WAITING_DETAIL


def test_a_waiting_build_then_no_task_returns_the_waiting_outcome_unchanged(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 7)
    calls, slugs = Next(monkeypatch), []
    out = aw.step(p, None, ENV, svc.send, build=waiting_build(slugs))
    assert len(calls.taken) == 1 and calls.started == [] and calls.planned == []
    assert out == {'task': 7, 'action': 'build', 'title': 'Plan of task 7', 'ok': True,
                   'detail': aw.WAITING_DETAIL, 'exit': 0, 'waiting': True}


def test_a_new_build_answer_that_cannot_start_is_reopened_and_the_wait_returned(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 7)

    def start(project, number):
        raise aw.autotasks.TaskError('the plan is missing')
    calls, slugs = Next(monkeypatch, build_answer(), start=start), []
    out = aw.step(p, None, ENV, svc.send, build=waiting_build(slugs))
    assert calls.started == [8] and calls.reopened == [8]
    assert out['task'] == 7 and out['waiting'] is True and out['detail'] == aw.WAITING_DETAIL


def test_a_claim_the_service_refuses_keeps_the_waiting_outcome(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 7)
    Next(monkeypatch)

    def refused(*a):
        raise aw.autodeliver.TransportUnavailable('down (simulated)')
    monkeypatch.setattr(aw.autotasks, 'take_next', refused)
    out = aw.step(p, None, ENV, svc.send, build=waiting_build([]))
    assert out['task'] == 7 and out['waiting'] is True


def test_a_failed_plan_while_waiting_is_a_failure_and_the_loop_waits_after_it(watched, monkeypatch, capsys):
    p, svc = watched
    waiting(p, svc, 7)
    Next(monkeypatch, plan_answer(), plan='refused')
    clock, slugs = Clock(), []
    runners = FakeRunners(svc.send)
    assert aw.watch(p, None, ENV, runners.send, build=waiting_build(slugs), clock=clock.time, sleep=clock.sleep,
                    max_steps=1) == 0
    assert 'task 8 (plan): failed, refused: the planner gave up' in capsys.readouterr().out
    beat = json.loads((p / '.auto' / 'run' / 'watch.json').read_text(encoding='utf-8'))
    assert beat['state'] == 'waiting_after_failure' and 'waiting' not in beat['last'] and sum(clock.slept) == POLL
    assert 'building #7' in runners.states() and 'planning #8' in runners.states()


def test_a_plan_that_raises_while_waiting_is_a_failure(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 7)
    Next(monkeypatch, plan_answer(), plan=RuntimeError('planner crashed'))
    out = aw.step(p, None, ENV, svc.send, build=waiting_build([]))
    assert out['task'] == 8 and out['ok'] is False and 'waiting' not in out
    assert out['detail'] == 'RuntimeError: planner crashed'


def test_the_loop_reports_a_successful_plan_while_waiting_as_waiting(watched, monkeypatch, capsys):
    p, svc = watched
    waiting(p, svc, 7)
    Next(monkeypatch, plan_answer())
    clock = Clock()
    runners = FakeRunners(svc.send)
    assert aw.watch(p, None, ENV, runners.send, build=waiting_build([]), clock=clock.time, sleep=clock.sleep,
                    max_steps=1) == 0
    assert 'task 8 (plan): waiting, posted' in capsys.readouterr().out
    beat = json.loads((p / '.auto' / 'run' / 'watch.json').read_text(encoding='utf-8'))
    assert beat['state'] == 'waiting_for_merge' and sum(clock.slept) == POLL


def test_the_plan_while_waiting_goes_through_the_meter(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 7)
    calls = Next(monkeypatch, plan_answer())
    clock = Clock()

    def claude(cmd, cwd):
        return 0, json.dumps({'type': 'result', 'result': 'ok', 'total_cost_usd': 0.25})

    def plan_(project, number, claude_, *a):
        claude_(['claude', '-p', 'plan'], project)
        return {'status': 'posted', 'reason': ''}
    monkeypatch.setattr(aw.autoplan, 'plan', plan_)
    aw.step(p, claude, ENV, svc.send, build=waiting_build([]), meter=aw.Meter(p, clock.time))
    spent = json.loads(aw.spend_path(p, clock.now).read_text(encoding='utf-8'))
    assert [(c['task'], c['action']) for c in spent['calls']] == [(8, 'plan')]
    assert calls.taken


def test_a_resumed_build_that_finishes_does_not_claim(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 7)
    calls = Next(monkeypatch, plan_answer())

    def build(project, slug, *a):
        shutil.rmtree(Path(project) / '.auto' / 'pending' / slug)
        return 0
    out = aw.step(p, None, ENV, svc.send, build=build)
    assert out['ok'] is True and 'waiting' not in out and calls.taken == [] and calls.planned == []


def test_a_resumed_build_that_fails_does_not_claim(watched, monkeypatch):
    p, svc = watched
    waiting(p, svc, 7)
    calls = Next(monkeypatch, plan_answer())
    out = aw.step(p, None, ENV, svc.send, build=lambda *a: 1)
    assert out['ok'] is False and 'waiting' not in out and calls.taken == [] and calls.planned == []
