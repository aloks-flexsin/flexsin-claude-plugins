"""auto-runner plugin: /auto-runner:update (bin/autoupdate.py). The `claude` command line is faked; nothing is downloaded."""
import io
import json
import subprocess
from pathlib import Path

import pytest

from tests.auto_runner.test_runner import ar

up = ar._load('autorunner_update', 'autoupdate.py')

CHANGELOG = """# Changelog

## [Unreleased]

## [0.7.0] - 2026-11-05

### Added
- The update command.

## [0.6.1] - 2026-11-04

### Fixed
- A small fix.

## [0.6.0] - 2026-11-03

### Added
- Engineering principles.
"""


def write_plugin(folder, version):
    p = Path(folder) / '.claude-plugin' / 'plugin.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({'name': 'auto-runner', 'version': version}), encoding='utf-8')


@pytest.fixture
def setup(tmp_path):
    """An installed copy at 0.6.0 and a Claude config dir whose marketplace copy has 0.7.0 and the changelog."""
    installed = tmp_path / 'cache' / 'auto-runner' / '0.6.0'
    write_plugin(installed, '0.6.0')
    market = tmp_path / 'marketplaces' / up.MARKETPLACE
    write_plugin(market / 'auto-runner', '0.7.0')
    (market / 'CHANGELOG.md').write_text(CHANGELOG, encoding='utf-8')
    cfg = tmp_path / 'claude'
    (cfg / 'plugins').mkdir(parents=True)
    (cfg / 'plugins' / 'known_marketplaces.json').write_text(
        json.dumps({up.MARKETPLACE: {'installLocation': str(market)}}), encoding='utf-8')
    return {'installed': installed, 'market': market, 'env': {'CLAUDE_CONFIG_DIR': str(cfg)}}


class FakeClaude:
    """Records every call; answers like `claude ... --json`."""

    def __init__(self, replies=None, code=0):
        self.calls, self.replies, self.code = [], replies or {}, code

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        assert cmd[-1] == '--json' and kw.get('timeout')
        key = ' '.join(cmd[1:4])
        reply = self.replies.get(key, {'outcome': 'ok'})
        return subprocess.CompletedProcess(cmd, self.code, 'Refreshing...\n' + json.dumps(reply) + '\n', '')


def which(name):
    return '/usr/bin/claude' if name == 'claude' else None


def test_check_refreshes_the_marketplace_and_lists_the_changes_in_between(setup):
    fake = FakeClaude()
    r = up.check(run=fake, which=which, env=setup['env'], plugin_dir=setup['installed'])
    assert fake.calls == [['/usr/bin/claude', 'plugin', 'marketplace', 'update', up.MARKETPLACE, '--json']]
    assert (r['installed'], r['available'], r['newer']) == ('0.6.0', '0.7.0', True)
    assert '## [0.7.0]' in r['changes'] and '## [0.6.1]' in r['changes'] and 'A small fix.' in r['changes']
    assert '0.6.0]' not in r['changes'] and 'Unreleased' not in r['changes']


def test_check_says_up_to_date_and_lists_nothing(setup):
    write_plugin(setup['market'] / 'auto-runner', '0.6.0')
    r = up.check(run=FakeClaude(), which=which, env=setup['env'], plugin_dir=setup['installed'])
    assert r['newer'] is False and r['changes'] == ''
    out = io.StringIO()
    assert up.main(['check'], out=out, run=FakeClaude(), which=which, env=setup['env'],
                   plugin_dir=setup['installed']) == 0
    assert 'up to date' in out.getvalue()


def test_check_never_installs(setup):
    fake = FakeClaude()
    up.main(['check'], out=io.StringIO(), run=fake, which=which, env=setup['env'], plugin_dir=setup['installed'])
    assert not any(c[1:3] == ['plugin', 'update'] for c in fake.calls)


def test_apply_runs_the_plugin_update_and_reports_both_versions():
    fake = FakeClaude({'plugin update auto-runner@flexsin-claude-plugins':
                       {'outcome': 'ok', 'updateOutcome': 'updated', 'oldVersion': '0.6.0', 'newVersion': '0.7.0'}})
    out = io.StringIO()
    assert up.main(['apply'], out=out, run=fake, which=which) == 0
    assert fake.calls == [['/usr/bin/claude', 'plugin', 'update', up.PLUGIN_ID, '--json']]
    assert '0.6.0 -> 0.7.0' in out.getvalue() and 'Restart Claude' in out.getvalue()


def test_apply_when_already_current():
    fake = FakeClaude({'plugin update auto-runner@flexsin-claude-plugins':
                       {'outcome': 'ok', 'updateOutcome': 'up_to_date', 'oldVersion': '0.7.0', 'newVersion': '0.7.0'}})
    out = io.StringIO()
    assert up.main(['apply'], out=out, run=fake, which=which) == 0
    assert 'already at the latest version (0.7.0)' in out.getvalue()


def test_without_the_claude_command_it_names_the_commands_to_run_by_hand(setup):
    out = io.StringIO()
    assert up.main(['check'], out=out, run=FakeClaude(), which=lambda n: None, env=setup['env'],
                   plugin_dir=setup['installed']) == 1
    assert 'update not done' in out.getvalue() and 'claude plugin update auto-runner@flexsin-claude-plugins' in out.getvalue()


def test_a_failed_refresh_is_reported_with_claudes_message(setup):
    fake = FakeClaude({'plugin marketplace update': {'outcome': 'error', 'message': 'network unreachable'}}, code=1)
    out = io.StringIO()
    assert up.main(['check'], out=out, run=fake, which=which, env=setup['env'], plugin_dir=setup['installed']) == 1
    assert 'network unreachable' in out.getvalue()


def test_an_unknown_marketplace_says_how_to_add_it(setup, tmp_path):
    env = {'CLAUDE_CONFIG_DIR': str(tmp_path / 'empty')}
    with pytest.raises(up.UpdateError, match='marketplace add'):
        up.check(run=FakeClaude(), which=which, env=env, plugin_dir=setup['installed'])


@pytest.mark.parametrize('old,new,expect', [('0.6.0', '0.6.0', ''), ('0.6.1', '0.7.0', '## [0.7.0]'),
                                            ('bad', '0.7.0', '')])
def test_changes_between(old, new, expect):
    got = up.changes_between(CHANGELOG, old, new)
    assert (expect in got) if expect else got == ''


def test_versions_compare_as_numbers_not_text():
    assert up.parse_version('0.10.0') > up.parse_version('0.9.9')


def test_the_installed_version_is_this_plugins_own():
    assert up.installed_version() == json.loads(
        (ar.HERE.parent / '.claude-plugin' / 'plugin.json').read_text(encoding='utf-8'))['version']
