from cgi import test
import pathlib
import os
import logging
import sys
import traceback
from typing import TYPE_CHECKING

from discord.ext import commands


from kuma_kuma import Kuma_Kuma


class Handler():
    """This is the Basic Module Loader for AMP to Discord Integration/Interactions"""

    def __init__(self, bot: Kuma_Kuma):
        self._bot: Kuma_Kuma = bot
        # self._test_path = os.chdir("/home/commandblock/repos/dpy_cogs/")
        self._cwd = pathlib.Path.cwd()
        self._cog_path = pathlib.Path.joinpath(pathlib.Path(__file__).parents[1], "repos/dpy_cogs/")
        self._name = os.path.basename(__file__).title()
        self._logger = logging.getLogger()
        sys.path.append(self._cog_path.as_posix())

        self._loaded_cogs: list[str] = []

        self._logger.info(f'**SUCCESS** Initializing {self._name} ')

    async def cog_auto_loader(self, reload=False):
        """This will load all Cogs inside of the cogs folder."""
        # path = f'cogs'  # This gets us to the folder for the module specific scripts to load via the cog.
        path = "cogs"
        # Grab all the cogs inside my `cogs` folder and duplicate the list.
        cog_file_list = pathlib.Path.joinpath(self._cog_path, "cogs").iterdir()
        cur_cog_file_list = list(cog_file_list)

        # This while loop will force it to load EVERY cog it finds until the list is empty.
        while len(cur_cog_file_list) > 0:
            for script in cur_cog_file_list:
                # Ignore Pycache or similar files.
                # Lets Ignore our Custom Permisisons Cog. We will load it on-demand.
                if script.name.startswith('__') or not script.name.endswith('.py'):
                    cur_cog_file_list.remove(script)
                    continue

                # module_name = script.name[:-3].capitalize()  # File name ofc.
                # spec = importlib.util.spec_from_file_location(module_name, script)
                # class_module = importlib.util.module_from_spec(spec) #type:ignore
                # spec.loader.exec_module(class_module) #type:ignore

                # #module_dependencies: list[str] | None = getattr(class_module, f'Dependencies', None)
                # try:
                #     module_dependencies: list[str] | None = class_module.Dependencies
                # except AttributeError:
                #     module_dependencies = None

                # missing_depen = False
                # if module_dependencies is not None:
                #     for dependency in module_dependencies:
                #         # If the cog we need isnt loaded; skip. We will come back around to it.
                #         if dependency.lower() not in loaded_cogs:
                #             missing_depen = True
                #             break

                #     if missing_depen:
                #         self._logger.warn(f'{module_name} is ')
                #         continue

                cog = f'{path}.{script.name[:-3]}'
                try:
                    if reload and cog in self._loaded_cogs:
                        await self._bot.reload_extension(cog)
                        cur_cog_file_list.remove(script)

                    else:
                        await self._bot.load_extension(cog)
                        # Append to our loaded cogs for dependency check
                        self._loaded_cogs.append(cog)
                        # Remove the entry from our cog list; so we don't attempt to load it again.
                        cur_cog_file_list.remove(script)

                    self._logger.info(f'**FINISHED LOADING** {self._name} -> **{cog}**')

                except commands.errors.ExtensionAlreadyLoaded:
                    cur_cog_file_list.remove(script)
                    self._logger.error(f'**ERROR** Loading Cog ** - {cog} ExtensionAlreadyLoaded {traceback.format_exc()}')
                    continue

                except FileNotFoundError as e:
                    self._logger.error(f'**ERROR** Loading Cog ** - {cog} File Not Found {traceback.format_exc()}')

        self._logger.info(f'**All Cog Modules Loaded**')
