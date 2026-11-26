#!/usr/bin/env python3
"""Headless isolated tests: no SSH, registry, Docker daemon, or production paths."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).with_name("deploy-babbi-world.sh")
SHA = "a" * 40
DIGEST = "ghcr.io/ashbreaksthecode/babbi-world@sha256:" + "b" * 64
TOKEN = "ghs_synthetic_test_token_not_a_credential"
MOCK = r'''#!/usr/bin/env python3
import json, os, signal, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as f:
    f.write(json.dumps({'tool': os.path.basename(sys.argv[0]), 'args': args,
                        'image': os.environ.get('BABBI_WORLD_IMAGE')}) + '\n')
if os.path.basename(sys.argv[0]) == 'curl':
    sys.exit(1 if os.environ.get('FAIL_HEALTH') else 0)
if args[0] == 'login':
    sys.stdin.read()
    sys.exit(1 if os.environ.get('FAIL_LOGIN') else 0)
if args[0] == 'pull':
    sys.exit(1 if os.environ.get('FAIL_PULL') else 0)
if args[:2] == ['image', 'inspect']:
    fmt = args[-1]
    if 'revision' in fmt: print('a' * 40)
    elif 'source' in fmt:
        print('https://github.com/' + ('wrong/repository' if os.environ.get('WRONG_SOURCE') else 'AshBreaksTheCode/babbi.world'))
    else: print('ghcr.io/ashbreaksthecode/babbi-world@sha256:' + 'b' * 64)
elif args[0] == 'inspect':
    print('a' * 40 if 'revision' in args[-1] else 'sha256:' + 'c' * 64)
elif args[0] == 'compose' and 'ps' in args:
    if not os.environ.get('ABSENT') and (not os.environ.get('STOPPED') or '--all' in args):
        print('babbi-test-container')
elif args[0] == 'compose' and 'up' in args and '@sha256:' in os.environ.get('BABBI_WORLD_IMAGE', ''):
    if os.environ.get('TERMINATE_UP'):
        os.kill(os.getppid(), signal.SIGTERM)
    if os.environ.get('FAIL_UP'):
        sys.exit(1)
'''


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = self.root / "calls"
        self.calls.touch()
        for name in ["docker", "curl"]:
            path = self.root / name
            path.write_text(MOCK)
            path.chmod(0o755)
        script = SOURCE.read_text()
        script = script.replace("export PATH=/usr/sbin:/usr/bin:/sbin:/bin", f"export PATH={self.root}:{os.environ['PATH']}")
        script = script.replace("/run/lock/babbi-world-deploy.lock", str(self.root / "lock"))
        script = script.replace("/run/babbi-world-registry.XXXXXX", str(self.root / "registry.XXXXXX"))
        script = script.replace("cd /var/www/containers", f"cd {self.root}")
        self.script = self.root / "deploy.sh"
        self.script.write_text(script)

    def run_deploy(self, command=None, token=TOKEN, **flags):
        env = dict(os.environ, CALLS=str(self.calls), SSH_ORIGINAL_COMMAND=command if command is not None else f"deploy {SHA} kungfusaini", **flags)
        result = subprocess.run(["bash", str(self.script)], input=token + "\n", text=True, capture_output=True, env=env, timeout=10)
        self.assertNotIn(TOKEN, result.stdout + result.stderr + self.calls.read_text())
        self.assertEqual(list(self.root.glob("registry.*")), [], "registry credentials not cleaned")
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return result, calls

    def test_rejects_other_commands_before_credentials_or_docker(self):
        for command in ["", "id", "sftp", f"deploy {SHA} actor;id", "deploy main actor", f"deploy {SHA} ../actor"]:
            with self.subTest(command=command):
                result, calls = self.run_deploy(command)
                self.assertEqual(result.returncode, 64)
                self.assertEqual(calls, [])

    def test_bot_actor_is_accepted(self):
        result, calls = self.run_deploy(command=f"deploy {SHA} dependabot[bot]")
        self.assertEqual(result.returncode, 0, result.stderr)
        login = next(c for c in calls if c['args'][0] == 'login')
        self.assertIn('dependabot[bot]', login['args'])

    def test_rejects_missing_token(self):
        result, calls = self.run_deploy(token="")
        self.assertEqual(result.returncode, 64)
        self.assertEqual(calls, [])

    def test_deploy_pins_digest_and_only_updates_babbi(self):
        result, calls = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        updates = [c for c in calls if c['tool'] == 'docker' and c['args'][0] == 'compose' and 'up' in c['args']]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]['image'], DIGEST)
        self.assertEqual(updates[0]['args'][-1], 'babbi_world')
        self.assertIn('--no-deps', updates[0]['args'])
        self.assertIn('never', updates[0]['args'])
        self.assertIn(SHA, result.stdout)

    def test_untrusted_image_rejected_before_service_changes(self):
        result, calls = self.run_deploy(WRONG_SOURCE="1")
        self.assertEqual(result.returncode, 65)
        self.assertFalse(any(c['args'][0] == 'compose' for c in calls))

    def test_pull_failure_does_not_touch_running_site(self):
        result, calls = self.run_deploy(FAIL_PULL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c['args'][0] == 'compose' for c in calls))

    def test_health_failure_rolls_back_exact_previous_image(self):
        result, calls = self.run_deploy(FAIL_HEALTH="1")
        self.assertNotEqual(result.returncode, 0)
        updates = [c for c in calls if c['args'][0] == 'compose' and 'up' in c['args']]
        self.assertEqual([c['image'] for c in updates], [DIGEST, 'sha256:' + 'c' * 64])
        self.assertIn('restoring', result.stderr)

    def test_stopped_site_can_be_recovered(self):
        result, calls = self.run_deploy(STOPPED="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        discovery = next(c for c in calls if c['args'][0] == 'compose' and 'ps' in c['args'])
        self.assertIn('--all', discovery['args'])

    def test_absent_site_can_be_created(self):
        result, calls = self.run_deploy(ABSENT="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c['args'][0] == 'tag' for c in calls))

    def test_termination_after_replacement_attempt_rolls_back(self):
        result, calls = self.run_deploy(TERMINATE_UP="1")
        self.assertEqual(result.returncode, 143)
        updates = [c for c in calls if c['args'][0] == 'compose' and 'up' in c['args']]
        self.assertEqual([c['image'] for c in updates], [DIGEST, 'sha256:' + 'c' * 64])

    def test_failed_recreation_rolls_back(self):
        result, calls = self.run_deploy(FAIL_UP="1")
        self.assertNotEqual(result.returncode, 0)
        updates = [c for c in calls if c['args'][0] == 'compose' and 'up' in c['args']]
        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[-1]['image'], 'sha256:' + 'c' * 64)


if __name__ == "__main__":
    unittest.main()
