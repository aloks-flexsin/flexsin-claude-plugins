import os
import sys

import pytest

AUTO_RUNNER = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'auto-runner'))
if AUTO_RUNNER not in sys.path:
    sys.path.insert(0, AUTO_RUNNER)


@pytest.fixture
def db_url(tmp_path):
    return 'sqlite:///' + (tmp_path / 'central.sqlite3').as_posix()


@pytest.fixture
def app(db_url, monkeypatch):
    import central
    from central import db
    monkeypatch.delenv('SECRET_KEY', raising=False)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    db.init_db(db_url)
    return central.create_app({'TESTING': True, 'DATABASE_URL': db_url})


@pytest.fixture
def client(app):
    # HTTPS, so Secure cookies are sent back in later items.
    client = app.test_client()
    client.environ_base['wsgi.url_scheme'] = 'https'
    return client
