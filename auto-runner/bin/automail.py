"""
In-process mail sink for browser acceptance: a tiny SMTP receiver on the loopback address, so sign-up and verification
flows can be tested without Docker, a third-party service or any real mail leaving the machine (stdlib only).

    python automail.py serve  --project DIR [--port N]     # listen on 127.0.0.1 (port 0 = any free port; the port is printed)
    python automail.py latest --project DIR ADDRESS        # print the newest stored message for ADDRESS as JSON

Rules:
  * It binds to 127.0.0.1 only; any other host is refused (ValueError) before a socket is opened. It never relays or sends.
  * It speaks the small part of SMTP a test app needs: HELO/EHLO, MAIL FROM, RCPT TO, DATA, RSET, NOOP, QUIT.
    A message larger than MAX_BYTES is refused with 552 and not stored.
  * Each accepted message is written atomically as JSON under `<project>/.auto/run/mail/` (git-ignored with `.auto/run/`):
    {seq, received_at, mail_from, rcpt_to, subject, body, raw}. `seq` orders messages.
  * `latest(mail_dir, address)` returns the newest message whose recipients include the address (case-insensitive), or None.
"""
import argparse
import email
import email.policy
import json
import os
import socketserver
import sys
import threading
import time
from pathlib import Path

MAIL_DIR = Path('.auto') / 'run' / 'mail'
LOOPBACK = '127.0.0.1'
MAX_BYTES = 1_000_000
_seq_lock = threading.Lock()
_last_seq = [0]


def mail_dir(project):
    return Path(project) / MAIL_DIR


def _next_seq():
    with _seq_lock:
        _last_seq[0] = max(_last_seq[0] + 1, time.time_ns())
        return _last_seq[0]


def _address(arg):
    """'FROM:<a@b.test> SIZE=10' -> 'a@b.test'."""
    arg = arg.split(':', 1)[1].strip() if ':' in arg else arg.strip()
    if arg.startswith('<'):
        arg = arg[1:arg.index('>')] if '>' in arg else arg[1:]
    return arg.split()[0] if arg.split() else ''


def store(folder, mail_from, rcpt_to, raw):
    """Write one message as JSON (atomic) and return the stored dict."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    part = msg.get_body(preferencelist=('plain', 'html')) if msg.is_multipart() else msg
    try:
        body = part.get_content() if part is not None else ''
    except (LookupError, ValueError):
        body = raw.decode('utf-8', 'replace')
    seq = _next_seq()
    record = {'seq': seq, 'received_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'mail_from': mail_from, 'rcpt_to': list(rcpt_to), 'subject': str(msg.get('Subject', '')),
              'body': body, 'raw': raw.decode('utf-8', 'replace')}
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f'.{seq}.tmp'
    tmp.write_text(json.dumps(record, indent=1), encoding='utf-8')
    os.replace(tmp, folder / f'{seq}.json')
    return record


def messages(folder):
    out = []
    for f in Path(folder).glob('*.json') if Path(folder).is_dir() else []:
        try:
            out.append(json.loads(f.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda m: m.get('seq', 0))


def latest(folder, address):
    want = address.strip().lower()
    hits = [m for m in messages(folder) if want in (r.lower() for r in m.get('rcpt_to', []))]
    return hits[-1] if hits else None


class _Handler(socketserver.StreamRequestHandler):
    def _say(self, line):
        self.wfile.write((line + '\r\n').encode('ascii'))

    def _reset(self):
        self.mail_from, self.rcpt_to = None, []

    def handle(self):
        self._reset()
        self._say('220 auto-runner mail sink (loopback only)')
        while True:
            line = self.rfile.readline(4096)
            if not line:
                return
            text = line.decode('utf-8', 'replace').rstrip('\r\n')
            verb, _, arg = text.partition(' ')
            verb = verb.upper()
            if verb in ('HELO', 'EHLO'):
                self._say('250 sink')
            elif verb == 'MAIL':
                self._reset()
                self.mail_from = _address(arg)
                self._say('250 OK')
            elif verb == 'RCPT':
                if self.mail_from is None:
                    self._say('503 MAIL first')
                    continue
                self.rcpt_to.append(_address(arg))
                self._say('250 OK')
            elif verb == 'DATA':
                if not self.rcpt_to:
                    self._say('503 RCPT first')
                    continue
                self._say('354 End data with <CR><LF>.<CR><LF>')
                self._data()
            elif verb == 'RSET':
                self._reset()
                self._say('250 OK')
            elif verb == 'NOOP':
                self._say('250 OK')
            elif verb == 'QUIT':
                self._say('221 Bye')
                return
            else:
                self._say('502 Command not implemented')

    def _data(self):
        chunks, size, too_big = [], 0, False
        while True:
            line = self.rfile.readline(MAX_BYTES + 3)
            if not line:
                return
            if line in (b'.\r\n', b'.\n'):
                break
            if line.startswith(b'.'):
                line = line[1:]                              # dot-stuffing (RFC 5321 4.5.2)
            size += len(line)
            if size > MAX_BYTES:
                too_big = True
                chunks = []
            elif not too_big:
                chunks.append(line)
        if too_big:
            self._say('552 Message too large; not stored')
        else:
            store(self.server.folder, self.mail_from, self.rcpt_to, b''.join(chunks))
            self._say('250 OK stored')
        self._reset()


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False


class MailSink:
    """`with MailSink(project) as sink:` ... `sink.port`. Binds 127.0.0.1 only."""

    def __init__(self, project, port=0, host=LOOPBACK):
        if host != LOOPBACK:
            raise ValueError(f'the mail sink binds to {LOOPBACK} only, not {host!r}')
        self.folder = mail_dir(project)
        self._server = _Server((LOOPBACK, port), _Handler)
        self._server.folder = self.folder
        self.host, self.port = self._server.server_address[:2]
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._server.serve_forever, name='auto-mail-sink', daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def latest(self, address):
        return latest(self.folder, address)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def main(argv=None):
    p = argparse.ArgumentParser(prog='automail.py', description=__doc__.split('\n\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('serve')
    s.add_argument('--project', default='.')
    s.add_argument('--port', type=int, default=0)
    lt = sub.add_parser('latest')
    lt.add_argument('--project', default='.')
    lt.add_argument('address')
    a = p.parse_args(argv)
    if a.cmd == 'latest':
        m = latest(mail_dir(a.project), a.address)
        if m is None:
            print(f'no message for {a.address}', file=sys.stderr)
            return 1
        print(json.dumps(m, indent=1))
        return 0
    sink = MailSink(a.project, a.port).start()
    print(f'mail sink listening on {sink.host}:{sink.port}, storing in {sink.folder}', flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sink.stop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
