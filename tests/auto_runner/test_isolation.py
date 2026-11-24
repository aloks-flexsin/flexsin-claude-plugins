"""Tests never read a signed-in machine's real credentials, even when a test passes its own `env` dict (no
AUTO_RUNNER_CREDENTIALS in it): the conftest points the home folder at an empty temporary folder."""
import importlib.util
import tempfile
from pathlib import Path


def _autosafe():
    spec = importlib.util.spec_from_file_location(
        'autosafe_isolation', Path(__file__).resolve().parents[2] / 'auto-runner' / 'bin' / 'autosafe.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_home_folder_is_temporary():
    assert Path.home().resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())


def test_a_bare_env_never_finds_real_credentials():
    autosafe = _autosafe()
    path = autosafe.credentials_path({})
    assert path.is_relative_to(Path.home()) and not path.exists() and autosafe.read_credentials({}) == ''
