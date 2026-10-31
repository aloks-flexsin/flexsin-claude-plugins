"""auto-runner plugin: the `acceptance` config section, its JSON Schema entry, the browser host policy and the preflight check."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner' / 'bin'


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, BIN / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cfg = _load('autorunner_cfg_b', 'autocfg.py')
safe = _load('autorunner_safe_b', 'autosafe.py')
browser = _load('autorunner_browser_t', 'autobrowser.py')
SCHEMA = json.loads((BIN.parent / 'config.schema.json').read_text(encoding='utf-8'))
from tests.auto_runner.test_config import OLD_ONLY, V2_FULL, _schema_ok  # noqa: E402

FULL = {'acceptance': {'browser': {'enabled': True, 'base_url': 'http://localhost:5000', 'command': 'npx playwright test',
                                   'headless': True, 'exploratory': False,
                                   'accounts': {'method': 'seed_command', 'seed_command': 'python seed.py'}}}}
BAD = [({'acceptance': 'x'}, '"acceptance" must be an object'),
       ({'acceptance': {'browser': []}}, '"acceptance.browser" must be an object'),
       ({'acceptance': {'browser': {'enabled': 'yes'}}}, '"acceptance.browser.enabled" must be true/false'),
       ({'acceptance': {'browser': {'base_url': 5}}}, '"acceptance.browser.base_url" must be text'),
       ({'acceptance': {'browser': {'headless': 1}}}, '"acceptance.browser.headless"'),
       ({'acceptance': {'browser': {'accounts': {'method': 'magic'}}}}, '"acceptance.browser.accounts.method" must be one of'),
       ({'acceptance': {'browser': {'accounts': {'seed_command': ['x']}}}}, '"acceptance.browser.accounts.seed_command"')]
UNKNOWN = [({'acceptance': {'api': {}}}, 'acceptance.api'),
           ({'acceptance': {'browser': {'password': 'x'}}}, 'acceptance.browser.password'),
           ({'acceptance': {'browser': {'accounts': {'users': []}}}}, 'acceptance.browser.accounts.users')]


def _write(project, data):
    (project / '.claude').mkdir(exist_ok=True)
    (project / '.claude' / 'auto.json').write_text(json.dumps(data), encoding='utf-8')


def test_acceptance_section_loads_and_is_a_private_copy(tmp_path):
    _write(tmp_path, {**OLD_ONLY, **FULL})
    c = cfg.load(tmp_path)
    assert c['acceptance'] == FULL['acceptance'] and cfg.section(c, 'acceptance') == FULL['acceptance']
    cfg.section(c, 'acceptance')['browser']['accounts']['method'] = 'signup'      # deep copy: the config is untouched
    assert c['acceptance']['browser']['accounts']['method'] == 'seed_command'
    _write(tmp_path, {'acceptance': {'browser': {'enabled': False}}})
    assert cfg.section(cfg.load(tmp_path), 'acceptance') == {'browser': {'enabled': False}}


@pytest.mark.parametrize('data,needle', BAD)
def test_wrong_types_name_the_full_key(tmp_path, data, needle):
    _write(tmp_path, data)
    with pytest.raises(cfg.ConfigError) as e:
        cfg.load(tmp_path)
    assert needle in str(e.value)


@pytest.mark.parametrize('data,needle', UNKNOWN)
def test_unknown_nested_keys_name_the_full_key(tmp_path, data, needle):
    _write(tmp_path, data)
    with pytest.raises(cfg.ConfigError, match='unknown key "' + needle.replace('.', r'\.') + '"'):
        cfg.load(tmp_path)


def test_old_key_configs_return_old_values(tmp_path):
    _write(tmp_path, OLD_ONLY)
    assert cfg.load(tmp_path) == OLD_ONLY and 'acceptance' not in cfg.load(tmp_path)
    _write(tmp_path, {**OLD_ONLY, **V2_FULL})
    c = cfg.load(tmp_path)
    assert {k: c[k] for k in OLD_ONLY} == OLD_ONLY and all(c[k] == v for k, v in V2_FULL.items())
    assert cfg.section(c, 'acceptance') == {}


def test_schema_accepts_valid_and_rejects_invalid(tmp_path):
    for good in [FULL, {**OLD_ONLY, **V2_FULL, **FULL}, {'acceptance': {}}, {'acceptance': {'browser': {'accounts': {'method': 'signup'}}}}]:
        _write(tmp_path, good)
        cfg.load(tmp_path)
        assert _schema_ok(SCHEMA, good), good
    for bad, _ in BAD + UNKNOWN:
        assert not _schema_ok(SCHEMA, bad), bad


URLS = [
    ('http://localhost', True), ('http://localhost:5000/path', True), ('https://127.0.0.1:8443', True),
    ('http://[::1]:3000', True), ('http://app.localhost', True), ('http://shop.test', True), ('http://shop.test.', True),
    ('http://app.staging.example.com', True), ('https://qa.example.org', True), ('http://dev.acme.io:8080', True),
    ('http://preview.site.net', True), ('http://sandbox.pay.example', True), ('http://LOCALHOST:5000', True),
    ('https://example.com', False), ('http://localhost.evil.com', False), ('http://user@localhost', False),
    ('http://user:pw@localhost:5000', False), ('ftp://localhost', False), ('file:///etc/passwd', False),
    ('javascript:alert(1)', False), ('localhost:5000', False), ('http://', False), ('', False), (None, False),
    ('http://evil.dev', False), ('http://staging-example.com', False), ('http://127.0.0.1.evil.com', False),
    ('http://10.0.0.5', False), ('https://test', False), ('http://localhost:99999', False),
]


@pytest.mark.parametrize('url,allowed', URLS)
def test_base_url_policy(url, allowed):
    ok, reason = browser.check_base_url(url)
    assert ok is allowed and reason


def _repo(path):
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(path)], check=True)
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=t', '-c', 'user.email=t@t.test', 'commit', '-q', '--allow-empty', '-m', 'x'], check=True)
    (path / '.claude').mkdir()
    (path / '.claude' / 'settings.json').write_text(json.dumps({'permissions': {'deny': safe.required_rules('main')}}), encoding='utf-8')
    return path


def _cfg(url, enabled=True):
    b = {'enabled': enabled}
    if url is not None:
        b['base_url'] = url
    return {'test_command': 'pytest', 'check_only': False, 'acceptance': {'browser': b}}


def test_preflight_refuses_a_refused_url_naming_the_key(tmp_path):
    p = _repo(tmp_path)
    problems = safe.preflight(p, _cfg('https://example.com'), 'main', doctor=lambda pr: [])
    assert len(problems) == 1 and '"acceptance.browser.base_url"' in problems[0] and 'example.com' in problems[0]


def test_preflight_passes_allowed_unset_or_disabled(tmp_path):
    p = _repo(tmp_path)
    for c in (_cfg('http://localhost:5000'), _cfg('http://app.staging.example.com'), _cfg(None), _cfg(''),
              _cfg('https://example.com', enabled=False), {'test_command': 'pytest', 'check_only': False}):
        assert safe.preflight(p, c, 'main', doctor=lambda pr: []) == [], c
