"""Copyright (C) 2021-2025 Katelynn Cadwallader.

This file is part of Kuma Kuma.

Kuma Kuma is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 3, or (at your option)
any later version.

Kuma Kuma is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public
License for more details.

You should have received a copy of the GNU General Public License
along with Kuma Kuma; see the file COPYING.  If not, write to the Free
Software Foundation, 51 Franklin Street - Fifth Floor, Boston, MA
02110-1301, USA.
"""

from __future__ import annotations

import importlib
import sys
import types
from types import ModuleType
from typing import TYPE_CHECKING, Optional

from .animation import *
from .codeblocks import *
from .cog import *
from .context import *
from .embeds import *
from .error import *
from .help import *
from .history import *
from .ui import *

if TYPE_CHECKING:
    from ._types import *


def reload_module_dependencies(module_path: str, /, *, package_root: str = "utils") -> set[str]:
    """Reload a module *and* every project-owned module it imports, leaf-first.

    Python caches imports in ``sys.modules``. When discord.py reloads an
    extension it only re-executes that single file; the modules it imports
    (e.g. ``utils.embeds``) are returned from cache unchanged. This walks the
    dependency graph of ``module_path`` and reloads each dependency that lives
    under ``package_root`` in bottom-up order, so a dependent module rebinds the
    freshly-reloaded names of its dependencies.

    Parameters
    ----------
    module_path : str
        The module to reload, dot-qualified (e.g. ``"utils.embeds"``).
    package_root : str, optional
        Only modules whose name starts with this prefix are reloaded. This
        keeps us from reloading stdlib / discord.py / third-party modules,
        which is both pointless and dangerous. Defaults to ``"utils"``.

    Returns
    -------
    set[str]
        The names of every module that was reloaded.

    Raises
    ------
    ModuleNotFoundError
        You passed an invalid module path.

    """
    root: ModuleType = importlib.import_module(name=module_path)

    # Post-order DFS: append a module to `ordered` only *after* visiting all of
    # its dependencies, so the list ends up leaf-first (deps before dependents).
    ordered: list[ModuleType] = []
    seen: set[str] = set()

    def visit(module: ModuleType) -> None:
        # Guard against re-visiting and circular imports (A imports B, B imports
        # A) — mark `seen` *before* recursing.
        if module.__name__ in seen:
            return
        seen.add(module.__name__)

        # Inspect this module's namespace for references to other modules we own.
        for value in vars(module).values():
            dep: Optional[ModuleType] = None

            if isinstance(value, types.ModuleType):
                # A direct `import utils.embeds` style reference.
                dep = value
            elif hasattr(value, "__module__"):
                # A `from utils.embeds import KumaEmbed` style reference: the
                # class/function isn't a module, but its `__module__` tells us
                # which module to reload.
                dep = sys.modules.get(getattr(value, "__module__", None) or "")

            if dep is not None and dep.__name__.startswith(package_root):
                visit(dep)

        ordered.append(module)

    visit(root)

    # Reload in dependency order. `ordered` has the root module last, so its
    # imports are refreshed before it re-binds them.
    reloaded: set[str] = set()
    for module in ordered:
        importlib.reload(module=module)
        reloaded.add(module.__name__)

    return reloaded
