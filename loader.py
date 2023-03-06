import pathlib
import os
import logging
import importlib.util
import traceback

import discord
import discord.ext


class Handler():
    """This is the Basic Module Loader for AMP to Discord Integration/Interactions"""

    def __init__(self, client: discord.Client):
        self._client = client
        self._cwd = pathlib.Path.cwd()
        self._name = os.path.basename(__file__).title()
        self._logger = logging.getLogger()

        self._logger.info(f'**SUCCESS** Initializing {self._name} ')

    async def cog_auto_loader(self, reload=False):
        """This will load all Cogs inside of the cogs folder."""
        path = f'cogs'  # This gets us to the folder for the module specific scripts to load via the cog.
        loaded_cogs = []
        # Grab all the cogs inside my `cogs` folder and duplicate the list.
        cog_file_list = pathlib.Path.joinpath(self._cwd, 'cogs').iterdir()
        cur_cog_file_list = [entry for entry in cog_file_list]

        # This while loop will force it to load EVERY cog it finds until the list is empty.
        while len(cur_cog_file_list) > 0:
            for script in cur_cog_file_list:
                # Ignore Pycache or similar files.
                # Lets Ignore our Custom Permisisons Cog. We will load it on-demand.
                if script.name.startswith('__') or not script.name.endswith('.py'):
                    cur_cog_file_list.remove(script)
                    continue

                module_name = script.name[4:-3].capitalize()  # File name ofc.
                spec = importlib.util.spec_from_file_location(
                    module_name, script)
                class_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(class_module)

                module_dependencies = getattr(class_module, f'Dependencies')
                if module_dependencies != None:
                    for dependency in getattr(class_module, f'Dependencies'):
                        # If the cog we need isnt loaded; skip. We will come back around to it.
                        if dependency.lower() not in loaded_cogs:
                            continue

                cog = f'{path}.{script.name[:-3]}'

                try:
                    if reload:
                        await self._client.reload_extension(cog)
                        # Append to our loaded cogs for dependency check
                        loaded_cogs.append(script.name.lower())
                        # Remove the entry from our cog list; so we don't attempt to load it again.
                        cur_cog_file_list.remove(script)

                    else:
                        await self._client.load_extension(cog)
                        # Append to our loaded cogs for dependency check
                        loaded_cogs.append(script.name.lower())
                        # Remove the entry from our cog list; so we don't attempt to load it again.
                        cur_cog_file_list.remove(script)

                    self._logger.info(
                        f'**FINISHED LOADING** {self._name} -> **{cog}**')

                except discord.ext.commands.errors.ExtensionAlreadyLoaded:
                    continue

                except FileNotFoundError as e:
                    self._logger.error(
                        f'**ERROR** Loading Cog ** - File Not Found {traceback.format_exc()}')

        self._logger.info(f'**All Cog Modules Loaded**')
