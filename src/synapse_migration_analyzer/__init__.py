"""Deprecated alias for :mod:`usma`.

The package was renamed from ``synapse_migration_analyzer`` to ``usma`` as
part of Phase 3 (USMA rebrand, ADR-0004). This shim is provided for one
minor release so existing scripts and notebooks that still ``import
synapse_migration_analyzer`` continue to work; it will be removed in the
next minor version.

The shim installs a meta-path finder that transparently routes every
``synapse_migration_analyzer[.<sub>]`` import to the matching ``usma``
module, then re-binds ``sys.modules['synapse_migration_analyzer']`` to
the real :mod:`usma` package so attribute access (``smal.cli``,
``smal.modules`` …) works identically.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
import warnings

_OLD = "synapse_migration_analyzer"
_NEW = "usma"


class _RenameFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Route ``synapse_migration_analyzer.*`` imports to ``usma.*``."""

    def find_spec(self, fullname, path=None, target=None):  # noqa: D401, ARG002
        if fullname != _OLD and not fullname.startswith(_OLD + "."):
            return None
        new_name = _NEW + fullname[len(_OLD):]
        try:
            real = importlib.import_module(new_name)
        except ImportError:
            return None
        sys.modules[fullname] = real
        spec = importlib.machinery.ModuleSpec(fullname, self)
        spec.submodule_search_locations = getattr(real, "__path__", None)
        return spec

    def create_module(self, spec):  # noqa: D401
        return sys.modules.get(spec.name)

    def exec_module(self, module):  # noqa: D401, ARG002
        return None


if not any(isinstance(f, _RenameFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _RenameFinder())

warnings.warn(
    "The 'synapse_migration_analyzer' package has been renamed to 'usma'. "
    "Update your imports to use 'usma' — this compatibility shim will be "
    "removed in the next minor release.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-bind the top-level name so `import synapse_migration_analyzer; smal.cli`
# resolves to the real `usma` package immediately, not the shim module.
sys.modules[__name__] = importlib.import_module(_NEW)
