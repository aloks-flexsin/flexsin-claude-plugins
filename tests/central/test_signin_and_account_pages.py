"""Central service: sign-in, invite, device and Account pages (UX audit, PR 6). An invited person gives their name with
their password; everyone sets their name on Account (admins can set anyone's); the password fields have Show and a live
checklist; the device page shows the code large with a countdown; a failed sign-in says how the lockout works without
saying whether the email exists."""
import pathlib

import pytest

from central import accounts, audit, personal

from .test_access import post
from .test_invites import LINK, NEW_PW, clock, use  # noqa: F401  (fixtures)
from .test_repo_url import conn, world  # noqa: F401  (fixtures)

APP_JS = pathlib.Path(__file__).resolve().parents[2] / 'auto-runner' / 'central' / 'static' / 'app.js'


def invite_link(world):
    resp = post(world['clients']['owner'], '/projects/alpha/members', email='new.person@example.com', role='developer')
    return LINK.search(resp.get_data(as_text=True)).group(1)


def test_an_invited_person_gives_their_name(app, world, conn, clock):
    path = invite_link(world)
    from .test_access import new_client
    page = new_client(app).get(path).get_data(as_text=True)
    assert 'name="name"' in page and 'data-show-password="inv-pw"' in page and 'data-rule="length"' in page
    assert '>in 3 days<' in page
    c = new_client(app)
    from .test_access import token
    resp = c.post(path, data={'name': '  Ravi   Kumar ', 'password': NEW_PW, 'password_again': NEW_PW,
                              'csrf_token': token(c, '/signin')})
    assert resp.status_code == 302
    assert accounts.find_by_email(conn, 'new.person@example.com')['name'] == 'Ravi Kumar'


def test_no_name_is_fine_and_a_bad_name_leaves_the_password_unset(app, world, conn, clock):
    assert use(app, invite_link(world))[1].status_code == 302
    from .test_access import new_client, token
    path2 = LINK.search(post(world['clients']['owner'], '/projects/alpha/members', email='x@example.com',
                             role='viewer').get_data(as_text=True)).group(1)
    c = new_client(app)
    resp = c.post(path2, data={'name': 'x' * 101, 'password': NEW_PW, 'password_again': NEW_PW,
                               'csrf_token': token(c, '/signin')})
    assert resp.status_code == 400 and 'longer than 100' in resp.get_data(as_text=True)
    assert accounts.find_by_email(conn, 'x@example.com')['password_hash'] == '!'


def test_account_page_sets_the_name_and_keeps_the_password_form(world, conn):
    c = world['clients']['viewer']
    page = c.get('/account').get_data(as_text=True)
    assert 'Your name' in page and 'Change password' in page and 'data-pw-form' in page
    assert c.get('/account/password').headers['Location'].endswith('/account#password')
    resp = post(c, '/account/name', name='Vee Ewer')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/account?saved=name')
    assert accounts.get(conn, world['accounts']['viewer']['id'])['name'] == 'Vee Ewer'
    assert 'Vee Ewer' in c.get('/my').get_data(as_text=True)            # the top bar uses it
    assert post(c, '/account/name', name='bad\x07name').status_code == 400
    assert post(c, '/account/name', name='').status_code == 302
    assert accounts.get(conn, world['accounts']['viewer']['id'])['name'] is None
    assert len(audit.rows(conn, 'account.name')) == 2


def test_admins_set_anyones_name_and_others_cannot(world, conn):
    target = world['accounts']['viewer']['id']
    assert post(world['clients']['admin'], '/admin/accounts/%s/name' % target, name='Viv').status_code == 302
    assert accounts.get(conn, target)['name'] == 'Viv'
    assert post(world['clients']['owner'], '/admin/accounts/%s/name' % target, name='Nope').status_code == 404
    assert accounts.get(conn, target)['name'] == 'Viv'
    assert post(world['clients']['admin'], '/admin/accounts/nope/name', name='x').status_code == 404


def test_the_account_menu_links_account_not_accounts(world):
    page = world['clients']['admin'].get('/account').get_data(as_text=True)
    assert 'href="/account">Account</a>' in page and 'aria-current="page">Accounts' not in page


def test_clean_name():
    assert accounts.clean_name('  a   b ') == 'a b' and accounts.clean_name('') is None and accounts.clean_name(None) is None
    for bad in ('x' * 101, 'a\x00b'):
        with pytest.raises(accounts.AccountError):
            accounts.clean_name(bad)


def test_a_failed_sign_in_explains_the_lockout_the_same_way_for_everyone(app, world):
    from .test_access import new_client, token
    c = new_client(app)
    resp = c.post('/signin', data={'email': 'nobody@example.com', 'password': 'x', 'csrf_token': token(c, '/signin')})
    assert resp.status_code == 401 and 'After 5 wrong tries an account waits 15 minutes' in resp.get_data(as_text=True)


def test_the_device_page_shows_the_code_large_with_a_countdown(app, world, conn, clock):
    _, user_code = personal.start(conn, 'Alok laptop', 'github.com/example/alpha', clock.now)
    resp = post(world['clients']['viewer'], '/device', user_code=user_code)
    page = resp.get_data(as_text=True)
    assert 'class="device-code"' in page and 'data-countdown="' in page and 'Alok laptop' in page
    assert page.index('value="deny"') < page.index('value="approve"')


def test_the_script_has_show_password_checks_and_countdown():
    js = APP_JS.read_text(encoding='utf-8')
    for needle in ('[data-show-password]', 'form[data-pw-form]', '[data-countdown]', ' left"'):
        assert needle in js, needle
