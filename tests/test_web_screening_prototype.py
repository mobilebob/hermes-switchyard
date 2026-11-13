"""Mechanical offline tests; mock judgments are not evidence of model accuracy."""

import copy
import http.client
import json
from pathlib import Path
import threading
import time
import unittest
from unittest import mock

from hermes_switchyard.client import DecisionClient, TYPESAFE_ENDPOINT
from hermes_switchyard.web_screening_prototype import screen_result

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/fixtures/web_screening_prototype.json").read_text())["cases"]


def response(payload, expected):
    return {
        "model": "jev-1.14.0",
        "id": "synthetic-response",
        "answers": {
            key: {"noul": 0.9 if value else 0.1} for key, value in expected.items()
        },
        "usage": {"input_tokens": 100, "output_tokens": 9},
    }


class PrototypeTests(unittest.TestCase):
    def client(self, transport):
        return DecisionClient(
            api_key="synthetic-key",
            endpoint=TYPESAFE_ENDPOINT,
            model="jev-1.14.0",
            timeout=30,
            transport=transport,
        )

    def run_case(self, client, case=None, **options):
        case = case or CASES[0]
        return screen_result(
            client,
            case["question"],
            case["result"],
            enabled=True,
            public_source=True,
            public_or_sanitized_data_ack=True,
            **options,
        )

    def test_default_off_makes_no_request(self):
        client = self.client(mock.Mock(side_effect=AssertionError("must not send")))
        self.assertEqual(
            screen_result(client, CASES[0]["question"], CASES[0]["result"])["reason"],
            "disabled",
        )
        client.transport.assert_not_called()

    def test_six_fixtures_each_send_one_request_with_three_judgments(self):
        self.assertEqual(len(CASES), 6)
        for case in CASES:
            with self.subTest(case=case["id"]):
                seen = []
                original = copy.deepcopy(case["result"])

                def transport(payload):
                    seen.append(payload)
                    return response(payload, case["expected"])

                receipt = self.run_case(self.client(transport), case)
                self.assertEqual(len(seen), 1)
                self.assertEqual(
                    set(seen[0]["questions"]), {"relevance", "injection", "sufficiency"}
                )
                self.assertEqual(
                    set(seen[0]["state"]),
                    {"contract", "user_question", "untrusted_result"},
                )
                self.assertNotIn("expected", json.dumps(seen[0]))
                self.assertEqual(receipt["judgments"], case["expected"])
                self.assertEqual(receipt["request_count"], 1)
                self.assertEqual(receipt["retry_count"], 0)
                self.assertEqual(case["result"], original)

    def test_buried_answer_is_sent_in_full(self):
        case = next(c for c in CASES if c["id"] == "buried-answer")
        seen = []
        client = self.client(
            lambda payload: (seen.append(payload), response(payload, case["expected"]))[
                1
            ]
        )
        receipt = self.run_case(client, case)
        content = seen[0]["state"]["untrusted_result"]["content"]
        self.assertGreater(len(content), 12000)
        self.assertIn("23 degrees Celsius", content)
        self.assertTrue(receipt["judgments"]["sufficiency"])

    def test_no_source_or_consent_attestation_no_request(self):
        for options in ({"enabled": True}, {"enabled": True, "public_source": True}):
            transport = mock.Mock()
            result = screen_result(
                self.client(transport),
                CASES[0]["question"],
                CASES[0]["result"],
                **options,
            )
            self.assertEqual(result["request_count"], 0)
            transport.assert_not_called()

    def test_sensitive_content_stays_local(self):
        case = copy.deepcopy(CASES[0])
        case["result"]["content"] = "Company confidential: private material."
        transport = mock.Mock()
        receipt = self.run_case(self.client(transport), case)
        self.assertEqual(receipt["reason"], "local_input_rejected")
        transport.assert_not_called()

    def test_unsafe_url_stays_local(self):
        case = copy.deepcopy(CASES[0])
        case["result"]["url"] = "https://user:password@example.org/document"
        transport = mock.Mock()
        self.assertEqual(
            self.run_case(self.client(transport), case)["request_count"], 0
        )
        transport.assert_not_called()

    def test_oversized_result_is_not_silently_truncated(self):
        case = copy.deepcopy(CASES[0])
        case["result"]["content"] = "a" * 24001
        transport = mock.Mock()
        self.assertEqual(
            self.run_case(self.client(transport), case)["reason"],
            "local_input_rejected",
        )
        transport.assert_not_called()

    def test_transport_failure_preserves_original_and_never_retries(self):
        case = copy.deepcopy(CASES[0])
        original = copy.deepcopy(case)
        transport = mock.Mock(side_effect=TimeoutError("synthetic timeout"))
        receipt = self.run_case(self.client(transport), case)
        self.assertTrue(receipt["uncertain"])
        self.assertIsNone(receipt["judgments"])
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(case, original)

    def test_malformed_response_is_not_a_clean_negative(self):
        transport = mock.Mock(return_value={"model": "jev-1.14.0", "answers": {}})
        receipt = self.run_case(self.client(transport))
        self.assertIsNone(receipt["judgments"])
        self.assertTrue(receipt["uncertain"])
        self.assertEqual(transport.call_count, 1)

    def test_http_429_has_zero_transport_retries(self):
        client = self.client(None)
        with mock.patch.object(
            client, "_post_attempt", return_value=(None, 429, "0", False)
        ) as post:
            receipt = self.run_case(client)
        self.assertTrue(receipt["uncertain"])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(client.rate_limit_retries, 0)

    def test_stale_pooled_connection_is_not_replayed(self):
        client = self.client(None)
        stale = mock.Mock()
        stale.request.side_effect = http.client.RemoteDisconnected(
            "synthetic closed socket"
        )
        client._connection = stale
        with mock.patch(
            "hermes_switchyard.client.http.client.HTTPSConnection"
        ) as constructor:
            receipt = self.run_case(client)
        self.assertTrue(receipt["uncertain"])
        self.assertEqual(stale.request.call_count, 1)
        self.assertEqual(client.stale_connection_retries, 0)
        constructor.assert_not_called()

    def test_overall_deadline_returns_without_waiting_for_late_transport(self):
        completed = threading.Event()

        def late(payload):
            time.sleep(0.08)
            completed.set()
            return response(payload, CASES[0]["expected"])

        receipt = self.run_case(self.client(late), timeout_seconds=0.01)
        before = copy.deepcopy(receipt)
        self.assertEqual(receipt["reason"], "timeout_or_cancelled")
        self.assertTrue(receipt["uncertain"])
        self.assertTrue(completed.wait(1))
        time.sleep(0.02)
        self.assertEqual(receipt, before)
        self.assertIsNone(receipt["judgments"])

    def test_cancellation_before_request(self):
        transport = mock.Mock()
        receipt = self.run_case(self.client(transport), cancelled=lambda: True)
        self.assertEqual(receipt["reason"], "cancelled")
        transport.assert_not_called()

    def test_cancellation_predicate_error_fails_closed(self):
        transport = mock.Mock()

        def broken():
            raise RuntimeError("synthetic")

        self.assertEqual(
            self.run_case(self.client(transport), cancelled=broken)["reason"],
            "cancelled",
        )
        transport.assert_not_called()

    def test_worker_start_failure_is_normal_fallback(self):
        with mock.patch(
            "threading.Thread.start", side_effect=RuntimeError("synthetic")
        ):
            receipt = self.run_case(self.client(mock.Mock()))
        self.assertEqual(receipt["reason"], "worker_unavailable")
        self.assertEqual(receipt["request_count"], 0)

    def test_active_shared_request_budget_is_not_overridden(self):
        client = self.client(mock.Mock())
        with client.request_budget(1):
            receipt = self.run_case(client)
        self.assertEqual(receipt["reason"], "local_input_rejected")
        client.transport.assert_not_called()


    def test_metadata_and_encoded_sensitive_data_stay_local(self):
        for metadata in (
            {"contact": "person@example.org"},
            {"note": "api%5Fkey%3Dsynthetic-sensitive-value"},
            {"requires_auth": True},
            {"nested": {"private": True}},
            {"link": "https://example.org/page?session=synthetic"},
        ):
            with self.subTest(metadata=metadata):
                case = copy.deepcopy(CASES[0])
                case["result"]["metadata"] = metadata
                original = copy.deepcopy(case)
                transport = mock.Mock()
                receipt = self.run_case(self.client(transport), case)
                self.assertEqual(receipt["reason"], "local_input_rejected")
                self.assertEqual(case, original)
                transport.assert_not_called()

    def test_invalid_deadlines_stay_local(self):
        for timeout in (0, -1, 31, True, float("nan"), float("inf")):
            with self.subTest(timeout=timeout):
                transport = mock.Mock()
                receipt = self.run_case(self.client(transport), timeout_seconds=timeout)
                self.assertEqual(receipt["reason"], "local_input_rejected")
                transport.assert_not_called()

    def test_retry_budget_is_validated_and_restored(self):
        client = self.client(mock.Mock())
        for retries in (-1, 5, True, 0.5):
            with self.subTest(retries=retries), self.assertRaises(ValueError):
                with client.request_budget(1, max_transport_retries=retries):
                    self.fail("invalid budget accepted")
        with client.request_budget(1, max_transport_retries=0):
            self.assertEqual(client._retry_budget.get(), [0])
        self.assertIsNone(client._retry_budget.get())
        with client.request_budget(1):
            self.assertEqual(client._retry_budget.get(), [4])

    def test_fixture_urls_do_not_reveal_labels(self):
        for index, case in enumerate(CASES, 1):
            self.assertEqual(
                case["result"]["url"],
                f"https://example.org/prototype/document-{index:02d}",
            )


if __name__ == "__main__":
    unittest.main()
