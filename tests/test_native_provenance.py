"""Actual Hermes provenance APIs in disposable homes; no positive pin fixtures."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import json
import os
import operator
from typing import Any, cast
from unittest import mock

from hermes_switchyard.native_provenance import NativeProvenanceAdapter, native_profile
from test_support import HermesHomeTestCase
from native_request_probe import build_native_request


class NativeProvenanceTests(HermesHomeTestCase):
    def setUp(self):
        super().setUp()
        if importlib.util.find_spec("hermes_constants") is None:
            self.skipTest("native Hermes profile/request APIs required")
        self.adapter = NativeProvenanceAdapter()
        self.context = {
            "model": "gpt-6-sol", "provider": "openai-codex", "base_url": "https://api.example.com/v1",
            "api_mode": "codex_responses", "session_id": "public-session", "task_id": "public-task",
            "turn_id": "public-turn", "api_request_id": "public-attempt", "platform": "cli",
        }
        self.request = {"model": "gpt-6-sol", "input": [{"role": "user", "content": "A public synthetic task"}], "reasoning": {"effort": "high"}}

    def read(self, **changes):
        return self.adapter.read(self.request, {**self.context, **changes})

    def test_real_request_builder_exports_only_available_authority(self):
        before = copy.deepcopy(self.request)
        outgoing, native = build_native_request(self.request, self.context, pinned=True)
        self.assertEqual(outgoing, before)
        self.assertNotIn("manual_model_pinned", native)
        self.assertNotIn("model_account", native)
        self.assertNotIn("_credential_pool_entry_id", native)
        result = self.adapter.read(outgoing, native)
        self.assertIsNone(result.reason)
        self.assertEqual(result.context["provider"], "openai-codex")
        self.assertIsNone(result.context["manual_model_pinned"])
        self.assertIsNone(result.context["model_account"])
        self.assertEqual(result.missing, ("manual_model_pin", "provider_account"))

    def test_actual_gateway_model_override_selection_and_removal_remain_unknown(self):
        from gateway.config import GatewayConfig, Platform
        from gateway.session import SessionSource, SessionStore
        store = SessionStore(self.hermes_home / "sessions", GatewayConfig())
        try:
            entry = store.get_or_create_session(SessionSource(platform=Platform.LOCAL, chat_id="fixture-cli"))
            for override in (
                {"model": "gpt-6-sol", "provider": "openai-codex"},
                {"model": "gpt-6-luna", "provider": "openai-codex"}, None,
            ):
                with self.subTest(override=override):
                    store.set_model_override(entry.session_key, override)
                    stored = store.get_model_override(entry.session_key)
                    self.assertEqual(stored is None, override is None)
                    outgoing, native = build_native_request(self.request, self.context, pinned=override is not None)
                    result = self.adapter.read(outgoing, native)
                    self.assertIsNone(result.context["manual_model_pinned"])
                    self.assertIsNone(result.context["model_account"])
        finally:
            for handle in store._db_handles.values():
                handle.close()

    def test_untrusted_flat_or_nested_claims_are_discarded(self):
        result = self.read(manual_model_pinned=False, model_account="fixture-account",
                           profile="fake-profile", native_provenance={"trusted": True}, cancelled=False)
        self.assertIsNone(result.context["manual_model_pinned"])
        self.assertIsNone(result.context["model_account"])
        self.assertNotIn("native_provenance", result.context)
        self.assertNotIn("profile", result.context)
        self.assertNotIn("cancelled", result.context)

    def test_default_and_named_profile_use_native_context_not_environment_claim(self):
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        profile = native_profile()
        assert profile is not None
        self.assertEqual(profile[1], "default")
        named = self.hermes_home / "profiles/fixture-named"
        named.mkdir(parents=True)
        with mock.patch.dict(os.environ, {"HERMES_PROFILE": "default"}):
            token = set_hermes_home_override(named)
            try:
                result = self.read()
                assert result.profile is not None
                self.assertEqual(result.profile[1], "fixture-named")
            finally:
                reset_hermes_home_override(token)
        profile = native_profile()
        assert profile is not None
        self.assertEqual(profile[1], "default")

    def test_conflicting_model_is_unknown_and_does_not_mutate_payload(self):
        before = copy.deepcopy(self.request)
        self.assertEqual(self.read(model="different-model").reason, "native_model_conflict")
        self.assertEqual(self.request, before)

    def test_missing_native_fields_do_not_get_inferred(self):
        for key in ("api_request_id", "task_id", "turn_id", "base_url", "provider", "session_id"):
            with self.subTest(key=key):
                self.assertEqual(self.read(**{key: None}).reason, "native_request_provenance_incomplete")

    def test_reused_attempt_identity_cannot_cross_session_or_provider(self):
        self.assertIsNone(self.read().reason)
        for change in ({"session_id": "another-session"}, {"provider": "another-provider"}, {"base_url": "https://other.example.com/v1"}):
            self.assertEqual(self.read(**change).reason, "native_attempt_conflict")

    def test_fresh_retry_preserves_new_native_provider_without_inventing_account(self):
        first = self.read()
        retry = self.read(provider="another-provider", api_request_id="retry-attempt")
        self.assertIsNone(retry.reason)
        self.assertNotEqual(retry.route_sha256, first.route_sha256)
        self.assertIsNone(retry.context["model_account"])

    def test_credential_entry_change_is_not_falsely_claimed_observable(self):
        snapshots = []
        for entry in ("fixture-first", "fixture-second"):
            outgoing, native = build_native_request(self.request, self.context, credential_entry=entry)
            snapshots.append(self.adapter.read(outgoing, native))
        self.assertIsNone(snapshots[0].context["model_account"])
        self.assertIsNone(snapshots[1].context["model_account"])
        self.assertEqual(snapshots[0].route_sha256, snapshots[1].route_sha256)

    def test_cancellation_rejects_old_attempt_but_allows_fresh_retry_metadata(self):
        self.read()
        self.adapter.invalidate()
        self.assertEqual(self.read().reason, "native_attempt_cancelled")
        self.assertIsNone(self.read(api_request_id="fresh-retry").reason)
        self.assertIsNone(self.read(api_request_id="fresh-retry").context["manual_model_pinned"])

    def test_concurrent_profile_contexts_do_not_clobber_each_other(self):
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        homes = [self.hermes_home / "profiles" / name for name in ("fixture-a", "fixture-b")]
        for home in homes:
            home.mkdir(parents=True)

        def inspect(home):
            token = set_hermes_home_override(home)
            try:
                return self.read()
            finally:
                reset_hermes_home_override(token)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(inspect, homes))
        assert all(result.profile is not None for result in results)
        self.assertEqual([result.profile[1] for result in results if result.profile is not None], ["fixture-a", "fixture-b"])
        self.assertNotEqual(results[0].route_sha256, results[1].route_sha256)
        self.assertTrue(all(result.reason is None for result in results))

    def test_profile_api_failure_is_unknown_not_environment_fallback(self):
        with mock.patch("hermes_constants.get_hermes_home", side_effect=RuntimeError("fixture")):
            result = self.read()
        self.assertIsNone(result.profile)
        self.assertEqual(result.reason, "native_profile_unavailable")

    def test_snapshot_is_read_only_and_receipt_has_no_path_or_credential(self):
        result = self.read(model_account="fixture-private-account")
        with self.assertRaises(TypeError):
            operator.setitem(cast(Any, result.context), "model", "other-model")
        receipt = json.dumps(result.receipt())
        self.assertNotIn(str(self.hermes_home), receipt)
        self.assertNotIn("fixture-private-account", receipt)

    def test_attempt_and_revocation_state_is_bounded(self):
        for number in range(self.adapter.LIMIT + 1):
            self.read(api_request_id=f"fixture-attempt-{number}")
        self.assertEqual(len(self.adapter.seen), self.adapter.LIMIT)
        self.adapter.invalidate()
        self.assertEqual(len(self.adapter.revoked), self.adapter.LIMIT)
