"""auto-runner plugin: join reads a project's repository URL from the central service, clones it (a fake git clone here)
and shows init's preview there. The central service is the real app through its Flask test client."""
import subprocess
from types import SimpleNamespace

import pytest

from tests.auto_runner.test_init import _mod, sh
from tests.auto_runner.test_init_central import BASE, ENV, REPO, SLUG, central  # noqa: F401  (central is a fixture)

aj = _mod('autorunner_join_t', 'auto_join.py')


class FakeGit:
    """Records the clone command; makes a git repository at the folder with `origin` set, as a real clone would."""

    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, argv):
        self.calls.append(argv)
        if self.code == 0:
            dest = argv[-1]
            subprocess.run(['git', 'init', '-q', '-b', 'main', dest], check=True, capture_output=True)
            sh(dest, 'remote', 'add', 'origin', argv[-2])
        return SimpleNamespace(returncode=self.code)


def join(tmp_path, central, *args, env=None, git=None, monkeypatch=None):
    git = git or FakeGit()
    monkeypatch.chdir(tmp_path)
    code = aj.main([SLUG, '--central-url', BASE, *args], env=env if env is not None else {ENV: central.token},
                   send=central.send, run=git)
    return code, git


def test_preview_reads_the_repo_url_and_clones_nothing(tmp_path, central, monkeypatch, capsys):
    code, git = join(tmp_path, central, monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert code == 0 and git.calls == []
    assert f'git clone -- {REPO}' in out and str(tmp_path / 'acme-web') in out and 'Preview only' in out
    assert central.token not in out
    assert not (tmp_path / 'acme-web').exists()


def test_apply_clones_with_git_and_shows_inits_preview(tmp_path, central, monkeypatch, capsys):
    code, git = join(tmp_path, central, '--apply', monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert code == 0
    assert git.calls == [['git', 'clone', '--', REPO, str(tmp_path / 'acme-web')]]
    assert 'origin is its repository' in out                       # init's check ran in the new folder
    assert '.claude/auto.json: create' in out and '--central-project acme-web' in out and '--apply' in out
    assert not (tmp_path / 'acme-web' / '.claude').exists()           # init itself was only previewed


def test_dest_chooses_the_folder(tmp_path, central, monkeypatch):
    dest = tmp_path / 'work' / 'mine'
    code, git = join(tmp_path, central, '--dest', str(dest), '--apply', monkeypatch=monkeypatch)
    assert code == 0 and git.calls[0][-1] == str(dest.resolve())


def test_an_existing_folder_is_refused(tmp_path, central, monkeypatch, capsys):
    (tmp_path / 'acme-web').mkdir()
    code, git = join(tmp_path, central, '--apply', monkeypatch=monkeypatch)
    assert code == 1 and git.calls == [] and 'already exists' in capsys.readouterr().out


def test_without_the_token_join_says_how_to_get_one(tmp_path, central, monkeypatch, capsys):
    code, git = join(tmp_path, central, '--apply', env={}, monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert code == 1 and git.calls == [] and central.calls == []
    assert f'the token is not in {ENV}' in out and 'Read-Host' in out


def test_a_token_of_another_project_is_refused(tmp_path, central, monkeypatch, capsys):
    code, git = join(tmp_path, central, '--apply', env={ENV: central.other_token}, monkeypatch=monkeypatch)
    assert code == 1 and git.calls == [] and 'belongs to project "other"' in capsys.readouterr().out


def test_a_failed_clone_stops_there(tmp_path, central, monkeypatch, capsys):
    code, git = join(tmp_path, central, '--apply', git=FakeGit(code=128), monkeypatch=monkeypatch)
    assert code == 128 and 'git clone failed' in capsys.readouterr().out


def test_a_project_without_a_repo_url_is_refused(tmp_path, central, monkeypatch, capsys):
    central.conn.execute('UPDATE project SET repo_url = NULL WHERE id = ?', (central.project['id'],))
    central.conn.commit()
    code, git = join(tmp_path, central, '--apply', monkeypatch=monkeypatch)
    assert code == 1 and git.calls == [] and 'no repository URL' in capsys.readouterr().out


@pytest.mark.parametrize('url,ok', [
    ('https://github.com/e/r.git', True), ('ssh://git@github.com/e/r.git', True), ('git@github.com:e/r.git', True),
    ('http://github.com/e/r.git', False), ('git://github.com/e/r.git', False), ('-uhttps://github.com/e/r', False),
    ('ext::sh -c x', False), ('/srv/r.git', False), ('file:///srv/e/r.git', False),
])
def test_only_https_and_ssh_urls_are_cloned(url, ok):
    assert aj.clonable(url) is ok


@pytest.mark.parametrize('args,needle', [
    (['--token-env', 'art_abc'], 'not a token'),
    (['--central-url', 'http://central.example.com'], '--central-url'),
])
def test_bad_options_are_refused(tmp_path, central, monkeypatch, capsys, args, needle):
    monkeypatch.chdir(tmp_path)
    argv = [SLUG, '--central-url', BASE] + args
    assert aj.main(argv, env={ENV: central.token}, send=central.send, run=FakeGit()) == 1
    assert needle in capsys.readouterr().out
