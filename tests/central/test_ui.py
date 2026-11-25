"""Central service: the shared presentation helpers (UX audit, PR 1): times, status vocabulary, people, runners,
reasons, the Markdown subset (escape first), the served app.js, and the confirm step for actions that cannot be undone."""
import datetime

import pytest

from central import tasks, ui

NOW = datetime.datetime(2026, 11, 24, 12, 0, tzinfo=datetime.timezone.utc)


@pytest.mark.parametrize('ts,expected', [
    ('2026-11-24T11:59:40Z', 'just now'), ('2026-11-24T11:48:00Z', '12 min ago'), ('2026-11-24T09:00:00+00:00', '3 h ago'),
    ('2026-11-23T10:00:00Z', 'yesterday'), ('2026-11-20T12:00:00Z', '4 days ago'), ('2026-10-01T08:05:00Z', '1 Oct, 08:05'),
    ('', ''), ('not a time', '')])
def test_ago(app, ts, expected):
    with app.app_context():
        assert ui.ago(ts, NOW) == expected


def test_when_abs_and_duration(app):
    app.config['CLOCK'] = lambda: NOW
    with app.app_context():
        assert ui.when_abs('2026-11-24T09:58:00Z') == '24 Nov, 09:58'
        assert ui.when_abs('2025-03-02T07:00:00Z') == '2 Mar 2025, 07:00'
    assert ui.duration('2026-11-24T09:00:00Z', '2026-11-24T09:42:00Z') == '42 min'
    assert ui.duration('2026-11-24T09:00:00Z', '2026-11-24T10:05:00Z') == '1 h 05'


def test_every_task_status_has_a_label_and_tone():
    for s in tasks.STATUSES:
        got = ui.status('task', s)
        assert got['label'] and got['tone'] in ('neutral', 'info', 'attention', 'ok', 'bad')
    assert ui.status('task', 'plan_review')['tone'] == 'attention'
    assert ui.status('run', 'running')['tone'] == 'info'                      # running is not a warning
    assert ui.status('run', 'stopped')['shape'] == 'square'                   # shape as well as colour


def test_reasons_people_and_runners_in_words():
    assert ui.reason_text('red') == 'tests failing' and ui.reason_text('claim_lost') == 'taken back by an admin'
    assert ui.next_step('red').startswith('Fix the failing test')
    assert ui.reason_text('something_new') == 'something new'
    assert ui.display_name('alok.sharma@flexsin.com') == 'Alok Sharma'
    assert ui.display_name({'email': 'x@y.z', 'name': 'Alok S.'}) == 'Alok S.'
    assert ui.initials('Alok Sharma') == 'AS'
    assert ui.runner_label('alok/bhawish-claude-auto-runner') == 'Alok (bhawish account)'
    assert ui.runner_label('a runner') == 'a runner'


def test_markdown_subset():
    html = ui.md('## Intent\nLet users **download** `csv`.\n\n- [ ] **E1 Link.** x\n  *Acceptance:* a test\n1. one\n2. two')
    assert '<h4>Intent</h4>' in html and '<strong>download</strong>' in html and '<code>csv</code>' in html
    assert '<ul>\n<li><strong>E1 Link.</strong> x<br><em>Acceptance:</em> a test</li>\n</ul>' in html
    assert '<ol>\n<li>one</li>\n<li>two</li>\n</ol>' in html


@pytest.mark.parametrize('evil', ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>', '**<b onclick=x>y</b>**',
                                  '[a](javascript:alert(1))', '`<svg onload=x>`', '# <iframe src=//e>'])
def test_markdown_never_lets_markup_through(evil):
    out = ui.md(evil)
    assert '<script' not in out and '<img' not in out and '<iframe' not in out and '<svg' not in out
    assert 'onclick=' not in out.replace('onclick=x&gt;', '') or '&lt;b onclick' in out
    assert '<a ' not in out


TABLE = '| Need | Found | Verdict |\n| --- | :---: | --- |\n| Dates | `datetime` | **use** |\n| <b>x</b> | y |'


def test_markdown_table():
    out = ui.md('Before\n\n' + TABLE + '\n\nAfter')
    assert '<div class="table-wrap"><table><thead>' in out and out.count('<th>') == 3
    assert '<td><code>datetime</code></td><td><strong>use</strong></td>' in out
    assert '<td>&lt;b&gt;x&lt;/b&gt;</td><td>y</td><td></td>' in out        # escaped; short row padded
    assert '<p>Before</p>' in out and '<p>After</p>' in out


def test_markdown_italic_and_bold():
    out = ui.md('*Acceptance:* a test and **bold** and 2 * 3 * 4')
    assert '<em>Acceptance:</em>' in out and '<strong>bold</strong>' in out and '<em>bold</em>' not in out
    assert '2 * 3 * 4' in out


def test_markdown_fenced_block_is_literal():
    out = ui.md('```\n<script>x</script>\n**no** [a](https://e.com)\n```\nafter')
    assert '<pre><code>&lt;script&gt;x&lt;/script&gt;\n**no** [a](https://e.com)</code></pre>' in out
    assert '<script' not in out and '<p>after</p>' in out


def test_markdown_links_only_http():
    out = ui.md('[docs](https://example.com/a?b=1&c=2) and [x](javascript:alert(1)) and [y](//e.com)')
    assert '<a href="https://example.com/a?b=1&amp;c=2" rel="noopener noreferrer">docs</a>' in out
    assert out.count('<a ') == 1 and 'href="javascript' not in out


def test_app_js_is_served_and_loaded_by_every_page(client):
    js = client.get('/static/app.js')
    assert js.status_code == 200 and 'javascript' in js.headers['Content-Type']
    page = client.get('/signin').get_data(as_text=True)
    assert '<script src="/static/app.js" defer></script>' in page
