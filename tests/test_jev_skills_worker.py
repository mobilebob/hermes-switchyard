"""Offline worker unit contracts; no production modules or transports are loaded."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1] / "evaluation/jev_skills_comparison"
spec = importlib.util.spec_from_file_location("offline_shadow_worker", HERE / "worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
CATALOG = json.loads((HERE / "catalog.json").read_text())


class WorkerTests(unittest.TestCase):
    def test_catalog_requires_explicit_approval_and_offline_scope(self):
        for field in ("scope", "provider", "current"):
            value = json.loads(json.dumps(CATALOG))
            del value[field]
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError)):
                worker._catalog(value)
        value = json.loads(json.dumps(CATALOG))
        del value["models"][0]["approved"]
        with self.assertRaises(ValueError):
            worker._catalog(value)

    def test_catalog_rejects_cross_provider_duplicate_and_nonfinite(self):
        for update in ({"model": "other:small"}, {"cost": float("nan")}, {"id": "large"},
                       {"approved": False}, {"cost": True}, {"context_limit": 0}):
            value = json.loads(json.dumps(CATALOG))
            value["models"][0].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                worker._catalog(value)

    def test_stub_unavailability_is_counted_before_failure(self):
        calls = []
        stub = worker.FixtureClient("no-response", calls)
        with self.assertRaises(worker.FixtureNoResponse):
            stub.decide({"task": "public"}, {"fit": {"type": "noul"}}, public_or_sanitized_data_ack=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["outcome"], "fixture_no_response")

    def test_stub_requires_ack_and_bounds_payload(self):
        stub = worker.FixtureClient("synthetic-simple", [])
        with self.assertRaises(ValueError):
            stub.decide({}, {"fit": {"type": "noul"}})
        with self.assertRaises(ValueError):
            stub.decide("x" * worker.MAX_STUB_BYTES, {"fit": {"type": "noul"}}, public_or_sanitized_data_ack=True)

    def test_nested_applied_decision_rejected(self):
        with self.assertRaises(worker.RouteApplicationDenied):
            worker._assert_not_applied({"details": [{"applied": True}]})
        worker._assert_not_applied({"applied": False})

    def test_loader_skips_plugin_initializer_and_checks_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "shadow_test_package"
            package.mkdir()
            (package / "__init__.py").write_text("raise RuntimeError('plugin entrypoint must not run')\n")
            (package / "decision.py").write_text("marker = 'offline'\n")
            with patch.dict(sys.modules):
                module = worker._load_module(root, "shadow_test_package", "decision")
                self.assertEqual(module.marker, "offline")
                self.assertEqual(worker._verified_source_root(root, "shadow_test_package"), str(root))
                with self.assertRaises(RuntimeError):
                    worker._load_module(root, "shadow_test_package", "decision")

    def test_duplicate_keys_and_nonfinite_json_are_rejected(self):
        for raw in ('{"task": 1, "task": 2}', '{"cost": NaN}'):
            with self.assertRaises(ValueError):
                json.loads(raw, object_pairs_hook=worker._no_duplicates, parse_constant=worker._no_constant)

    def test_network_and_process_guards_are_counted_in_fresh_interpreter(self):
        code = (
            "import importlib.util,json,socket,subprocess; "
            f"s=importlib.util.spec_from_file_location('worker', {str(HERE / 'worker.py')!r}); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "counts={'network':0,'process':0}; m._deny_external_operations(counts)\n"
            "for op in (lambda:socket.create_connection(('example.com',443)), lambda:socket.getaddrinfo('example.com',443), lambda:subprocess.Popen(['false'])):\n"
            " try: op()\n"
            " except m.OfflineDenied: pass\n"
            "print(json.dumps(counts))\n"
        )
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), {"network": 2, "process": 1})


if __name__ == "__main__":
    unittest.main()
