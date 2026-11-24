from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / '.github' / 'workflows' / 'tests.yml'


def text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_workflow_name_job_and_runner():
    t = text()
    assert t.startswith('name: tests\n')
    assert '\n  tests:\n' in t
    assert 'runs-on: ubuntu-latest' in t


def test_triggers_are_pull_request_and_push_to_main():
    t = text()
    assert 'pull_request:\n    branches: [main]' in t
    assert 'push:\n    branches: [main]' in t


def test_python_version_and_pip_cache():
    t = text()
    assert "python-version: '3.12'" in t
    assert 'cache: pip' in t
    assert 'actions/checkout@v4' in t and 'actions/setup-python@v5' in t


def test_permissions_are_read_only_and_concurrency_cancels():
    t = text()
    assert 'permissions:\n  contents: read' in t
    assert 'cancel-in-progress: true' in t
    assert 'github.ref' in t


def test_installs_both_requirement_files_and_runs_pytest():
    t = text()
    assert '-r requirements-dev.txt' in t
    assert '-r auto-runner/central/requirements.txt' in t
    assert 'python -m pytest -q' in t


def test_no_secrets_and_no_browser_install():
    t = text()
    assert 'secrets.' not in t
    assert 'playwright' not in t
