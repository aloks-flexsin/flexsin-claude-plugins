"""Central service: the board and task pages never scroll sideways (V1)."""
import re
from pathlib import Path

from .test_repo_url import conn, world  # noqa: F401  (fixtures)
from .test_tasks import add, clock, team  # noqa: F401  (fixtures)

URL = '/projects/alpha/tasks'
CSS = (Path(__file__).resolve().parents[2] / 'auto-runner' / 'central' / 'static' / 'style.css').read_text(encoding='utf-8')


def main_tag(page):
    return re.search(r'<main[^>]*>', page).group(0)


def test_board_main_is_wide_and_list_is_not(team):
    add(team, title='One')
    c = team['clients']['dev']
    assert 'wide' in main_tag(c.get(URL + '?view=board').get_data(as_text=True)).split('"')[1].split()
    assert 'wide' not in main_tag(c.get(URL + '?view=list').get_data(as_text=True))
    assert 'wide' not in main_tag(c.get('/my').get_data(as_text=True))


def rule(selector):
    """Every declaration block whose selector is exactly `selector`, outside any @media block."""
    top = re.sub(r'@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}', '', CSS)
    return ''.join(re.findall(r'(?:^|\})\s*' + re.escape(selector) + r'\s*\{([^}]*)\}', top))


def test_style_rules_keep_the_board_inside_its_own_scroll():
    assert 'overflow-x:auto' in rule('.board')
    assert 'repeat(6,minmax(200px,1fr))' in rule('.board')
    assert 'max-width:1440px' in rule('main.wide')
    assert 'align-items:flex-start' in rule('.board-card')
    assert 'overflow-wrap:anywhere' in rule('.page-title')
    assert 'overflow-wrap:anywhere' in rule('.row-title')


def test_board_card_puts_runner_and_pr_on_own_lines(team, conn):
    number = add(team, title='Has runner and PR')
    conn.execute("UPDATE project_task SET status = 'queued', claim_id = 'c1', claim_runner = ?, pr_url = ?"
                 " WHERE project_id = ? AND number = ?",
                 ('alok/bhawish-claude-auto-runner', 'https://github.com/o/r/pull/3', team['alpha']['id'], number))
    conn.commit()
    page = team['clients']['dev'].get(URL + '?view=board').get_data(as_text=True)
    card = re.search(r'<div class="board-card">(.*?)</div>\s*</section>', page, re.S).group(1)
    assert 'bhawish' in card and 'pull/3' in card
    assert not re.search(r'>\s*·', card)
    assert card.count('<div class="row-sub') == 3


def test_task_page_renders_a_plan_table(app, team):
    from .test_tasks import api, take
    n = add(team)
    claim = take(app, team)['claim']
    api(app, 'POST', '/api/v1/tasks/%d/plan' % n, team['token']['dev'],
        {'claim': claim, 'plan': '## Reuse check\n| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |', 'items': '- [ ] **E1 Export.** ...'})
    page = team['clients']['dev'].get('/projects/alpha/tasks/%d' % n).get_data(as_text=True)
    assert '<table' in page and 'table-wrap' in page
