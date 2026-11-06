"""auto-runner plugin: engineering principles (vertical slices, libraries first) in planning, the queue check and the prompt."""
import json

import pytest

from tests.auto_runner.test_runner import OLD_PROMPT_B7, ar, fake_agent, project, set_cfg  # noqa: F401  (project is a fixture)

q = ar.queue
LONG = 'Build the thing in the app, in enough words to pass the length check that the queue applies here.'


def item(item_id, slice_line=None):
    text = f'- [ ] **{item_id} Title.** {LONG}\n  *Acceptance:* a test.\n'
    return text + (f'  *Slice:* {slice_line}\n' if slice_line else '')


# ---------- the shipped file ----------

def test_the_plugin_ships_both_principles():
    text = ar.autocfg.PRINCIPLES_FILE.read_text(encoding='utf-8')
    assert 'vertical slices' in text.lower() and 'deploy' in text
    assert 'open-source' in text and 'shadcn/ui' in text and 'MIT' in text
    assert len(text) < ar.autocfg.PRINCIPLES_MAX


def test_the_plugin_ships_the_technical_manager_mindset_first():
    text = ar.autocfg.PRINCIPLES_FILE.read_text(encoding='utf-8')
    headings = [line for line in text.splitlines() if line.startswith('## ')]
    assert headings == ['## 1. Think like a senior technical manager', '## 2. Build in vertical slices',
                        '## 3. Use what exists before you build']
    for needle in ('Start from the intent', 'end to end', 'Decide what is yours to decide', 'trade-offs',
                   'Record the decisions'):
        assert needle in text


def test_the_planning_command_asks_for_intent_and_key_decisions():
    text = (ar.HERE.parent / 'commands' / 'auto.md').read_text(encoding='utf-8')
    assert '**Intent**' in text and '**Key decisions**' in text and 'deployment and rollback' in text


def test_the_planning_command_asks_for_slices_and_libraries():
    text = (ar.HERE.parent / 'commands' / 'auto.md').read_text(encoding='utf-8')
    assert 'engineering-principles.md' in text and '*Slice:*' in text and 'Libraries first' in text


# ---------- *Slice:* ----------

@pytest.mark.parametrize('line,want', [('1 Sign up', (1, 'Sign up')), ('2 - Export CSV', (2, 'Export CSV')),
                                       ('3: Search', (3, 'Search')), ('12', (12, ''))])
def test_parse_slice(line, want):
    assert q.parse_slice(item('A1', line)) == want


def test_no_slice_line_is_none_and_a_bad_one_is_an_error():
    assert q.parse_slice(item('A1')) is None
    with pytest.raises(ValueError):
        q.parse_slice(item('A1', 'first'))
    with pytest.raises(ValueError):
        q.parse_slice(item('A1', '0 Nothing'))


def test_validate_requires_slices_only_when_asked():
    assert q.validate('', item('A1'))[1] == []
    [err] = q.validate('', item('A1'), require_slice=True)[1]
    assert 'add a *Slice:* line' in err
    assert q.validate('', item('A1', '1 Sign up') + item('A2', '1 Sign up') + item('A3', '2 Profile'),
                      require_slice=True)[1] == []


def test_slices_must_not_go_backwards():
    [err] = q.validate('', item('A1', '2 Profile') + item('A2', '1 Sign up'), require_slice=True)[1]
    assert 'A2: slice 1 comes after slice 2' in err


# ---------- config ----------

def test_defaults_are_on_with_the_plugin_file():
    p = ar.autocfg.principles({})
    assert p == {'enabled': True, 'file': '', 'require_slices': True}
    assert ar.autocfg.principles_path('/x', {}) == ar.autocfg.PRINCIPLES_FILE


def test_off_turns_everything_off():
    cfg = {'principles': {'enabled': False}}
    assert ar.autocfg.principles(cfg)['require_slices'] is False
    assert ar.autocfg.principles_path('/x', cfg) is None


def test_require_slices_can_be_dropped_alone():
    cfg = {'principles': {'require_slices': False}}
    assert ar.autocfg.principles(cfg) == {'enabled': True, 'file': '', 'require_slices': False}


def test_a_project_file_must_be_inside_the_project(project):
    cfg = {**ar.autocfg.load(project), 'principles': {'file': '../elsewhere.md'}}
    [problem] = ar.autosafe.principles_problems(project, cfg)
    assert 'principles.file' in problem
    (project / 'docs').mkdir()
    (project / 'docs' / 'ours.md').write_text('Our way.', encoding='utf-8')
    cfg['principles']['file'] = 'docs/ours.md'
    assert ar.autosafe.principles_problems(project, cfg) == []


# ---------- the prompt ----------

def test_the_prompt_carries_the_principles_and_is_otherwise_unchanged():
    cfg = {'test_command': 'npm test', 'check_only': False, 'queue_file': '.auto/queue.md', 'rules_files': ['RULES.md']}
    text = ar.build_prompt(cfg, '.auto/queue.md', 'B7', principles='Slice it.')
    assert '<principles>\nSlice it.\n</principles>' in text
    assert text.replace(ar.principles_paragraph('Slice it.') + '\n', '') == OLD_PROMPT_B7


def test_principles_text_follows_the_config(project):
    cfg = ar.autocfg.load(project)
    assert 'vertical slices' in ar.principles_text(project, cfg).lower()
    (project / 'ours.md').write_text('Our own way.\n', encoding='utf-8')
    assert ar.principles_text(project, {**cfg, 'principles': {'file': 'ours.md'}}) == 'Our own way.'
    assert ar.principles_text(project, {**cfg, 'principles': {'enabled': False}}) == ''


def test_every_iteration_gets_the_principles(project):
    prompts = []
    agent = fake_agent()

    def claude(cmd, cwd):
        prompts.append(cmd[2])
        return agent(cmd, cwd)
    assert ar.run(project, max_iterations=1, claude=claude) == 0
    assert prompts and '<principles>' in prompts[0] and 'vertical slices' in prompts[0].lower()


def test_a_project_can_turn_them_off(project):
    set_cfg(project, principles={'enabled': False})
    prompts = []
    agent = fake_agent()

    def claude(cmd, cwd):
        prompts.append(cmd[2])
        return agent(cmd, cwd)
    assert ar.run(project, max_iterations=1, claude=claude) == 0
    assert '<principles>' not in prompts[0]


# ---------- enqueue ----------

def test_a_request_without_slices_is_refused_up_front(project, capsys):
    req = project / '.auto' / 'pending' / 'r' / 'items.md'
    req.parent.mkdir(parents=True)
    req.write_text(item('Z1'), encoding='utf-8')
    assert ar.run(project, enqueue='r', dry_run=True, claude=fake_agent()) == 1
    assert 'add a *Slice:* line' in capsys.readouterr().out
    req.write_text(item('Z1', '1 First usable piece'), encoding='utf-8')
    assert ar.run(project, enqueue='r', dry_run=True, claude=fake_agent()) == 0


def test_require_slices_false_takes_the_old_requests(project):
    set_cfg(project, principles={'require_slices': False})
    req = project / '.auto' / 'pending' / 'r' / 'items.md'
    req.parent.mkdir(parents=True)
    req.write_text(item('Z1'), encoding='utf-8')
    assert ar.run(project, enqueue='r', dry_run=True, claude=fake_agent()) == 0


def test_the_schema_knows_the_section():
    schema = json.loads((ar.HERE.parent / 'config.schema.json').read_text(encoding='utf-8'))
    assert set(schema['properties']['principles']['properties']) == {'enabled', 'file', 'require_slices'}
