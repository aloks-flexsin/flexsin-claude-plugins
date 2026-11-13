import pytest


@pytest.fixture(autouse=True)
def _own_claude_config(tmp_path_factory, monkeypatch):
    """Tests never read the real ~/.claude (installed plugin versions, marketplaces): each gets an empty config folder."""
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path_factory.mktemp('claude-config')))
