"""auto-runner plugin: Claude Code hooks (registered in ../hooks/hooks.json).

    python autohook.py prompt     # UserPromptSubmit: reads the hook input (JSON) on stdin

`prompt`: in a project whose `.claude/auto.json` has `central.enabled`, prints a one-line reminder (Claude Code adds a
UserPromptSubmit hook's stdout to the conversation) that new work is planned with `/auto-runner:auto` and sent for
approval with `autotasks.py propose`. Prints nothing for other projects, for prompts that start with `/` (slash commands),
and when the config or the hook input cannot be read. A hook never stops the person's prompt: it always exits 0.
Standard library only.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

REMINDER = ('auto-runner: this project is connected to the central service. Do not build a new request in this '
            'checkout: plan it with /auto-runner:auto, then send the plan for approval with autotasks.py propose.')


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def project_dir(hook_input, env):
    """The project folder: CLAUDE_PROJECT_DIR when Claude Code sets it, else the hook input's cwd, else this folder."""
    return Path(env.get('CLAUDE_PROJECT_DIR') or hook_input.get('cwd') or os.getcwd())


def central_enabled(project):
    """True only when `.claude/auto.json` loads cleanly and its `central` section is enabled; any error -> False."""
    try:
        autocfg = _load('autorunner_cfg', 'autocfg.py')
        cfg = autocfg.load(project)
        central = cfg.get('central')
        return isinstance(central, dict) and central.get('enabled') is True
    except Exception:
        return False


def prompt_reminder(hook_input, env=None):
    """-> the reminder line, or '' when nothing should be printed."""
    env = os.environ if env is None else env
    if not isinstance(hook_input, dict):
        return ''
    text = hook_input.get('prompt')
    if isinstance(text, str) and text.lstrip().startswith('/'):
        return ''
    return REMINDER if central_enabled(project_dir(hook_input, env)) else ''


def _read_input(stream):
    try:
        data = json.loads(stream.read() or '{}')
    except (ValueError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def main(argv=None, stdin=None, stdout=None, env=None):
    argv = sys.argv[1:] if argv is None else argv
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        if argv[:1] == ['prompt']:
            hook_input = _read_input(stdin)
            line = prompt_reminder(hook_input, env) if hook_input is not None else ''
            if line:
                stdout.write(line + '\n')
    except Exception:
        pass        # a hook never blocks the person's prompt
    return 0


if __name__ == '__main__':
    sys.exit(main())
