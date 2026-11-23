import pytest


@pytest.fixture(autouse=True)
def _own_claude_config(tmp_path_factory, monkeypatch):
    """Tests never read the real ~/.claude (installed plugin versions, marketplaces): each gets an empty config folder."""
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path_factory.mktemp('claude-config')))


@pytest.fixture(autouse=True)
def _runner_name(monkeypatch):
    """Every run needs AUTO_RUNNER_USER and AUTO_RUNNER_ACCOUNT (branches are <user>/<account>-claude-auto-runner/...);
    tests run as `tester` with account `acct`."""
    monkeypatch.setenv('AUTO_RUNNER_USER', 'tester')
    monkeypatch.setenv('AUTO_RUNNER_ACCOUNT', 'acct')


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch):
    """Publishing is on by default: a test that reaches the real `gh` (network) without passing a fake fails instead."""
    import sys

    def refuse(argv, cwd):
        raise AssertionError(f'a test called the real gh: {argv[:3]}')
    for mod in list(sys.modules.values()):
        if getattr(mod, '__file__', '') and str(mod.__file__).endswith('auto_run.py') and hasattr(mod, 'run_pr'):
            monkeypatch.setattr(mod, 'run_pr', refuse)


@pytest.fixture(autouse=True)
def _no_real_credentials(tmp_path_factory, monkeypatch):
    """Tests never see a real personal token: AUTO_RUNNER_TOKEN is unset and the credentials file points to an empty folder."""
    monkeypatch.delenv('AUTO_RUNNER_TOKEN', raising=False)
    monkeypatch.setenv('AUTO_RUNNER_CREDENTIALS', str(tmp_path_factory.mktemp('credentials') / 'credentials'))
