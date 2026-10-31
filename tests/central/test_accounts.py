import io
import os
import re
import subprocess
import sys

import pytest

from central import accounts, cli, db, passwords

GOOD = 'violet-anchor-58-tide'


@pytest.fixture
def conn(db_url):
    db.init_db(db_url)
    c = db.connect(db_url)
    yield c
    c.close()


def test_hash_differs_per_salt_and_verifies():
    a = passwords.hash_password(GOOD)
    b = passwords.hash_password(GOOD)
    assert a != b
    assert passwords.verify_password(GOOD, a) and passwords.verify_password(GOOD, b)
    assert not passwords.verify_password(GOOD + 'x', a)


def test_hash_carries_its_parameters_and_hides_the_password():
    stored = passwords.hash_password(GOOD)
    scheme, n, r, p, salt, key = stored.split('$')
    assert (scheme, int(n), int(r), int(p)) == ('scrypt', passwords.N, passwords.R, passwords.P)
    assert len(passwords._unb64(salt)) == 16
    assert GOOD not in stored


def test_verify_uses_the_stored_parameters():
    salt = b's' * 16
    key = passwords._derive(GOOD, salt, 2 ** 10, 8, 1, 32)
    stored = 'scrypt$1024$8$1$%s$%s' % (passwords._b64(salt), passwords._b64(key))
    assert passwords.verify_password(GOOD, stored)


def test_verify_is_constant_time(monkeypatch):
    seen = []
    real = passwords.hmac.compare_digest
    monkeypatch.setattr(passwords.hmac, 'compare_digest', lambda a, b: seen.append(1) or real(a, b))
    passwords.verify_password(GOOD, passwords.hash_password(GOOD))
    assert seen


@pytest.mark.parametrize('stored', ['', 'nonsense', 'bcrypt$1$2$3$a$b', 'scrypt$x$8$1$a$b', 'scrypt$3$8$1$YQ$YQ', None])
def test_malformed_hash_never_verifies(stored):
    assert passwords.verify_password(GOOD, stored) is False


@pytest.mark.parametrize('pw', ['', 'short', 'elevenchars', 'a' * 11])
def test_short_passwords_are_refused(pw):
    with pytest.raises(passwords.PasswordError):
        passwords.hash_password(pw)


@pytest.mark.parametrize('pw', ['password1234', 'Password1234', 'QWERTY123456', '123456789012'])
def test_common_passwords_are_refused(pw):
    assert len(pw) >= passwords.MIN_LENGTH
    with pytest.raises(passwords.PasswordError, match='common'):
        passwords.hash_password(pw)


def test_common_list_is_bundled():
    assert os.path.isfile(passwords.COMMON_PATH)
    assert len(passwords.common_passwords()) >= 50


def test_create_and_find_by_email_case_insensitive(conn):
    acc = accounts.create(conn, '  Ada@Example.COM ', GOOD)
    assert acc['email'] == 'ada@example.com'
    assert acc['role'] == 'user' and acc['disabled'] == 0
    assert accounts.find_by_email(conn, 'ADA@example.com')['id'] == acc['id']
    assert passwords.verify_password(GOOD, acc['password_hash'])
    assert GOOD not in acc['password_hash']
    with pytest.raises(accounts.AccountError, match='already exists'):
        accounts.create(conn, 'ada@EXAMPLE.com', GOOD)
    assert conn.query_one('SELECT COUNT(*) AS n FROM account') == {'n': 1}


def test_roles_and_disabled_flag(conn):
    admin = accounts.create(conn, 'root@example.com', GOOD, role='admin')
    assert admin['role'] == 'admin'
    with pytest.raises(accounts.AccountError):
        accounts.create(conn, 'x@example.com', GOOD, role='owner')
    assert accounts.is_enabled(admin)
    assert accounts.set_disabled(conn, admin['id'], True)['disabled'] == 1
    assert not accounts.is_enabled(accounts.get(conn, admin['id']))
    assert accounts.set_disabled(conn, admin['id'], False)['disabled'] == 0
    with pytest.raises(accounts.AccountError):
        accounts.set_disabled(conn, 'missing', True)


def test_create_refuses_weak_password_and_bad_email(conn):
    with pytest.raises(passwords.PasswordError):
        accounts.create(conn, 'a@example.com', 'short')
    for bad in ('', 'no-at-sign', '@example.com', 'a@', 'a@b', 'a b@example.com', 'a@@example.com'):
        with pytest.raises(accounts.AccountError):
            accounts.create(conn, bad, GOOD)
    assert accounts.find_by_email(conn, 'not an email') is None
    assert conn.query_one('SELECT COUNT(*) AS n FROM account') == {'n': 0}


def _getpass(monkeypatch, *answers):
    prompts = []
    replies = iter(answers)
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt='': prompts.append(prompt) or next(replies))
    return prompts


def test_cli_creates_admin_from_getpass(monkeypatch, conn, db_url):
    prompts = _getpass(monkeypatch, GOOD, GOOD)
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(['create-admin', '--email', 'Boss@Example.com'], environ={'DATABASE_URL': db_url}, out=out, err=err)
    assert code == 0, err.getvalue()
    assert len(prompts) == 2
    acc = accounts.find_by_email(conn, 'boss@example.com')
    assert acc['role'] == 'admin' and acc['disabled'] == 0
    assert passwords.verify_password(GOOD, acc['password_hash'])
    assert GOOD not in out.getvalue() + err.getvalue()


@pytest.mark.parametrize('answers, message', [
    ((GOOD, GOOD + '!'), 'differ'),
    (('short', 'short'), 'at least'),
    (('password1234', 'password1234'), 'common'),
])
def test_cli_refuses_mismatch_and_weak(monkeypatch, conn, db_url, answers, message):
    _getpass(monkeypatch, *answers)
    err = io.StringIO()
    code = cli.main(['create-admin', '--email', 'a@example.com'], environ={'DATABASE_URL': db_url},
                    out=io.StringIO(), err=err)
    assert code == 1 and message in err.getvalue()
    assert conn.query_one('SELECT COUNT(*) AS n FROM account') == {'n': 0}


def test_cli_refuses_duplicate_email(monkeypatch, conn, db_url):
    accounts.create(conn, 'a@example.com', GOOD)
    _getpass(monkeypatch, GOOD, GOOD)
    err = io.StringIO()
    assert cli.main(['create-admin', '--email', 'A@example.com'], environ={'DATABASE_URL': db_url},
                    out=io.StringIO(), err=err) == 1
    assert 'already exists' in err.getvalue()


def test_cli_needs_database_url(monkeypatch):
    _getpass(monkeypatch, GOOD, GOOD)
    err = io.StringIO()
    assert cli.main(['create-admin', '--email', 'a@example.com'], environ={}, out=io.StringIO(), err=err) == 2


@pytest.mark.parametrize('argv', [
    ['create-admin', '--email', 'a@example.com', '--password', GOOD],
    ['create-admin', '--email', 'a@example.com', '--password=' + GOOD],
    ['create-admin', '--email', 'a@example.com', '--pass', GOOD],
    ['create-admin', '--email', 'a@example.com', GOOD],
])
def test_argparse_rejects_a_password_argument(monkeypatch, argv, capsys):
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt='': pytest.fail('getpass must not be reached'))
    with pytest.raises(SystemExit) as exc:
        cli.main(argv, environ={'DATABASE_URL': 'sqlite:///unused'})
    assert exc.value.code == 2


def test_parser_has_no_password_option():
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == 'command')
    for p in [parser] + list(sub.choices.values()):
        for action in p._actions:
            assert not any('pass' in s for s in action.option_strings)


def test_python_m_central_entry_point():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'auto-runner'))
    proc = subprocess.run([sys.executable, '-m', 'central', 'create-admin', '--password', 'x'],
                          cwd=root, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2
    assert 'unrecognized arguments' in proc.stderr or 'required' in proc.stderr


def test_no_self_signup_route(app, client):
    for rule in app.url_map.iter_rules():
        assert not re.search(r'sign-?up|register', rule.rule, re.I), rule.rule
    for path in ('/signup', '/sign-up', '/register', '/api/v1/signup', '/api/v1/register'):
        assert client.get(path).status_code == 404
        assert client.post(path, data={'email': 'a@example.com', 'password': GOOD}).status_code == 404
