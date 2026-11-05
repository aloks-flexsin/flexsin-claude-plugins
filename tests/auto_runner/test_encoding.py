"""auto-runner plugin: child output is read as UTF-8 on every platform. On Windows the default would be the ANSI code page
(cp1252), which cannot decode some UTF-8 bytes (0x81 in 'ā') and garbles the rest ('₹'); the agent's output then failed
to decode and the iteration lost its cost figure."""
import json
import sys

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
