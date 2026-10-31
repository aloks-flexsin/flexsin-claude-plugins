"""auto-runner plugin: `autodeliver.HttpTransport` error mapping, with a fake `send` (no network, no service)."""
import json
import urllib.error

import pytest

from tests.auto_runner.test_runner import ar

ad = ar.autodeliver

TOKEN = 'art_secret-token-value-0123456789'
ROW = {'delivery_id': 'B-a1.v1', 'blocker_id': 'B-a1', 'version': 1, 'project': 'alpha', 'task': 'A1', 'decision': {}}


class FakeSend:
    """Answers each call with the next (status, body) or raises the next exception; records every request."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({'method': method, 'url': url, 'headers': dict(headers), 'body': body, 'timeout': timeout})
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        status, body = r
        return status, body if isinstance(body, bytes) else json.dumps(body).encode()


def transport(*replies, **kw):
    send = FakeSend(*replies)
    return ad.HttpTransport('https://central.test/', TOKEN, send=send, **kw), send


def test_pull_sends_the_token_only_in_the_authorization_header():
    t, send = transport((200, {'deliveries': [ROW]}))
    assert t.pull_pending('alpha') == [ROW]
    c = send.calls[0]
    assert (c['method'], c['url'], c['timeout']) == ('GET', 'https://central.test/api/v1/deliveries/pending', 10)
    assert c['headers']['Authorization'] == f'Bearer {TOKEN}'
    assert TOKEN not in c['url'] and not c['body']
    assert [k for k, v in c['headers'].items() if TOKEN in v] == ['Authorization']
    assert TOKEN not in repr(t) and TOKEN not in str(vars(t).get('base_url'))


def test_ack_posts_to_the_quoted_delivery_id_and_returns_the_row():
    row = {'delivery_id': 'B-a1.v1', 'state': 'acknowledged'}
    t, send = transport((200, row), timeout=3)
    assert t.ack('B-a1.v1') == row
    assert (send.calls[0]['method'], send.calls[0]['url'], send.calls[0]['timeout']) == \
        ('POST', 'https://central.test/api/v1/deliveries/B-a1.v1/ack', 3)
    t, send = transport((200, row))
    t.ack('B/x?y')
    assert send.calls[0]['url'].endswith('/deliveries/B%2Fx%3Fy/ack')


def test_ack_404_is_key_error_and_409_is_value_error():
    t, _ = transport((404, {'error': 'no delivery B-x.v1'}), (409, {'error': 'only a delivered row can be acknowledged'}))
    with pytest.raises(KeyError):
        t.ack('B-x.v1')
    with pytest.raises(ValueError, match='only a delivered row'):
        t.ack('B-a1.v1')


@pytest.mark.parametrize('reply', [
    (500, {'error': 'boom'}), (502, b'<html>bad gateway</html>'), (503, b''),
    ConnectionRefusedError('refused'), TimeoutError('timed out'), ad.TransportUnavailable('no route'),
])
@pytest.mark.parametrize('call', ['pull', 'ack'])
def test_outages_raise_transport_unavailable(reply, call):
    t, _ = transport(reply)
    with pytest.raises(ad.TransportUnavailable) as e:
        t.pull_pending('alpha') if call == 'pull' else t.ack('B-a1.v1')
    assert isinstance(e.value, ConnectionError) and TOKEN not in str(e.value)


@pytest.mark.parametrize('body', [b'not json', b'[]', {'deliveries': 'x'}, {'deliveries': ['x']}, {}])
def test_an_unreadable_pull_reply_is_an_outage(body):
    t, _ = transport((200, body))
    with pytest.raises(ad.TransportUnavailable, match='unreadable reply'):
        t.pull_pending('alpha')


@pytest.mark.parametrize('status', [400, 401, 403, 413, 429])
def test_other_refusals_are_not_outages(status):
    t, _ = transport((status, {'error': 'token refused'}), (status, {'error': 'token refused'}))
    with pytest.raises(ad.TransportRefused, match=str(status)) as e:
        t.pull_pending('alpha')
    assert not isinstance(e.value, ConnectionError) and TOKEN not in str(e.value)
    with pytest.raises(ad.TransportRefused):
        t.ack('B-a1.v1')


def test_needs_a_url_and_a_token():
    with pytest.raises(ValueError):
        ad.HttpTransport('', TOKEN)
    with pytest.raises(ValueError):
        ad.HttpTransport('https://central.test', '')


def test_default_send_maps_urllib_failures(monkeypatch):
    import urllib.request

    def refuse(req, timeout):
        assert req.get_header('Authorization') == f'Bearer {TOKEN}' and timeout == 7
        raise urllib.error.URLError(ConnectionRefusedError('refused'))
    monkeypatch.setattr(urllib.request, 'urlopen', refuse)
    t = ad.HttpTransport('https://central.test', TOKEN, timeout=7)
    with pytest.raises(ad.TransportUnavailable) as e:
        t.pull_pending('alpha')
    assert TOKEN not in str(e.value)

    def not_found(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', {}, None)
    monkeypatch.setattr(urllib.request, 'urlopen', not_found)
    with pytest.raises(KeyError):
        t.ack('B-x.v1')
