"""auto-runner plugin: each iteration asks the agent for only the tests that cover its change (the runner's own run of the
whole suite stays the gate) and keeps the agent's full stream-json transcript in the iteration log."""
import json

from tests.auto_runner.test_runner import ar, fake_agent, project, runs  # noqa: F401  (project is a fixture)

TRANSCRIPT = [
    {'type': 'system', 'subtype': 'init', 'session_id': 's1', 'model': 'claude-opus-5-5'},
    {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Bash',
                                                   'input': {'command': 'python -m pytest -q tests/test_a.py'}}]}},
    {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': '3 passed'}]}},
    {'type': 'result', 'subtype': 'success', 'num_turns': 3, 'total_cost_usd': 0.42, 'result': 'Done.'},
]
STREAM = '\n'.join(json.dumps(e) for e in TRANSCRIPT) + '\n'


def test_the_prompt_asks_for_related_tests_and_names_the_runners_full_run():
    cfg = {'test_command': 'npm test', 'check_only': False, 'queue_file': '.auto/queue.md', 'rules_files': []}
    text = ar.build_prompt(cfg, '.auto/queue.md', 'B7')
    assert 'run only those' in text and 'npm test' in text and 'a red suite stops the run' in text
    assert 'shared code or setup, run the whole suite once' in text
    assert 'the whole suite green' not in text


def test_check_only_projects_keep_their_wording():
    cfg = {'test_command': '', 'check_only': True, 'queue_file': '.auto/queue.md', 'rules_files': []}
    assert 'unverified: no tests' in ar.build_prompt(cfg, '.auto/queue.md', 'B7')


def test_the_agent_is_asked_for_a_stream_json_transcript(project):
    calls = []
    assert ar.run(project, max_iterations=1, claude=fake_agent(output=STREAM, calls=calls)) == 0
    cmd = calls[0]
    assert cmd[cmd.index('--output-format') + 1] == 'stream-json' and '--verbose' in cmd


def test_the_log_keeps_every_tool_call_and_the_cost_is_still_read(project):
    assert ar.run(project, max_iterations=1, claude=fake_agent(output=STREAM)) == 0
    log = sorted((project / '.auto' / 'logs').glob('*-iter1.log'))[-1].read_text(encoding='utf-8')
    assert '"name": "Bash"' in log and 'pytest -q tests/test_a.py' in log and '"subtype": "init"' in log
    assert runs(project)[-1]['cost_usd'] == 0.42


def test_cost_is_read_from_the_result_line_of_a_stream():
    assert ar.parse_cost(STREAM) == 0.42
    assert ar.parse_cost(STREAM.replace('"total_cost_usd": 0.42, ', '')) is None
