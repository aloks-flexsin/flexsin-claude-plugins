"""auto-runner plugin (0.13.0): the journey deck, a slide deck of the journeys' screenshots kept by the journey check.
Screenshots and the click-through are faked; no browser or model runs."""
import json

from tests.auto_runner.test_journeys import JOURNEYS, aj, clicker, hooks, journey_project, results
from tests.auto_runner.test_runner import ar, fake_agent, project  # noqa: F401  (project is a fixture)

ad = ar.autodeck
EVIDENCE = '.auto/evidence/T-slice-2/journeys'


def check(project, verdicts, key='T-slice-2', when='2026-11-19 10:00 UTC', redact=None):
    folder = project / f'.auto/evidence/{key}/journeys'
    results(folder, key, verdicts)
    return ad.update(project, 'T', key, aj.parse(JOURNEYS), f'.auto/evidence/{key}/journeys', redact=redact, now=when)


def test_a_passing_journey_gets_a_cover_and_one_slide_per_step(tmp_path):
    assert check(tmp_path, {'J1': ['pass'], 'J2': ['pass', 'pass']}) == ['J1', 'J2']
    deck = ad.load(tmp_path)
    j2 = deck['journeys']['T:J2']
    assert j2['name'] == 'Correction reaches the bell' and j2['expect'] == 'the bell shows the correction.'
    assert [s['step'] for s in j2['steps']] == ['As reviewer, file a correction.', 'As owner, open the home page.']
    assert all((tmp_path / '.auto/journeys' / s['image']).is_file() for s in j2['steps'])
    page = (tmp_path / '.auto/journeys/deck.html').read_text(encoding='utf-8')
    assert page.count('class="slide"') == 1 + 2 + 3          # title, two covers, three steps
    assert 'src="deck/T/J2/2.png"' in page and 'step 2 of 2' in page and 'http' not in page.split('<body>')[1]


def test_a_failed_journey_keeps_its_last_passing_slides(tmp_path):
    check(tmp_path, {'J1': ['pass'], 'J2': ['pass', 'pass']}, when='first')
    assert check(tmp_path, {'J1': ['pass'], 'J2': ['pass', 'fail']}, when='second') == ['J1']
    deck = ad.load(tmp_path)['journeys']
    assert deck['T:J1']['updated'] == 'second' and deck['T:J2']['updated'] == 'first'
    assert (tmp_path / '.auto/journeys/deck/T/J2/2.png').is_file()


def test_a_failing_journey_is_marked_until_it_passes_again(tmp_path):
    check(tmp_path, {'J1': ['pass'], 'J2': ['pass', 'pass']}, when='first')
    check(tmp_path, {'J1': ['pass'], 'J2': ['pass', 'fail']}, when='second')
    check(tmp_path, {'J1': ['pass'], 'J2': ['pass', 'fail']}, when='third')
    j2 = ad.load(tmp_path)['journeys']['T:J2']
    assert (j2['last_result'], j2['failing_since'], j2['last_checked']) == ('fail', 'second', 'third')
    assert j2['failure'] == {'n': 2, 'expected': 'step 2 shows it', 'seen': 'nothing', 'key': 'T-slice-2'}
    page = (tmp_path / '.auto/journeys/deck.html').read_text(encoding='utf-8')
    assert page.count('class="failing"') == 1 + 2                # J2's cover and both its step slides
    assert 'Failing since second</b> at step 2' in page and 'last pass (first)' in page and '>failing</span>' in page
    check(tmp_path, {'J1': ['pass'], 'J2': ['pass', 'pass']}, when='fourth')
    j2 = ad.load(tmp_path)['journeys']['T:J2']
    assert j2['last_result'] == 'pass' and 'failing_since' not in j2 and 'failure' not in j2
    assert 'class="failing"' not in (tmp_path / '.auto/journeys/deck.html').read_text(encoding='utf-8')


def test_a_journey_that_never_passed_shows_a_cover_with_the_banner(tmp_path):
    check(tmp_path, {'J1': ['pass'], 'J2': ['fail']}, when='first')
    j2 = ad.load(tmp_path)['journeys']['T:J2']
    assert j2['steps'] == [] and j2['failing_since'] == 'first'
    page = (tmp_path / '.auto/journeys/deck.html').read_text(encoding='utf-8')
    assert 'never passed, so there are no screenshots' in page and page.count('class="slide"') == 1 + 2 + 1


def test_text_is_redacted_and_escaped(tmp_path):
    folder = tmp_path / EVIDENCE
    data = results(folder, 'T-slice-2', {'J1': ['pass'], 'J2': ['pass', 'pass']})
    data['journeys'][1]['steps'][0]['seen'] = 'signed in with s3cret-pw <script>x</script>'
    (folder / 'results.json').write_text(json.dumps(data), encoding='utf-8')
    ad.update(tmp_path, 'T', 'T-slice-2', aj.parse(JOURNEYS), EVIDENCE, redact=lambda s: s.replace('s3cret-pw', '[x]'))
    page = (tmp_path / '.auto/journeys/deck.html').read_text(encoding='utf-8')
    assert 's3cret-pw' not in page and '&lt;script&gt;x' in page and '<script>x' not in page


def test_a_screenshot_outside_the_evidence_folder_is_not_copied(tmp_path):
    folder = tmp_path / EVIDENCE
    data = results(folder, 'T-slice-2', {'J1': ['pass'], 'J2': ['pass', 'pass']})
    (tmp_path / 'secret.png').write_bytes(b'x')
    data['journeys'][0]['steps'][0]['screenshot'] = '../../../../secret.png'
    (folder / 'results.json').write_text(json.dumps(data), encoding='utf-8')
    ad.update(tmp_path, 'T', 'T-slice-2', aj.parse(JOURNEYS), EVIDENCE)
    assert ad.load(tmp_path)['journeys']['T:J1']['steps'][0]['image'] == ''
    assert 'No screenshot' in (tmp_path / '.auto/journeys/deck.html').read_text(encoding='utf-8')


def test_an_empty_deck_still_renders():
    page = ad.render({'journeys': {}, 'updated': ''}, 'shop')
    assert 'No journey has passed yet' in page and page.count('class="slide"') == 1


def test_a_run_keeps_the_deck_up_to_date(project, capsys):
    journey_project(project)
    agent = clicker([{}, {}])                                     # both slices' journeys pass
    assert ar.run(project, max_iterations=2, claude=fake_agent(), journey_hooks=hooks(agent), allow_dirty=True) == 0
    deck = ad.load(project)['journeys']
    assert set(deck) == {'T:J1', 'T:J2'} and deck['T:J2']['key'] == 'T-slice-2'
    assert (project / '.auto/journeys/deck.html').is_file()
    assert 'journey deck updated (J1, J2)' in capsys.readouterr().out


def test_a_deck_that_cannot_be_written_never_stops_the_run(project, capsys, monkeypatch):
    journey_project(project)

    def broken(*a, **k):
        raise OSError('disk full')
    monkeypatch.setattr(ad, 'update', broken)
    assert ar.run(project, max_iterations=2, claude=fake_agent(), journey_hooks=hooks(clicker([{}, {}])),
                  allow_dirty=True) == 0
    assert 'journey deck not updated: OSError: disk full' in capsys.readouterr().out
