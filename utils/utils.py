import importlib
import inspect
import os
import re
import types
from types import ModuleType

import aiofiles


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


async def count_lines(path: str, filetype: str = ".py", skip_venv: bool = True) -> int:
    lines = 0
    for i in os.scandir(path=path):
        if i.is_file():
            if i.path.endswith(filetype):
                if skip_venv and re.search(pattern=r"(\\|/)?venv(\\|/)", string=i.path):
                    continue
                lines += len((await (await aiofiles.open(file=i.path)).read()).split(sep="\n"))
        elif i.is_dir():
            lines += await count_lines(path=i.path, filetype=filetype)
    return lines


async def count_others(path: str, filetype: str = ".py", file_contains: str = "def", skip_venv: bool = True) -> int:
    """Counts the files in directory or functions."""
    line_count = 0
    for i in os.scandir(path=path):
        if i.is_file():
            if i.path.endswith(filetype):
                if skip_venv and re.search(pattern=r"(\\|/)?venv(\\|/)", string=i.path):
                    continue
                line_count += len([
                    line
                    for line in (await (await aiofiles.open(file=i.path)).read()).split(sep="\n")
                    if file_contains in line
                ])
        elif i.is_dir():
            line_count += await count_others(path=i.path, filetype=filetype, file_contains=file_contains)
    return line_count
