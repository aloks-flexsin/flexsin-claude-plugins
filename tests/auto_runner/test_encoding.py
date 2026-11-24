"""auto-runner plugin: child output is read as UTF-8 on every platform. On Windows the default would be the ANSI code page
(cp1252), which cannot decode some UTF-8 bytes (0x81 in 'ā') and garbles the rest ('₹'); the agent's output then failed
to decode and the iteration lost its cost figure."""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.auto_runner.test_runner import ar

TEXT = 'Credit ₹4Cr — ā “quoted” ✓'
COST = {'type': 'result', 'total_cost_usd': 1.25, 'result': TEXT}


def utf8_child(text):
    """A child that writes `text` to stdout as raw UTF-8 bytes, whatever its own console encoding is."""
    return [sys.executable, '-c', f'import sys; sys.stdout.buffer.write({text!r}.encode("utf-8"))']


def test_the_agents_utf8_output_is_read_exactly_and_its_cost_found(tmp_path):
    code, out = ar.run_claude(utf8_child(TEXT + '\n' + json.dumps(COST, ensure_ascii=False) + '\n'), tmp_path)
    assert code == 0 and TEXT in out
    assert ar.parse_cost(out) == 1.25


def test_the_suites_utf8_output_is_read_exactly(tmp_path):
    import subprocess
    code, out = ar.run_tests(subprocess.list2cmdline(utf8_child(TEXT)), tmp_path)
    assert code == 0 and TEXT in out


def test_a_byte_that_is_not_utf8_does_not_crash_the_read(tmp_path):
    bad = [sys.executable, '-c', 'import sys; sys.stdout.buffer.write(b"ok \\xff\\xfe end")']
    code, out = ar.run_claude(bad, tmp_path)
    assert code == 0 and out.startswith('ok ') and out.endswith(' end')


def test_python_children_are_asked_to_write_utf8(monkeypatch):
    monkeypatch.delenv('PYTHONIOENCODING', raising=False)
    assert ar.child_env()['PYTHONIOENCODING'] == 'utf-8'
    monkeypatch.setenv('PYTHONIOENCODING', 'latin-1')
    assert ar.child_env()['PYTHONIOENCODING'] == 'latin-1'          # a project's own choice is kept


def test_a_python_child_printing_non_ascii_is_read_correctly(tmp_path):
    child = [sys.executable, '-c', f'print({TEXT!r})']                 # print(), not raw bytes: uses PYTHONIOENCODING
    code, out = ar.run_claude(child, tmp_path)
    assert code == 0 and out.strip() == TEXT


def test_git_output_with_non_ascii_is_read_exactly(tmp_path):
    ar.git(tmp_path, 'init', '-q')
    ar.git(tmp_path, '-c', 'user.name=T', '-c', 'user.email=t@example.com', 'commit', '-q', '--allow-empty', '-m', TEXT)
    assert ar.git(tmp_path, 'log', '-1', '--format=%s') == TEXT


# --- W1: the task commands print any task on a console that cannot encode it ---------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
ARROW = 'A → B'
PLAN = 'Step 1 → Step 2'
DRIVER = '''
import importlib.util, json, sys
sys.path.insert(0, {root!r})
from tests.auto_runner import fake_central
spec = importlib.util.spec_from_file_location('autotasks_under_test', {bin!r} + '/autotasks.py')
at = importlib.util.module_from_spec(spec)
sys.modules['autotasks_under_test'] = at
spec.loader.exec_module(at)
tasks = fake_central.FakeTasks()
tasks.add(1, {title!r}, 'desc')
tasks.tasks[1]['plan'] = {plan!r}


def send(method, url, headers, body, timeout):
    if '/api/v1/tasks/next' in url:
        task = {{k: v for k, v in tasks.tasks[1].items() if k != 'claim'}}
        return 200, json.dumps({{'task': task, 'claim': 'c1', 'action': 'build'}}).encode()
    return tasks.send(method, url, headers, body, timeout)


orig = at.transport
at.transport = lambda project, env=None, send_=None: orig(project, env, send)
at.autosafe.utf8_console()
sys.exit(at.main(sys.argv[1:] + ['--project', {project!r}]))
'''


def run_driver(tmp_path, cmd, io_encoding):
    project = tmp_path / 'proj'
    (project / '.claude').mkdir(parents=True, exist_ok=True)
    (project / '.claude' / 'auto.json').write_text(json.dumps(
        {'central': {'enabled': True, 'url': 'https://central.example', 'project': 'acme'}}), encoding='utf-8')
    script = tmp_path / 'driver.py'
    script.write_text(DRIVER.format(root=str(ROOT), bin=str(ROOT / 'auto-runner' / 'bin'), title=ARROW, plan=PLAN,
                                    project=str(project)), encoding='utf-8')
    env = {**os.environ, 'AUTO_RUNNER_TOKEN': 'arp_testtoken', 'AUTO_RUNNER_USER': 'alok', 'AUTO_RUNNER_ACCOUNT': 'bhawish'}
    env.pop('PYTHONIOENCODING', None)
    if io_encoding:
        env['PYTHONIOENCODING'] = io_encoding
    done = subprocess.run([sys.executable, str(script), cmd], capture_output=True, env=env, cwd=tmp_path)
    return done.returncode, done.stdout.decode('utf-8'), done.stderr.decode('utf-8', 'replace')


@pytest.mark.parametrize('io_encoding', ['cp1252', None])
def test_list_and_next_print_a_task_with_an_arrow(tmp_path, io_encoding):
    code, out, err = run_driver(tmp_path, 'list', io_encoding)
    assert code == 0, err
    assert ARROW in out
    code, out, err = run_driver(tmp_path, 'next', io_encoding)
    assert code == 0, out + err
    assert ARROW in out and PLAN in out


def test_utf8_console_lets_a_cp1252_stream_print_an_arrow():
    stream = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')
    ar.autosafe.utf8_console([stream])
    print('→', file=stream)
    stream.flush()
    assert stream.buffer.getvalue().decode('utf-8').strip() == '→'


def test_utf8_console_ignores_streams_without_reconfigure():
    ar.autosafe.utf8_console([io.StringIO(), object()])
    ar.autosafe.utf8_console()
