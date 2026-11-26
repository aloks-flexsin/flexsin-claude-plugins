"""Version control on every merge into main: the `version` check (.github/scripts/version_check.py), the `release`
workflow that tags a new version, and the repository's own version files agreeing. No git or network: readers are fakes."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('version_check', ROOT / '.github' / 'scripts' / 'version_check.py')
vc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vc)


def files(plugin='0.24.0', market='0.24.0', changelog='0.24.0'):
    texts = {vc.PLUGIN: json.dumps({'name': 'auto-runner', 'version': plugin}),
             vc.MARKETPLACE: json.dumps({'plugins': [{'name': 'auto-runner', 'version': market}]}),
             vc.CHANGELOG: f'# Changelog\n\n## [Unreleased]\n\n## [{changelog}] - 2026-11-26\n\n## [0.1.0] - 2026-01-01\n'}
    return texts.__getitem__


def no_base(path):
    return None


def test_the_repository_itself_says_one_version():
    read = lambda p: (ROOT / p).read_text(encoding='utf-8')                       # noqa: E731
    assert vc.problems(read, [], [], no_base) == []


def test_a_version_that_differs_names_every_file():
    [p] = vc.problems(files(market='0.23.0'), [], [], no_base)
    assert 'marketplace.json says 0.23.0' in p and 'autochange.py release --apply' in p


def test_a_plugin_change_needs_a_changelog_entry():
    [p] = vc.problems(files(), ['auto-runner/bin/auto_run.py'], [], no_base)
    assert 'gained no entry' in p and 'auto-runner/bin/auto_run.py' in p
    assert vc.problems(files(), ['auto-runner/bin/auto_run.py'], ['+- Runs push as they go.'], no_base) == []
    assert vc.problems(files(), ['auto-runner/bin/auto_run.py'], [], no_base, no_changelog=True) == []


def test_docs_tests_and_other_files_need_no_entry():
    changed = ['auto-runner/README.md', 'auto-runner/docs/central-guide.md', 'tests/auto_runner/test_x.py',
               'docs/plans/p.md', '.github/workflows/tests.yml', 'CLAUDE.md']
    assert vc.problems(files(), changed, [], no_base) == []


def test_the_version_never_goes_back():
    base = lambda p: json.dumps({'version': '0.25.0'}) if p == vc.PLUGIN else None    # noqa: E731
    [p] = vc.problems(files(), [], [], base)
    assert p == 'the version goes back from 0.25.0 to 0.24.0'


def test_the_workflows_run_the_check_and_tag_new_versions():
    version = (ROOT / '.github' / 'workflows' / 'version.yml').read_text(encoding='utf-8')
    assert 'pull_request:' in version and 'version_check.py --base' in version and 'no-changelog' in version
    assert 'permissions:\n  contents: read' in version
    release = (ROOT / '.github' / 'workflows' / 'release.yml').read_text(encoding='utf-8')
    assert 'push:\n    branches: [main]' in release and 'auto-runner/.claude-plugin/plugin.json' in release
    assert 'contents: write' in release and 'tag="auto-runner-v${version}"' in release
    assert 'git push' not in release                                     # it tags and publishes, never pushes commits
