"""auto-runner plugin: the doc map and documentation checks (autodocs.py), on scratch git repos."""
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


docs = _load('autorunner_docs', 'autodocs.py')
cfgmod = _load('autorunner_cfg_docs', 'autocfg.py')

MAP = {'entries': [
    {'name': 'schema', 'paths': ['db/schema.sql'], 'docs': ['docs/data.md'], 'pattern': 'sql_table'},
    {'name': 'routes', 'paths': ['app/**/*.py'], 'docs': ['README.md'], 'pattern': 'route'},
]}


def sh(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def write(root, files):
    for path, text in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')


def commit(root, files, msg='change'):
    write(root, files)
    sh(root, 'add', '-A')
    sh(root, 'commit', '-q', '-m', msg)


@pytest.fixture
def repo(tmp_path):
    sh(tmp_path, 'init', '-q', '-b', 'main')
    sh(tmp_path, 'config', 'user.email', 't@example.com')
    sh(tmp_path, 'config', 'user.name', 'T')
    commit(tmp_path, {
        'docs/doc-map.json': json.dumps(MAP),
        'db/schema.sql': 'CREATE TABLE IF NOT EXISTS company (id INTEGER);\n',
        'docs/data.md': '# Data\n\nThe `company` table holds companies. See [readme](../README.md).\n',
        'README.md': '# App\n\nRoutes: /home\n',
        'app/web/views.py': "@bp.route('/home')\ndef home():\n    return 'x'\n",
    }, 'init')
    sh(tmp_path, 'checkout', '-q', '-b', 'auto/x')
    return tmp_path


def run(repo):
    return docs.check(repo, 'main', 'HEAD')


def test_code_change_without_its_doc_fails_naming_both(repo):
    commit(repo, {'app/web/views.py': "@bp.route('/home')\ndef home():\n    return 'y'\n"})
    out = run(repo)
    assert len(out['failures']) == 1
    assert 'app/web/views.py' in out['failures'][0] and 'README.md' in out['failures'][0]


def test_same_change_with_doc_updated_passes(repo):
    commit(repo, {'app/web/views.py': "@bp.route('/home')\ndef home():\n    return 'y'\n",
                  'README.md': '# App\n\nRoutes: /home (now says y)\n'})
    assert run(repo)['failures'] == []


def test_change_split_over_two_commits_counts_as_one_range(repo):
    commit(repo, {'app/web/views.py': "@bp.route('/home')\ndef home():\n    return 'y'\n"})
    commit(repo, {'README.md': '# App\n\nRoutes: /home (y)\n'})
    assert run(repo)['failures'] == []


def test_dead_relative_link_fails_naming_the_link(repo):
    commit(repo, {'docs/data.md': '# Data\n\n`company` table. See [guide](guide/missing.md) and [ok](../README.md).\n'})
    out = run(repo)
    assert len(out['failures']) == 1
    assert '"guide/missing.md"' in out['failures'][0] and 'docs/data.md' in out['failures'][0]


def test_links_that_are_not_relative_files_are_ignored(repo):
    commit(repo, {'docs/data.md': ('# Data\n\n[web](https://example.com/x.md) [mail](mailto:a@example.com) '
                                   '[top](#data) [dir](../app/) [file](../db/schema.sql#L1) [root](/README.md)\n\n'
                                   '```\n[code](not/here.md)\n```\nand `[inline](nope.md)`\n')})
    assert run(repo)['failures'] == []


def test_link_outside_the_project_is_dead(repo):
    commit(repo, {'docs/data.md': '# Data\n\n[up](../../elsewhere.md)\n'})
    out = run(repo)
    assert len(out['failures']) == 1 and '../../elsewhere.md' in out['failures'][0]


def test_new_table_missing_from_docs_fails_then_passes_once_documented(repo):
    sql = 'CREATE TABLE IF NOT EXISTS company (id INTEGER);\nCREATE TABLE IF NOT EXISTS invoice (id INTEGER);\n'
    commit(repo, {'db/schema.sql': sql, 'docs/data.md': '# Data\n\nThe `company` table, now with more words.\n'})
    out = run(repo)
    assert len(out['failures']) == 1
    assert '"invoice"' in out['failures'][0] and 'table' in out['failures'][0]
    commit(repo, {'docs/data.md': '# Data\n\nThe `company` and `invoice` tables.\n'})
    assert run(repo)['failures'] == []


def test_new_route_missing_from_docs_fails_naming_it(repo):
    commit(repo, {'app/web/views.py': "@bp.route('/home')\ndef home():\n    return 'x'\n\n"
                                      "@bp.get('/reports/<id>')\ndef rep(id):\n    return id\n",
                  'README.md': '# App\n\nRoutes: /home and more\n'})
    out = run(repo)
    assert len(out['failures']) == 1 and '"/reports/<id>"' in out['failures'][0]
    commit(repo, {'README.md': '# App\n\nRoutes: /home, /reports/<id>\n'})
    assert run(repo)['failures'] == []


def test_symbols_present_before_the_range_are_ignored(repo):
    # The same table line is touched (reformatted) and the README never names "company": it existed before the range.
    commit(repo, {'db/schema.sql': 'CREATE TABLE IF NOT EXISTS company (id INTEGER, name TEXT);\n',
                  'docs/data.md': '# Data\n\nCompanies live here.\n'})
    assert run(repo)['failures'] == []


def test_symbol_moved_between_mapped_files_is_not_new(repo):
    commit(repo, {'app/web/views.py': "def home():\n    return 'x'\n",
                  'app/web/more.py': "@bp.route('/home')\ndef home():\n    return 'x'\n",
                  'README.md': '# App\n\nRoutes moved.\n'})
    assert run(repo)['failures'] == []


def test_custom_regex_pattern(repo):
    m = {'entries': [{'name': 'settings', 'paths': ['app/settings.py'], 'docs': ['docs/env.md'],
                      'pattern': r'\b(APP_[A-Z_]+)\b'}]}
    commit(repo, {'docs/doc-map.json': json.dumps(m), 'docs/env.md': '# Env\n'})
    commit(repo, {'app/settings.py': "LIMIT = env('APP_LIMIT')\n", 'docs/env.md': '# Env\n\nNothing yet.\n'})
    out = run(repo)
    assert len(out['failures']) == 1 and '"APP_LIMIT"' in out['failures'][0]


def test_own_doc_never_triggers_its_entry(repo):
    m = {'entries': [{'name': 'all docs', 'paths': ['docs/**'], 'docs': ['docs/data.md']}]}
    commit(repo, {'docs/doc-map.json': json.dumps(m)})
    sh(repo, 'branch', '-f', 'main', 'HEAD')
    commit(repo, {'docs/data.md': '# Data\n\nThe `company` table, edited.\n'})
    assert run(repo)['failures'] == []


def test_no_map_passes_with_a_note(repo):
    sh(repo, 'rm', '-q', 'docs/doc-map.json')
    sh(repo, 'commit', '-q', '-m', 'no map')
    commit(repo, {'app/web/views.py': "@bp.route('/new')\ndef n():\n    return 'x'\n"})
    out = run(repo)
    assert out['failures'] == [] and out['map'] is None
    assert out['notes'] and 'No doc map' in out['notes'][0]


def test_map_path_comes_from_config(repo):
    (repo / '.claude').mkdir()
    (repo / '.claude' / 'auto.json').write_text(json.dumps({'docs': {'map': 'nowhere.json'}}), encoding='utf-8')
    commit(repo, {'app/web/views.py': "@bp.route('/new')\ndef n():\n    return 'x'\n"})
    out = run(repo)
    assert out['failures'] == [] and 'nowhere.json' in out['notes'][0]


def test_check_writes_nothing(repo):
    commit(repo, {'app/web/views.py': "@bp.route('/new')\ndef n():\n    return 'x'\n"})
    before = sh(repo, 'status', '--porcelain', '--ignored'), sh(repo, 'for-each-ref'), sh(repo, 'rev-parse', 'HEAD')
    run(repo)
    assert docs.main(['check', '--project', str(repo), '--base', 'main']) == 1
    after = sh(repo, 'status', '--porcelain', '--ignored'), sh(repo, 'for-each-ref'), sh(repo, 'rev-parse', 'HEAD')
    assert before == after


def test_cli_exit_codes_and_plain_output(repo, capsys):
    assert docs.main(['check', '--project', str(repo), '--base', 'main']) == 0
    assert 'docs ok' in capsys.readouterr().out
    commit(repo, {'app/web/views.py': "@bp.route('/home')\ndef home():\n    return 'z'\n"})
    assert docs.main(['check', '--project', str(repo), '--base', 'main']) == 1
    out = capsys.readouterr().out
    assert out.startswith('FAIL ') and 'README.md' in out and 'Traceback' not in out


@pytest.mark.parametrize('bad, word', [
    ('[]', 'entries'),
    ({'entries': [{'paths': ['a'], 'docs': []}]}, '"docs"'),
    ({'entries': [{'paths': 'a', 'docs': ['b']}]}, '"paths"'),
    ({'entries': [{'paths': ['a'], 'docs': ['b'], 'colour': 1}]}, '"colour"'),
    ({'entries': [{'paths': ['a'], 'docs': ['b'], 'pattern': '('}]}, '"pattern"'),
    ({'entries': [{'paths': ['a'], 'docs': ['b'], 'stamp': 'yes'}]}, '"stamp"'),
    ({'entries': [], 'extra': 1}, '"extra"'),
])
def test_broken_map_is_an_error_naming_the_key(bad, word):
    with pytest.raises(docs.DocMapError) as e:
        docs.parse_map(json.dumps(bad) if not isinstance(bad, str) else bad)
    assert word in str(e.value)


def test_broken_map_exits_2(repo, capsys):
    commit(repo, {'docs/doc-map.json': '{"entries": [{"paths": ["a"]}]}'})
    assert docs.main(['check', '--project', str(repo), '--base', 'main']) == 2
    assert 'doc map error' in capsys.readouterr().out


@pytest.mark.parametrize('glob, path, hit', [
    ('app/*.py', 'app/a.py', True), ('app/*.py', 'app/x/a.py', False),
    ('app/**/*.py', 'app/a.py', True), ('app/**/*.py', 'app/x/y/a.py', True),
    ('docs/**', 'docs/a/b.md', True), ('db/schema.sql', 'db/schema.sql', True), ('db/schema.sql', 'db/schema.sqlx', False),
    ('a?.md', 'ab.md', True), ('a?.md', 'a/.md', False),
])
def test_glob(glob, path, hit):
    assert bool(docs.glob_regex(glob).match(path)) is hit


def test_config_docs_map_default_and_override(tmp_path):
    assert cfgmod.docs_map(cfgmod.load(tmp_path)) == 'docs/doc-map.json'
    (tmp_path / '.claude').mkdir()
    (tmp_path / '.claude' / 'auto.json').write_text(json.dumps({'docs': {'map': 'x/map.json'}}), encoding='utf-8')
    assert cfgmod.docs_map(cfgmod.load(tmp_path)) == 'x/map.json'
    (tmp_path / '.claude' / 'auto.json').write_text(json.dumps({'docs': {'map': 3}}), encoding='utf-8')
    with pytest.raises(cfgmod.ConfigError, match='docs.map'):
        cfgmod.load(tmp_path)


# --- Q11: stale docs for `stamp: true` entries (autodocs.gate / stale_docs) ---

st = _load('autorunner_stale_docs', 'autostale.py')
STAMP_MAP = {'entries': [
    {'name': 'runner', 'paths': ['bin/*.py'], 'docs': ['docs/runner.md'], 'stamp': True},
    {'name': 'schema', 'paths': ['db/schema.sql'], 'docs': ['docs/data.md']},
]}


def _ff(repo):
    sh(repo, 'checkout', '-q', 'auto/x')
    sh(repo, 'merge', '-q', '--ff-only', 'main')


@pytest.fixture
def stamped(repo):
    sh(repo, 'checkout', '-q', 'main')
    write(repo, {'docs/doc-map.json': json.dumps(STAMP_MAP), 'bin/run.py': 'def run():\n    return 1\n',
                 'docs/runner.md': '# Runner\n\nrun() returns 1.\n'})
    assert st.revalidate(repo, 'docs/runner.md', ['bin/run.py'])
    commit(repo, {}, 'stamp the runner doc')
    _ff(repo)
    return repo


def gate(repo):
    return docs.gate(repo, 'main', 'HEAD')


def _doc_saying(repo, n):
    return (repo / 'docs/runner.md').read_text(encoding='utf-8').replace('returns 1', f'returns {n}')


def test_watched_change_makes_the_stamped_doc_stale_until_revalidated(stamped):
    # The doc text changes too, so the Q10 rule alone passes; the stamp still says nobody checked it against the code.
    commit(stamped, {'bin/run.py': 'def run():\n    return 2\n', 'docs/runner.md': _doc_saying(stamped, 2)})
    out = gate(stamped)
    assert len(out['stale']) == 1 and out['failures'] == out['stale']
    assert 'docs/runner.md is stale' in out['stale'][0] and 'bin/run.py' in out['stale'][0]
    assert 'autostale.py revalidate docs/runner.md --from bin/run.py' in out['stale'][0]
    assert docs.check(stamped, 'main', 'HEAD')['failures'] == []          # the Q10 checks alone do not see it
    assert st.revalidate(stamped, 'docs/runner.md')
    commit(stamped, {}, 'revalidate')
    assert gate(stamped)['failures'] == []


def test_revalidating_without_changing_the_doc_text_is_enough(stamped):
    commit(stamped, {'bin/run.py': 'def run():\n    return 1  # same\n'})
    assert st.revalidate(stamped, 'docs/runner.md')                        # the header changes, so the Q10 rule passes too
    commit(stamped, {}, 'revalidate')
    assert gate(stamped)['failures'] == []


def test_new_watched_file_must_be_listed_in_built_from(stamped):
    write(stamped, {'bin/extra.py': 'X = 1\n'})
    assert not st.revalidate(stamped, 'docs/runner.md')                    # its own sources did not move: nothing to do
    commit(stamped, {'docs/runner.md': _doc_saying(stamped, 1) + 'extra.py holds X.\n'})
    out = gate(stamped)
    assert len(out['stale']) == 1 and 'does not list bin/extra.py' in out['stale'][0]
    assert '--from bin/extra.py,bin/run.py' in out['stale'][0]
    st.revalidate(stamped, 'docs/runner.md', ['bin/extra.py', 'bin/run.py'])
    commit(stamped, {}, 'revalidate with the new source')
    assert gate(stamped)['failures'] == []


def test_doc_marked_stale_or_without_header_is_blocking(stamped):
    header, body = st.read(stamped, 'docs/runner.md')
    st.write(stamped, 'docs/runner.md', {**header, 'status': 'stale'}, body)
    commit(stamped, {'bin/run.py': 'def run():\n    return 3\n'})
    assert 'is marked stale' in gate(stamped)['stale'][0]
    commit(stamped, {'docs/runner.md': '# Runner\n\nno header now\n'})
    assert 'has no artifact header' in gate(stamped)['stale'][0]


def test_artifact_built_from_the_doc_goes_stale_through_it(stamped):
    sh(stamped, 'checkout', '-q', 'main')
    write(stamped, {'docs/guide.md': '# Guide\n\nSee the runner doc.\n'})
    st.revalidate(stamped, 'docs/guide.md', ['docs/runner.md'])
    commit(stamped, {}, 'guide built from the runner doc')
    _ff(stamped)
    write(stamped, {'bin/run.py': 'def run():\n    return 2\n', 'docs/runner.md': _doc_saying(stamped, 2)})
    st.revalidate(stamped, 'docs/runner.md')                               # its body changed: the guide's source moved
    commit(stamped, {}, 'code and doc')
    out = gate(stamped)
    assert len(out['stale']) == 1 and out['stale'][0].startswith('docs/guide.md is stale')
    st.revalidate(stamped, 'docs/guide.md')
    commit(stamped, {}, 'revalidate the guide')
    assert gate(stamped)['failures'] == []


def test_unstamped_doc_is_only_checked_by_the_q10_rules(stamped):
    commit(stamped, {'db/schema.sql': 'CREATE TABLE IF NOT EXISTS company (id INTEGER, x TEXT);\n'})
    out = gate(stamped)
    assert out['stale'] == [] and len(out['failures']) == 1 and 'docs/data.md' in out['failures'][0]
    commit(stamped, {'docs/data.md': '# Data\n\nThe `company` table, with x.\n'})
    assert gate(stamped)['failures'] == []


def test_no_change_to_watched_files_means_no_stale_check(stamped):
    commit(stamped, {'notes.txt': 'unrelated\n'})
    assert gate(stamped)['stale'] == []


def test_gate_without_a_map_passes_with_the_note(repo):
    sh(repo, 'rm', '-q', 'docs/doc-map.json')
    sh(repo, 'commit', '-q', '-m', 'no map')
    out = gate(repo)
    assert out['failures'] == [] and out['stale'] == [] and out['map'] is None


def test_gate_writes_nothing(stamped):
    commit(stamped, {'bin/run.py': 'def run():\n    return 9\n'})
    before = sh(stamped, 'status', '--porcelain', '--ignored'), sh(stamped, 'rev-parse', 'HEAD')
    assert gate(stamped)['stale']
    assert docs.main(['check', '--project', str(stamped), '--base', 'main']) == 1
    assert before == (sh(stamped, 'status', '--porcelain', '--ignored'), sh(stamped, 'rev-parse', 'HEAD'))


def test_config_docs_check_switch(tmp_path):
    assert cfgmod.docs_check(cfgmod.load(tmp_path)) is True
    (tmp_path / '.claude').mkdir()
    (tmp_path / '.claude' / 'auto.json').write_text(json.dumps({'docs': {'check': False}}), encoding='utf-8')
    assert cfgmod.docs_check(cfgmod.load(tmp_path)) is False
    (tmp_path / '.claude' / 'auto.json').write_text(json.dumps({'docs': {'check': 'no'}}), encoding='utf-8')
    with pytest.raises(cfgmod.ConfigError, match='docs.check'):
        cfgmod.load(tmp_path)


REPO = BIN.parent.parent


def test_repo_doc_map_is_valid_and_points_at_real_files():
    entries = docs.parse_map((REPO / 'docs' / 'doc-map.json').read_text(encoding='utf-8'))
    files = [p.relative_to(REPO).as_posix() for p in (REPO / 'auto-runner').rglob('*') if p.is_file()]
    assert entries
    for e in entries:
        assert all((REPO / d).is_file() for d in e['docs']), e['name']
        assert any(rx.match(f) for rx in e['paths'] for f in files), e['name']


def test_repo_doc_map_gates_a_plugin_change_until_the_readme_changes(tmp_path):
    sh(tmp_path, 'init', '-q', '-b', 'main')
    sh(tmp_path, 'config', 'user.email', 't@example.com')
    sh(tmp_path, 'config', 'user.name', 'T')
    commit(tmp_path, {
        'docs/doc-map.json': (REPO / 'docs' / 'doc-map.json').read_text(encoding='utf-8'),
        'auto-runner/README.md': '# auto-runner\n',
        'auto-runner/bin/auto_run.py': 'X = 1\n',
    }, 'init')
    sh(tmp_path, 'checkout', '-q', '-b', 'auto/x')
    commit(tmp_path, {'auto-runner/bin/auto_run.py': 'X = 2\n'})
    out = docs.check(tmp_path, 'main', 'HEAD')
    assert len(out['failures']) == 1 and 'auto-runner/README.md' in out['failures'][0]
    commit(tmp_path, {'auto-runner/README.md': '# auto-runner\n\nX is now 2.\n'})
    assert docs.check(tmp_path, 'main', 'HEAD')['failures'] == []
