"""Release-installed web result observations through native Hermes dispatch."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from scripts.build_release import build_release
from test_release import _fixture_repo

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = (
    'web-off', 'web-search', 'web-extract', 'web-private', 'web-no-consent',
    'web-policy-missing', 'web-apply', 'web-outage', 'web-cancel',
    'web-consent-revoke', 'web-source-change', 'web-observer-failure',
    'web-duplicate', 'web-coexist', 'web-local-rejection', 'web-missing-scope',
)


@unittest.skipUnless(importlib.util.find_spec('hermes_cli'), 'native Hermes runtime required')
class WebScreenNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix='web-screen-artifact-')
        cls.addClassCleanup(cls.fixture.cleanup)
        base = Path(cls.fixture.name)
        source, sha = _fixture_repo(base)
        cls.archive = build_release(source, base/'dist', sha)

    def exercise(self, scenario):
        with tempfile.TemporaryDirectory(prefix='web-screen-native-') as directory:
            base = Path(directory)
            home = base/'hermes'
            home.mkdir()
            env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(base),
                'HERMES_HOME': str(home), 'HERMES_PROFILE': 'default',
                'HERMES_SHARED_AUTH_DIR': str(base/'shared'),
                'HERMES_BUNDLED_PLUGINS': str(base/'empty-bundled'),
                'HERMES_ENABLE_PROJECT_PLUGINS': '0', 'PYTHONNOUSERSITE': '1',
                'TMPDIR': tempfile.gettempdir(), 'LANG': 'C.UTF-8'}
            for _ in range(2 if scenario == 'web-off' else 1):
                completed = subprocess.run([sys.executable, str(ROOT/'tests/web_screen_native_worker.py'),
                    str(self.archive), scenario], cwd=base, env=env, text=True,
                    capture_output=True, timeout=25)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                receipt = json.loads(completed.stdout.strip().splitlines()[-1])
                self.assertEqual(receipt['scenario'], scenario)
                self.assertEqual(receipt['installed_module'], 'plugins/hermes-switchyard/hermes_switchyard/__init__.py')
                self.assertFalse(receipt['mutated'])
                self.assertEqual(receipt['network'], 'socket.connect blocked')
                print('WEB_NATIVE_RECEIPT ' + json.dumps(receipt), flush=True)


def _case(scenario):
    def test(self):
        self.exercise(scenario)
    return test


for _scenario in SCENARIOS:
    setattr(WebScreenNativeTests, 'test_' + _scenario.replace('-', '_'), _case(_scenario))


if __name__ == '__main__':
    unittest.main()
