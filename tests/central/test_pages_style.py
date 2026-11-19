"""The pages share one stylesheet served from the app itself (the CSP allows only same-origin styles)."""
import re


def test_stylesheet_is_served_and_linked(client):
    page = client.get('/signin').get_data(True)
    assert 'href="/static/style.css"' in page
    css = client.get('/static/style.css')
    assert css.status_code == 200 and css.headers['Content-Type'].startswith('text/css')
    assert "style-src 'self'" in css.headers['Content-Security-Policy']


def test_no_template_uses_inline_styles():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2] / 'auto-runner' / 'central' / 'templates'
    for path in root.glob('*.html'):
        text = path.read_text(encoding='utf-8')
        assert not re.search(r'\sstyle=|<style|<script', text), path.name
