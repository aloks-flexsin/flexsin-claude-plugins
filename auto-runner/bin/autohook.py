"""auto-runner plugin: Claude Code hooks (registered in ../hooks/hooks.json).

    python autohook.py prompt     # UserPromptSubmit: reads the hook input (JSON) on stdin
    python autohook.py guard      # PreToolUse (Edit, Write, MultiEdit, NotebookEdit)
    python autohook.py allow      # writes .auto/run/allow-<session_id> for the hook input's session

`prompt`: in a project whose `.claude/auto.json` has `central.enabled`, prints a one-line reminder (Claude Code adds a
UserPromptSubmit hook's stdout to the conversation) that new work is planned with `/auto-runner:auto` and sent for
approval with `autotasks.py propose`. Prints nothing for other projects, for prompts that start with `/` (slash commands),
and when the config or the hook input cannot be read. A hook never stops the person's prompt: it always exits 0.
A prompt that is `/auto-runner:allow-edits` instead calls `allow` for the hook input's `session_id` and prints one line.

`guard` (opt-in with `"enforce_runner": true` in `.claude/auto.json`): blocks an edit of a path inside the main checkout
(exit 2 with a message on stderr naming `/auto-runner:auto`; Claude Code shows it to the session). Allowed: paths under
`.auto/`, paths outside the project (the runner's worktree sits next to it), every path when the project itself is a
linked git worktree (`.git` is a file, as in the runner's worktree), and a session with a live allow file.
`allow` writes `.auto/run/allow-<session_id>` holding the time; it lets that one session edit until the file is deleted
or ALLOW_HOURS pass. When the config or the hook input cannot be read the guard allows: it is opt-in and a parse error
must never lock a person out of their own checkout.
Standard library only.
"""
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

REMINDER = ('auto-runner: this project is connected to the central service. Do not build a new request in this '
            'checkout: plan it with /auto-runner:auto, then send the plan for approval with autotasks.py propose.')

ALLOW_COMMAND = '/auto-runner:allow-edits'
ALLOW_HOURS = 12
ALLOW_DIR = Path('.auto') / 'run'
BLOCKED = ('auto-runner: this checkout is guarded ("enforce_runner": true in .claude/auto.json), so {path} is not '
           'edited here. Queue the work and run it with /auto-runner:auto (it builds in its own worktree), or type '
           '/auto-runner:allow-edits to let this session edit here for 12 hours.')
ALLOWED = ('auto-runner: edits in this checkout are allowed for this session for 12 hours '
           '(delete .auto/run/allow-{sid} to end it sooner).')


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


def load_cfg(project):
    """The project's config, or None when it cannot be read."""
    try:
        return _load('autorunner_cfg', 'autocfg.py').load(project)
    except Exception:
        return None


def central_enabled(project):
    """True only when `.claude/auto.json` loads cleanly and its `central` section is enabled; any error -> False."""
    cfg = load_cfg(project)
    central = cfg.get('central') if cfg else None
    return isinstance(central, dict) and central.get('enabled') is True


def prompt_reminder(hook_input, env=None):
    """-> the reminder line, or '' when nothing should be printed."""
    env = os.environ if env is None else env
    if not isinstance(hook_input, dict):
        return ''
    text = hook_input.get('prompt')
    if isinstance(text, str) and text.lstrip().startswith('/'):
        return ''
    return REMINDER if central_enabled(project_dir(hook_input, env)) else ''


def _session_id(hook_input):
    """The hook input's session_id when it is safe as part of a file name, else None."""
    sid = hook_input.get('session_id') if isinstance(hook_input, dict) else None
    if isinstance(sid, str) and re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}', sid):
        return sid
    return None


def allow_file(project, sid):
    return Path(project) / ALLOW_DIR / f'allow-{sid}'


def allow(hook_input, env=None, now=None):
    """Write the allow file for the hook input's session. -> its path, or None when there is no usable session_id."""
    env = os.environ if env is None else env
    sid = _session_id(hook_input)
    if sid is None:
        return None
    path = allow_file(project_dir(hook_input, env), sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = time.time() if now is None else now
    path.write_text(json.dumps({'session_id': sid, 'created': created}) + '\n', encoding='utf-8')
    return path


def allowed_session(project, sid, now=None):
    """True when the session's allow file exists and is younger than ALLOW_HOURS (its stored time, else its mtime)."""
    if sid is None:
        return False
    path = allow_file(project, sid)
    try:
        try:
            created = float(json.loads(path.read_text(encoding='utf-8'))['created'])
        except (ValueError, KeyError, TypeError):
            created = path.stat().st_mtime
    except OSError:
        return False
    now = time.time() if now is None else now
    return 0 <= now - created < ALLOW_HOURS * 3600


def _inside(path, folder):
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


def _target(hook_input, base):
    tool_input = hook_input.get('tool_input')
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get('file_path') or tool_input.get('notebook_path')
    if not isinstance(raw, str) or not raw:
        return None
    p = Path(raw)
    return Path(os.path.realpath(p if p.is_absolute() else base / p))


def guard(hook_input, env=None, now=None):
    """-> '' to allow the edit, or the message to block it with."""
    env = os.environ if env is None else env
    if not isinstance(hook_input, dict):
        return ''
    project = project_dir(hook_input, env)
    cfg = load_cfg(project)
    if not cfg or cfg.get('enforce_runner') is not True:
        return ''
    if (project / '.git').is_file():                     # a linked worktree, such as the runner's own
        return ''
    root = Path(os.path.realpath(project))
    target = _target(hook_input, Path(hook_input.get('cwd') or project))
    if target is None or not _inside(target, root):
        return ''                                         # outside the project, including the runner's worktree
    worktree = Path(os.path.realpath(project.parent / (project.name + cfg.get('worktree_suffix', '-auto'))))
    if _inside(target, worktree) or _inside(target, root / '.auto'):
        return ''
    if allowed_session(project, _session_id(hook_input), now):
        return ''
    return BLOCKED.format(path=target.relative_to(root).as_posix())


def is_allow_prompt(hook_input):
    text = hook_input.get('prompt') if isinstance(hook_input, dict) else None
    return isinstance(text, str) and text.strip().split(maxsplit=1)[:1] == [ALLOW_COMMAND]


def _read_input(stream):
    try:
        data = json.loads(stream.read() or '{}')
    except (ValueError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _allow_line(hook_input, env, now):
    return ALLOWED.format(sid=_session_id(hook_input)) if allow(hook_input, env, now) is not None else ''


def main(argv=None, stdin=None, stdout=None, env=None, stderr=None, now=None):
    argv = sys.argv[1:] if argv is None else argv
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        cmd = argv[:1]
        if cmd not in (['prompt'], ['guard'], ['allow']):
            return 0
        hook_input = _read_input(stdin)
        if hook_input is None:
            return 0
        if cmd == ['guard']:
            message = guard(hook_input, env, now)
            if message:
                stderr.write(message + '\n')
                return 2                                  # PreToolUse exit 2: the tool call is blocked
            return 0
        if cmd == ['allow'] or is_allow_prompt(hook_input):
            line = _allow_line(hook_input, env, now)
        else:
            line = prompt_reminder(hook_input, env)
        if line:
            stdout.write(line + '\n')
    except Exception:
        pass        # a hook never blocks the person's prompt; the guard allows when it cannot decide
    return 0


if __name__ == '__main__':
    try:                                                  # the hook always exits 0, even if the console cannot be set up
        _load('autorunner_safe', 'autosafe.py').utf8_console()
    except Exception:
        pass
    sys.exit(main())
