#!/usr/bin/env python3
"""Offline tests for the example deployment client; no network or real secrets."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

path = Path(__file__).parents[2] / "examples/babbi-app/deploy.py"
spec = importlib.util.spec_from_file_location("babbi_deploy", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
DIGEST = "ghcr.io/ashbreaksthecode/financeapp@sha256:" + "b" * 64


class FakeAPI:
    def __init__(self, rootless=True, existing=False):
        self.calls = []
        self.rootless = rootless
        self.existing = existing

    def request(self, path, method="GET", data=None, **kwargs):
        self.calls.append((path, method, data, kwargs))
        if path == "/endpoints":
            return [{"Id": 1}]
        if path.endswith("/info"):
            return {"SecurityOptions": ["name=rootless"] if self.rootless else []}
        if path.endswith("/json") and "/images/" in path:
            return {"Id": "sha256:candidate", "RepoDigests": [DIGEST]}
        if path == "/stacks":
            return [{"Id": 2, "Name": "financeapp", "EndpointId": 1, "Env": [{"name": "DATABASE_PASSWORD", "value": "synthetic-example"}]}] if self.existing else []
        if "/containers/json?" in path:
            return [{"ImageID": "sha256:candidate", "State": "running"}]
        return {}


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.compose = Path(self.tmp.name) / "compose.yml"
        self.compose.write_text("services: {}")
        self.env = patch.dict(os.environ, {"REGISTRY_TOKEN": "", "REGISTRY_USER": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def deploy(self, api, name="financeapp"):
        module.deploy(api, name, "ghcr.io/ashbreaksthecode/financeapp:abcdef", self.compose)

    def test_names_cannot_override_infrastructure(self):
        for name in ["apps", "www", "demo", "mail", "../bad", "app;id"]:
            api = FakeAPI()
            with self.assertRaises(ValueError):
                self.deploy(api, name)
            self.assertEqual(api.calls, [])

    def test_non_rootless_environment_rejected(self):
        api = FakeAPI(rootless=False)
        with self.assertRaises(RuntimeError):
            self.deploy(api)
        self.assertFalse(any("images/create" in c[0] for c in api.calls))

    def test_create_uses_digest_and_one_app(self):
        api = FakeAPI()
        self.deploy(api)
        create = next(c for c in api.calls if c[0].startswith("/stacks/create"))
        self.assertEqual(create[2]["Name"], "financeapp")
        env = {e["name"]: e["value"] for e in create[2]["Env"]}
        self.assertEqual(env["APP_IMAGE"], DIGEST)
        self.assertEqual(env["APP_HOST"], "financeapp.babbi.world")
        self.assertFalse(any("%2F" in c[0] for c in api.calls if "/images/" in c[0] and c[0].endswith("/json")))

    def test_update_preserves_app_environment(self):
        api = FakeAPI(existing=True)
        self.deploy(api)
        update = next(c for c in api.calls if c[1] == "PUT")
        env = {e["name"]: e["value"] for e in update[2]["Env"]}
        self.assertEqual(env["DATABASE_PASSWORD"], "synthetic-example")
        self.assertFalse(update[2]["Prune"])

    def test_registry_credentials_stay_with_github(self):
        api = FakeAPI()
        with patch.dict(os.environ, {"REGISTRY_TOKEN": "synthetic-token", "REGISTRY_USER": "AshBreaksTheCode"}):
            self.deploy(api)
            pull = next(c for c in api.calls if "images/create?" in c[0])
            self.assertIn("X-Registry-Auth", pull[3]["extra_headers"])
            with self.assertRaises(ValueError):
                module.deploy(FakeAPI(), "financeapp", "untrusted.example/app:latest", self.compose)

    def test_missing_running_image_is_not_success(self):
        api = FakeAPI()
        original = api.request
        def request(path, *args, **kwargs):
            if "/containers/json?" in path:
                return []
            return original(path, *args, **kwargs)
        api.request = request
        with patch.object(module.time, "sleep"), self.assertRaises(RuntimeError):
            self.deploy(api)

    def test_https_required_for_remote_dashboard(self):
        for url in ["http://apps.babbi.world", "https://user:password@apps.babbi.world", "https://apps.babbi.world/?secret=x"]:
            with self.assertRaises(ValueError):
                module.Portainer(url, "synthetic-api-key")
        module.Portainer("https://apps.babbi.world", "synthetic-api-key")
        module.Portainer("http://127.0.0.1:19000", "synthetic-api-key")


if __name__ == "__main__":
    unittest.main()
