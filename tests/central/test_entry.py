import importlib.util
import io
import json
import os
import re
import sqlite3

import pytest

from central import cli, config, db

CENTRAL = os.path.join(os.path.dirname(__file__), '..', '..', 'auto-runner', 'central')
ENTRY = os.path.join(CENTRAL, 'api', 'index.py')
GOOD_KEY = 'k3Y-' + 'aZ9qW2eR7tY1uI5oP8sD4fG6hJ0lXcVbNm'


def load_entry(name='central_vercel_entry'):
    spec = importlib.util.spec_from_file_location(name, ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(monkeypatch, db_url):
    for name in config.ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('DATABASE_URL', db_url)
    return monkeypatch


def test_entry_point_exposes_app(env, db_url):
    env.setenv('SECRET_KEY', GOOD_KEY)
    db.init_db(db_url)
    entry = load_entry()
    assert entry.app.config['DATABASE_URL'] == db_url
    assert entry.app.config['SECRET_KEY'] == GOOD_KEY
    response = entry.app.test_client().get('/healthz')
    assert response.status_code == 200
    assert 'Content-Security-Policy' in response.headers


def test_entry_point_in_production_with_good_config(env, db_url):
    env.setenv('SECRET_KEY', GOOD_KEY)
    env.setenv('VERCEL_ENV', 'production')
    env.setenv('PUBLIC_URL', 'https://central.example.test')
    db.init_db(db_url)
    assert load_entry().app.test_client().get('/healthz').status_code == 200


def test_entry_point_refuses_without_secret_key(env):
    with pytest.raises(config.ConfigError):
        load_entry()


@pytest.mark.parametrize('url', [None, '', 'http://central.example.test', 'https://', 'central.example.test'])
def test_production_refuses_non_https_public_url(env, url):
    env.setenv('SECRET_KEY', GOOD_KEY)
    env.setenv('CENTRAL_ENV', 'production')
    if url is not None:
        env.setenv('PUBLIC_URL', url)
    with pytest.raises(config.ConfigError, match='https'):
        load_entry()


@pytest.mark.parametrize('key', ['changeme', 'Secret', 'dev', 'x' * 64, 'short-but-random-9Qz'])
def test_production_refuses_default_or_short_secret_key(env, key):
    env.setenv('SECRET_KEY', key)
    env.setenv('VERCEL_ENV', 'production')
    env.setenv('PUBLIC_URL', 'https://central.example.test')
    with pytest.raises(config.ConfigError, match='SECRET_KEY'):
        load_entry()


def test_production_refuses_missing_secret_key_even_when_testing():
    with pytest.raises(config.ConfigError, match='SECRET_KEY'):
        config.load({'TESTING': True, 'CENTRAL_ENV': 'production',
                     'PUBLIC_URL': 'https://central.example.test'}, environ={})


def test_outside_production_short_key_is_allowed():
    cfg = config.load({'SECRET_KEY': 'dev', 'PUBLIC_URL': 'http://localhost:5000'}, environ={})
    assert cfg['SECRET_KEY'] == 'dev'
    assert not config.is_production(cfg)
    assert config.is_production({'VERCEL_ENV': 'production'})
    assert not config.is_production({'VERCEL_ENV': 'preview'})


def test_vercel_json_routes_everything_to_the_entry_point():
    with open(os.path.join(CENTRAL, 'vercel.json'), encoding='utf-8') as f:
        conf = json.load(f)
    rewrites = conf['rewrites']
    assert len(rewrites) == 1
    rule = rewrites[0]
    assert rule['destination'] == '/api/index'
    # Vercel sources are path-to-regexp patterns; this one is also a plain regex.
    pattern = re.compile('^' + rule['source'] + '$')
    for path in ['/', '/healthz', '/signin', '/api/v1/events', '/api/v1/deliveries/x.v1/ack', '/admin/accounts']:
        assert pattern.match(path), path
    target = rule['destination'].lstrip('/') + '.py'
    assert os.path.isfile(os.path.join(CENTRAL, target))


def test_init_db_applies_schema_to_database_url(env, db_url):
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(['init-db'], out=out, err=err) == 0
    assert 'schema applied' in out.getvalue()
    path = db.parse_url(db_url)[1]
    conn = sqlite3.connect(path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    assert set(db.TABLES) <= names
    # Running it again is safe.
    assert cli.main(['init-db'], out=io.StringIO(), err=io.StringIO()) == 0


def test_init_db_needs_database_url():
    err = io.StringIO()
    assert cli.main(['init-db'], environ={}, out=io.StringIO(), err=err) == 2
    assert 'DATABASE_URL' in err.getvalue()


def test_init_db_rejects_unknown_scheme():
    err = io.StringIO()
    assert cli.main(['init-db'], environ={'DATABASE_URL': 'mysql://x'}, out=io.StringIO(), err=err) == 2
    assert 'unsupported' in err.getvalue()
