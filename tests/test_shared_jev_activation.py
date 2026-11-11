"""Registration gates only; never install or activate a plugin in a real profile."""
from __future__ import annotations

from unittest import mock

import hermes_switchyard as switchyard
from hermes_switchyard.combined_decisions import SharedDecisionBroker
from test_support import HermesHomeTestCase


class RecordingContext:
    def __init__(self, settings):
        self.settings = settings
        self.hooks = []
        self.middleware = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def register_middleware(self, name, callback):
        self.middleware.append((name, callback))

    def register_tool(self, **_kwargs):
        pass

    def register_skill(self, *_args, **_kwargs):
        pass

    def register_auxiliary_task(self, *_args, **_kwargs):
        pass


class SharedJevActivationTests(HermesHomeTestCase):
    ELIGIBLE = {
        "shared_jev_skill_effort": True,
        "automatic_skill_recommendation": True,
        "adaptive_reasoning_effort": True,
        "automatic_skill_routing_mode": "hosted_sanitized",
        "automatic_skill_consumer_mode": "load",
        "automatic_skill_public_or_sanitized_data_ack": True,
        "public_or_sanitized_data_ack": True,
    }

    def register(self, settings):
        ctx = RecordingContext(settings)
        with mock.patch.object(switchyard, "SharedDecisionBroker", wraps=SharedDecisionBroker) as factory:
            switchyard.register(ctx)
        return ctx, factory

    def test_missing_cancellation_seam_disables_sharing_not_existing_middleware(self):
        original = RecordingContext.register_hook

        def unsupported(ctx, name, callback):
            if name == "agent_loop_stopped":
                raise ValueError("unsupported lifecycle hook")
            return original(ctx, name, callback)

        with mock.patch.object(RecordingContext, "register_hook", unsupported):
            ctx, _ = self.register(dict(self.ELIGIBLE))
        self.assertIs(switchyard._RUNTIME_STATUS["shared_jev_skill_effort"], False)
        self.assertEqual(sum(name == "llm_request" for name, _ in ctx.middleware), 1)

    def test_default_configuration_never_creates_shared_broker(self):
        _, factory = self.register({})
        factory.assert_not_called()
        self.assertIs(switchyard._RUNTIME_STATUS["shared_jev_skill_effort"], False)

    def test_eligible_explicit_opt_in_wraps_existing_hook_not_a_second_owner(self):
        ctx, factory = self.register(dict(self.ELIGIBLE))
        factory.assert_called_once()
        wrapped = [callback for name, callback in ctx.hooks
                   if name == "pre_llm_call" and hasattr(callback, "__wrapped__")]
        self.assertEqual(len(wrapped), 1)
        # Exactly the existing reasoning-effort request middleware owns changes.
        self.assertEqual(sum(name == "llm_request" for name, _ in ctx.middleware), 1)
        self.assertIs(switchyard._RUNTIME_STATUS["shared_jev_skill_effort"], True)

    def test_each_prerequisite_can_disable_sharing_without_removing_other_features(self):
        for key, value in (
            ("shared_jev_skill_effort", False),
            ("automatic_skill_recommendation", False),
            ("adaptive_reasoning_effort", False),
            ("automatic_skill_routing_mode", "local_only"),
            ("automatic_skill_consumer_mode", "advisory"),
            ("automatic_skill_public_or_sanitized_data_ack", False),
            ("public_or_sanitized_data_ack", False),
        ):
            with self.subTest(setting=key):
                _, factory = self.register({**self.ELIGIBLE, key: value})
                factory.assert_not_called()
                self.assertIs(switchyard._RUNTIME_STATUS["shared_jev_skill_effort"], False)
