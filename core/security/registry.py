"""Feature- and dependency-filtered tool registry.

Consumes a validated :class:`~core.security.manifest.Manifest` and a
:class:`~core.security.features.Features` set, then evaluates every declared
tool against a fixed gate order (defaults, feature, optional dependency,
handler resolution, handler location) before exposing it.

Design contract:

- Per-tool problems are *never* raised; they are recorded as stable tokens in
  :meth:`ToolRegistry.disabled_reasons` and the tool stays unavailable.
- :class:`RegistryError` is reserved for programmer errors: bad constructor
  input, or resolving a tool that is not enabled.
- Before any handler module is imported, ``sys.path`` is sanitized in place so
  a writable sibling directory cannot shadow ``core.tools`` (source mode).
  The registry never restores ``sys.path`` itself; callers/tests do.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import site
import sys
import sysconfig
from collections.abc import Callable, Mapping
from typing import Any

from core.security.features import Features
from core.security.manifest import Manifest, ToolSpec

__all__ = ["RegistryError", "ToolRegistry"]


class RegistryError(Exception):
    """Programmer error or misuse of the tool registry."""


def _normalized(path: str) -> str:
    """Absolute, symlink-resolved, case-normalized form of *path*."""
    return os.path.normcase(os.path.realpath(path))


def _is_under(path: str, root: str) -> bool:
    """True when *path* is *root* or beneath it.

    Uses ``commonpath`` on ``normcase(realpath(...))`` values -- never a bare
    ``startswith`` -- and guards ``ValueError`` (different drives) and
    ``OSError`` by failing closed.
    """
    try:
        candidate = os.path.normcase(os.path.realpath(path))
        keep = os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([keep, candidate]) == keep
    except (ValueError, OSError):
        return False


def _keep_prefixes(code_root: str) -> list[str]:
    """Paths that may remain on ``sys.path`` regardless of writability."""
    prefixes: list[str] = [code_root]
    config = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        value = config.get(key)
        if value:
            prefixes.append(value)
    try:
        prefixes.extend(site.getsitepackages())
    except Exception:  # site machinery may be trimmed down; never crash load()
        pass
    for value in (sys.prefix, sys.base_prefix):
        if value:
            prefixes.append(value)
    return prefixes


def _sanitize_sys_path(code_root: str) -> None:
    """Rewrite ``sys.path`` in place, dropping untrusted entries.

    An entry is dropped when it is empty/relative (including ``""`` and
    ``"."``), when it resolves to the current working directory (unless it is
    under ``code_root``), or when it is writable and outside every keep-prefix.
    Non-writable entries and keep-prefix children are kept, order is preserved,
    nothing is added, and an entry that cannot be stat'ed is never dropped.
    """
    keeps = _keep_prefixes(code_root)
    try:
        cwd = os.path.normcase(os.path.realpath(os.getcwd()))
    except OSError:
        cwd = None

    kept: list[str] = []
    for entry in sys.path:
        if not entry or not os.path.isabs(entry):
            continue  # "", ".", and other relative entries are untrusted
        try:
            resolved = os.path.normcase(os.path.realpath(entry))
        except OSError:
            kept.append(entry)  # cannot stat: drop nothing
            continue
        if cwd is not None and resolved == cwd and not _is_under(entry, code_root):
            continue  # cwd shadow, unless it lives inside code_root
        try:
            writable = os.access(entry, os.W_OK)
        except OSError:
            writable = False  # cannot stat: drop nothing
        if writable and not any(_is_under(entry, keep) for keep in keeps):
            continue
        kept.append(entry)
    sys.path[:] = kept


class ToolRegistry:
    """Evaluate a manifest and expose only the tools that pass every gate."""

    def __init__(
        self,
        manifest: Manifest,
        features: Features,
        code_root: str,
        frozen: bool = False,
    ) -> None:
        if not isinstance(manifest, Manifest):
            raise RegistryError("manifest must be a Manifest")
        if not isinstance(features, Features):
            raise RegistryError("features must be a Features")
        tools = manifest.tools
        if not isinstance(tools, Mapping):
            raise RegistryError("manifest tools must be a mapping")
        for key, spec in tools.items():
            if not isinstance(spec, ToolSpec) or key != spec.name:
                raise RegistryError("manifest tools mapping is inconsistent")
        try:
            self._code_root = os.path.realpath(os.fspath(code_root))
        except (TypeError, ValueError, OSError):
            raise RegistryError("code_root must be a filesystem path") from None

        self._manifest = manifest
        self._features = features
        self._frozen = bool(frozen)
        self._enabled: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._disabled: dict[str, str] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Sanitize ``sys.path`` once, then classify every manifest tool.

        Idempotent: a second call is a no-op.  Only programmer errors raise;
        per-tool problems become entries in :meth:`disabled_reasons`.
        """
        if self._loaded:
            return
        _sanitize_sys_path(self._code_root)
        for name in sorted(self._manifest.tools):
            spec = self._manifest.tools[name]
            reason, handler = self._evaluate(spec)
            if reason is None:
                self._enabled[name] = spec
                self._handlers[name] = handler
            else:
                self._disabled[name] = reason
        self._loaded = True

    def _evaluate(self, spec: ToolSpec) -> tuple[str | None, Callable[..., Any] | None]:
        """Run the gate order; returns ``(None, handler)`` when enabled."""
        # 1. declared default
        if not spec.enabled_by_default:
            return "disabled_by_default", None
        # 2. feature gate (unknown feature names fail closed via Features)
        if spec.feature is not None and not self._features.enabled(spec.feature):
            return "feature_disabled", None
        # 3. optional dependency must be importable
        if spec.optional_dependency is not None:
            try:
                found = importlib.util.find_spec(spec.optional_dependency)
            except (ModuleNotFoundError, ValueError):
                found = None
            if found is None:
                return "missing_optional_dependency", None
        # 4. handler resolution, then 5. location check (source mode)
        return self._resolve(spec)

    def _resolve(
        self, spec: ToolSpec
    ) -> tuple[str | None, Callable[..., Any] | None]:
        module_name, separator, func_name = spec.handler.partition(":")
        if not separator or not module_name or not func_name:
            return "unknown_handler", None  # malformed in a hand-built spec
        try:
            module = importlib.import_module(module_name)
        except Exception:
            # Class name only: exception text may carry paths or secrets.
            return "handler_import_error", None
        try:
            target = getattr(module, func_name)
        except AttributeError:
            return "unknown_handler", None
        except Exception:
            return "handler_import_error", None
        if not callable(target):
            return "handler_not_callable", None
        if not self._frozen:
            # Source mode: the handler's file must exist inside code_root.
            # Frozen mode trusts the signed executable and manifest signature.
            location = getattr(module, "__file__", None)
            if (
                not location
                or not os.path.isfile(location)
                or not _is_under(location, self._code_root)
            ):
                return "handler_outside_code_root", None
        return None, target

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def enabled_tools(self) -> list[ToolSpec]:
        """Enabled tools sorted by name; never re-imports disabled ones."""
        return [self._enabled[name] for name in sorted(self._enabled)]

    def get(self, name: str) -> ToolSpec | None:
        """The tool's spec when enabled, otherwise ``None``."""
        return self._enabled.get(name)

    def resolve_handler(self, name: str) -> Callable[..., Any]:
        """Cached handler for an enabled tool.

        Raises ``RegistryError("handler_outside_code_root:<name>")`` when the
        tool failed the location check and
        ``RegistryError("tool_not_enabled:<name>")`` for unknown or otherwise
        disabled tools.  A handler that failed the location check is never
        returned.
        """
        handler = self._handlers.get(name)
        if handler is not None:
            return handler
        if self._disabled.get(name) == "handler_outside_code_root":
            raise RegistryError(f"handler_outside_code_root:{name}")
        raise RegistryError(f"tool_not_enabled:{name}")

    def openai_schemas(self) -> list[dict]:
        """OpenAI function-calling schemas for enabled tools, sorted by name.

        ``parameters`` is passed through by reference, unchanged.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self.enabled_tools()
        ]

    def disabled_reasons(self) -> dict[str, str]:
        """Copy of ``{tool_name: reason}`` for every disabled tool."""
        return dict(self._disabled)
