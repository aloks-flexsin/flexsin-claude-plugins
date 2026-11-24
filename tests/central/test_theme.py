"""Central service: light is the default theme. The page carries data-theme on <html>, chosen by the `theme` cookie
(only the exact value `dark` turns it dark); the stylesheet no longer follows the operating system's setting."""
import pathlib

import pytest

from .test_signin import ada, clock, conn, signin  # noqa: F401  (fixtures)

CSS = pathlib.Path(__file__).resolve().parents[2] / 'auto-runner' / 'central' / 'static' / 'style.css'


def _html_tag(page):
    return page[page.index('<html'):page.index('>', page.index('<html')) + 1]


@pytest.fixture
def signed_in(client, ada, clock):
    assert signin(client).status_code == 302
    return client


@pytest.mark.parametrize('cookie', [None, 'blue', 'DARK', ''])
def test_sign_in_page_is_light_without_a_dark_cookie(client, cookie):
    if cookie is not None:
        client.set_cookie('theme', cookie)
    resp = client.get('/signin')
    assert resp.status_code == 200
    assert _html_tag(resp.get_data(as_text=True)) == '<html lang="en" data-theme="light">'


@pytest.mark.parametrize('cookie', [None, 'blue'])
def test_my_work_is_light_without_a_dark_cookie(signed_in, cookie):
    if cookie is not None:
        signed_in.set_cookie('theme', cookie)
    resp = signed_in.get('/my')
    assert resp.status_code == 200
    assert 'data-theme="light"' in _html_tag(resp.get_data(as_text=True))


def test_dark_cookie_renders_dark_signed_out(client):
    client.set_cookie('theme', 'dark')
    assert 'data-theme="dark"' in _html_tag(client.get('/signin').get_data(as_text=True))


def test_dark_cookie_renders_dark_signed_in(signed_in):
    signed_in.set_cookie('theme', 'dark')
    assert 'data-theme="dark"' in _html_tag(signed_in.get('/my').get_data(as_text=True))


def test_stylesheet_no_longer_follows_the_system_setting():
    css = CSS.read_text()
    assert 'prefers-color-scheme' not in css
    start = css.index(':root[data-theme="dark"]{')
    block = css[start:css.index('}', start)]
    assert '--bg:#10141b' in block
    assert css.count(':root[data-theme="dark"]') == 2      # both former dark blocks kept their token values
