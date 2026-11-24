"""Central service: actions that cannot be undone ask first (UX audit). Without `confirm=1` each route answers with a
confirm page saying what will happen, changes nothing, and posts the same form again with `confirm=1`."""
import datetime

import pytest

from central import accounts, personal, projects, tasks, tokens

from .test_access import post
from .test_repo_url import AT, conn, world  # noqa: F401  (fixtures)

NOW = datetime.datetime(2026, 11, 24, 9, 0, tzinfo=datetime.timezone.utc)


def asks(resp, question):
    text = resp.get_data(as_text=True)
    assert resp.status_code == 200 and question in text, text[-600:]
    assert 'name="confirm" value="1"' in text and 'Keep it as it is' in text


def test_remove_member_asks(world, conn):
    viewer = world['accounts']['viewer']
    asks(post(world['clients']['owner'], '/projects/alpha/members/%s/remove' % viewer['id']), 'Remove Viewer from Alpha?')
    assert projects.member_role(conn, world['alpha']['id'], viewer['id']) == 'viewer'


def test_disable_and_revoke_all_tokens_ask(world, conn):
    viewer = world['accounts']['viewer']
    personal.create_token(conn, viewer['id'], 'laptop', NOW)
    admin = world['clients']['admin']
    asks(post(admin, '/admin/accounts/%s/disabled' % viewer['id'], disabled='1'), 'Disable viewer@example.com?')
    assert accounts.get(conn, viewer['id'])['disabled'] == 0
    asks(post(admin, '/admin/accounts/%s/tokens/revoke' % viewer['id']), 'Revoke every token of viewer@example.com?')
    assert conn.query_one('SELECT revoked_at FROM personal_token')['revoked_at'] is None
    assert post(admin, '/admin/accounts/%s/disabled' % viewer['id'], disabled='0').status_code == 302   # enabling: no question


def test_my_token_revoke_asks(world, conn):
    personal.create_token(conn, world['accounts']['viewer']['id'], 'LAPTOP-1', NOW)
    [row] = conn.query('SELECT id FROM personal_token')
    asks(post(world['clients']['viewer'], '/account/tokens/%s/revoke' % row['id']), 'Revoke the token of LAPTOP-1?')
    assert conn.query_one('SELECT revoked_at FROM personal_token')['revoked_at'] is None


def test_machine_token_revoke_and_rotate_ask(world, conn):
    _, row = tokens.create(conn, world['alpha']['id'], ['pull'], None, AT)
    owner = world['clients']['owner']
    asks(post(owner, '/projects/alpha/tokens/%s/revoke' % row['id']), 'Revoke the machine token')
    asks(post(owner, '/projects/alpha/tokens/%s/rotate' % row['id']), 'Replace the machine token')
    assert tokens.get(conn, world['alpha']['id'], row['id'])['revoked_at'] is None


def test_reject_and_release_ask(world, conn):
    owner = accounts.find_by_email(conn, 'owner@example.com')
    t = tasks.create(conn, world['alpha'], owner, owner, 'CSV export', '', NOW)
    _, claim = tasks.take_next(conn, world['alpha'], owner, 'laptop', NOW)
    tasks.post_plan(conn, world['alpha'], t['number'], claim, 'P', '- [ ] **E1 x.**', NOW)
    asks(post(world['clients']['owner'], '/projects/alpha/tasks/1/decide', decision='reject'), 'Reject task #1 CSV export?')
    assert tasks.get(conn, world['alpha']['id'], 1)['status'] == 'plan_review'
    asks(post(world['clients']['admin'], '/projects/alpha/tasks/1/release'), 'Take task #1 back from its runner?')
    assert tasks.get(conn, world['alpha']['id'], 1)['claim_id'] == claim


def test_the_confirm_page_reposts_the_original_fields(world, conn):
    viewer = world['accounts']['viewer']
    text = post(world['clients']['admin'], '/admin/accounts/%s/disabled' % viewer['id'], disabled='1').get_data(as_text=True)
    assert 'name="disabled" value="1"' in text and 'action="/admin/accounts/%s/disabled"' % viewer['id'] in text


def test_destructive_forms_carry_the_dialog_text(world, conn):
    page = world['clients']['owner'].get('/projects/alpha').get_data(as_text=True)
    assert 'data-confirm="Remove' in page and 'data-confirm-action="Remove"' in page
