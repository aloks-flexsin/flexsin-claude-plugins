"""Central service: the "turn on push" bar shown once per browser on any signed-in page (queue item A1)."""
import re

import pytest

from .test_access import new_client
from .test_push_subscriptions import KEYS, STATIC, csrf_of
from .test_repo_url import conn, world  # noqa: F401  (fixtures)

TEMPLATES = STATIC.parent / 'templates'
TEXT = 'Get a notification when a plan or blocker waits for you.'


@pytest.fixture
def keys(app):
    app.config.update(KEYS)
    return KEYS


def bar(page):
    m = re.search(r'<div class="push-ask" data-push-ask[^>]*>.*?</div>\s*</div>\s*</div>', page, re.S)
    return m.group(0) if m else None


def test_a_signed_in_page_has_the_hidden_bar_with_key_csrf_and_subscribe_url(world, keys):
    c = world['clients']['owner']
    for path in ('/my', '/projects', '/notifications'):
        page = c.get(path, follow_redirects=True).get_data(as_text=True)
        section = bar(page)
        assert section, path
        opening = re.match(r'<div[^>]*>', section).group(0)
        assert ' hidden' in opening
        assert 'data-vapid-public-key="%s"' % KEYS['CENTRAL_VAPID_PUBLIC_KEY'] in opening
        assert 'data-csrf-token="%s"' % csrf_of(c) in opening
        assert 'data-subscribe-url="/push/subscribe"' in opening
        assert TEXT in section and 'data-push-ask-on' in section and 'data-push-ask-off' in section
        assert '>Turn on<' in section and '>Not now<' in section
        assert KEYS['CENTRAL_VAPID_PRIVATE_KEY'] not in page
        assert 'style=' not in section and '<script' not in section
        # under the top bar, before the page content
        assert page.index('</header>') < page.index('data-push-ask') < page.index('<main')


def test_no_bar_without_the_vapid_settings(world):
    page = world['clients']['owner'].get('/projects').get_data(as_text=True)
    assert 'data-push-ask' not in page and TEXT not in page


@pytest.mark.parametrize('missing', sorted(KEYS))
def test_no_bar_when_one_vapid_setting_is_missing(world, app, missing):
    app.config.update(dict(KEYS, **{missing: ''}))
    assert 'data-push-ask' not in world['clients']['owner'].get('/projects').get_data(as_text=True)


def test_no_bar_when_signed_out(app, keys):
    page = new_client(app).get('/signin').get_data(as_text=True)
    assert 'data-push-ask' not in page and TEXT not in page


def test_no_bar_on_the_account_page_which_has_its_own_section(world, keys):
    page = world['clients']['owner'].get('/account').get_data(as_text=True)
    assert 'data-push-ask' not in page and TEXT not in page
    assert 'id="push"' in page and 'data-push ' in page


def test_templates_have_no_inline_script_or_style():
    for path in TEMPLATES.glob('*.html'):
        text = path.read_text(encoding='utf-8')
        assert not re.search(r'\sstyle=|<style', text), path.name
        for tag in re.findall(r'<script\b[^>]*>', text):
            assert ' src=' in tag, path.name
        assert not re.search(r'\son[a-z]+=', text), path.name


def test_app_js_shares_enable_push_and_remembers_the_answer():
    js = (STATIC / 'app.js').read_text(encoding='utf-8')
    assert 'function enablePush(box)' in js
    assert js.count('enablePush(') >= 3            # the definition, the Account page and the bar
    for needle in ('data-push-ask', 'data-push-ask-on', 'data-push-ask-off', 'Notification.permission !== "denied"',
                   'localStorage.getItem("push-ask")', 'localStorage.setItem("push-ask", v)', 'askWrite("done")',
                   'askWrite("dismissed")', 'getSubscription()'):
        assert needle in js, needle
    # storage reads and writes are guarded
    assert re.search(r'try \{ return window\.localStorage\.getItem', js)
    assert re.search(r'try \{ window\.localStorage\.setItem', js)


def test_bar_styles_use_existing_tokens():
    css = (STATIC / 'style.css').read_text(encoding='utf-8')
    rules = re.findall(r'\.push-ask[^{]*\{[^}]*\}', css)
    assert rules
    for rule in rules:
        assert not re.search(r'#[0-9a-fA-F]{3,6}\b', rule), rule
    assert '.push-ask-in' in css and 'max-width:640px' in css
