"""Local qualification for a restricted *authorization* hook, never a result gate.

No import-time registration, discovery, version allowlist, live request, or core
patching. Only fresh-manager synthetic callbacks are used for qualification.
The cached host which swallows pre_tool_call exceptions is rejected, not fixed.
This is not a generic-host security claim: skipped hooks, direct registry calls,
infrastructure failures, and tools outside the restricted entrypoint still need
an independently restricted session. require_native_enforcement remains refused.
"""
from __future__ import annotations

import builtins
import json
import math
import sys
import threading
import types
import weakref
from dataclasses import dataclass
from typing import Any

from .web_security_gate import EnforcementUnavailable

_MESSAGE = "Restricted authorization unavailable"


def _block():
    # Native dispatch requires message, not merely reason or action.
    return {"action": "block", "message": _MESSAGE}


class _Eligibility:
    """Opaque process-local identity; JSON/string/constructed lookalikes cannot grant it."""
    __slots__ = ("__weakref__",)


_issued = weakref.WeakKeyDictionary()
_lock = threading.RLock()


def _runtime(context):
    from hermes_cli import lifecycle, plugins, plugins_dispatch

    dispatch = sys.modules.get("model_tools")
    manager = getattr(context, "_manager", None)
    if (type(context) is not plugins.PluginContext
            or type(manager) is not plugins.PluginManager or dispatch is None):
        raise EnforcementUnavailable("Native authorization runtime is not loaded or trusted")
    try:
        from agent.safe_worker_policy import safe_worker_enabled
    except ModuleNotFoundError as exc:
        if exc.name != "agent.safe_worker_policy":
            raise
        # Older hosts have no safe-worker route at all. Their real callback
        # behavior must still pass the canaries; absence grants nothing.
        safe_worker_enabled = None
    if safe_worker_enabled is not None and safe_worker_enabled():
        raise EnforcementUnavailable("Native hooks are disabled in this worker")
    timeout = plugins._resolve_hook_callback_timeout()
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise EnforcementUnavailable("Native authorization requires bounded hooks")
    names = ("invoke_hook", "_run_hook_callback_bounded", "_invoke_hook_callback")
    if any(name in vars(manager) for name in names):
        raise EnforcementUnavailable("Instance-overridden native hook dispatcher")
    functions = [getattr(plugins.PluginManager, name) for name in names]
    functions += [plugins.invoke_hook, plugins._delivery_manager,
                  plugins._get_pre_tool_call_directive_details,
                  plugins._dispatch_pre_tool_call_hooks,
                  plugins._resolve_hook_callback_timeout, plugins_dispatch._hook_uses_callback_timeout,
                  lifecycle.invoke_hook, dispatch.handle_function_call,
                  dispatch._pre_dispatch_guards, safe_worker_enabled]
    identity = (plugins, dispatch, lifecycle, plugins.PluginManager, plugins.PluginContext,
                tuple((fn, getattr(fn, "__code__", None)) for fn in functions),
                frozenset(plugins_dispatch._HOOK_TIMEOUT_FAIL_CLOSED_HOOKS),
                frozenset(plugins_dispatch._HOOK_TIMEOUT_BOUNDED_HOOKS), float(timeout))
    return plugins, manager, identity


def _generation(manager):
    # No universal load-generation API exists. Conservatively revoke on ANY
    # registration/hook-list change, manager/home change or known event epoch.
    return (manager.scope_key, getattr(manager, "_event_generation", None),
            id(manager._hooks), tuple((key, id(value), tuple(value))
                                      for key, value in sorted(manager._hooks.items())),
            tuple(manager._registration_order), getattr(manager, "_discovered", None))


def _is_block(results):
    return any(type(item) is dict and item.get("action") == "block"
               and type(item.get("message")) is str and bool(item["message"])
               for item in results)


def _canaries(plugins, manifest):
    """Use actual fresh host managers; no real tools, global swaps or fake claims."""
    from hermes_cli import plugins_dispatch

    def directive_blocks(manager):
        # Execute the actual loaded host directive parser in a PRIVATE globals
        # copy, routing its sole lifecycle import to the fresh canary manager.
        # No monkeypatch, real delivery/discovery, tools, or module mutation.
        # This checks message parsing and block > approve, not host delivery.
        original = plugins._get_pre_tool_call_directive_details
        namespace = dict(original.__globals__)
        local_builtins = dict(builtins.__dict__)
        def local_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "hermes_cli.lifecycle" and level == 0:
                return types.SimpleNamespace(invoke_hook=manager.invoke_hook)
            raise EnforcementUnavailable("Unexpected native canary parser dependency")
        local_builtins["__import__"] = local_import
        namespace["__builtins__"] = local_builtins
        namespace["_thread_tool_whitelist"] = threading.local()
        parser = types.FunctionType(original.__code__, namespace,
                                    original.__name__, original.__defaults__, original.__closure__)
        parser.__kwdefaults__ = original.__kwdefaults__
        directive = parser("__switchyard_local_canary__", {})
        return directive.action == "block" and bool(directive.message)

    def run(callbacks):
        manager = plugins.PluginManager()
        context = plugins.PluginContext(manifest, manager)
        handles = [context.register_hook("pre_tool_call", cb) for cb in callbacks]
        return manager, handles

    def crash(**kwargs):
        raise RuntimeError("restricted authorization qualification canary")

    def exits(**kwargs):
        raise SystemExit("restricted authorization qualification canary")

    kwargs = dict(tool_name="__switchyard_local_canary__", args={},
                  session_id="local-canary", task_id="local-canary",
                  turn_id="local-canary", tool_call_id="local-canary")
    for callback in (crash, exits, lambda **kw: _block()):
        manager, handles = run([lambda **kw: {"action": "approve", "message": "canary"}, callback])
        try:
            # Also reject hosts whose approval directive masks a later veto.
            results = manager.invoke_hook("pre_tool_call", **kwargs)
            if not _is_block(results) or not directive_blocks(manager):
                raise EnforcementUnavailable("Native authorization callback failure is not fail-closed")
        except (SystemExit, Exception) as exc:
            if isinstance(exc, EnforcementUnavailable):
                raise
            raise EnforcementUnavailable("Native authorization callback canary failed") from None
        finally:
            for handle in reversed(handles):
                handle.dispose()

    release = threading.Event()
    def slow(**kwargs):
        release.wait(1.0)

    manager, handles = run([slow])
    try:
        # Exercise the real bounded worker with a short local deadline, then
        # invoke the full manager while the same canary is still running. This
        # tests the actual timeout/suppression -> block translation without
        # changing process-global timeout configuration or waiting its 30s default.
        result = manager._run_hook_callback_bounded("pre_tool_call", slow, kwargs, .02)
        if result is not plugins_dispatch._HOOK_SKIPPED:
            raise EnforcementUnavailable("Native authorization deadline was not enforced")
        if (not _is_block(manager.invoke_hook("pre_tool_call", **kwargs))
                or not directive_blocks(manager)):
            raise EnforcementUnavailable("Native authorization timeout is not fail-closed")
    finally:
        release.set()
        for handle in reversed(handles):
            handle.dispose()


def qualify_native_authorization(context):
    """Issue opaque eligibility for this exact context/runtime/registration epoch.

    This is a necessary compatibility check, NOT permission to run an unrestricted
    session and NOT a native result-security contract. Failures register nothing
    on the target manager. The returned token is single-use at registration.
    """
    try:
        plugins, manager, identity = _runtime(context)
        generation = _generation(manager)
        _canaries(plugins, context.manifest)
        if _runtime(context)[2] != identity or _generation(manager) != generation:
            raise EnforcementUnavailable("Native authorization changed during qualification")
        token = _Eligibility()
        with _lock:
            _issued[token] = (context, manager, identity, generation)
        return token
    except EnforcementUnavailable:
        raise
    except (Exception, SystemExit):
        raise EnforcementUnavailable("Native authorization could not be qualified") from None


@dataclass
class RestrictedRegistration:
    """Local registration lease; dispose revokes before unregistering the hook."""
    _context: Any
    _identity: tuple
    _generation: tuple
    _authorizer: Any
    _handle: Any = None
    _tool_handle: Any = None
    _revoked: bool = False

    def valid(self):
        if self._revoked:
            return False
        try:
            valid = (_runtime(self._context)[2] == self._identity
                     and _generation(self._context._manager) == self._generation)
        except (Exception, SystemExit):
            valid = False
        if not valid:
            self._revoked = True
        return valid

    def authorize(self, **context):
        if not self.valid() or context.get("tool_name") != "secure_web_retrieve":
            return _block()
        try:
            decision = self._authorizer(**context)
            if decision is None:
                return None
            # Normalize the owner's deny directive: reason-only is NOT an
            # enforceable host veto. Never forward approve/modify/malformed data.
            return _block()
        except BaseException:
            return _block()

    def guard_handler(self, handler):
        """Supplemental owned-handler guard; not a repair of generic host tools."""
        def guarded(args, **context):
            if (not self.valid() or self.authorize(
                    tool_name="secure_web_retrieve", args=args, **context) is not None):
                return json.dumps({"status": "quarantined", "sources": [], "action_authorized": False})
            return handler(args, **context)
        return guarded

    def dispose(self):
        self._revoked = True
        if self._tool_handle is not None:
            self._tool_handle.dispose()
        if self._handle is not None:
            self._handle.dispose()


def register_restricted_authorization(context, authorizer, *, eligibility=None,
                                      handler=None, schema=None):
    """Qualify BEFORE registering any restricted hook or optional owned tool.

    Optional tool name is fixed to secure_web_retrieve; handler is wrapped, never
    exposed raw. Further manager registration changes revoke the entire lease.
    There is intentionally no production plugin.register() integration.
    """
    if not callable(authorizer) or (handler is not None and not callable(handler)):
        raise EnforcementUnavailable("Local authorizer/handler required")
    if handler is not None and (type(schema) is not dict
                               or schema.get("name") != "secure_web_retrieve"):
        raise EnforcementUnavailable("Owned restricted tool schema required")
    if eligibility is None:
        eligibility = qualify_native_authorization(context)
    with _lock:
        record = _issued.pop(eligibility, None) if type(eligibility) is _Eligibility else None
    if record is None:
        raise EnforcementUnavailable("Locally issued native eligibility required")
    qualified_context, manager, identity, generation = record
    if (context is not qualified_context or _runtime(context)[1] is not manager
            or _runtime(context)[2] != identity or _generation(manager) != generation):
        raise EnforcementUnavailable("Native eligibility is stale or belongs to another context")
    lease = RestrictedRegistration(context, identity, generation, authorizer)
    try:
        lease._handle = context.register_hook("pre_tool_call", lease.authorize)
        if handler is not None:
            lease._tool_handle = context.register_tool(
                name="secure_web_retrieve", toolset="switchyard_restricted",
                schema=schema, handler=lease.guard_handler(handler),
                check_fn=lease.valid, requires_env=[], is_async=False, override=False)
            if lease._tool_handle is None:
                raise EnforcementUnavailable("Restricted tool registration was refused")
        lease._generation = _generation(manager)
        return lease
    except BaseException:
        lease.dispose()
        raise
