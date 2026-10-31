"""auto-runner plugin: artifact staleness and impact analysis (roadmap K5). Local files only, fake claude."""
import subprocess
import sys

import pytest

from tests.auto_runner.test_runner import BIN, BLOCKER, ar, blocking_agent, events, picking_agent, project, \
    project_statuses  # noqa: F401  (project is a fixture)
from tests.auto_runner.test_delivery import _raised

ab = ar.autoblock
ad = ar.autodeliver
st = ad.autostale


def _art(root, rel, body, built_from=()):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body.encode('utf-8'))                                               # bytes: keep CRLF exactly as given
    assert st.stamp(root, rel, built_from)
    return rel


def _tree(root):
    """spec -> plan -> {code, tests}; notes is a sibling built from brief, which plan was also built from."""
    _art(root, 'brief.md', 'the brief\n')
    _art(root, 'spec.md', 'the spec\n')
    _art(root, 'plan.md', 'the plan\n', ['spec.md', 'brief.md'])
    _art(root, 'code.md', 'the code\n', ['plan.md'])
    _art(root, 'tests.md', 'the tests\n', ['plan.md'])
    _art(root, 'notes.md', 'notes\n', ['brief.md'])
    return ['brief.md', 'spec.md', 'plan.md', 'code.md', 'tests.md', 'notes.md']


def _status(root, rel):
    return st.read(root, rel)[0]['status']


def test_header_round_trip_and_untracked_files(tmp_path):
    _art(tmp_path, 'a.md', 'body line\r\nsecond\n', ['x.md', 'y.md'])
    header, body = st.read(tmp_path, 'a.md')
    assert header == {'version': 1, 'hash': st.content_hash('body line\nsecond\n'), 'built_from': ['x.md', 'y.md'],
                      'status': 'valid'}
    assert body == 'body line\nsecond\n'
    assert st.parse(st.render(header, body)) == (header, body)
    (tmp_path / 'plain.md').write_text('---\ntitle: someone else\n---\ntext', encoding='utf-8')
    assert st.read(tmp_path, 'plain.md')[0] is None                                  # other front matter: not tracked
    assert st.stale(tmp_path, ['plain.md', 'missing.md', 'A2', '../outside.md']) == []
    with pytest.raises(ValueError, match='status'):
        st.parse('---\nartifact: v1\nstatus: rotten\n---\n')


def test_pure_walk_marks_exactly_the_descendants():
    graph = {'plan': ['spec', 'brief'], 'code': ['plan'], 'tests': ['plan'], 'notes': ['brief'], 'spec': [], 'brief': []}
    assert st.mark_descendants_stale(['spec'], graph) == ['code', 'plan', 'tests']
    assert st.mark_descendants_stale(['plan'], graph) == ['code', 'tests']           # not its sources
    assert st.mark_descendants_stale(['code'], graph) == []                          # a leaf: not its sibling tests
    assert st.mark_descendants_stale(['brief'], graph) == ['code', 'notes', 'plan', 'tests']


def test_changing_an_artifact_marks_its_descendants_stale_and_not_siblings(tmp_path):
    paths = _tree(tmp_path)
    assert st.scan(tmp_path, paths) == {'changed': [], 'stale': []}
    (tmp_path / 'spec.md').write_text(st.render(st.read(tmp_path, 'spec.md')[0], 'the spec, changed\n'), encoding='utf-8')
    assert st.changed(tmp_path, 'spec.md')
    r = st.scan(tmp_path, paths)
    assert r == {'changed': ['spec.md'], 'stale': ['code.md', 'plan.md', 'tests.md']}
    assert {p: _status(tmp_path, p) for p in paths} == {
        'brief.md': 'valid', 'spec.md': 'valid', 'plan.md': 'stale', 'code.md': 'stale', 'tests.md': 'stale', 'notes.md': 'valid'}
    spec = st.read(tmp_path, 'spec.md')[0]
    assert spec['version'] == 2 and spec['hash'] == st.content_hash('the spec, changed\n')
    assert st.scan(tmp_path, paths) == {'changed': [], 'stale': []}                  # re-running changes nothing


def test_same_content_is_a_no_op(tmp_path):
    paths = _tree(tmp_path)
    before = {p: (tmp_path / p).read_bytes() for p in paths}
    header, body = st.read(tmp_path, 'spec.md')
    (tmp_path / 'spec.md').write_bytes(st.render({**header, 'version': 9}, body).replace('\n', '\r\n').encode('utf-8'))
    assert not st.changed(tmp_path, 'spec.md')                                        # header edit and CRLF: same content
    assert st.scan(tmp_path, paths) == {'changed': [], 'stale': []}
    assert not st.stamp(tmp_path, 'plan.md')                                          # re-stamping the same body writes nothing
    assert {p: (tmp_path / p).read_bytes() for p in paths if p != 'spec.md'} == {p: b for p, b in before.items() if p != 'spec.md'}
    assert all(_status(tmp_path, p) == 'valid' for p in paths)


def test_cycles_are_reported_and_nothing_is_marked(tmp_path):
    with pytest.raises(st.CycleError, match='a -> b -> a'):
        st.mark_descendants_stale(['a'], {'a': ['b'], 'b': ['a']})
    _art(tmp_path, 'x.md', 'x\n', ['z.md'])
    _art(tmp_path, 'y.md', 'y\n', ['x.md'])
    _art(tmp_path, 'z.md', 'z\n', ['y.md'])
    header, _ = st.read(tmp_path, 'x.md')
    st.write(tmp_path, 'x.md', header, 'x changed\n')
    with pytest.raises(st.CycleError) as e:
        st.scan(tmp_path, ['x.md', 'y.md', 'z.md'])
    assert set(e.value.cycle) == {'x.md', 'y.md', 'z.md'}
    assert all(_status(tmp_path, p) == 'valid' for p in ('x.md', 'y.md', 'z.md'))
    assert st.read(tmp_path, 'x.md')[0]['hash'] == header['hash']                     # the change is not recorded either
    r = subprocess.run([sys.executable, str(BIN / 'autostale.py'), 'scan', 'x.md', 'y.md', 'z.md', '--root', str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and 'cycle' in r.stderr


def test_resume_is_refused_while_a_required_artifact_is_stale(tmp_path):
    auto = tmp_path / '.auto'
    _tree(tmp_path)
    st.write(tmp_path, 'plan.md', {**st.read(tmp_path, 'plan.md')[0], 'status': 'stale'}, 'the plan\n')
    _raised(auto, blocking_artifact={'path': 'plan.md', 'hash': 'x'}, downstream=['A2', 'code.md'])
    store = ab.Store(auto)
    store.answer('B-test1', 'pat', 'owner', option='Orange')
    t = ad.LocalTransport(auto)
    ad.pull(t, auto, 'proj')
    assert ad.task_answered(auto, 'A1') and not ad.resumable(auto, 'A1')
    with pytest.raises(st.StaleArtifact, match='plan.md') as e:
        ad.resume(t, auto, 'A1')
    assert e.value.paths == ['plan.md']
    assert store.load('B-test1')['state'] == 'DELIVERED' and t.row('B-test1.v1')['state'] != 'acknowledged'
    assert st.revalidate(tmp_path, 'plan.md') and not st.revalidate(tmp_path, 'plan.md')
    assert ad.resumable(auto, 'A1')
    packet, ids = ad.resume(t, auto, 'A1')
    assert ids == ['B-test1'] and store.load('B-test1')['state'] == 'RESUMED'


def test_runner_holds_a_stale_item_and_resumes_it_after_revalidation(project):
    root = project
    _art(root, 'spec.md', 'spec\n')
    _art(root, 'design.md', 'design\n', ['spec.md'])
    blocker = {**BLOCKER, 'blocking_artifact': {'path': 'design.md', 'hash': st.content_hash('design\n')}}
    assert ar.run(project, max_iterations=4, claude=blocking_agent('A1', blocker), allow_dirty=True) == 0
    auto = project / '.auto'
    bid = ab.Store(auto).all()[0][0]['id']
    ab.Store(auto).answer(bid, 'pat', 'product owner', option='Orange')
    st.write(root, 'spec.md', st.read(root, 'spec.md')[0], 'spec, revised\n')
    assert st.scan(root, ['spec.md', 'design.md'])['stale'] == ['design.md']

    calls = []
    assert ar.run(project, max_iterations=3, claude=picking_agent(calls), allow_dirty=True) == 0
    assert calls == []                                                                # never resumed on a stale design
    state, detail = project_statuses(project)['A1']
    assert state == 'blocked' and detail.startswith(ar.STALE_NOTE) and 'design.md' in detail
    assert ab.Store(auto).load(bid)['state'] == 'DELIVERED'
    stale_events = [e for e in events(project) if e['type'] == 'blocker' and e['detail']['action'] == 'stale']
    assert len(stale_events) == 1 and stale_events[0]['detail']['artifacts'] == ['design.md']

    assert ar.run(project, max_iterations=3, claude=picking_agent(calls), allow_dirty=True) == 0
    assert calls == []                                                                # still stale: still held, no new event
    assert len([e for e in events(project) if e['type'] == 'blocker' and e['detail']['action'] == 'stale']) == 1

    st.revalidate(root, 'design.md')
    assert ar.run(project, max_iterations=3, claude=picking_agent(calls), allow_dirty=True) == 0
    assert calls == ['A1'] and project_statuses(project)['A1'][0] == 'done'
    assert ab.Store(auto).load(bid)['state'] == 'RESUMED'


# --- Q11: sources_hash for docs that describe code ---

def test_header_without_sources_hash_renders_exactly_as_before(tmp_path):
    _art(tmp_path, 'a.md', 'body\n', ['x.md'])
    text = (tmp_path / 'a.md').read_text(encoding='utf-8')
    assert 'sources_hash' not in text and text.count('\n') == 8                       # 7 header lines + 1 body line


def test_revalidate_from_stamps_a_plain_doc_and_tracks_its_code(tmp_path):
    (tmp_path / 'run.py').write_text('x = 1\n', encoding='utf-8')
    (tmp_path / 'doc.md').write_text('# Doc\n', encoding='utf-8')
    with pytest.raises(ValueError, match='--from'):
        st.revalidate(tmp_path, 'doc.md')
    assert st.revalidate(tmp_path, 'doc.md', ['run.py'])
    header, body = st.read(tmp_path, 'doc.md')
    assert body == '# Doc\n' and header['built_from'] == ['run.py'] and header['status'] == 'valid'
    assert header['sources_hash'] == st.sources_hash(['run.py'], lambda p: (tmp_path / p).read_text(encoding='utf-8'))
    assert st.parse(st.render(header, body)) == (header, body)
    assert not st.revalidate(tmp_path, 'doc.md')                                     # unchanged: no write
    (tmp_path / 'run.py').write_text('x = 2\n', encoding='utf-8')
    assert st.revalidate(tmp_path, 'doc.md')                                         # code moved: the hash is refreshed
    assert st.read(tmp_path, 'doc.md')[0]['sources_hash'] != header['sources_hash']
    assert st.read(tmp_path, 'doc.md')[0]['version'] == header['version']             # the body did not change


def test_sources_hash_ignores_line_endings_and_artifact_headers():
    a = st.sources_hash(['a.py', 'gone.py'], {'a.py': 'x\r\ny\n'}.get)
    assert a == st.sources_hash(['gone.py', 'a.py'], {'a.py': 'x\ny\n'}.get)
    assert a != st.sources_hash(['a.py'], {'a.py': 'x\ny\n'}.get)                     # a missing file still counts
    art = st.render({'version': 1, 'hash': 'h', 'built_from': [], 'status': 'valid'}, 'body\n')
    assert st.sources_hash(['d.md'], {'d.md': art}.get) == st.sources_hash(
        ['d.md'], {'d.md': art.replace('version: 1', 'version: 7')}.get)


def test_scan_keeps_sources_hash(tmp_path):
    (tmp_path / 'run.py').write_text('x\n', encoding='utf-8')
    (tmp_path / 'doc.md').write_text('doc\n', encoding='utf-8')
    st.revalidate(tmp_path, 'doc.md', ['run.py'])
    kept = st.read(tmp_path, 'doc.md')[0]['sources_hash']
    header, _ = st.read(tmp_path, 'doc.md')
    (tmp_path / 'doc.md').write_text(st.render(header, 'doc, edited\n'), encoding='utf-8')
    assert st.scan(tmp_path, ['doc.md'])['changed'] == ['doc.md']
    assert st.read(tmp_path, 'doc.md')[0]['sources_hash'] == kept


def test_cli_revalidate_from(tmp_path, capsys):
    (tmp_path / 'a.py').write_text('a\n', encoding='utf-8')
    (tmp_path / 'b.py').write_text('b\n', encoding='utf-8')
    (tmp_path / 'doc.md').write_text('doc\n', encoding='utf-8')
    assert st.main(['revalidate', 'doc.md', '--root', str(tmp_path), '--from', 'a.py, b.py']) == 0
    assert 'revalidated' in capsys.readouterr().out
    assert st.read(tmp_path, 'doc.md')[0]['built_from'] == ['a.py', 'b.py']
    assert st.main(['revalidate', 'doc.md', '--root', str(tmp_path)]) == 0
    assert 'already valid' in capsys.readouterr().out
