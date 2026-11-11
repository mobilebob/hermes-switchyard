"""Offline audit contracts; no Hermes installation or external calls required."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
import httpx

ROOT = Path(__file__).resolve().parents[1] / "evaluation/jev_skills_comparison"


def import_source(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = import_source("stage_a2_transport_audit")
worker = import_source("stage_a2_native_worker")


def sample_stream():
    max_stream_retries, model = 1, "test"
    return model


class DummyClientLifecycle:
    def _create_request_openai_client(self):
        request_kwargs = {"api_key": "not-a-real-key"}
        request_kwargs["max_retries"] = 0
        return request_kwargs


class AuditTests(unittest.TestCase):
    def test_outer_retry_count_from_ast(self):
        self.assertEqual(audit.inspect_outer_retries(sample_stream), 1)

    def test_sdk_client_retry_zero_from_ast(self):
        self.assertTrue(audit.sdk_retry_zero_from_source(DummyClientLifecycle._create_request_openai_client))

    def test_mock_httpx_transport_enforces_one_post(self):
        result = audit.mock_http_guard(worker, httpx)
        self.assertEqual(result["actual_http_requests"], 0)
        self.assertEqual(result["simulated_http_requests_sent"], 1)
        self.assertTrue(result["second_mock_http_blocked_before_send"])

    def test_source_binding_hashes_file(self):
        item = audit.source_binding(worker)
        self.assertEqual(len(item["sha256"]), 64)
        self.assertTrue(Path(item["file"]).exists())


if __name__ == "__main__":
    unittest.main()
