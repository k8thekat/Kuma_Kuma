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

import importlib
import inspect
import types
from types import ModuleType
from typing import TYPE_CHECKING

from .cog import *
from .context import *
from .embeds import *

if TYPE_CHECKING:
    from ._types import *


def reload_module_dependencies(module_path: str, /) -> set[str]:
    """Reloads all dependencies of a module with importlib.

    Parameters
    ----------
    module_path : str
        The module to reload, dot qualified.

    Returns
    -------
    set[str]
        The reloaded modules

    Raises
    ------
    ModuleNotFoundError
        You passed an invalid module path.

    """
    out: list = []
    mod_to_reload: ModuleType = importlib.import_module(name=module_path)

    def get_pred(value: object):  # noqa: ANN202
        return isinstance(value, types.ModuleType) or (
            inspect.isclass(object=value) or (inspect.isfunction(object=value) and value.__module__ is not mod_to_reload)
        )

    items = inspect.getmembers(object=mod_to_reload, predicate=get_pred)

    for _, value in items:
        if isinstance(value, types.ModuleType):
            importlib.reload(module=value)
            out.append(value.__name__)
        elif inspect.isclass(object=value) or (inspect.isfunction(object=value) and value.__module__ is not mod_to_reload):
            module: ModuleType = importlib.import_module(name=value.__module__)
            importlib.reload(module=module)
            out.append(module.__name__)

    return set(out)
