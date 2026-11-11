"""Release-stabilization regression tests written before implementation."""
from __future__ import annotations

import io
import json
import math
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hermes_switchyard import schemas
from hermes_switchyard.automatic import AutomaticSkillRecommender
from hermes_switchyard.client import DecisionClient
from hermes_switchyard.routing import route_model, select_skill
from scripts.build_release import RELEASE_FILES
from scripts.ci import check_native_hermes


class _Response:
    def __init__(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None):
        self.body = body
        self.status = status
        self.reason = "fixture"
        self.headers = headers or {}
        self.will_close = False
        self.read_sizes: list[int | None] = []

    def read(self, size: int | None = None) -> bytes:
        self.read_sizes.append(size)
        if size is None:
            return self.body
        return self.body[:size]

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, response: _Response):
        self.response = response
        self.closed = False
        self.timeout = None

    def request(self, *_args, **_kwargs):
        pass

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class ResponseBoundaryTests(unittest.TestCase):
    def _client(self, response: _Response) -> DecisionClient:
        connection = _Connection(response)
        client = DecisionClient(api_key="fixture", timeout=2)
        client._connection = connection
        return client

    def test_provider_control_fields_are_removed_from_validated_response(self):
        client = DecisionClient(
            api_key="fixture",
            transport=lambda _payload: {
                "model": "typesafe/jev-1.13",
                "answers": {"answer": {"noul": 0.9}},
                "usage": {},
                "provider_control": {"override": "unsafe"},
                "system_fingerprint": "not-for-callers",
            },
        )
        result = client.decide(
            "public",
            {"answer": {"type": "noul", "instructions": "Is it true?"}},
            public_or_sanitized_data_ack=True,
        )
        self.assertNotIn("provider_control", result)
        self.assertNotIn("system_fingerprint", result)

    def test_duplicate_json_keys_are_rejected_before_typed_validation(self):
        body = b'{"model":"typesafe/jev-1.13","model":"typesafe/jev-1.13","answers":{}}'
        response = _Response(body)
        client = self._client(response)
        with mock.patch("hermes_switchyard.client.http.client.HTTPSConnection", return_value=client._connection):
            client._connection = None
            with self.assertRaises(RuntimeError):
                client.decide(
                    "public",
                    {"answer": {"type": "noul", "instructions": "Is it true?"}},
                    public_or_sanitized_data_ack=True,
                )

    def test_nonfinite_json_constants_are_rejected_even_in_unknown_fields(self):
        client = DecisionClient(
            api_key="fixture",
            transport=lambda _payload: {
                "model": "typesafe/jev-1.13",
                "answers": {"answer": {"noul": 0.9}},
                "usage": {},
                "diagnostic": math.nan,
            },
        )
        with self.assertRaises(ValueError):
            client.decide(
                "public",
                {"answer": {"type": "noul", "instructions": "Is it true?"}},
                public_or_sanitized_data_ack=True,
            )

    def test_http_body_is_read_with_a_hard_cap(self):
        from hermes_switchyard import client as module

        response = _Response(b"x" * (module.MAX_RESPONSE_BYTES + 1))
        connection = _Connection(response)
        with mock.patch.object(module.http.client, "HTTPSConnection", return_value=connection):
            client = DecisionClient(api_key="fixture")
            with self.assertRaises(RuntimeError):
                client.decide(
                    "public",
                    {"answer": {"type": "noul", "instructions": "Is it true?"}},
                    public_or_sanitized_data_ack=True,
                )
        self.assertEqual(response.read_sizes, [module.MAX_RESPONSE_BYTES + 1])
        self.assertTrue(connection.closed)

    def test_http_error_json_is_strict_and_bounded(self):
        from hermes_switchyard import client as module

        response = _Response(
            b'{"error":"bad","error":"worse"}',
            status=400,
            headers={"Content-Length": "31"},
        )
        connection = _Connection(response)
        with mock.patch.object(module.http.client, "HTTPSConnection", return_value=connection):
            client = DecisionClient(api_key="fixture")
            with self.assertRaises(RuntimeError):
                client.decide(
                    "public",
                    {"answer": {"type": "noul", "instructions": "Is it true?"}},
                    public_or_sanitized_data_ack=True,
                )
        self.assertEqual(response.read_sizes, [module.MAX_ERROR_BYTES + 1])
        self.assertTrue(connection.closed)

    def test_http_requests_send_the_real_bearer_token(self):
        from hermes_switchyard import client as module

        class RecordingConnection(_Connection):
            def __init__(self, response: _Response):
                super().__init__(response)
                self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []

            def request(self, method, path, body=None, headers=None):
                self.requests.append((method, path, body, dict(headers or {})))

        response = _Response(
            b'{"model":"typesafe/jev-1.13","answers":{"answer":{"noul":0.9}},"usage":{}}'
        )
        connection = RecordingConnection(response)
        with mock.patch.object(module.http.client, "HTTPSConnection", return_value=connection):
            client = DecisionClient(api_key="fixture-key")
            client.decide(
                "public",
                {"answer": {"type": "noul", "instructions": "Is it true?"}},
                public_or_sanitized_data_ack=True,
            )
        self.assertEqual(len(connection.requests), 1)
        headers = connection.requests[0][3]
        self.assertEqual(headers["Authorization"], "Bearer " + client.api_key)
        self.assertEqual(headers["HTTP-Referer"], "https://github.com/bgrablin/hermes-switchyard")
        self.assertEqual(headers["X-Title"], "Hermes-Switchyard")
        self.assertEqual(headers["X-OpenRouter-Title"], "Hermes-Switchyard")

    def test_typesafe_requests_omit_openrouter_app_headers(self):
        from hermes_switchyard import client as module
        from hermes_switchyard.client import TYPESAFE_ENDPOINT

        class RecordingConnection(_Connection):
            def __init__(self, response: _Response):
                super().__init__(response)
                self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []

            def request(self, method, path, body=None, headers=None):
                self.requests.append((method, path, body, dict(headers or {})))

        response = _Response(
            b'{"model":"jev-latest","answers":{"answer":{"noul":0.9}},"usage":{}}'
        )
        connection = RecordingConnection(response)
        with mock.patch.object(module.http.client, "HTTPSConnection", return_value=connection):
            client = DecisionClient(
                api_key="fixture-key",
                endpoint=TYPESAFE_ENDPOINT,
                model="jev-latest",
            )
            client.decide(
                "public",
                {"answer": {"type": "noul", "instructions": "Is it true?"}},
                public_or_sanitized_data_ack=True,
            )
        self.assertEqual(len(connection.requests), 1)
        headers = connection.requests[0][3]
        self.assertEqual(headers["Authorization"], "Bearer " + client.api_key)
        self.assertNotIn("HTTP-Referer", headers)
        self.assertNotIn("X-Title", headers)
        self.assertNotIn("X-OpenRouter-Title", headers)


class RoutingBoundaryTests(unittest.TestCase):
    def test_catalog_batches_fit_the_complete_serialized_request(self):
        calls: list[dict] = []

        def transport(payload):
            calls.append(payload)
            return {
                "model": "typesafe/jev-1.13",
                "answers": {name: {"noul": 0.9} for name in payload["questions"]},
                "usage": {},
            }

        client = DecisionClient(api_key="fixture", transport=transport)
        candidates = [
            {
                "id": f"model-{index}",
                "description": "long public metadata " + ("x" * 700),
                "approved": True,
                "cost": index + 1,
            }
            for index in range(100)
        ]
        result = route_model(
            task="public task",
            candidates=candidates,
            requirements={},
            client=client,
            public_or_sanitized_data_ack=True,
        )
        self.assertEqual(len(result["capability_fit_scores"]), 100)
        self.assertTrue(all(len(json.dumps(payload, ensure_ascii=False).encode()) <= 96_000 for payload in calls))

    def test_long_skill_catalogs_are_partitioned_by_complete_request_bytes(self):
        calls: list[dict] = []

        def transport(payload):
            calls.append(payload)
            answers = {}
            for name, question in payload["questions"].items():
                criteria = question.get("criteria", {})
                if name == "needs_skill":
                    answers[name] = {"noul": 0.95}
                    continue
                selected = "skill-179" if "skill-179" in criteria else next(iter(criteria))
                probabilities = {
                    key: (0.95 if key == selected else 0.05 / max(1, len(criteria) - 1))
                    for key in criteria
                }
                answers[name] = {
                    "choice": selected,
                    "probabilities": probabilities,
                    "confidence": 0.95,
                }
            return {"model": "typesafe/jev-1.13", "answers": answers, "usage": {}}

        candidates = [
            {
                "name": f"skill-{index}",
                "description": "public long description " + ("x" * 700),
            }
            for index in range(180)
        ]
        result = select_skill(
            task="public task",
            candidates=candidates,
            client=DecisionClient(api_key="fixture", transport=transport),
            public_or_sanitized_data_ack=True,
        )
        self.assertEqual(result["selected"], "skill-179")
        self.assertGreater(len(calls), 1)
        self.assertTrue(
            all(len(json.dumps(payload, ensure_ascii=False).encode()) <= 96_000 for payload in calls)
        )
        offered = {
            candidate["name"]
            for payload in calls
            for candidate in payload["state"].get("skills", [])
        }
        self.assertEqual(offered, {candidate["name"] for candidate in candidates})

    def test_multi_skill_contract_returns_typed_list_without_using_single_selector(self):
        from hermes_switchyard.routing import select_skills

        class Client:
            def decide(self, _state, questions, *, public_or_sanitized_data_ack=False):
                return {
                    "model": "typesafe/jev-1.13",
                    "answers": {name: {"noul": 0.95 if name.endswith("0") else 0.1} for name in questions},
                    "usage": {},
                }

        result = select_skills(
            task="public task",
            candidates=[
                {"name": "skill-a", "description": "A"},
                {"name": "skill-b", "description": "B"},
            ],
            client=Client(),
            public_or_sanitized_data_ack=True,
        )
        self.assertEqual(result["selected"], ["skill-a"])
        self.assertIsInstance(result["scores"], dict)

    def test_recommendation_cache_key_is_hash_only_and_changes_with_route_identity(self):
        route = {"endpoint": "https://api.typesafe.ai/v1/systemone", "model": "jev-latest", "profile": "A"}
        recommender = AutomaticSkillRecommender(
            configured_candidates=[{"name": "skill-a", "description": "A"}],
            cache_identity=lambda: route,
        )
        recommender.recommend("public task")
        key = next(iter(recommender._cache))
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        route["profile"] = "B"
        second = recommender.recommend("public task")
        self.assertFalse(second["cache_hit"])


class NamespaceAndAckTests(unittest.TestCase):
    def test_multi_skill_contract_is_registered_and_shipped(self):
        import hermes_switchyard

        manifest = Path("plugin.yaml").read_text(encoding="utf-8")
        self.assertIn("  - jev_skill_select_many", manifest)

        class Context:
            def __init__(self):
                self.tools = {}

            def get_config(self, _key, default=None):
                return default

            def register_tool(self, *, name, handler, **_kwargs):
                self.tools[name] = handler

            def register_auxiliary_task(self, *_args, **_kwargs):
                pass

            def register_skill(self, *_args, **_kwargs):
                pass

            def register_hook(self, *_args, **_kwargs):
                pass

        context = Context()
        hermes_switchyard.register(context)
        self.assertIn("jev_skill_select_many", context.tools)
        self.assertIn("jev_skill_select_many", check_native_hermes.EXPECTED_TOOLS)
        self.assertEqual(
            check_native_hermes.EXPECTED_REQUIRED_FIELDS["jev_skill_select_many"],
            {"task", "candidates"},
        )
        self.assertIn("jev_session_search_rerank", check_native_hermes.EXPECTED_TOOLS)
        self.assertEqual(
            check_native_hermes.EXPECTED_REQUIRED_FIELDS["jev_session_search_rerank"],
            {"query", "candidates"},
        )
        self.assertIn("  - jev_session_search_rerank", manifest)

    def test_model_facing_acknowledgement_defaults_on_and_is_optional(self):
        for schema in (schemas.ASSESS, schemas.COMPUTER_USE, schemas.SKILL_SELECT, schemas.MODEL_ROUTE, schemas.MULTI_SKILL_SELECT, schemas.SESSION_SEARCH_RERANK):
            self.assertNotIn("public_or_sanitized_data_ack", schema["parameters"]["required"])
            self.assertIs(schema["parameters"]["properties"]["public_or_sanitized_data_ack"]["default"], True)
        text_inputs = schemas.COMPUTER_USE["parameters"]["properties"]["text_inputs"]
        self.assertEqual(text_inputs["maxItems"], 16)
        self.assertEqual(text_inputs["items"]["properties"]["value"]["maxLength"], 2000)

    def test_standalone_scan_reasons_are_preserved_in_hosted_skip_receipts(self):
        from hermes_switchyard.receipt_state import HOSTED_SKIP_REASONS

        self.assertIn("ack_required", HOSTED_SKIP_REASONS)
        self.assertTrue(any(reason.startswith("local_scan_") for reason in HOSTED_SKIP_REASONS))

    def test_status_and_guide_are_local_and_live_test_requires_explicit_flag(self):
        import hermes_switchyard

        status = SimpleNamespace(switchyard_command="status", json_output=True)
        with mock.patch.object(hermes_switchyard, "_secret", return_value=""), \
             mock.patch.object(hermes_switchyard, "DecisionClient", side_effect=AssertionError("status must stay network-free")), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(hermes_switchyard._cli_handler(status), 0)
        guide = SimpleNamespace(switchyard_command="guide")
        with mock.patch.object(hermes_switchyard, "_secret", side_effect=AssertionError("guide must stay local")), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(hermes_switchyard._cli_handler(guide), 0)
        live = SimpleNamespace(switchyard_command="test", live=False, public_or_sanitized_data_ack=False)
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertNotEqual(hermes_switchyard._cli_handler(live), 0)

    def test_install_guide_names_required_operator_steps(self):
        import hermes_switchyard

        text = hermes_switchyard._after_install_text()
        self.assertIn("hermes switchyard setup --provider typesafe", text)
        self.assertIn("hermes switchyard setup --provider openrouter", text)
        self.assertIn("hermes gateway restart", text)
        self.assertIn("public_or_sanitized_data_ack", text)
        self.assertIn("hermes switchyard guide", text)
        guide = SimpleNamespace(switchyard_command="guide")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(hermes_switchyard._cli_handler(guide), 0)
        self.assertEqual(stdout.getvalue().strip(), text)
        status = SimpleNamespace(switchyard_command="status", json_output=False)
        with mock.patch.object(hermes_switchyard, "_secret", return_value=""), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(hermes_switchyard._cli_handler(status), 0)
        self.assertIn("hermes switchyard setup --provider typesafe", stdout.getvalue())

    def test_status_exposes_registered_routing_mode_after_fresh_register(self):
        import hermes_switchyard

        class Context:
            def __init__(self, settings):
                self.settings = dict(settings)
                self.tools = {}

            def get_config(self, key, default=None):
                return self.settings.get(key, default)

            def register_tool(self, *, name, handler, **_kwargs):
                self.tools[name] = handler

            def register_auxiliary_task(self, *_args, **_kwargs):
                pass

            def register_skill(self, *_args, **_kwargs):
                pass

            def register_hook(self, *_args, **_kwargs):
                pass

        hermes_switchyard.reset_runtime_status()
        first = Context({
            "automatic_skill_routing_mode": "local_only",
            "automatic_skill_consumer_mode": "advisory",
            "automatic_skill_jev_mode": "uncertain_only",
            "automatic_skill_public_or_sanitized_data_ack": False,
        })
        hermes_switchyard.register(first)
        status = SimpleNamespace(switchyard_command="status", json_output=True)
        with mock.patch.object(hermes_switchyard, "_secret", return_value=""), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(hermes_switchyard._cli_handler(status), 0)
            payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["routing_mode"], "local_only")
        self.assertEqual(payload["consumer_mode"], "advisory")
        self.assertIs(payload["public_or_sanitized_data_ack"], False)
        self.assertEqual(payload["automatic_skill_jev_mode"], "uncertain_only")
        self.assertIs(payload["hosted_construction_allowed"], False)
        self.assertIs(payload["plugin_loaded"], True)

        second = Context({
            "automatic_skill_routing_mode": "hosted_sanitized",
            "automatic_skill_consumer_mode": "load",
            "automatic_skill_jev_mode": "always",
            "automatic_skill_public_or_sanitized_data_ack": True,
        })
        hermes_switchyard.register(second)
        with mock.patch.object(hermes_switchyard, "_secret", return_value=""), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(hermes_switchyard._cli_handler(status), 0)
            payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["routing_mode"], "hosted_sanitized")
        self.assertEqual(payload["consumer_mode"], "load")
        self.assertIs(payload["public_or_sanitized_data_ack"], True)
        self.assertIs(payload["hosted_construction_allowed"], True)

    def test_unset_routing_mode_defaults_to_hosted_sanitized(self):
        import hermes_switchyard

        class Context:
            def __init__(self, settings):
                self.settings = dict(settings)
                self.tools = {}

            def get_config(self, key, default=None):
                return self.settings.get(key, default)

            def register_tool(self, *, name, handler, **_kwargs):
                self.tools[name] = handler

            def register_auxiliary_task(self, *_args, **_kwargs):
                pass

            def register_skill(self, *_args, **_kwargs):
                pass

            def register_hook(self, *_args, **_kwargs):
                pass

        hermes_switchyard.reset_runtime_status()
        # An unset profile inherits install defaults: hosted_sanitized + load + ack.
        hermes_switchyard.register(Context({}))
        status = SimpleNamespace(switchyard_command="status", json_output=True)
        with mock.patch.object(hermes_switchyard, "_secret", return_value=""), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(hermes_switchyard._cli_handler(status), 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["routing_mode"], "hosted_sanitized")
        self.assertEqual(payload["consumer_mode"], "load")
        self.assertIs(payload["public_or_sanitized_data_ack"], True)
        self.assertIs(payload["hosted_construction_allowed"], True)

    def test_registered_route_resolves_config_and_secret_each_call(self):
        import hermes_switchyard

        class FakeClient:
            instances: list["FakeClient"] = []

            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.closed = False
                type(self).instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

            def close(self):
                self.closed = True

            def decide(self, _state, questions, *, public_or_sanitized_data_ack=False):
                return {
                    "model": self.kwargs["model"],
                    "answers": {name: {"noul": 0.9} for name in questions},
                    "usage": {},
                }

        class Context:
            def __init__(self):
                self.settings = {
                    "jev_provider": "openrouter",
                    "api_endpoint": "https://openrouter.ai/api/alpha/decisions",
                    "jev_model": "typesafe/jev-1.13",
                }
                self.tools = {}

            def get_config(self, key, default=None):
                return self.settings.get(key, default)

            def register_auxiliary_task(self, *_args, **_kwargs):
                pass

            def register_tool(self, *, name, handler, **_kwargs):
                self.tools[name] = handler

            def register_skill(self, *_args, **_kwargs):
                pass

            def register_hook(self, *_args, **_kwargs):
                pass

        active = {"profile": "A", "secret": "secret-a"}
        context = Context()
        with mock.patch.object(hermes_switchyard, "DecisionClient", FakeClient), \
             mock.patch.object(hermes_switchyard, "_secret", side_effect=lambda _provider: active["secret"]):
            hermes_switchyard.register(context)
            first = json.loads(context.tools["jev_assess"]({
                "state": "public",
                "questions": {"answer": {"type": "noul", "instructions": "Is it true?"}},
                "public_or_sanitized_data_ack": True,
            }))
            active["profile"] = "B"
            active["secret"] = "secret-b"
            context.settings["jev_model"] = "typesafe/jev-1.13-20260917"
            second = json.loads(context.tools["jev_assess"]({
                "state": "public",
                "questions": {"answer": {"type": "noul", "instructions": "Is it true?"}},
                "public_or_sanitized_data_ack": True,
            }))
        self.assertEqual(first["model"], "typesafe/jev-1.13")
        self.assertEqual(second["model"], "typesafe/jev-1.13-20260917")
        self.assertEqual([item.kwargs["api_key"] for item in FakeClient.instances], ["secret-a", "secret-b"])
        self.assertTrue(all(item.closed for item in FakeClient.instances))

    def test_provider_endpoint_conflicts_reject_before_secret_or_client(self):
        import hermes_switchyard

        class Context:
            def __init__(self, settings):
                self.settings = settings
                self.tools = {}

            def get_config(self, key, default=None):
                return self.settings.get(key, default)

            def register_auxiliary_task(self, *_args, **_kwargs): pass
            def register_tool(self, *, name, handler, **_kwargs): self.tools[name] = handler
            def register_skill(self, *_args, **_kwargs): pass
            def register_hook(self, *_args, **_kwargs): pass

        cases = (
            {
                "jev_provider": "openrouter",
                "api_endpoint": "https://api.typesafe.ai/v1/systemone",
            },
            {
                "jev_provider": "typesafe",
                "api_endpoint": "https://openrouter.ai/api/alpha/decisions",
            },
        )
        for settings in cases:
            context = Context(settings)
            with mock.patch.object(hermes_switchyard, "_secret", side_effect=AssertionError("secret accessed")), \
                 mock.patch.object(hermes_switchyard, "DecisionClient", side_effect=AssertionError("client constructed")):
                hermes_switchyard.register(context)
                result = json.loads(context.tools["jev_assess"]({
                    "state": "public",
                    "questions": {"answer": {"type": "noul", "instructions": "Is it true?"}},
                    "public_or_sanitized_data_ack": True,
                }))
            self.assertEqual(result["error"]["code"], "invalid_request")

    def test_automatic_recommender_reuses_and_explicitly_closes_pooled_client(self):
        from hermes_switchyard.automatic import AutomaticSkillRecommender

        class PooledClient:
            def __init__(self):
                self.closed = 0

            def decide(self, _state, questions, *, public_or_sanitized_data_ack=False):
                criteria = questions["skill"]["criteria"]
                selected = next(iter(criteria))
                return {
                    "model": "typesafe/jev-1.13",
                    "answers": {
                        "skill": {
                            "choice": selected,
                            "probabilities": {selected: 1.0},
                            "confidence": 0.95,
                        },
                        "needs_skill": {"noul": 0.95},
                    },
                    "usage": {},
                }

            def close(self):
                self.closed += 1

        created: list[PooledClient] = []

        def factory():
            created.append(PooledClient())
            return created[-1]

        recommender = AutomaticSkillRecommender(
            configured_candidates=[{"name": "skill-a", "description": "A"}],
            hosted_enabled=True,
            public_or_sanitized_data_ack=True,
            client_factory=factory,
            cache_seconds=0,
        )
        allowed_policy = {
            "version": 1,
            "decision": "allow",
            "data_class": "sanitized",
            "reason_code": "synthetic_fixture_allowed",
            "allowed_payload": "SANITIZED_POOLED_CLIENT_TASK",
        }
        recommender.recommend("public task", turn_egress_policy=allowed_policy)
        recommender.recommend("another public task", turn_egress_policy=allowed_policy)
        self.assertEqual(len(created), 1)
        recommender.close()
        self.assertEqual(created[0].closed, 1)

    def test_current_identity_is_canonical_and_only_known_readme_path_migrates(self):
        root = Path(__file__).resolve().parent.parent
        manifest = (root / "plugin.yaml").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertIn("name: hermes-switchyard", manifest)
        self.assertIn("version: 0.6.0", manifest)
        self.assertNotIn("plugins doctor /path/to/jev-decision", readme)
        self.assertIn("hermes switchyard", readme)
        self.assertNotIn("hermes jev-decision", readme)

    def test_canonical_scan_checks_tracked_files_but_not_ignored_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forbidden = "jev" + "_decision"
            (root / "plugin.yaml").write_text("version: 0.5.3\n", encoding="utf-8")
            (root / "README.md").write_text("hermes switchyard\n", encoding="utf-8")
            skill = root / "hermes_switchyard/skills/hermes-switchyard-operations/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("fixture skill\n", encoding="utf-8")
            tracked = root / "scan-check.txt"
            tracked.write_text(forbidden + "\n", encoding="utf-8")
            (root / ".gitignore").write_text("evaluation/*results*.json\n", encoding="utf-8")
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            ignored = root / "evaluation/results.json"
            ignored.parent.mkdir()
            ignored.write_text(forbidden + "\n", encoding="utf-8")
            with mock.patch(f"{__name__}.__file__", str(root / "tests/test_release_stabilization.py")):
                with self.assertRaises(AssertionError):
                    self.test_release_is_canonical_only()
                # A historical receipt label is not an importable namespace.
                tracked.write_text("live_" + forbidden + "\n", encoding="utf-8")
                self.test_release_is_canonical_only()
                for namespace in (
                    "from " + forbidden + " import client",
                    "import " + forbidden + ".client",
                    forbidden + "/client.py",
                    "'" + forbidden + "'",
                ):
                    with self.subTest(namespace=namespace):
                        tracked.write_text(namespace, encoding="utf-8")
                        with self.assertRaises(AssertionError):
                            self.test_release_is_canonical_only()

    def test_release_is_canonical_only(self):
        root = Path(__file__).resolve().parent.parent
        forbidden = "jev" + "_decision"
        namespace = r"(?<!\w)" + re.escape(forbidden) + r"(?!\w)"
        self.assertIn("hermes_switchyard/__init__.py", RELEASE_FILES)
        self.assertIn("hermes_switchyard/skills/hermes-switchyard-operations/SKILL.md", RELEASE_FILES)
        self.assertTrue(
            (root / "hermes_switchyard/skills/hermes-switchyard-operations/SKILL.md").is_file()
        )
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        paths = set(RELEASE_FILES) | {os.fsdecode(entry) for entry in tracked.split(b"\0") if entry}
        for relative in sorted(paths):
            path = root / relative
            if not path.is_file():
                continue
            self.assertNotRegex(path.as_posix(), namespace)
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            self.assertNotRegex(content, namespace, path.as_posix())


if __name__ == "__main__":
    unittest.main()
