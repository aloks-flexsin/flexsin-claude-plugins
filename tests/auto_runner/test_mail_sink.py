"""auto-runner plugin: the loopback SMTP mail sink stores what smtplib sends and returns the latest message per address."""
import importlib.util
import json
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent.parent / 'auto-runner' / 'bin'
spec = importlib.util.spec_from_file_location('autorunner_mail_t', BIN / 'automail.py')
mail = importlib.util.module_from_spec(spec)
sys.modules['autorunner_mail_t'] = mail
spec.loader.exec_module(mail)


def _send(port, to, subject, body, sender='app@shop.test'):
    m = EmailMessage()
    m['From'], m['To'], m['Subject'] = sender, to, subject
    m.set_content(body)
    with smtplib.SMTP('127.0.0.1', port, timeout=5) as s:
        s.send_message(m)


def test_message_is_stored_and_retrievable(tmp_path):
    with mail.MailSink(tmp_path) as sink:
        assert sink.host == '127.0.0.1' and sink.port > 0
        _send(sink.port, 'Ann@Users.test', 'Verify your account', 'Code: 123456\n.leading dot line')
        _send(sink.port, 'bob@users.test', 'Hello Bob', 'other')
        _send(sink.port, 'ann@users.test', 'Second for Ann', 'Code: 999')
        got = sink.latest('ANN@users.test')
    assert got['subject'] == 'Second for Ann' and 'Code: 999' in got['body'] and got['mail_from'] == 'app@shop.test'
    first = mail.messages(tmp_path / '.auto' / 'run' / 'mail')
    assert [m['subject'] for m in first] == ['Verify your account', 'Hello Bob', 'Second for Ann']
    assert '.leading dot line' in first[0]['body']                       # dot-stuffing undone
    files = sorted((tmp_path / '.auto' / 'run' / 'mail').iterdir())
    assert len(files) == 3 and all(f.suffix == '.json' for f in files)   # no temp files left
    assert json.loads(files[1].read_text(encoding='utf-8'))['rcpt_to'] == ['bob@users.test']
    assert mail.latest(tmp_path / '.auto' / 'run' / 'mail', 'nobody@users.test') is None


@pytest.mark.parametrize('host', ['0.0.0.0', '::', '192.168.1.10', 'localhost', ''])
def test_binds_loopback_only(tmp_path, host):
    with pytest.raises(ValueError, match='127.0.0.1 only'):
        mail.MailSink(tmp_path, host=host)
    assert not (tmp_path / '.auto').exists()


def test_oversized_message_is_refused_and_not_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(mail, 'MAX_BYTES', 200)
    with mail.MailSink(tmp_path) as sink:
        with pytest.raises(smtplib.SMTPDataError) as e:
            _send(sink.port, 'a@users.test', 'big', 'x' * 500)
        assert e.value.smtp_code == 552
        _send(sink.port, 'a@users.test', 'small', 'ok')
    assert [m['subject'] for m in mail.messages(sink.folder)] == ['small']


def test_latest_command(tmp_path, capsys):
    with mail.MailSink(tmp_path) as sink:
        _send(sink.port, 'c@users.test', 'Welcome', 'hi')
    assert mail.main(['latest', '--project', str(tmp_path), 'c@users.test']) == 0
    assert json.loads(capsys.readouterr().out)['subject'] == 'Welcome'
    assert mail.main(['latest', '--project', str(tmp_path), 'd@users.test']) == 1
