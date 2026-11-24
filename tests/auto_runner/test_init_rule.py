"""auto-runner plugin: init writes the "Work goes through the auto-runner" rule for projects with the central service.
No token is set, so the central check never sends a request."""
import json

from tests.auto_runner.test_init import ai, make_repo

CENTRAL = {'url': 'https://central.test', 'project': 'acme-web'}
ORIGINAL = b'# Project rules\r\n\r\n- Keep it green.\r\n- No trailing newline here'


def plan(p, central=CENTRAL):
    return ai.make_plan(p, dict(central) if central else central, env={})


def with_claude_md(tmp_path, content=ORIGINAL):
    p = make_repo(tmp_path)
    (p / 'CLAUDE.md').write_bytes(content)
    return p


def test_preview_names_the_section_and_writes_nothing(tmp_path):
    p = with_claude_md(tmp_path)
    pl = plan(p)
    text = '\n'.join(ai.describe(pl))
    assert 'CLAUDE.md: add the section "Work goes through the auto-runner"' in text
    assert 'autotasks.py propose' in text
    assert (p / 'CLAUDE.md').read_bytes() == ORIGINAL


def test_main_preview_prints_it(tmp_path, capsys, monkeypatch):
    p = with_claude_md(tmp_path)
    for k in list(__import__('os').environ):
        if k.startswith('AUTO_RUNNER_TOKEN'):
            monkeypatch.delenv(k)
    assert ai.main(['--project', str(p), '--central-url', CENTRAL['url'], '--central-project', CENTRAL['project']]) == 0
    assert 'Work goes through the auto-runner' in capsys.readouterr().out
    assert (p / 'CLAUDE.md').read_bytes() == ORIGINAL


def test_apply_appends_the_rule_and_records_it(tmp_path):
    p = with_claude_md(tmp_path)
    changed = ai.apply(plan(p))
    assert any('routing rule' in c for c in changed)
    data = (p / 'CLAUDE.md').read_bytes()
    assert data.startswith(ORIGINAL)
    added = data[len(ORIGINAL):].decode('utf-8')
    assert ai.ROUTING_START in added and ai.ROUTING_END in added
    assert '## Work goes through the auto-runner' in added and 'autotasks.py propose' in added
    man = json.loads((p / '.auto' / 'init-manifest.json').read_text(encoding='utf-8'))
    assert man['routing_rule'] == {'file': 'CLAUDE.md', 'added': added}


def test_apply_is_idempotent(tmp_path):
    p = with_claude_md(tmp_path)
    ai.apply(plan(p))
    once = (p / 'CLAUDE.md').read_bytes()
    again = plan(p)
    assert again['routing_how'] is None
    assert 'CLAUDE.md already has the auto-runner routing rule.' in again['notes']
    assert ai.apply(again) == []
    assert (p / 'CLAUDE.md').read_bytes() == once
    assert once.count(ai.ROUTING_START.encode()) == 1


def test_existing_enabled_central_section_also_gets_the_rule(tmp_path):
    p = with_claude_md(tmp_path, b'# Rules\n')
    (p / '.claude').mkdir()
    (p / '.claude' / 'auto.json').write_text(json.dumps({'central': dict(CENTRAL, enabled=True)}), encoding='utf-8')
    ai.apply(plan(p, None))
    assert ai.ROUTING_START in (p / 'CLAUDE.md').read_text(encoding='utf-8')


def test_local_mode_and_disabled_central_get_no_rule(tmp_path):
    p = with_claude_md(tmp_path)
    pl = plan(p, {'local': True})
    assert pl['routing_how'] is None
    assert 'Work goes through' not in '\n'.join(ai.describe(pl))
    ai.apply(pl)
    assert (p / 'CLAUDE.md').read_bytes() == ORIGINAL

    (tmp_path / 'q').mkdir()
    q = with_claude_md(tmp_path / 'q', b'# Rules\n')
    (q / '.claude').mkdir()
    (q / '.claude' / 'auto.json').write_text(json.dumps({'central': dict(CENTRAL, enabled=False)}), encoding='utf-8')
    ai.apply(plan(q, None))
    assert (q / 'CLAUDE.md').read_bytes() == b'# Rules\n'


def test_remove_takes_out_only_the_section(tmp_path):
    p = with_claude_md(tmp_path)
    ai.apply(plan(p))
    (p / 'CLAUDE.md').write_bytes((p / 'CLAUDE.md').read_bytes() + b'\r\nAdded later by a person.\r\n')
    actions, _ = ai.remove_plan(p)
    assert ('routing', 'CLAUDE.md') in actions
    done = ai.remove(p)
    assert any('routing rule' in d for d in done)
    assert (p / 'CLAUDE.md').read_bytes() == ORIGINAL + b'\r\nAdded later by a person.\r\n'


def test_remove_restores_the_file_byte_for_byte(tmp_path):
    for content in (ORIGINAL, b'# Rules\n', b'# Rules\n\n\n', b''):
        root = tmp_path / str(len(content))
        root.mkdir()
        p = with_claude_md(root, content)
        ai.apply(plan(p))
        assert ai.ROUTING_START in (p / 'CLAUDE.md').read_text(encoding='utf-8')
        ai.remove(p)
        assert (p / 'CLAUDE.md').read_bytes() == content


def test_remove_keeps_an_edited_section(tmp_path):
    p = with_claude_md(tmp_path)
    ai.apply(plan(p))
    with open(p / 'CLAUDE.md', encoding='utf-8', newline='') as f:   # Path.read_text(newline=) needs Python 3.13
        edited = f.read().replace('Do not build', 'Please do not build')
    (p / 'CLAUDE.md').write_bytes(edited.encode('utf-8'))
    actions, _ = ai.remove_plan(p)
    assert ('keep', 'the routing rule in CLAUDE.md') in actions
    ai.remove(p)
    assert (p / 'CLAUDE.md').read_bytes() == edited.encode('utf-8')


def test_existing_auto_rules_md_gets_the_rule_when_there_is_no_claude_md(tmp_path):
    p = make_repo(tmp_path)
    (p / 'AUTO_RULES.md').write_bytes(b'# Our rules\n')
    pl = plan(p)
    assert (pl['routing_file'], pl['routing_how']) == ('AUTO_RULES.md', 'append')
    ai.apply(pl)
    assert ai.ROUTING_START in (p / 'AUTO_RULES.md').read_text(encoding='utf-8')
    ai.remove(p)
    assert (p / 'AUTO_RULES.md').read_bytes() == b'# Our rules\n'


def test_with_rules_creates_auto_rules_md_with_the_rule(tmp_path):
    p = make_repo(tmp_path)
    pl = plan(p)
    assert pl['routing_how'] == 'with-rules'
    assert 'AUTO_RULES.md: with --with-rules' in '\n'.join(ai.describe(pl))
    ai.apply(pl, with_rules=True)
    text = (p / 'AUTO_RULES.md').read_text(encoding='utf-8')
    assert text.startswith(ai.RULES_TEMPLATE) and 'autotasks.py propose' in text
    ai.remove(p)
    assert not (p / 'AUTO_RULES.md').exists()


def test_without_with_rules_and_no_rules_file_nothing_is_written(tmp_path):
    p = make_repo(tmp_path)
    ai.apply(plan(p))
    assert not (p / 'AUTO_RULES.md').exists() and not (p / 'CLAUDE.md').exists()
