"""auto-runner plugin: nobody types a slug (it is read from origin and the service confirms it), and a project is set up
and run from its main checkout, never from a git worktree. The service is the real app through its Flask test client."""
import pytest

from tests.auto_runner.test_init_central import AT, BASE, ENV, REPO, SLUG, ac, ai, central, repo, sh  # noqa: F401
from tests.auto_runner.test_runner import ar

safe = ai.autosafe


def init_plan(p, central, env=None, **opts):
    return ai.make_plan(p, {'url': BASE, **opts}, env=env if env is not None else {}, send=central.send)


def section(plan):
    """The central section init would write: into a new auto.json, or added to an existing one."""
    return (plan['new_cfg'] or {}).get('central') or plan['central_add']


# ---------- the slug comes from origin ----------

def test_slug_from_repo_matches_the_service_rule():
    assert ac.slug_from_repo('git@github.com:Example/Case-Study-VA.git') == 'case-study-va'
    assert ac.slug_from_repo('https://github.com/o/My_Repo') == 'my-repo'
    assert ac.slug_from_repo('C:/not/a/remote') is None


def test_init_reads_the_project_from_origin_without_a_token(tmp_path, central):
    p = repo(tmp_path, REPO)
    plan = init_plan(p, central)
    assert section(plan) == {'enabled': True, 'url': BASE, 'project': SLUG, 'token_env': ENV}
    assert any('read from this folder\'s origin (the service confirms it once the token is set)' in n
               for n in plan['central_notes'])
    assert central.calls == []


def test_init_confirms_the_project_with_the_token(tmp_path, central):
    p = repo(tmp_path, REPO)
    plan = init_plan(p, central, env={ENV: central.token})
    assert section(plan)['project'] == SLUG and not plan['central_problems']


def test_the_services_slug_wins_when_it_named_the_repository_differently(tmp_path, central):
    from central import projects, tokens
    other = projects.create(central.conn, 'example-thing', 'Thing', AT, 'https://github.com/example/thing.git')
    token, _ = tokens.create(central.conn, other['id'], ['pull'], None, AT)
    p = repo(tmp_path, 'git@github.com:example/thing.git')
    plan = init_plan(p, central, env={'AUTO_RUNNER_TOKEN_THING': token})
    assert section(plan)['project'] == 'example-thing' and not plan['central_problems']
    assert section(plan)['token_env'] == 'AUTO_RUNNER_TOKEN_THING'      # the variable the person already set


def test_a_token_for_another_repository_is_still_refused(tmp_path, central):
    p = repo(tmp_path, REPO)
    plan = init_plan(p, central, env={ENV: central.other_token})
    assert any('belongs to project "other"' in x for x in plan['central_problems'])


def test_no_origin_means_no_guess(tmp_path, central):
    p = repo(tmp_path)
    plan = init_plan(p, central)
    assert section(plan) is None and any('no "origin" remote' in x for x in plan['central_problems'])


def test_a_given_slug_is_still_honoured(tmp_path, central):
    p = repo(tmp_path, REPO)
    assert section(init_plan(p, central, project='acme-web'))['project'] == SLUG


# ---------- worktrees ----------

@pytest.fixture
def worktree(tmp_path):
    main = repo(tmp_path, REPO)
    wt = tmp_path / 'main-wt'
    sh(main, 'worktree', 'add', '-q', str(wt), '-b', 'side')
    return main, wt


def test_worktree_main_names_the_main_checkout(worktree):
    main, wt = worktree
    assert safe.worktree_main(wt) == main.resolve() and safe.worktree_main(main) is None


def test_init_refuses_in_a_worktree(worktree, central):
    _, wt = worktree
    plan = init_plan(wt, central)
    assert any(f'git worktree of {worktree[0].resolve()}' in x for x in plan['problems'])


def test_central_status_points_to_the_main_checkout(worktree, central):
    main, wt = worktree
    lines, ok = ac.status(wt, env={}, send=central.send)
    assert not ok and f'git worktree of {main.resolve()}' in lines[0] and central.calls == []


def test_the_runner_refuses_to_start_in_a_worktree(worktree):
    main, wt = worktree
    cfg = ar.autocfg.load(wt)
    assert any('git worktree of' in p for p in safe.preflight(wt, cfg, 'main'))
    assert not any('git worktree of' in p for p in safe.preflight(main, ar.autocfg.load(main), 'main'))
