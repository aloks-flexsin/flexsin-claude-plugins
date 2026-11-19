"""
Journey deck: a slide deck of the app's user journeys, kept up to date by the journey check.

After a journey check that produced valid results, `update(...)` takes every journey that passed and replaces its slides
with the new screenshots; a journey that failed keeps its last passing slides, marked with a red "Failing since <time>"
banner (the failed step, expected and seen) on its cover and step slides until it passes again. A journey that has never
passed appears as a cover slide with that banner and no screenshots.
It then writes `.auto/journeys/deck.html`: one self-contained page (no network, no external files except the copied
screenshots next to it), a title slide, then per journey a cover slide (name, slice, what a person must see) and one slide
per step (screenshot, the step as planned, what was expected and seen). Arrow keys, Page Up/Down and the space bar move
between slides; without JavaScript it is a scrolling page.

Files (all under `.auto/journeys/`, which is not committed):
  deck.json             schema `deck.v1`: {"schema", "updated", "journeys": {"<LETTER>:<Jn>": {letter, id, name, slice,
                        expect, request, key, updated, steps: [{n, step, expected, seen, image}], last_result,
                        last_checked, and while it fails failing_since and failure {n, expected, seen, key}}}}
  deck/<LETTER>/<Jn>/   the copied screenshots (`<n>.<ext>`), replaced as a whole when the journey passes again
  deck.html             the deck itself

Text from the results is redacted with the check's generated secrets. Screenshots cannot be redacted: journeys must run on
test data only. Stdlib only.
"""
import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 'deck.v1'
FOLDER = Path('.auto') / 'journeys'
IMAGE_TYPES = ('.png', '.jpg', '.jpeg', '.gif', '.webp')


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def load(project):
    try:
        data = json.loads((Path(project) / FOLDER / 'deck.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or not isinstance(data.get('journeys'), dict):
        return {'schema': SCHEMA, 'updated': '', 'journeys': {}}
    return data


def _order(entry):
    jid = entry.get('id', '')
    return (entry.get('letter', ''), int(jid[1:]) if jid[1:].isdigit() else 0)


def update(project, letter, key, journeys, evidence, request='', redact=None, now=None):
    """Put the passing journeys of one check into the deck and rewrite deck.html.
    `journeys`: the journey definitions checked (from autojourney.load); `evidence`: the results folder in the project,
    relative to it (`.auto/evidence/<key>/journeys`). -> the ids whose slides were replaced."""
    project = Path(project)
    redact = redact or (lambda s: s)
    when = now or _now()
    folder = project / evidence
    data = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
    defs = {j['id']: j for j in journeys}
    deck = load(project)
    replaced = []
    for res in data.get('journeys', []):
        jid = res.get('id')
        if jid not in defs:
            continue
        j = defs[jid]
        if res.get('status') != 'pass':
            entry = deck['journeys'].get(f'{letter}:{jid}') or {
                'letter': letter, 'id': jid, 'name': redact(j['name']), 'slice': j['slice'],
                'expect': redact(j['expect']), 'request': request, 'key': '', 'updated': '', 'steps': []}
            step = next((s for s in res.get('steps', []) if s.get('status') == 'fail'), {})
            entry.update({'last_result': 'fail', 'last_checked': when, 'failing_since': entry.get('failing_since') or when,
                          'failure': {'n': step.get('n'), 'expected': redact(step.get('expected', '')),
                                      'seen': redact(step.get('seen', '')), 'key': key}})
            deck['journeys'][f'{letter}:{jid}'] = entry
            continue
        target = project / FOLDER / 'deck' / letter / jid
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        steps = []
        for s in sorted(res['steps'], key=lambda s: s['n']):
            src = (folder / s['screenshot']).resolve()
            image = ''
            if src.is_file() and folder.resolve() in src.parents and src.suffix.lower() in IMAGE_TYPES:
                name = f'{s["n"]}{src.suffix.lower()}'
                shutil.copyfile(src, target / name)
                image = f'deck/{letter}/{jid}/{name}'
            n = s['n']
            planned = j['steps'][n - 1] if isinstance(n, int) and 0 < n <= len(j['steps']) else ''
            steps.append({'n': n, 'step': redact(planned), 'expected': redact(s['expected']), 'seen': redact(s['seen']),
                          'image': image})
        deck['journeys'][f'{letter}:{jid}'] = {
            'letter': letter, 'id': jid, 'name': redact(j['name']), 'slice': j['slice'], 'expect': redact(j['expect']),
            'request': request, 'key': key, 'updated': when, 'steps': steps, 'last_result': 'pass', 'last_checked': when}
        replaced.append(jid)
    deck['updated'] = when
    out = project / FOLDER
    out.mkdir(parents=True, exist_ok=True)
    (out / 'deck.json').write_text(json.dumps(deck, indent=2) + '\n', encoding='utf-8')
    (out / 'deck.html').write_text(render(deck, project.name), encoding='utf-8')
    return replaced


STYLE = """
:root{--bg:#0f1218;--slide:#fff;--ink:#1b2330;--muted:#667085;--line:#e3e7ee;--brand:#3b5bdb;--ok:#2b8a3e}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.slide{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:28px;scroll-snap-align:start}
html{scroll-snap-type:y mandatory}
.card{background:var(--slide);color:var(--ink);border-radius:14px;width:min(1200px,100%);padding:32px 36px;
 box-shadow:0 10px 40px rgba(0,0,0,.35)}
.kicker{color:var(--brand);font-weight:700;font-size:13px;letter-spacing:.08em;text-transform:uppercase;margin:0 0 6px}
h1{font-size:40px;line-height:1.15;margin:0 0 12px;letter-spacing:-.02em}h2{font-size:26px;margin:0 0 14px}
.muted{color:var(--muted)}.expect{font-size:20px;border-left:4px solid var(--ok);padding:6px 14px;margin:18px 0 0}
.step{display:grid;grid-template-columns:minmax(0,3fr) minmax(220px,1fr);gap:26px;align-items:start}
.shot{width:100%;border:1px solid var(--line);border-radius:10px;display:block;background:#f5f6f8}
.noshot{border:1px dashed var(--line);border-radius:10px;padding:60px 20px;text-align:center;color:var(--muted)}
dl{margin:0}dt{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:14px 0 2px}
dd{margin:0}ol{padding-left:22px}li{margin:4px 0}.toc a{color:var(--brand);text-decoration:none}
.failing{background:#fff0f0;color:#a61e1e;border:1px solid #e03131;border-radius:8px;padding:10px 14px;margin:0 0 16px}
.tag{background:#fff0f0;color:#c92a2a;border-radius:999px;padding:1px 8px;font-size:12px;font-weight:700}
.foot{position:fixed;right:16px;bottom:12px;color:#98a2b3;font-size:12px}
@media (max-width:760px){.step{grid-template-columns:1fr}.card{padding:22px}h1{font-size:30px}}
@media print{html{scroll-snap-type:none}.slide{page-break-after:always;min-height:auto}.foot{display:none}}
"""

SCRIPT = """
(function(){var s=[].slice.call(document.querySelectorAll('.slide')),f=document.getElementById('pos');
function cur(){var y=window.scrollY+window.innerHeight/3,i=0;s.forEach(function(e,k){if(e.offsetTop<=y)i=k});return i}
function go(i){i=Math.max(0,Math.min(s.length-1,i));s[i].scrollIntoView({behavior:'smooth'});}
function show(){f.textContent=(cur()+1)+' / '+s.length}
document.addEventListener('keydown',function(e){var k=e.key;
 if(k==='ArrowRight'||k==='ArrowDown'||k==='PageDown'||k===' '){e.preventDefault();go(cur()+1)}
 else if(k==='ArrowLeft'||k==='ArrowUp'||k==='PageUp'){e.preventDefault();go(cur()-1)}
 else if(k==='Home'){go(0)}else if(k==='End'){go(s.length-1)}});
window.addEventListener('scroll',show);show();})();
"""


def _banner(j):
    """The red banner on a journey that fails now ('' when it passed last time)."""
    if j.get('last_result') != 'fail':
        return ''
    e, f = html.escape, j.get('failure') or {}
    where = f' at step {e(str(f["n"]))}' if f.get('n') is not None else ''
    seen = f' Expected "{e(f.get("expected", ""))}", seen "{e(f.get("seen", ""))}".' if f.get('seen') else ''
    shots = (f' These screenshots are from its last pass ({e(j["updated"])}), not the app as it is now.' if j.get('steps')
             else ' It has never passed, so there are no screenshots.')
    return (f'<p class="failing" role="alert"><b>Failing since {e(j.get("failing_since", ""))}</b>{where} '
            f'(last checked {e(j.get("last_checked", ""))}).{seen}{shots}</p>')


def render(deck, title=''):
    e = html.escape
    entries = sorted(deck.get('journeys', {}).values(), key=_order)
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f'<title>{e(title or "Journeys")} journeys</title><style>{STYLE}</style></head><body>']
    toc = ''.join(f'<li><a href="#{e(j["letter"])}-{e(j["id"])}">{e(j["id"])} {e(j["name"])}</a> '
                  + ('<span class="tag">failing</span> ' if j.get('last_result') == 'fail' else '')
                  + f'<span class="muted">· slice {e(str(j["slice"]))} · '
                  f'{e(j["updated"] or "never passed")}</span></li>' for j in entries)
    parts.append(f'<section class="slide"><div class="card"><p class="kicker">User journeys</p>'
                 f'<h1>{e(title or "The app")}, as it last worked</h1>'
                 f'<p class="muted">Screenshots taken by the journey check on the running app. Updated {e(deck.get("updated") or "never")}.'
                 f' Each journey shows its last passing run.</p>'
                 + (f'<ol class="toc">{toc}</ol>' if toc else '<p>No journey has passed yet.</p>') + '</div></section>')
    for j in entries:
        anchor = f'{e(j["letter"])}-{e(j["id"])}'
        parts.append(f'<section class="slide" id="{anchor}"><div class="card"><p class="kicker">{e(j["id"])} · slice '
                     f'{e(str(j["slice"]))}</p><h1>{e(j["name"])}</h1>{_banner(j)}<p class="muted">{len(j["steps"])} '
                     f'steps · passed {e(j["updated"] or "never")}' + (f' · check {e(j["key"])}' if j['key'] else '')
                     + f'</p><p class="expect">{e(j["expect"])}</p></div></section>')
        for s in j['steps']:
            img = (f'<img class="shot" src="{e(s["image"])}" alt="{e(j["id"])} step {e(str(s["n"]))}">' if s['image']
                   else '<div class="noshot">No screenshot</div>')
            parts.append(f'<section class="slide"><div class="card"><p class="kicker">{e(j["id"])} {e(j["name"])} · step '
                         f'{e(str(s["n"]))} of {len(j["steps"])}</p>{_banner(j)}<div class="step"><div>{img}</div><dl>'
                         + (f'<dt>Step</dt><dd>{e(s["step"])}</dd>' if s['step'] else '')
                         + f'<dt>Expected</dt><dd>{e(s["expected"])}</dd><dt>Seen</dt><dd>{e(s["seen"])}</dd>'
                         '</dl></div></div></section>')
    parts.append(f'<div class="foot" id="pos"></div><script>{SCRIPT}</script></body></html>\n')
    return ''.join(parts)
