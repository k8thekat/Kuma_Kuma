import importlib
import inspect
import os
import re
import types
from types import ModuleType

import aiofiles


# todo - Put this funciton into a command.
def reload_module_dependencies(module_path: str, /) -> set[str]:
    """
    Reloads all dependencies of a module with importlib

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
