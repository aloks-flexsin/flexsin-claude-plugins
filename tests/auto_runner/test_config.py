"""auto-runner plugin: config defaults and overrides, base-branch detection, queue status lines."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner' / 'bin'


def _load(name, file):
    # By path, under a private name: bin/queue.py must never shadow the standard library's `queue`.
    spec = importlib.util.spec_from_file_location(name, BIN / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cfg = _load('autorunner_cfg', 'autocfg.py')
q = _load('autorunner_queue_t', 'queue.py')


def _repo(path, branch):
    subprocess.run(['git', 'init', '-q', '-b', branch, str(path)], check=True)
    return path


def _write_cfg(project, data):
    (project / '.claude').mkdir(exist_ok=True)
    (project / '.claude' / 'auto.json').write_text(json.dumps(data), encoding='utf-8')


def test_defaults_when_no_file(tmp_path):
    c = cfg.load(tmp_path)
    assert c == cfg.DEFAULTS
    assert c['queue_file'] == '.auto/queue.md' and c['branch_prefix'] == 'auto/' and c['check_only'] is False
    c['rules_files'].append('x')                      # a caller changing the result must not change the defaults
    assert cfg.DEFAULTS['rules_files'] == []


def test_overrides_keep_the_other_defaults(tmp_path):
    _write_cfg(tmp_path, {'test_command': 'pytest -q', 'share_dirs': ['venv'], 'check_only': True})
    c = cfg.load(tmp_path)
    assert c['test_command'] == 'pytest -q' and c['share_dirs'] == ['venv'] and c['check_only'] is True
    assert c['worktree_suffix'] == '-auto' and c['commit_trailer'] == ''


def test_bad_config_names_the_key(tmp_path):
    for data, needle in [({'nope': 1}, 'nope'), ({'check_only': 'yes'}, 'check_only'), ({'share_dirs': 'venv'}, 'share_dirs'),
                         ({'test_command': 5}, 'test_command'), ([], 'JSON object')]:
        _write_cfg(tmp_path, data)
        with pytest.raises(cfg.ConfigError, match=needle):
            cfg.load(tmp_path)
    (tmp_path / '.claude' / 'auto.json').write_text('{oops', encoding='utf-8')
    with pytest.raises(cfg.ConfigError, match='not valid JSON'):
        cfg.load(tmp_path)


@pytest.mark.parametrize('branch', ['main', 'master', 'trunk', 'develop'])
def test_base_branch_is_the_current_known_branch(tmp_path, branch):
    assert cfg.detect_base_branch(_repo(tmp_path, branch)) == branch


def test_unknown_branch_asks_for_one_answer(tmp_path):
    _repo(tmp_path, 'feature/x')
    with pytest.raises(cfg.BaseBranchUnknown, match='base_branch'):
        cfg.detect_base_branch(tmp_path)


def test_config_wins_over_detection(tmp_path):
    _repo(tmp_path, 'feature/x')
    _write_cfg(tmp_path, {'base_branch': 'release'})
    assert cfg.detect_base_branch(tmp_path) == 'release'


def test_origin_head_wins_over_current_branch(tmp_path):
    _repo(tmp_path, 'feature/x')
    subprocess.run(['git', '-C', str(tmp_path), 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/trunk'], check=True)
    assert cfg.detect_base_branch(tmp_path) == 'trunk'


SCHEMA = json.loads((BIN.parent / 'config.schema.json').read_text(encoding='utf-8'))
OLD_ONLY = {'test_command': 'pytest -q', 'check_only': False, 'queue_file': 'q.md', 'rules_files': ['CLAUDE.md'],
            'share_dirs': ['venv'], 'worktree_suffix': '-x', 'base_branch': 'main', 'branch_prefix': 'run/',
            'commit_trailer': 'T: x', 'env_files': ['.env']}
V2_FULL = {
    'models': {'implementer': 'sonnet', 'planner': 'opus', 'plan_reviewer': 'opus', 'work_reviewer': 'claude-opus-5-5',
               'tester': 'haiku', 'scribe': 'inherit'},
    'lanes': {'protected': ['main', 'release/*'], 'sandbox': ['staging'], 'deploy_command': ''},
    'review': {'model': 'opus', 'max_plan_revisions': 2, 'max_fix_rounds': 2, 'max_budget_usd': 3.5},
    'budgets': {'iteration_usd': 5, 'run_usd': 40.0, 'max_iterations': 13},
    'docs': {'adr_dir': 'docs/decisions', 'required': ['README.md']},
    'central': {'enabled': False, 'url': 'https://central.example', 'project': 'siva'},
    'locks': {'path': '.auto/run.lock', 'stale_after_minutes': 120},
    'safety': {'allow_env_read': True},
}
# One wrong value per nested kind and per section shape.
V2_BAD = [({'models': 'opus'}, '"models" must be an object'), ({'models': {'implementer': 5}}, 'models.implementer'),
          ({'lanes': {'protected': 'main'}}, 'lanes.protected'), ({'lanes': {'sandbox': [1]}}, 'lanes.sandbox'),
          ({'review': {'max_fix_rounds': 1.5}}, 'review.max_fix_rounds'), ({'review': {'max_plan_revisions': True}}, 'review.max_plan_revisions'),
          ({'budgets': {'run_usd': '10'}}, 'budgets.run_usd'), ({'budgets': {'iteration_usd': -1}}, 'budgets.iteration_usd'),
          ({'budgets': {'max_iterations': -2}}, 'budgets.max_iterations'), ({'docs': {'required': 'README.md'}}, 'docs.required'),
          ({'central': {'enabled': 'yes'}}, 'central.enabled'), ({'central': {'url': None}}, 'central.url'),
          ({'locks': {'stale_after_minutes': '60'}}, 'locks.stale_after_minutes'), ({'locks': []}, '"locks" must be an object')]
V2_UNKNOWN = [({'models': {'reviewer': 'opus'}}, 'models.reviewer'), ({'central': {'token': 'x'}}, 'central.token'),
              ({'lanes': {'auto_merge': True}}, 'lanes.auto_merge'), ({'budgets': {'usd': 1}}, 'budgets.usd')]


def _schema_ok(schema, value, root=SCHEMA):
    """Small stdlib validator for the subset config.schema.json uses: $ref, type, properties, additionalProperties, items, minimum."""
    if '$ref' in schema:
        node = root
        for part in schema['$ref'].lstrip('#/').split('/'):
            node = node[part]
        return _schema_ok(node, value, root)
    t = schema.get('type')
    checks = {'object': lambda v: isinstance(v, dict), 'array': lambda v: isinstance(v, list),
              'string': lambda v: isinstance(v, str), 'boolean': lambda v: isinstance(v, bool),
              'integer': lambda v: isinstance(v, int) and not isinstance(v, bool),
              'number': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)}
    if t and not checks[t](value):
        return False
    if 'enum' in schema and value not in schema['enum']:
        return False
    if 'minimum' in schema and value < schema['minimum']:
        return False
    if t == 'array' and 'items' in schema and not all(_schema_ok(schema['items'], x, root) for x in value):
        return False
    if t == 'object':
        props = schema.get('properties', {})
        if schema.get('additionalProperties') is False and set(value) - set(props):
            return False
        return all(_schema_ok(props[k], v, root) for k, v in value.items() if k in props)
    return True


def test_v2_sections_validate_and_load(tmp_path):
    _write_cfg(tmp_path, {**OLD_ONLY, **V2_FULL})
    c = cfg.load(tmp_path)
    for name, data in V2_FULL.items():
        assert c[name] == data and cfg.section(c, name) == data
    assert {k: c[k] for k in OLD_ONLY} == OLD_ONLY
    _write_cfg(tmp_path, {'models': {'implementer': 'sonnet'}, 'budgets': {}})      # every nested key is optional
    c = cfg.load(tmp_path)
    assert cfg.section(c, 'models') == {'implementer': 'sonnet'} and cfg.section(c, 'budgets') == {}
    assert cfg.section(c, 'lanes') == {}


@pytest.mark.parametrize('data,needle', V2_BAD)
def test_v2_wrong_types_name_the_key(tmp_path, data, needle):
    _write_cfg(tmp_path, data)
    with pytest.raises(cfg.ConfigError, match=needle.replace('.', r'\.')):
        cfg.load(tmp_path)


@pytest.mark.parametrize('data,needle', V2_UNKNOWN)
def test_v2_unknown_nested_keys_are_rejected(tmp_path, data, needle):
    _write_cfg(tmp_path, data)
    with pytest.raises(cfg.ConfigError, match='unknown key "' + needle.replace('.', r'\.')):
        cfg.load(tmp_path)


def test_old_only_config_loads_exactly_as_before(tmp_path):
    assert set(cfg.DEFAULTS) == set(OLD_ONLY)
    assert cfg.DEFAULTS == {'test_command': '', 'check_only': False, 'queue_file': '.auto/queue.md', 'rules_files': [],
                            'share_dirs': [], 'worktree_suffix': '-auto', 'base_branch': '', 'branch_prefix': 'auto/',
                            'commit_trailer': '', 'env_files': []}
    _write_cfg(tmp_path, OLD_ONLY)
    assert cfg.load(tmp_path) == OLD_ONLY                     # no v2 section appears unless the file sets it
    assert all(cfg.section(cfg.load(tmp_path), s) == {} for s in cfg.SECTIONS)
    c = cfg.load(tmp_path)
    cfg.section(c, 'docs')['adr_dir'] = 'x'                   # a caller changing a section never changes the config
    assert 'docs' not in c
    with pytest.raises(KeyError):
        cfg.section(c, 'nope')


def test_schema_matches_the_loader():
    props = SCHEMA['properties']
    assert SCHEMA['additionalProperties'] is False
    assert set(props) == set(cfg.DEFAULTS) | set(cfg.SECTIONS)
    def same(node, spec):                                     # nested objects (dict kind) and word lists (tuple kind) too
        assert node['additionalProperties'] is False and set(node['properties']) == set(spec)
        for k, kind in spec.items():
            if isinstance(kind, dict):
                same(node['properties'][k], kind)
            elif isinstance(kind, tuple):
                assert node['properties'][k] == {'type': 'string', 'enum': list(kind)}
            else:
                assert node['properties'][k] == {'$ref': f'#/$defs/{kind}'}
    for name, spec in cfg.SECTIONS.items():
        same(props[name], spec)


def test_schema_accepts_valid_and_rejects_invalid_configs(tmp_path):
    for good in [{}, OLD_ONLY, V2_FULL, {**OLD_ONLY, **V2_FULL}, {'models': {'implementer': 'sonnet'}}]:
        _write_cfg(tmp_path, good)
        cfg.load(tmp_path)                                    # the loader agrees it is valid
        assert _schema_ok(SCHEMA, good), good
    for bad, _ in V2_BAD + V2_UNKNOWN + [({'nope': 1}, ''), ({'check_only': 'yes'}, ''), ({'share_dirs': 'venv'}, '')]:
        assert not _schema_ok(SCHEMA, bad), bad
        _write_cfg(tmp_path, bad)
        with pytest.raises(cfg.ConfigError):                 # and the loader agrees it is not
            cfg.load(tmp_path)


def test_changelog_section_defaults_and_validation(tmp_path):
    c = cfg.load(tmp_path)
    assert 'changelog' not in c and cfg.changelog(c) == {'enabled': False, 'file': 'CHANGELOG.md', 'versioning': 'semver'}
    _write_cfg(tmp_path, {'changelog': {'enabled': True}})
    c = cfg.load(tmp_path)
    assert cfg.section(c, 'changelog') == {'enabled': True}
    assert cfg.changelog(c) == {'enabled': True, 'file': 'CHANGELOG.md', 'versioning': 'semver'}
    good = {'changelog': {'enabled': True, 'file': 'docs/CHANGES.md', 'versioning': 'semver'}}
    _write_cfg(tmp_path, good)
    assert cfg.changelog(cfg.load(tmp_path)) == good['changelog'] and _schema_ok(SCHEMA, good)
    for bad, needle in [({'changelog': {'enabled': 'yes'}}, 'changelog.enabled'), ({'changelog': {'file': 3}}, 'changelog.file'),
                        ({'changelog': {'versioning': 'calver'}}, 'changelog.versioning'),
                        ({'changelog': {'tag': True}}, 'unknown key "changelog.tag'), ({'changelog': True}, '"changelog" must be an object')]:
        _write_cfg(tmp_path, bad)
        with pytest.raises(cfg.ConfigError, match=needle.replace('.', r'\.')):
            cfg.load(tmp_path)
        assert not _schema_ok(SCHEMA, bad), bad


safe = _load('autorunner_safe_t', 'autosafe.py')


def test_model_aliases_allowed_list(tmp_path):
    _write_cfg(tmp_path, V2_FULL)
    assert safe.model_problems(cfg.load(tmp_path)) == []                 # opus, sonnet, haiku, inherit, claude-...
    assert safe.model_problems(cfg.load(tmp_path / 'none')) == []        # no models section: nothing to check
    for bad in ['sonet', 'gpt-4o', '', 'Opus', 'claude-', 'claude-opus 5', 'claude-x;rm']:
        _write_cfg(tmp_path, {'models': {'implementer': 'opus', 'scribe': bad}})
        problems = safe.model_problems(cfg.load(tmp_path))                # the loader accepts any text; preflight refuses
        assert len(problems) == 1 and '"models.scribe"' in problems[0], bad


QUEUE = """# Queue

- [x] **A1 Old.** done earlier, described in enough words to count as a real item here.
  *Acceptance:* tests pass.

- [ ] **A2 Next.** the second thing to build, with detail about where and what must not change.
  *Acceptance:* a test shows it.

- [ ] **A3 Third.** the third thing to build, with detail about where and what must not change.
  *Status:* blocked which provider do we use?
  *Acceptance:* a test shows it.

## Needs a person
- Deploy.
"""


def test_status_defaults_and_parsing():
    assert q.statuses(QUEUE) == [('A1', 'done', ''), ('A2', 'queued', ''), ('A3', 'blocked', 'which provider do we use?')]
    assert q.parse_status('  *Status:* done 1a2b3c4')[0:2] == ('done', '1a2b3c4')
    assert q.parse_status('  *Status:* rejected too short; no acceptance')[0] == 'rejected'
    assert q.parse_status('  *Status:* weird')[0] == 'unknown'


def test_set_status_adds_replaces_and_touches_nothing_else():
    out = q.set_status(QUEUE, 'A2', 'running')
    assert q.statuses(out)[1] == ('A2', 'running', '')
    again = q.set_status(out, 'A2', 'done', 'abc1234')
    assert q.statuses(again)[1] == ('A2', 'done', 'abc1234') and again.count('*Status:*') == 2
    assert q.set_status(again, 'A2', 'done', 'abc1234') == again                          # idempotent
    assert again.replace('  *Status:* done abc1234\n', '') == QUEUE                                # only the status line was added
    last = q.set_status(QUEUE, 'A3', 'queued')                                              # last item before a heading
    assert q.statuses(last)[2] == ('A3', 'queued', '') and last.index('## Needs a person') > last.index('*Status:* queued')
    with pytest.raises(KeyError):
        q.set_status(QUEUE, 'Z9', 'done')
    with pytest.raises(ValueError):
        q.set_status(QUEUE, 'A2', 'finished')


def test_multiline_detail_stays_one_line():
    out = q.set_status(QUEUE, 'A2', 'blocked', 'why?\nsecond line')
    assert q.statuses(out)[1] == ('A2', 'blocked', 'why? second line')


def test_status_command(tmp_path, capsys):
    f = tmp_path / 'q.md'
    f.write_text(QUEUE, encoding='utf-8')
    assert q.main(['status', '--queue', str(f)]) == 0
    assert capsys.readouterr().out.splitlines() == ['A1\tdone', 'A2\tqueued', 'A3\tblocked\twhich provider do we use?']
