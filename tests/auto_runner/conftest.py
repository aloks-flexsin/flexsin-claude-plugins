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
    # A test that passes its own env dict (without AUTO_RUNNER_CREDENTIALS) falls back to Path.home(): without this it
    # would read the real ~/.config/auto-runner/credentials of the machine running the suite. The bin modules are loaded
    # per test file (not always in sys.modules), so the home folder itself is pointed at an empty one.
    from pathlib import Path
    home = tmp_path_factory.mktemp('home')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))


@pytest.fixture(autouse=True)
def _no_sandbox(monkeypatch):
    """Runs behave the same on every machine (a Linux CI host with bubblewrap would otherwise sandbox its agents): the
    sandbox counts as unavailable unless a test says otherwise (tests/auto_runner/test_sandbox.py)."""
    import sys
    for mod in list(sys.modules.values()):
        if getattr(mod, '__file__', '') and str(mod.__file__).endswith('autosafe.py') and hasattr(mod, 'sandbox_available'):
            monkeypatch.setattr(mod, 'sandbox_available', lambda platform=None, which=None: (False, 'not in tests'))
