"""auto-runner plugin: the changelog writer (autochange.py) and its step in the runner, on scratch git repos with a fake claude."""
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


ch = _load('autorunner_change_t', 'autochange.py')
ar = _load('autorunner_run_ch', 'auto_run.py')

PY = f'"{sys.executable}"'
GREEN = f'{PY} -c "import sys; sys.exit(0)"'
SENTENCE = 'You can now export the portfolio as a spreadsheet from the records page.'

QUEUE = """# Queue

- [ ] **A1 First thing.** Build the first thing in a small file, with enough words to pass any length rule we might have.
  *Acceptance:* the file exists.
  *Kind:* feature

- [ ] **A2 Second thing.** Build the second thing in a small file, with enough words to pass any length rule we might have.
  *Acceptance:* the second file exists.
  *Kind:* fix

## Needs a person
- nothing
"""


def sh(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


# ---- sentence rules -------------------------------------------------------------------------------------------------------

def test_a_plain_sentence_is_valid():
    assert ch.validate_sentence(SENTENCE + '\n') == []


@pytest.mark.parametrize('text, reason', [
    (None, 'no sentence'),
    ('Too short', '10 to 200'),
    ('x' * 201, '10 to 200'),
    ('First line of the change.\nSecond line of it.', 'one line'),
    ('The export works now. It is also faster than before.', 'one sentence'),
    ('Item A1 adds a spreadsheet export to the records page.', 'item ids'),
    ('See https://example.com for the new spreadsheet export.', 'link'),
    ('See www.example.com for the new spreadsheet export.', 'link'),
])
def test_invalid_sentences_say_why(text, reason):
    errors = ch.validate_sentence(text)
    assert errors and any(reason in e for e in errors), errors


def test_known_ids_limit_the_id_rule_and_secrets_are_never_repeated():
    assert ch.validate_sentence('Songs in MP3 now play in the preview pane.', item_ids=['A1', 'A2']) == []
    assert ch.validate_sentence('Songs in MP3 now play in the preview pane.')            # shaped like an id without the list
    errors = ch.validate_sentence('The sign-in now accepts the password hunter2xyz quickly.', secrets={'P': 'hunter2xyz'})
    assert errors == ['it contains a generated test credential'] and 'hunter2xyz' not in ' '.join(errors)


# ---- the file -------------------------------------------------------------------------------------------------------------

def test_entries_go_under_their_kind_heading_in_order(tmp_path):
    f = tmp_path / 'CHANGELOG.md'
    assert ch.append_entry(f, 'fix', 'A2', 'Second', 'bbb2222', 'it works', 'pass', 'The search no longer drops the last result.')
    assert ch.append_entry(f, 'feature', 'A1', 'First', 'aaa1111', 'a file', 'pass', SENTENCE)
    assert ch.append_entry(f, 'breaking', 'A3', 'Third', 'ccc3333', '', 'pass', 'Old sign-in links no longer open the app.')
    assert ch.append_entry(f, 'feature', 'A4', 'Fourth', 'ddd4444', '', 'pass', 'Prospects can now be shared with a team.')
    text = f.read_text(encoding='utf-8')
    assert text.startswith('# Changelog') and '## [Unreleased]' in text
    heads = [ln for ln in text.splitlines() if ln.startswith('### ')]
    assert heads == ['### Breaking', '### Added', '### Fixed']
    added = text.split('### Added')[1].split('###')[0]
    assert added.index('_(A1 First') < added.index('_(A4 Fourth')
    assert f'- {SENTENCE} _(A1 First; commit aaa1111; acceptance: a file; tests: pass)_' in text
    assert 'A2 Second' in text.split('### Fixed')[1]


def test_same_item_is_never_added_twice(tmp_path):
    f = tmp_path / 'CHANGELOG.md'
    assert ch.append_entry(f, 'feature', 'A1', 'First', 'aaa1111', '', 'pass', SENTENCE) is True
    before = f.read_bytes()
    assert ch.append_entry(f, 'fix', 'A1', 'First', 'zzz9999', '', 'pass', 'A different sentence for the same item.') is False
    assert f.read_bytes() == before


def test_existing_releases_and_unknown_headings_are_kept(tmp_path):
    f = tmp_path / 'CHANGELOG.md'
    f.write_text('# Changelog\n\nIntro.\n\n## [1.0.0] - 2026-01-01\n\n### Added\n- Old thing.\n', encoding='utf-8')
    ch.append_entry(f, 'fix', 'A1', 'First', 'aaa1111', '', 'pass', SENTENCE)
    text = f.read_text(encoding='utf-8')
    assert text.index('## [Unreleased]') < text.index('## [1.0.0]') and '- Old thing.' in text
    assert text.split('## [1.0.0]')[0].count('### Fixed') == 1
    f.write_text(text.replace('## [Unreleased]', '## [Unreleased]\n\n### Security\n- Kept as written.'), encoding='utf-8')
    ch.append_entry(f, 'feature', 'A2', 'Second', 'bbb2222', '', 'pass', 'Prospects can now be shared with a team.')
    text = f.read_text(encoding='utf-8')
    assert '### Security\n- Kept as written.' in text and text.index('### Added') < text.index('### Security')


def test_commit_entry_commits_only_the_changelog_and_refuses_the_base_branch(tmp_path):
    repo = tmp_path / 'r'
    repo.mkdir()
    sh(repo, 'init', '-q', '-b', 'main')
    sh(repo, 'config', 'user.email', 't@example.com')
    sh(repo, 'config', 'user.name', 'T')
    (repo / 'a.txt').write_text('a', encoding='utf-8')
    sh(repo, 'add', 'a.txt')
    sh(repo, 'commit', '-q', '-m', 'init')
    ch.append_entry(repo / 'CHANGELOG.md', 'feature', 'A1', 'First', 'aaa1111', '', 'pass', SENTENCE)
    (repo / 'other.txt').write_text('not mine', encoding='utf-8')
    with pytest.raises(ch.ChangelogError, match='main'):
        ch.commit_entry(repo, 'CHANGELOG.md', 'A1', forbid_branch='main')
    sh(repo, 'checkout', '-q', '-b', 'auto/x')
    sha = ch.commit_entry(repo, 'CHANGELOG.md', 'A1', 'First', 'Trailer: yes', forbid_branch='main')
    assert sha and sh(repo, 'show', '--name-only', '--format=%B', 'HEAD').split() == \
        ['Changelog:', 'A1', 'First', 'Trailer:', 'yes', 'CHANGELOG.md']
    assert ch.commit_entry(repo, 'CHANGELOG.md', 'A1', forbid_branch='main') is None    # nothing new: no commit
    assert sh(repo, 'rev-parse', '--abbrev-ref', 'main') == 'main' and sh(repo, 'log', '--oneline', 'main').count('\n') == 0


# ---- the runner ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path):
    p = tmp_path / 'proj'
    p.mkdir()
    sh(p, 'init', '-q', '-b', 'main')
    sh(p, 'config', 'user.email', 't@example.com')
    sh(p, 'config', 'user.name', 'T')
    (p / 'README.md').write_text('x', encoding='utf-8')
    (p / '.claude').mkdir()
    (p / '.claude' / 'auto.json').write_text(json.dumps({'test_command': GREEN, 'changelog': {'enabled': True}}),
                                             encoding='utf-8')
    (p / '.claude' / 'settings.json').write_text(
        json.dumps({'permissions': {'deny': ar.autosafe.required_rules('main', changelog=True)}}), encoding='utf-8')
    (p / '.auto').mkdir()
    (p / '.auto' / 'queue.md').write_text(QUEUE, encoding='utf-8')
    sh(p, 'add', 'README.md', '.claude/auto.json', '.claude/settings.json')
    sh(p, 'commit', '-q', '-m', 'init')
    return p


def worktree(p):
    return p.parent / (p.name + '-auto')


def agent(sentences):
    """Ticks the first open item with one commit and writes sentences[item] (None: no sentence file)."""
    def claude(cmd, cwd):
        cwd = Path(cwd)
        q = cwd / '.auto' / 'queue.md'
        lines = q.read_text(encoding='utf-8').splitlines()
        for n, line in enumerate(lines):
            if line.startswith('- [ ] **'):
                item = line.split('**')[1].split()[0]
                lines[n] = line.replace('- [ ]', '- [x]', 1)
                break
        else:
            return 0, 'nothing'
        q.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        (cwd / f'{item}.txt').write_text('made', encoding='utf-8')
        sh(cwd, 'add', f'{item}.txt')
        sh(cwd, '-c', 'user.email=t@example.com', '-c', 'user.name=T', 'commit', '-q', '-m', item)
        if sentences.get(item) is not None:
            d = cwd / '.auto' / 'changes'
            d.mkdir(parents=True, exist_ok=True)
            (d / f'{item}.txt').write_text(sentences[item], encoding='utf-8')
        return 0, 'done'
    return claude


def runs(p):
    return [json.loads(x) for x in (p / '.auto' / 'runs.jsonl').read_text(encoding='utf-8').splitlines()]


def events(p, type_):
    return [e for e in map(json.loads, (p / '.auto' / 'events.jsonl').read_text(encoding='utf-8').splitlines())
            if e['type'] == type_]


def log_lines(wt):
    return sh(wt, 'log', '--format=%s').splitlines()


def test_green_item_gets_one_entry_and_one_extra_commit(project):
    assert ar.run(project, max_iterations=1, claude=agent({'A1': SENTENCE})) == 0
    wt = worktree(project)
    assert log_lines(wt)[:2] == ['Changelog: A1 First thing', 'A1']
    assert sh(wt, 'show', '--name-only', '--format=', 'HEAD').split() == ['CHANGELOG.md']
    text = (wt / 'CHANGELOG.md').read_text(encoding='utf-8')
    assert text.count('_(A1 ') == 1 and SENTENCE in text.split('### Added')[1]
    assert 'acceptance: the file exists; tests: pass' in text
    r = runs(project)[0]
    assert r['result'] == 'done' and r['changelog'] and r['commit'] != r['changelog']
    assert f'commit {r["commit"]}' in text
    assert [e['detail']['result'] for e in events(project, 'changelog')] == ['added']
    assert sh(project, 'log', '--format=%s', 'main') == 'init'                            # the base branch is untouched


def test_entries_land_under_each_items_kind(project):
    assert ar.run(project, max_iterations=2, claude=agent({'A1': SENTENCE, 'A2': 'The search no longer drops the last result.'})) == 0
    text = (worktree(project) / 'CHANGELOG.md').read_text(encoding='utf-8')
    assert '_(A1 ' in text.split('### Added')[1].split('###')[0]
    assert '_(A2 Second thing; ' in text.split('### Fixed')[1]


def test_rerun_on_unchanged_input_changes_nothing(project):
    ar.run(project, max_iterations=1, claude=agent({'A1': SENTENCE}))
    wt = worktree(project)
    head, before = sh(wt, 'rev-parse', 'HEAD'), (wt / 'CHANGELOG.md').read_bytes()
    cfg = ar.autocfg.load(project)
    again = ar.changelog_step(wt, cfg, (wt / '.auto' / 'queue.md').read_text(encoding='utf-8'), 'A1', 'x', 'pass', base='main')
    assert again == {'ok': True, 'added': False, 'commit': None, 'errors': []}
    assert sh(wt, 'rev-parse', 'HEAD') == head and (wt / 'CHANGELOG.md').read_bytes() == before


def test_missing_sentence_stops_the_run_naming_the_item(project, capsys):
    calls = []
    inner = agent({})
    code = ar.run(project, max_iterations=3, claude=lambda c, w: calls.append(1) or inner(c, w))
    assert code == 1 and len(calls) == 1
    assert runs(project)[0]['result'] == 'changelog'
    out = capsys.readouterr().out
    assert 'item A1' in out and 'no sentence was written' in out
    st = {i: (s, d) for i, s, d in ar.queue.statuses((project / '.auto' / 'queue.md').read_text(encoding='utf-8'))}
    assert st['A1'][0] == 'blocked' and st['A1'][1].startswith('changelog: .auto/changes/A1.txt')
    assert not (worktree(project) / 'CHANGELOG.md').exists()
    assert [e['detail']['result'] for e in events(project, 'changelog')] == ['refused']
    assert [e['detail']['reason'] for e in events(project, 'run_end')] == ['changelog']


def test_invalid_sentence_is_rejected_with_the_reason(project, capsys):
    assert ar.run(project, max_iterations=1, claude=agent({'A1': 'A1 is done. See https://example.com now.'})) == 1
    out = capsys.readouterr().out
    assert 'item ids (A1)' in out and 'one sentence' in out and 'link' in out
    assert runs(project)[0]['result'] == 'changelog'
    assert log_lines(worktree(project))[0] == 'A1'                                          # no changelog commit


def test_with_the_changelog_off_nothing_changes(project):
    cfg = json.loads((project / '.claude' / 'auto.json').read_text(encoding='utf-8'))
    cfg['changelog'] = {'enabled': False}
    (project / '.claude' / 'auto.json').write_text(json.dumps(cfg), encoding='utf-8')
    sh(project, 'add', '.claude/auto.json')
    sh(project, 'commit', '-q', '-m', 'off')
    assert ar.run(project, max_iterations=1, claude=agent({})) == 0
    wt = worktree(project)
    assert log_lines(wt)[0] == 'A1' and not (wt / 'CHANGELOG.md').exists()
    assert 'changelog' not in runs(project)[0] and not events(project, 'changelog')
