"""Central service: people and access (UX audit, PR 5). Members: people with when they were last active, a role select
that saves itself (a Save button without JavaScript), Remove in a "⋯" menu, and Pending invites with the link's expiry,
a new link and Cancel invite. Links and new tokens come in a copy field. Admin › Accounts: search, projects count, last
active, actions in a menu. Last active is read from sessions and personal tokens; nothing new is stored."""
import pathlib

from central import accounts, audit, invites, personal, projects

from .test_access import post
from .test_invites import LINK, clock, use  # noqa: F401  (fixtures)
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)

APP_JS = pathlib.Path(__file__).resolve().parents[2] / 'auto-runner' / 'central' / 'static' / 'app.js'


def invite(world, email='new@example.com', role='developer'):
    resp = post(world['clients']['owner'], '/projects/alpha/members', email=email, role=role)
    assert resp.status_code == 200
    return resp


def test_members_show_last_active_and_a_self_saving_role(world, clock):
    page = world['clients']['owner'].get('/projects/alpha/settings/members').get_data(as_text=True)
    assert 'active <time' in page                                            # their own sessions say when
    assert 'data-autosave' in page and 'class="btn autosave-btn">Save</button>' in page
    assert '<details class="pop row-menu">' in page and 'Remove from project' in page
    viewer = world['clients']['viewer'].get('/projects/alpha/settings/members').get_data(as_text=True)
    assert 'data-autosave' not in viewer and 'Remove from project' not in viewer and 'Invite people' not in viewer


def test_an_invite_shows_its_link_in_a_copy_field_and_waits_under_pending(world, clock):
    resp = invite(world)
    page = resp.get_data(as_text=True)
    assert 'data-copy="invite-link"' in page and 'as developer' in page and LINK.search(page)
    members = world['clients']['owner'].get('/projects/alpha/settings/members').get_data(as_text=True)
    assert 'Pending invites' in members and 'link expires <time' in members and '>in 3 days<' in members
    assert 'Cancel invite' in members


def test_cancel_invite_asks_first_then_ends_the_link_and_the_membership(app, world, conn, clock):
    path = LINK.search(invite(world).get_data(as_text=True)).group(1)
    new = accounts.find_by_email(conn, 'new@example.com')
    url = '/projects/alpha/members/%s/cancel' % new['id']
    asked = post(world['clients']['owner'], url)
    assert asked.status_code == 200 and 'Cancel the invite for new@example.com?' in asked.get_data(as_text=True)
    assert projects.member_role(conn, world['alpha']['id'], new['id']) == 'developer'      # nothing happened yet
    assert post(world['clients']['owner'], url, confirm='1').status_code == 302
    assert projects.member_role(conn, world['alpha']['id'], new['id']) is None
    assert use(app, path)[1].status_code == 404 and len(audit.rows(conn, 'invite.cancel')) == 1
    invite(world)                                                            # the same email can be invited again


def test_cancel_invite_only_for_people_who_have_not_joined(world, conn):
    viewer = world['accounts']['viewer']['id']
    resp = post(world['clients']['owner'], '/projects/alpha/members/%s/cancel' % viewer, confirm='1')
    assert resp.status_code == 400 and 'remove them instead' in resp.get_data(as_text=True)
    assert post(world['clients']['manager'], '/projects/alpha/members/%s/cancel' % viewer, confirm='1').status_code == 404


def test_last_active_comes_from_sessions_and_tokens(world, conn):
    import datetime
    later = datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc)
    raw = personal.create_token(conn, world['accounts']['viewer']['id'], 'laptop', later)
    personal.touch(conn, personal.lookup(conn, raw, later)[0], later)
    seen = accounts.last_active(conn)
    assert seen[world['accounts']['viewer']['id']].startswith('2030-01-01')
    assert world['accounts']['owner']['id'] in seen
    assert accounts.project_counts(conn)[world['accounts']['owner']['id']] == 1


def test_machine_tokens_copy_field_and_my_tokens_rows(world, conn):
    page = post(world['clients']['manager'], '/projects/alpha/settings/tokens', scope=['ingest', 'pull']).get_data(as_text=True)
    assert 'data-copy="new-token"' in page and 'Machine tokens' in page and '</span>Active</span>' in page
    personal.create_token(conn, world['accounts']['viewer']['id'], 'Alok laptop', AT_DT())
    mine = world['clients']['viewer'].get('/account/tokens').get_data(as_text=True)
    assert '<strong>Alok laptop</strong>' in mine and 'never used' in mine and 'data-confirm="Revoke the token of Alok laptop?"' in mine


def AT_DT():
    import datetime
    return datetime.datetime(2026, 11, 23, 9, 0, tzinfo=datetime.timezone.utc)


def test_admin_accounts_search_counts_and_menu(world, conn):
    c = world['clients']['admin']
    page = c.get('/admin/accounts').get_data(as_text=True)
    assert '1 project ·' in page and 'Send reset link' in page and '<details class="pop row-menu">' in page
    found = c.get('/admin/accounts?q=MANAGER').get_data(as_text=True)
    assert 'manager@example.com' in found and 'viewer@example.com' not in found and '1 of 4' in found
    assert 'No account matches.' in c.get('/admin/accounts?q=zzz').get_data(as_text=True)


def test_the_script_saves_roles_and_keeps_future_times():
    js = APP_JS.read_text(encoding='utf-8')
    assert 'select[data-autosave]' in js and 's < 0) return null' in js
    assert invites.open_links is not None
