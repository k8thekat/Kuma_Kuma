import inspect
import importlib
import types
import os
import aiofiles
import re

def reload_module_dependencies(module_path: str, /) -> set[str]:
    """Reloads all dependencies of a module with importlib

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
    out = []
    mod_to_reload = importlib.import_module(module_path)

    def get_pred(value):
        return isinstance(value, types.ModuleType) or (inspect.isclass(value) or inspect.isfunction(value) and value.__module__ is not mod_to_reload)

    items = inspect.getmembers(mod_to_reload, predicate=get_pred)

    for _, value in items:
        if isinstance(value, types.ModuleType):
            importlib.reload(value)
            out.append(value.__name__)
        elif inspect.isclass(value) or (inspect.isfunction(value) and value.__module__ is not mod_to_reload):
            module = importlib.import_module(value.__module__)
            importlib.reload(module)
            out.append(module.__name__)

    return set(out)



async def count_lines(path: str, filetype: str = ".py", skip_venv: bool = True) -> int:
    lines: int = 0
    for i in os.scandir(path):
        if i.is_file():
            if i.path.endswith(filetype):
                if skip_venv and re.search(r"(\\|/)?venv(\\|/)", i.path):
                    continue
                lines += len((await (await aiofiles.open(i.path, "r")).read()).split("\n"))
        elif i.is_dir():
            lines += await count_lines(i.path, filetype)
    return lines


async def count_others(path: str, filetype: str = ".py", file_contains: str = "def", skip_venv: bool = True) -> int:
    line_count: int = 0
    for i in os.scandir(path):
        if i.is_file():
            if i.path.endswith(filetype):
                if skip_venv and re.search(r"(\\|/)?venv(\\|/)", i.path):
                    continue
                line_count += len(
                    [line for line in (await (await aiofiles.open(i.path, "r")).read()).split("\n") if file_contains in line]
                )
        elif i.is_dir():
            line_count += await count_others(i.path, filetype, file_contains)
    return line_count
