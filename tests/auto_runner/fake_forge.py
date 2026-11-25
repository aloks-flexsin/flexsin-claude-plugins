"""An in-memory Forge for tests (no network). Records merges; like the real interface it cannot write a branch."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'auto-runner' / 'bin'))
from autoforge import Forge, ForgeError  # noqa: E402


class FakeForge(Forge):
    def __init__(self, prs=None, base=None):
        # prs: {number: {pr: {...}, files: [...], checks: [...], reviews: [...]}}
        self.prs = prs or {}
        self.merges = []
        self.base = base if base is not None else []      # CI on the base head: list of {name, state}

    def _get(self, number):
        if number not in self.prs:
            raise ForgeError('not found', 404)
        return self.prs[number]

    def pr(self, number):
        return dict(self._get(number)['pr'])

    def changed_files(self, number):
        return list(self._get(number).get('files', []))

    def checks(self, number):
        return list(self._get(number).get('checks', []))

    def base_checks(self, branch):
        return list(self.base)

    def reviews(self, number):
        return list(self._get(number).get('reviews', []))

    def merge(self, pr, sha, method):
        cur = self._get(pr)['pr']
        if cur['head_sha'] != sha:
            raise ForgeError('head moved', 409)
        self.merges.append((pr, sha, method))
        cur['merged'] = True
        cur['state'] = 'closed'
        return {'merged': True, 'sha': 'merge-' + sha}
