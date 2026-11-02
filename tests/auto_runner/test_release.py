"""auto-runner plugin: the release proposal (autochange.py release) and the git tag deny rule, on scratch git repos."""
import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner' / 'bin'


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, BIN / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ch = _load('autorunner_change_rel', 'autochange.py')
safe = _load('autorunner_safe_rel', 'autosafe.py')
init = _load('autorunner_init_rel', 'auto_init.py')

DAY = date(2026, 10, 30)


def changelog(entries, released=''):
    """entries: [(kind, item, sentence)] added through the real writer, then an optional released section below."""
    text = ''
    for kind, item, sentence in entries:
        text = ch.add_entry(text, kind, ch.format_entry(item, 'Title', 'abc1234', 'it works', 'pass', sentence))
    return text + (('\n' + released) if released else '')


def sh(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    sh(tmp_path, 'init', '-q', '-b', 'main')
    sh(tmp_path, 'config', 'user.email', 't@example.com')
    sh(tmp_path, 'config', 'user.name', 'T')
    (tmp_path / 'README.md').write_text('x', encoding='utf-8')
    sh(tmp_path, 'add', 'README.md')
    sh(tmp_path, 'commit', '-q', '-m', 'init')
    return tmp_path


@pytest.mark.parametrize('kinds, expected', [
    (['breaking', 'feature', 'fix', 'internal'], 'major'),
    (['feature', 'fix'], 'minor'),
    (['fix', 'internal'], 'patch'),
    (['fix'], 'patch'),
    (['feature'], 'minor'),
    (['breaking'], 'major'),
    (['internal'], None),
    ([], None),
])
def test_bump_follows_the_strongest_kind(kinds, expected):
    text = changelog([(k, f'A{n}', 'Something users can now see in the app.') for n, k in enumerate(kinds, 1)])
    assert ch.propose_bump(ch.unreleased_entries(text)) == expected


def test_bump_arithmetic():
    assert ch.bump((1, 2, 3), 'major') == (2, 0, 0)
    assert ch.bump((1, 2, 3), 'minor') == (1, 3, 0)
    assert ch.bump((1, 2, 3), 'patch') == (1, 2, 4)


def test_released_sections_are_not_unreleased_entries():
    old = '## [1.0.0] - 2026-01-01\n\n### Added\n- An older feature that shipped long ago. _(A9 Old)_\n'
    text = changelog([('fix', 'A1', 'A small fix to the export button.')], old)
    entries = ch.unreleased_entries(text)
    assert list(entries) == ['fix'] and len(entries['fix']) == 1
    assert entries['fix'][0].startswith('- A small fix to the export button.')


def test_version_from_heading_highest_wins(tmp_path):
    old = '## [1.4.0] - 2026-02-01\n\n### Added\n- x _(A2 X)_\n\n## [1.10.2] - 2026-03-01\n\n## [0.9.0]\n'
    (tmp_path / 'VERSION').write_text('7.0.0\n', encoding='utf-8')
    assert ch.last_version(old, tmp_path / 'VERSION') == ((1, 10, 2), 'changelog heading')


def test_version_from_file_when_no_heading(tmp_path):
    (tmp_path / 'VERSION').write_text('v2.3.4\n', encoding='utf-8')
    assert ch.last_version(changelog([]), tmp_path / 'VERSION') == ((2, 3, 4), 'VERSION file')
    assert ch.last_version('', tmp_path / 'missing') == ((0, 0, 0), 'none (0.0.0)')
    (tmp_path / 'VERSION').write_text('soon\n', encoding='utf-8')
    with pytest.raises(ch.ChangelogError, match='VERSION'):
        ch.last_version('', tmp_path / 'VERSION')


def test_release_writes_notes_and_commands(repo):
    old = '## [1.2.0] - 2026-09-01\n\n### Added\n- Old thing. _(A0 Old)_\n'
    text = changelog([('feature', 'A1', 'You can now export the portfolio as a spreadsheet.'),
                      ('fix', 'A2', 'The search box keeps your words after an error.'),
                      ('internal', 'A3', 'Tests now run on a scratch database only.')], old)
    (repo / 'CHANGELOG.md').write_text(text, encoding='utf-8')
    res = ch.release(repo, today=DAY)
    assert (res['bump'], res['previous'], res['version']) == ('minor', '1.2.0', '1.3.0')
    out = res['path'].read_text(encoding='utf-8')
    assert res['path'] == repo / '.auto' / 'release' / '1.3.0.md'
    assert '## [1.3.0] - 2026-10-30' in out
    assert '### Added\n- You can now export the portfolio as a spreadsheet.' in out
    assert '### Fixed\n- The search box keeps your words after an error.' in out
    assert 'abc1234' not in out and 'Old thing' not in out
    for cmd in ('git add -- CHANGELOG.md', 'git commit -m "Release 1.3.0"', 'git tag -a v1.3.0 -m "Release 1.3.0"',
                'git push origin v1.3.0'):
        assert cmd in out
    assert 'nothing has been tagged or pushed' in out
    assert (repo / 'CHANGELOG.md').read_text(encoding='utf-8') == text  # the changelog itself is untouched


def test_release_with_version_file_names_it(repo):
    (repo / 'VERSION').write_text('0.4.1\n', encoding='utf-8')
    (repo / 'CHANGELOG.md').write_text(changelog([('breaking', 'A1', 'Old saved searches are no longer read.')]),
                                       encoding='utf-8')
    res = ch.release(repo, today=DAY)
    assert res['version'] == '1.0.0'
    out = res['path'].read_text(encoding='utf-8')
    assert 'Write 1.0.0 into VERSION.' in out and 'git add -- CHANGELOG.md VERSION' in out


def test_internal_only_gives_no_release_and_writes_nothing(repo):
    (repo / 'CHANGELOG.md').write_text(changelog([('internal', 'A1', 'Tests now run on a scratch database only.')]),
                                       encoding='utf-8')
    res = ch.release(repo, today=DAY)
    assert res['bump'] is None and res['version'] is None and res['path'] is None
    assert not (repo / '.auto').exists()


def test_release_changes_no_git_ref_or_remote(repo):
    (repo / 'CHANGELOG.md').write_text(changelog([('fix', 'A1', 'The search box keeps your words after an error.')]),
                                       encoding='utf-8')
    before = (sh(repo, 'for-each-ref'), sh(repo, 'remote', '-v'), sh(repo, 'rev-parse', 'HEAD'))
    assert ch.main(['release', '--project', str(repo)]) == 0
    assert (repo / '.auto' / 'release' / '0.0.1.md').exists()
    assert (sh(repo, 'for-each-ref'), sh(repo, 'remote', '-v'), sh(repo, 'rev-parse', 'HEAD')) == before
    assert sh(repo, 'tag') == ''


def test_cli_uses_the_configured_changelog_file(repo, capsys):
    (repo / '.claude').mkdir()
    (repo / '.claude' / 'auto.json').write_text(json.dumps({'test_command': 'x', 'changelog': {'enabled': True,
                                                                                               'file': 'docs/CHANGES.md'}}),
                                                encoding='utf-8')
    (repo / 'docs').mkdir()
    (repo / 'docs' / 'CHANGES.md').write_text(changelog([('feature', 'A1', 'You can now export the portfolio.')]),
                                              encoding='utf-8')
    assert ch.main(['release', '--project', str(repo)]) == 0
    assert 'proposed 0.1.0 (minor bump from 0.0.0)' in capsys.readouterr().out
    assert 'git add -- docs/CHANGES.md' in (repo / '.auto' / 'release' / '0.1.0.md').read_text(encoding='utf-8')


# ---- the git tag deny rule ------------------------------------------------------------------------------------------------

def test_tag_rule_required_only_with_changelog():
    assert not any('git tag' in r for r in safe.required_rules('main'))
    on = safe.required_rules('main', changelog=True)
    assert on[-2:] == ['Bash(git tag:*)', 'Bash(git tag)']
    assert on[:-2] == safe.required_rules('main')


def test_preflight_demands_tag_rule_only_when_changelog_enabled(repo):
    (repo / '.claude').mkdir()
    (repo / '.claude' / 'settings.json').write_text(json.dumps({'permissions': {'deny': safe.required_rules('main')}}),
                                                    encoding='utf-8')
    base = {'test_command': 'x', 'check_only': False}
    assert safe.preflight(repo, base, 'main') == []
    assert safe.preflight(repo, {**base, 'changelog': {'enabled': False}}, 'main') == []
    problems = safe.preflight(repo, {**base, 'changelog': {'enabled': True}}, 'main')
    assert len(problems) == 1 and 'git tag' in problems[0]
    (repo / '.claude' / 'settings.json').write_text(
        json.dumps({'permissions': {'deny': safe.required_rules('main', changelog=True)}}), encoding='utf-8')
    assert safe.preflight(repo, {**base, 'changelog': {'enabled': True}}, 'main') == []


def test_init_appends_tag_rule_only_when_changelog_enabled(repo):
    plan = init.make_plan(repo, {'local': True})
    assert not any('git tag' in r for r in plan['rules'])
    (repo / '.claude').mkdir()
    (repo / '.claude' / 'auto.json').write_text(json.dumps({'test_command': 'x', 'changelog': {'enabled': True}}),
                                                encoding='utf-8')
    plan = init.make_plan(repo, {'local': True})
    assert plan['rules'][-2:] == ['Bash(git tag:*)', 'Bash(git tag)']
    init.apply(plan)
    deny = json.loads((repo / '.claude' / 'settings.json').read_text(encoding='utf-8'))['permissions']['deny']
    assert 'Bash(git tag:*)' in deny and 'Bash(git tag)' in deny
    assert init.make_plan(repo, {'local': True})['rules'] == []
