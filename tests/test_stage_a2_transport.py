"""Offline boundary checks for physical HTTP request guard. No Hermes or network calls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1] / "evaluation/jev_skills_comparison"
spec = importlib.util.spec_from_file_location("stage_a2_native_worker_transport", ROOT / "stage_a2_native_worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class DummyClient:
    def __init__(self, max_retries=0, hooks=True):
        self.max_retries = max_retries
        self._client = SimpleNamespace(event_hooks={"request": []} if hooks else None)
        self.sent = 0

    def post(self, path="/backend-api/codex/responses", host="chatgpt.com", method="POST"):
        request = SimpleNamespace(method=method, url=SimpleNamespace(host=host, path=path))
        for cb in self._client.event_hooks["request"]:
            cb(request)
        self.sent += 1


class TransportTests(unittest.TestCase):
    def test_one_actual_request_permitted_and_hooks_cleaned(self):
        client = DummyClient()
        with worker.one_http_post(client, "gpt-6-luna") as count:
            client.post()
            self.assertEqual(count["requests"], 1)
        self.assertEqual(client.sent, 1)
        self.assertEqual(client._client.event_hooks["request"], [])

    def test_second_request_blocked_even_within_one_logical_stream(self):
        client = DummyClient()
        with worker.one_http_post(client, "gpt-6-luna"):
            client.post()
            with self.assertRaisesRegex(RuntimeError, "Second physical HTTP"):
                client.post()
        self.assertEqual(client.sent, 1)

    def test_sdk_retries_must_be_disabled(self):
        for value in (None, 1, 2, False):
            client = DummyClient(max_retries=value)
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "SDK retries"):
                with worker.one_http_post(client, "gpt-6-luna"):
                    pass
            self.assertEqual(client.sent, 0)

    def test_uninstrumented_http_transport_is_rejected(self):
        client = DummyClient(hooks=False)
        with self.assertRaisesRegex(RuntimeError, "physical HTTP request limit"):
            with worker.one_http_post(client, "gpt-6-luna"):
                pass

    def test_nonapproved_destination_and_method_are_refused(self):
        for change in ({"host": "example.test"}, {"path": "/v1/other"}, {"method": "GET"}):
            client = DummyClient()
            with self.subTest(change=change), worker.one_http_post(client, "gpt-6-luna"):
                with self.assertRaisesRegex(RuntimeError, "Unexpected Codex"):
                    client.post(**change)
            self.assertEqual(client.sent, 0)
            self.assertEqual(client._client.event_hooks["request"], [])

    def test_codex_wire_must_omit_unsupported_output_cap(self):
        valid = {"model": "gpt-6.1-sol", "tools": []}
        worker.check_wire_payload(valid, "gpt-6.1-sol")
        for change in ({"model": "gpt-6-luna"}, {"tools": [{"type": "function"}]},
                       {"max_output_tokens": None}, {"max_output_tokens": 128},
                       {"max_output_tokens": 512}, {"previous_response_id": "old-response"}):
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "contract mismatch"):
                worker.check_wire_payload({**valid, **change}, "gpt-6.1-sol")

    def test_application_retries_reduced_to_one(self):
        agent = SimpleNamespace(_api_max_retries=3)
        worker.enforce_single_application_attempt(agent)
        self.assertEqual(agent._api_max_retries, 1)
        for value in (None, "3", False):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "retry setting"):
                worker.enforce_single_application_attempt(SimpleNamespace(_api_max_retries=value))


if __name__ == "__main__":
    unittest.main()
