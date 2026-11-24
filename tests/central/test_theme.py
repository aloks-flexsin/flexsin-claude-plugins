"""Central service: light is the default theme. The page carries data-theme on <html>, chosen by the `theme` cookie
(only the exact value `dark` turns it dark); the stylesheet no longer follows the operating system's setting."""
import pathlib
import re

import pytest

from .test_signin import _csrf, _set_cookie_headers, ada, clock, conn, signin  # noqa: F401  (fixtures)

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


# --- L2: the dark mode toggle -------------------------------------------------------------------------------------

def _theme_form(page):
    start = page.index('<form method="post" class="theme-form"')
    return page[start:page.index('</form>', start)]


def _post_theme(client, theme, next_path='/my', csrf_from='/my'):
    return client.post('/theme', data={'theme': theme, 'next': next_path, 'csrf_token': _csrf(client, csrf_from)})


def test_posting_dark_sets_the_cookie_and_the_next_page_is_dark(signed_in):
    resp = _post_theme(signed_in, 'dark')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/my')
    (header,) = _set_cookie_headers(resp, 'theme')
    assert header.startswith('theme=dark;')
    flags = [part.strip().lower() for part in header.split(';')]
    assert 'secure' in flags and 'samesite=lax' in flags and 'path=/' in flags
    assert 'max-age=31536000' in flags
    page = signed_in.get('/my').get_data(as_text=True)
    assert 'data-theme="dark"' in _html_tag(page)
    form = _theme_form(page)
    assert '>Light mode</button>' in form and 'name="theme" value="light"' in form


def test_posting_light_switches_back(signed_in):
    _post_theme(signed_in, 'dark')
    resp = _post_theme(signed_in, 'light')
    assert resp.status_code == 302
    (header,) = _set_cookie_headers(resp, 'theme')
    assert header.startswith('theme=light;')
    page = signed_in.get('/my').get_data(as_text=True)
    assert 'data-theme="light"' in _html_tag(page)
    form = _theme_form(page)
    assert '>Dark mode</button>' in form and 'name="theme" value="dark"' in form


def test_signed_out_toggle_works_and_returns_to_the_page(client):
    form = _theme_form(client.get('/signin').get_data(as_text=True))
    assert '>Dark mode</button>' in form and 'name="next" value="/signin"' in form
    resp = _post_theme(client, 'dark', next_path='/signin', csrf_from='/signin')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/signin')
    assert 'data-theme="dark"' in _html_tag(client.get('/signin').get_data(as_text=True))


@pytest.mark.parametrize('value', ['', 'blue', 'DARK', 'system'])
def test_a_bad_value_gets_400_and_no_cookie(signed_in, value):
    resp = _post_theme(signed_in, value)
    assert resp.status_code == 400
    assert _set_cookie_headers(resp, 'theme') == []


@pytest.mark.parametrize('next_path', ['//evil.example', 'https://evil.example/', '/\\evil.example', ''])
def test_an_unsafe_next_goes_to_my_work(signed_in, next_path):
    resp = _post_theme(signed_in, 'dark', next_path=next_path)
    assert resp.status_code == 302
    assert resp.headers['Location'] in ('/my', 'http://localhost/my')


def test_next_keeps_the_query_string(signed_in):
    form = _theme_form(signed_in.get('/my?project=x').get_data(as_text=True))
    assert 'name="next" value="/my?project=x"' in form


def test_the_form_carries_csrf_and_a_post_without_it_gets_400(signed_in):
    form = _theme_form(signed_in.get('/my').get_data(as_text=True))
    assert re.search(r'name="csrf_token" value="[^"]+"', form)
    resp = signed_in.post('/theme', data={'theme': 'dark', 'next': '/my'})
    assert resp.status_code == 400 and _set_cookie_headers(resp, 'theme') == []
    forged = signed_in.post('/theme', data={'theme': 'dark', 'next': '/my', 'csrf_token': 'forged'})
    assert forged.status_code == 400 and _set_cookie_headers(forged, 'theme') == []
