import io

import pytest

from central import accounts, cli, invites, projects, tasks

from .test_account_admin import ADMIN_PW, USER_PW, as_root, bob, conn, new_client, post, root, signin  # noqa: F401
from .test_tasks import AT

GOOD = 'violet-anchor-58-tide'


@pytest.fixture
def sup(conn, db_url, monkeypatch):
    replies = iter([GOOD, GOOD])
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt='': next(replies))
    out = io.StringIO()
    assert cli.main(['supervisor-create', 'Sup@Example.com'], environ={'DATABASE_URL': db_url}, out=out,
                    err=io.StringIO()) == 0
    assert 'created supervisor sup@example.com' in out.getvalue()
    return accounts.find_by_email(conn, 'sup@example.com')


def project(conn, bob):
    proj = projects.create(conn, 'alpha', 'Alpha', AT, 'https://github.com/example/alpha.git')
    projects.add_member(conn, proj['id'], 'bob@example.com', 'developer', AT)
    return proj


def test_cli_creates_a_supervisor_who_can_sign_in(sup, app):
    assert sup['role'] == 'supervisor' and 'supervisor' in accounts.ROLES
    assert signin(new_client(app), 'sup@example.com', GOOD).status_code == 302


def test_a_supervisor_is_a_manager_member_but_never_runs_or_creates_tasks(conn, sup, bob):
    proj = project(conn, bob)
    projects.add_member(conn, proj['id'], 'sup@example.com', 'manager', AT)
    assert projects.member_role(conn, proj['id'], sup['id']) == 'manager'
    assert tasks._can_run(conn, proj, sup) is False
    assert tasks._can_run(conn, proj, bob) is not None
    with pytest.raises(tasks.TaskError) as exc:
        tasks.create(conn, proj, sup, sup, 'x', '', AT)
    assert exc.value.status == 403
    with pytest.raises(tasks.TaskError):                      # never an assignee, not even when an admin asks
        tasks.create(conn, proj, bob, sup, 'x', '', AT)


def test_invites_do_not_offer_or_accept_supervisor(conn):
    assert 'supervisor' not in accounts.INVITE_ROLES
    with pytest.raises(accounts.AccountError):
        invites.create_account(conn, 'new@example.com', role='supervisor')
    assert accounts.find_by_email(conn, 'new@example.com') is None


def test_admin_shows_the_role_but_refuses_changes_to_or_from_it(conn, as_root, bob, sup):
    page = as_root.get('/admin/accounts').get_data(as_text=True)
    assert 'sup@example.com' in page and 'supervisor' in page
    assert 'value="supervisor"' not in page                   # not offered as a choice
    assert post(as_root, '/admin/accounts/%s/role' % bob['id'], role='supervisor').status_code == 400
    assert post(as_root, '/admin/accounts/%s/role' % sup['id'], role='user').status_code == 400
    assert accounts.get(conn, bob['id'])['role'] == 'user' and accounts.get(conn, sup['id'])['role'] == 'supervisor'
    assert post(as_root, '/admin/accounts', email='x@example.com', role='supervisor').status_code == 400
    assert post(as_root, '/admin/accounts/%s/role' % sup['id'], role='supervisor').status_code == 302   # unchanged is harmless


def test_existing_roles_still_change(conn, as_root, bob):
    assert post(as_root, '/admin/accounts/%s/role' % bob['id'], role='admin').status_code == 302
