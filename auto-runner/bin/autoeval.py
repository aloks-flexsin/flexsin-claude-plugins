"""
Evaluation harness skeleton: run a benchmark through a runner, score the delivery against held-out tests, store the result.

    python autoeval.py run --benchmark ID --runner fake [--eval-root DIR] [--run NAME]
    python autoeval.py validate [--benchmark ID] [--eval-root DIR]
    python autoeval.py validate-results FILE [--eval-root DIR]

Layout under the eval root (default `plugins/auto-runner/eval/`):
    rubric.json                        the scoring dimensions (all eleven are required, see REQUIRED_DIMENSIONS)
    benchmarks/<id>/benchmark.json     id, shape, requirement file, criteria with IDs, scripted owner answers, expected fixtures
    benchmarks/<id>/expected/...       expected-outcome fixtures (what a correct delivery contains)
    heldout/<id>/*.json                held-out tests; never copied into the working directory handed to an agent
    results/<run>.json                 one result per run name; re-running unchanged input rewrites the same bytes

Rules:
- The agent gets a fresh temporary directory outside the eval root holding only the requirement file and `criteria.json`
  (criterion id, text, negative). Before the runner starts, the harness refuses if any file there equals a held-out file.
- A held-out test may have a `check` (`matches_expected`, `file_absent`, `file_contains`); one without is a written
  description only and is reported `not_run`, never passed.
- A criterion passes only when it has at least one executable held-out check and all of them pass; the runner's own
  approval never counts as a pass. An approval of a criterion whose held-out check failed is an incorrect approval.
- A score with nothing to measure is null with its reason, never 0 and never estimated. The evidence class of the fake
  runner is E0 (mock/fake); a fake result proves the wiring, not delivery quality.
- A baseline file (`baseline.v1`, procedure in `eval/BASELINE.md`, template `eval/results/template.json`) records
  manual real-model runs: model versions and settings, the run count, every run's raw result file and scores, and the
  variance per rubric dimension, which must agree with the runs. It is E2 or above and never holds targets.
Stdlib only.
"""
import argparse
import hashlib
import json
import shutil
import statistics
import sys
import tempfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent / 'eval'
BENCHMARK_FORMAT = 'benchmark.v1'
RESULT_FORMAT = 'result.v1'
REQUIRED_DIMENSIONS = (
    'functional_completeness', 'business_correctness', 'architecture', 'security', 'integration', 'reliability',
    'operational_readiness', 'human_rework', 'escaped_defects', 'cost', 'incorrect_approval_rate',
)
MEASURES = ('criteria_pass_rate', 'heldout_pass_rate', 'incorrect_approval_rate', 'reported')
CHECK_KINDS = ('matches_expected', 'file_absent', 'file_contains')
EVIDENCE_CLASS = {'fake': 'E0'}
BASELINE_FORMAT = 'baseline.v1'
BASELINE_EVIDENCE = ('E2', 'E3', 'E4')
VARIANCE_KEYS = ('n', 'mean', 'stdev', 'min', 'max')


class BenchmarkError(ValueError):
    """A benchmark, held-out test or rubric is invalid; the message names the reason."""


class IsolationError(RuntimeError):
    """A held-out file would have been visible to the agent."""


def _json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise BenchmarkError(f'{what}: file missing ({Path(path).name})')
    except ValueError as e:
        raise BenchmarkError(f'{what}: not valid JSON ({e})')


def _safe_rel(value, what):
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f'{what}: path missing')
    p = PurePosixPath(value.replace('\\', '/'))
    if p.is_absolute() or '..' in p.parts or ':' in value:
        raise BenchmarkError(f'{what}: path must be relative and stay inside its folder ({value})')
    return p.as_posix()


def _text(obj, key, what):
    v = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(v, str) or not v.strip():
        raise BenchmarkError(f'{what} has no {key}')
    return v.strip()


def load_rubric(root=DEFAULT_ROOT):
    """-> {dimension id: entry}. Every required dimension must be present exactly once with a known measure."""
    data = _json(Path(root) / 'rubric.json', 'rubric')
    dims = data.get('dimensions') if isinstance(data, dict) else None
    if not isinstance(dims, list):
        raise BenchmarkError('rubric has no dimensions list')
    out = {}
    for i, d in enumerate(dims, 1):
        did = _text(d, 'id', f'rubric dimension {i}')
        if did in out:
            raise BenchmarkError(f'rubric dimension {did} appears twice')
        if d.get('measure') not in MEASURES:
            raise BenchmarkError(f'rubric dimension {did} has unknown measure {d.get("measure")!r}')
        if d.get('better') not in ('higher', 'lower'):
            raise BenchmarkError(f'rubric dimension {did} must say better: higher or lower')
        if d['measure'] == 'reported':
            _text(d, 'key', f'rubric dimension {did}')
        out[did] = d
    missing = [k for k in REQUIRED_DIMENSIONS if k not in out]
    if missing:
        raise BenchmarkError('rubric is missing dimension(s): ' + ', '.join(missing))
    unknown = [k for k in out if k not in REQUIRED_DIMENSIONS]
    if unknown:
        raise BenchmarkError('rubric has unknown dimension(s): ' + ', '.join(unknown))
    return out


def load_benchmark(bench_id, root=DEFAULT_ROOT):
    """-> validated benchmark dict with `dir`, `heldout_dir` and `heldout` (list of held-out tests) added."""
    root = Path(root)
    bdir = root / 'benchmarks' / bench_id
    what = f'benchmark {bench_id}'
    data = _json(bdir / 'benchmark.json', what)
    if not isinstance(data, dict):
        raise BenchmarkError(f'{what}: benchmark.json must be an object')
    if data.get('format') != BENCHMARK_FORMAT:
        raise BenchmarkError(f'{what}: format must be {BENCHMARK_FORMAT}')
    if _text(data, 'id', what) != bench_id:
        raise BenchmarkError(f'{what}: id {data["id"]!r} does not match its folder')
    _text(data, 'shape', what)
    req = _safe_rel(data.get('requirement'), f'{what} requirement')
    if not (bdir / req).is_file():
        raise BenchmarkError(f'{what}: requirement file missing ({req})')

    crits = data.get('criteria')
    if not isinstance(crits, list) or not crits:
        raise BenchmarkError(f'{what} has no acceptance criteria')
    ids = []
    for i, c in enumerate(crits, 1):
        cid = _text(c, 'id', f'{what} criterion {i}')
        if cid in ids:
            raise BenchmarkError(f'{what}: criterion id {cid} appears twice')
        _text(c, 'text', f'{what} criterion {cid}')
        if not isinstance(c.get('negative', False), bool):
            raise BenchmarkError(f'{what} criterion {cid}: negative must be true or false')
        ids.append(cid)

    answers = data.get('owner_answers', [])
    if not isinstance(answers, list):
        raise BenchmarkError(f'{what}: owner_answers must be a list')
    for i, a in enumerate(answers, 1):
        _text(a, 'match', f'{what} owner answer {i}')
        _text(a, 'answer', f'{what} owner answer {i}')
        if not isinstance(a.get('sufficient', True), bool):
            raise BenchmarkError(f'{what} owner answer {i}: sufficient must be true or false')

    expected = data.get('expected', [])
    if not isinstance(expected, list):
        raise BenchmarkError(f'{what}: expected must be a list')
    expected = [_safe_rel(e, f'{what} expected fixture') for e in expected]
    for e in expected:
        if not (bdir / 'expected' / e).is_file():
            raise BenchmarkError(f'{what}: expected fixture missing ({e})')

    hdir = root / 'heldout' / bench_id
    files = sorted(hdir.glob('*.json')) if hdir.is_dir() else []
    if not files:
        raise BenchmarkError(f'{what} has no held-out test (heldout/{bench_id}/*.json)')
    heldout, seen = [], set()
    for f in files:
        h = _json(f, f'{what} held-out {f.name}')
        hid = _text(h, 'id', f'{what} held-out {f.name}')
        if hid in seen:
            raise BenchmarkError(f'{what}: held-out id {hid} appears twice')
        seen.add(hid)
        _text(h, 'description', f'{what} held-out {hid}')
        refs = h.get('criteria')
        if not isinstance(refs, list) or not refs:
            raise BenchmarkError(f'{what} held-out {hid} names no criterion')
        for r in refs:
            if r not in ids:
                raise BenchmarkError(f'{what} held-out {hid} names unknown criterion {r!r}')
        dims = h.get('dimensions', [])
        if not isinstance(dims, list) or any(d not in REQUIRED_DIMENSIONS for d in dims):
            raise BenchmarkError(f'{what} held-out {hid} has an unknown dimension')
        check = h.get('check')
        if check is not None:
            if not isinstance(check, dict) or check.get('kind') not in CHECK_KINDS:
                raise BenchmarkError(f'{what} held-out {hid} has an unknown check kind')
            path = _safe_rel(check.get('path'), f'{what} held-out {hid} check')
            if check['kind'] == 'matches_expected' and path not in expected:
                raise BenchmarkError(f'{what} held-out {hid} compares with {path}, which is not an expected fixture')
            if check['kind'] == 'file_contains':
                _text(check, 'text', f'{what} held-out {hid} check')
        heldout.append(dict(h, file=f.name))
    return dict(data, requirement=req, expected=expected, dir=bdir, heldout_dir=hdir, heldout=heldout)


def list_benchmarks(root=DEFAULT_ROOT):
    bdir = Path(root) / 'benchmarks'
    return sorted(p.name for p in bdir.iterdir() if (p / 'benchmark.json').is_file()) if bdir.is_dir() else []


class OwnerSimulator:
    """Scripted owner: answers a question when a scripted `match` occurs in it (case-insensitive); records every exchange."""

    def __init__(self, answers):
        self.answers = answers
        self.transcript = []

    def ask(self, question):
        q = str(question)
        hit = next((a for a in self.answers if a['match'].lower() in q.lower()), None)
        self.transcript.append({'question': q, 'answer': hit['answer'] if hit else None,
                                'sufficient': bool(hit) and hit.get('sufficient', True)})
        return hit['answer'] if hit else None


def agent_view(bench):
    """What the agent may see: the requirement and the criteria. Never held-out tests, expected fixtures or owner answers."""
    return {'id': bench['id'], 'shape': bench['shape'], 'requirement': PurePosixPath(bench['requirement']).name,
            'criteria': [{'id': c['id'], 'text': c['text'], 'negative': c.get('negative', False)} for c in bench['criteria']]}


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare_workdir(bench, workdir):
    """Fill `workdir` with the agent's view only and prove no held-out file is in it."""
    workdir = Path(workdir)
    view = agent_view(bench)
    shutil.copyfile(bench['dir'] / bench['requirement'], workdir / view['requirement'])
    (workdir / 'criteria.json').write_text(json.dumps(view['criteria'], indent=2) + '\n', encoding='utf-8')
    check_isolated(workdir, bench['heldout_dir'])
    return view


def check_isolated(workdir, heldout_dir):
    workdir, heldout_dir = Path(workdir).resolve(), Path(heldout_dir).resolve()
    if workdir == heldout_dir or heldout_dir in workdir.parents or workdir in heldout_dir.parents:
        raise IsolationError('the working directory overlaps the held-out folder')
    secret = {_digest(f): f.name for f in heldout_dir.rglob('*') if f.is_file()}
    for f in workdir.rglob('*'):
        if f.is_file() and _digest(f) in secret:
            raise IsolationError(f'held-out file {secret[_digest(f)]} is visible to the agent as {f.relative_to(workdir)}')


def oracle_runner(bench):
    """The `fake` runner: asks every scripted question, writes the expected fixtures and approves every criterion.
    It proves the harness wiring end to end; it says nothing about how well a real agent delivers."""
    def run(workdir, view, owner):
        for a in bench.get('owner_answers', []):
            owner.ask(a['match'])
        for rel in bench['expected']:
            dest = Path(workdir) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bench['dir'] / 'expected' / rel, dest)
        return {'approved': [c['id'] for c in view['criteria']]}
    return run


RUNNERS = {'fake': oracle_runner}


def run_check(h, workdir, bench):
    """-> (status, reason) for one held-out test against the delivered working directory."""
    check = h.get('check')
    if not check:
        return 'not_run', 'description only, no executable check'
    target = Path(workdir) / check['path']
    if check['kind'] == 'file_absent':
        return ('passed', '') if not target.exists() else ('failed', f'{check["path"]} exists')
    if not target.is_file():
        return 'failed', f'{check["path"]} missing'
    if check['kind'] == 'matches_expected':
        same = target.read_bytes() == (bench['dir'] / 'expected' / check['path']).read_bytes()
        return ('passed', '') if same else ('failed', f'{check["path"]} differs from the expected fixture')
    found = check['text'] in target.read_text(encoding='utf-8', errors='replace')
    return ('passed', '') if found else ('failed', f'{check["path"]} lacks the expected text')


def _rate(num, den, basis):
    return {'value': round(num / den, 4) if den else None, 'basis': basis if den else 'nothing to measure'}


def score(bench, rubric, heldout, approved, delivery):
    """-> (criteria, scores). `heldout` is the list of held-out results with their status."""
    criteria = {}
    for c in bench['criteria']:
        mine = [h for h in heldout if c['id'] in h['criteria'] and h['status'] != 'not_run']
        status = 'untested' if not mine else 'failed' if any(h['status'] == 'failed' for h in mine) else 'passed'
        criteria[c['id']] = {'status': status, 'approved': c['id'] in approved, 'negative': c.get('negative', False),
                             'heldout': sorted(h['id'] for h in heldout if c['id'] in h['criteria'])}
    judged = [v for v in criteria.values() if v['status'] != 'untested']
    scores = {}
    for did, d in rubric.items():
        m = d['measure']
        if m == 'criteria_pass_rate':
            n = sum(v['status'] == 'passed' for v in judged)
            scores[did] = _rate(n, len(judged), f'{n} of {len(judged)} tested criteria passed')
        elif m == 'heldout_pass_rate':
            tag = d.get('dimension')
            run = [h for h in heldout if h['status'] != 'not_run' and (tag is None or tag in h.get('dimensions', []))]
            n = sum(h['status'] == 'passed' for h in run)
            scores[did] = _rate(n, len(run), f'{n} of {len(run)} held-out checks passed')
        elif m == 'incorrect_approval_rate':
            appr = [v for v in judged if v['approved']]
            n = sum(v['status'] == 'failed' for v in appr)
            scores[did] = _rate(n, len(appr), f'{n} of {len(appr)} approved criteria failed held-out checks')
        else:
            v = delivery.get(d['key'])
            ok = isinstance(v, (int, float)) and not isinstance(v, bool)
            scores[did] = {'value': v if ok else None, 'basis': f'reported by the runner ({d["key"]})' if ok else 'not reported'}
    return criteria, scores


def input_hash(bench, root=DEFAULT_ROOT):
    """Hash of everything that defines the benchmark run: its folder, its held-out folder and the rubric."""
    h = hashlib.sha256()
    for base, label in ((bench['dir'], 'benchmark'), (bench['heldout_dir'], 'heldout')):
        for f in sorted(p for p in Path(base).rglob('*') if p.is_file() and '__pycache__' not in p.parts):
            h.update(f'{label}/{f.relative_to(base).as_posix()}\0'.encode())
            h.update(f.read_bytes())
    h.update(b'rubric\0' + (Path(root) / 'rubric.json').read_bytes())
    return h.hexdigest()


def run(bench_id, runner_name='fake', root=DEFAULT_ROOT, results_dir=None, run_name=None, runner=None):
    """Run one benchmark and write `results/<run>.json`. `runner` (a callable `(workdir, view, owner) -> dict`) may be
    injected; otherwise `runner_name` picks a built-in one. -> (result dict, result path)."""
    root = Path(root)
    rubric = load_rubric(root)
    bench = load_benchmark(bench_id, root)
    if runner is None:
        if runner_name not in RUNNERS:
            raise BenchmarkError(f'unknown runner {runner_name!r} (known: {", ".join(sorted(RUNNERS))})')
        runner = RUNNERS[runner_name](bench)
    owner = OwnerSimulator(bench.get('owner_answers', []))
    with tempfile.TemporaryDirectory(prefix='autoeval-') as tmp:
        workdir = Path(tmp)
        view = prepare_workdir(bench, workdir)
        delivery = runner(workdir, view, owner) or {}
        heldout = []
        for h in bench['heldout']:
            status, reason = run_check(h, workdir, bench)
            heldout.append({'id': h['id'], 'criteria': h['criteria'], 'dimensions': h.get('dimensions', []),
                            'status': status, 'reason': reason})
    approved = [a for a in delivery.get('approved', []) if isinstance(a, str)]
    criteria, scores = score(bench, rubric, heldout, approved, delivery)
    name = run_name or f'{bench_id}-{runner_name}'
    _safe_rel(name, 'run name')
    result = {
        'format': RESULT_FORMAT, 'run': name, 'benchmark': bench_id, 'shape': bench['shape'], 'runner': runner_name,
        'evidence_class': EVIDENCE_CLASS.get(runner_name, 'unknown'), 'input_hash': input_hash(bench, root),
        'criteria': criteria, 'heldout': heldout, 'scores': scores,
        'owner': {'asked': len(owner.transcript), 'unanswered': sum(t['answer'] is None for t in owner.transcript),
                  'insufficient': sum(t['answer'] is not None and not t['sufficient'] for t in owner.transcript),
                  'transcript': owner.transcript},
    }
    out_dir = Path(results_dir) if results_dir else root / 'results'
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f'{name}.json'
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return result, path


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def variance_of(values):
    """-> {n, mean, stdev, min, max} over the non-null values. stdev is the sample deviation and null below two runs."""
    xs = [float(v) for v in values if v is not None]
    if not xs:
        return {'n': 0, 'mean': None, 'stdev': None, 'min': None, 'max': None}
    return {'n': len(xs), 'mean': round(statistics.mean(xs), 4),
            'stdev': round(statistics.stdev(xs), 4) if len(xs) > 1 else None,
            'min': round(min(xs), 4), 'max': round(max(xs), 4)}


def validate_results(data, root=DEFAULT_ROOT):
    """Check a `baseline.v1` results file; raise BenchmarkError naming the first missing or wrong field. -> data."""
    what = 'results file'
    if not isinstance(data, dict):
        raise BenchmarkError(f'{what} must be an object')
    if data.get('format') != BASELINE_FORMAT:
        raise BenchmarkError(f'{what}: format must be {BASELINE_FORMAT}')
    if 'targets' in data:
        raise BenchmarkError(f'{what}: targets is not allowed; targets are set only after a baseline exists, never in it')
    bench = _text(data, 'benchmark', what)
    known = list_benchmarks(root)
    if bench not in known:
        raise BenchmarkError(f'{what}: benchmark {bench!r} is unknown (known: {", ".join(known)})')
    for key in ('plugin_version', 'date', 'operator'):
        _text(data, key, what)
    if data.get('evidence_class') not in BASELINE_EVIDENCE:
        raise BenchmarkError(f'{what}: evidence_class must be one of {", ".join(BASELINE_EVIDENCE)} '
                             '(a baseline is a real-model run; E0/E1 do not count)')

    models = data.get('models')
    if not isinstance(models, list) or not models:
        raise BenchmarkError(f'{what} has no models list')
    for i, m in enumerate(models, 1):
        _text(m, 'role', f'{what} model {i}')
        _text(m, 'model_version', f'{what} model {i}')
        if not isinstance(m.get('settings'), dict):
            raise BenchmarkError(f'{what} model {i} has no settings object')

    count = data.get('run_count')
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise BenchmarkError(f'{what} has no run_count (a whole number of at least 1)')
    runs = data.get('runs')
    if not isinstance(runs, list) or len(runs) != count:
        raise BenchmarkError(f'{what}: runs must list exactly run_count ({count}) runs')
    dims = list(load_rubric(root))
    names = set()
    for i, r in enumerate(runs, 1):
        name = _text(r, 'run', f'{what} run {i}')
        if name in names:
            raise BenchmarkError(f'{what}: run {name} appears twice')
        names.add(name)
        _safe_rel(r.get('raw_result'), f'{what} run {name} raw_result')
        sc = r.get('scores')
        if not isinstance(sc, dict):
            raise BenchmarkError(f'{what} run {name} has no scores object')
        for d in dims:
            if d not in sc:
                raise BenchmarkError(f'{what} run {name} scores is missing {d}')
            if sc[d] is not None and not _num(sc[d]):
                raise BenchmarkError(f'{what} run {name} score {d} must be a number or null')

    var = data.get('variance')
    if not isinstance(var, dict):
        raise BenchmarkError(f'{what} has no variance object')
    for d in dims:
        v = var.get(d)
        if not isinstance(v, dict):
            raise BenchmarkError(f'{what} variance is missing {d}')
        for k in VARIANCE_KEYS:
            if k not in v:
                raise BenchmarkError(f'{what} variance {d} is missing {k}')
        want = variance_of([r['scores'][d] for r in runs])
        for k in VARIANCE_KEYS:
            got = v[k]
            same = got is None and want[k] is None or _num(got) and want[k] is not None and abs(got - want[k]) <= 1e-4
            if not same:
                raise BenchmarkError(f'{what} variance {d} {k} is {got!r} but the runs give {want[k]!r}')
    return data


def summary(result):
    lines = [f'{result["run"]}: benchmark {result["benchmark"]} ({result["shape"]}), runner {result["runner"]}, '
             f'evidence {result["evidence_class"]}']
    for cid, c in result['criteria'].items():
        lines.append(f'  {cid}: {c["status"]}{" (approved by runner)" if c["approved"] else ""}')
    for did, s in result['scores'].items():
        lines.append(f'  {did}: {"n/a" if s["value"] is None else s["value"]} - {s["basis"]}')
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='autoeval.py', description='Evaluation harness for the auto-runner.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run', help='run one benchmark and store the scored result')
    r.add_argument('--benchmark', required=True)
    r.add_argument('--runner', default='fake', choices=sorted(RUNNERS))
    r.add_argument('--run', dest='run_name')
    r.add_argument('--eval-root', default=str(DEFAULT_ROOT))
    v = sub.add_parser('validate', help='validate the rubric and benchmarks')
    v.add_argument('--benchmark')
    v.add_argument('--eval-root', default=str(DEFAULT_ROOT))
    vr = sub.add_parser('validate-results', help='validate a baseline results file (baseline.v1)')
    vr.add_argument('file')
    vr.add_argument('--eval-root', default=str(DEFAULT_ROOT))
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'validate-results':
            data = validate_results(_json(a.file, 'results file'), a.eval_root)
            print(f'{a.file}: valid ({data["run_count"]} run(s) of {data["benchmark"]}, evidence {data["evidence_class"]})')
            return 0
        if a.cmd == 'run':
            result, path = run(a.benchmark, a.runner, a.eval_root, run_name=a.run_name)
            print(summary(result))
            print(f'Result written to {path}')
            return 0
        load_rubric(a.eval_root)
        ids = [a.benchmark] if a.benchmark else list_benchmarks(a.eval_root)
        for b in ids:
            load_benchmark(b, a.eval_root)
            print(f'{b}: valid')
        print(f'rubric: valid; {len(ids)} benchmark(s) checked')
        return 0
    except (BenchmarkError, IsolationError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
