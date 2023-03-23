'''
   Copyright (C) 2021-2022 Katelynn Cadwallader.

   This file is part of Kuma Kuma Bear, a Discord Bot.

   Kuma Kuma Bear is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 3, or (at your option)
   any later version.

   Kuma Kuma Bear is distributed in the hope that it will be useful, but WITHOUT
   ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
   or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public
   License for more details.

   You should have received a copy of the GNU General Public License
   along with Kuma Kuma Bear; see the file COPYING.  If not, write to the Free
   Software Foundation, 51 Franklin Street - Fifth Floor, Boston, MA
   02110-1301, USA. 

'''
# Python Libs
import os
import logging
import aiofiles
import psutil
import re
import time
from datetime import timedelta
import aiohttp
import json
import mystbin
import aiohttp
import os
import json
import time
import mystbin
import unicodedata
import inspect
from typing import Union

# Discord Libs
import discord
from discord.ext import commands
from discord import app_commands

from kuma_kuma import Kuma_Kuma

Dependencies = None


class Util(commands.Cog):
    PATTERN: re.Pattern[str] = re.compile(
        r'`{3}(?P<LANG>\w+)?\n?(?P<CODE>(?:(?!`{3}).)+)\n?`{3}', flags=re.DOTALL | re.MULTILINE)

    def __init__(self, client: Kuma_Kuma) -> None:
        self._client: Kuma_Kuma = client
        self._name: str = os.path.basename(__file__).title()
        self._logger = logging.getLogger()
        self._logger.info(f'**SUCCESS** Initializing {self._name} ')
        self._mb_client = mystbin.Client()

    async def cog_unload(self) -> None:
        await self._mb_client.close()

    @property
    def _uptime(self) -> timedelta:
        return timedelta(seconds=(round(time.time() - self._client._start_time)))

    @commands.Cog.listener('on_message')
    async def on_message_listener(self, message: discord.Message) -> None:
        if isinstance(message.channel, discord.abc.GuildChannel) and message.channel.type is not discord.ChannelType.news and str(message.channel.category).lower() not in ['staff', 'dev channels', 'gaming', 'info']:
            # So if our message is over 1k char length and doesn't use our prefix; Lets push it to a mystbin URL.
            if len(message.content) > 1000 and not message.content.startswith(self._client._prefix):
                await self._auto_on_mystbin(message)

    def _self_check(self, message: discord.Message) -> bool:
        return message.author == self._client.user

    async def _auto_on_mystbin(self, message: discord.Message) -> None:
        """Converts a `discord.Message` into a Mystbin URL"""
        content = message.content

        files: list[mystbin.File] = []

        should_upload_to_bin: bool = False

        for idx, match in enumerate(self.PATTERN.finditer(content), start=1):
            language: str = match.group('LANG') or 'python'
            filename: str = f'File-{idx}.{language}'
            file_content: str = match.group('CODE')

            files.append(mystbin.File(filename=filename, content=file_content))
            content = content.replace(match.group(), f'`[{filename}]`')

            should_upload_to_bin = should_upload_to_bin or len(
                file_content) > 1100
        if should_upload_to_bin:
            paste = await self._mb_client.create_multifile_paste(files=files)

            author = discord.utils.escape_markdown(str(message.author))
            await message.channel.send(f"'Hey {author}, *Kuma Kuma Bear* moved your codeblock(s) to `Mystbin`'\n\n{content}\n\n{paste.url}")
            await message.delete()

    async def _auto_on_hastebin(self, message: discord.Message) -> None:
        """Converts a `discord.Message` into a Hastebin URL"""
        url = "https://hastebin.com/documents "
        if message.content.startswith(self._client._prefix):
            message.content = message.content[8:]
        async with aiohttp.ClientSession() as session:
            session_post = await session.post(url=url, data=message.content)
            response = json.loads(await session_post.text())
        await message.channel.send(content=f"Here is {message.author.mention} Hastebin `url` \n> {url[:-10]}raw/{response['key']}")

    async def count_lines(self, path: str, filetype: str = ".py", skip_venv: bool = True):
        lines = 0
        for i in os.scandir(path):
            if i.is_file():
                if i.path.endswith(filetype):
                    if skip_venv and re.search(r"(\\|/)?venv(\\|/)", i.path):
                        continue
                    lines += len((await (await aiofiles.open(i.path, "r")).read()).split("\n"))
            elif i.is_dir():
                lines += await self.count_lines(i.path, filetype)
        return lines

    async def count_others(self, path: str, filetype: str = ".py", file_contains: str = "def", skip_venv: bool = True):
        """Counts the files in directory or functions."""
        line_count = 0
        for i in os.scandir(path):
            if i.is_file():
                if i.path.endswith(filetype):
                    if skip_venv and re.search(r"(\\|/)?venv(\\|/)", i.path):
                        continue
                    line_count += len(
                        [line for line in (await (await aiofiles.open(i.path, "r")).read()).split("\n") if file_contains in line]
                    )
            elif i.is_dir():
                line_count += await self.count_others(i.path, filetype, file_contains)
        return line_count

    @commands.command(help="Shows info about the bot", aliases=["botinfo", "info", "bi"])
    async def about(self, ctx: commands.Context):
        """Tells you information about the bot itself."""
        await ctx.defer()
        information = await self._client.application_info()
        embed = discord.Embed()
        #embed.add_field(name="Latest updates:", value=get_latest_commits(limit=5), inline=False)

        embed.set_author(
            name=f"Made by {information.owner}", icon_url=information.owner.display_avatar.url,)
        memory_usage = psutil.Process().memory_full_info().uss / 1024**2
        cpu_usage = psutil.cpu_percent()

        embed.add_field(
            name="Process", value=f"{memory_usage:.2f} MiB\n{cpu_usage:.2f}% CPU")
        embed.add_field(
            name=f"{self._client.user.name} info:",
            value=f"**Uptime:**\n{self._uptime}")
        try:
            embed.add_field(
                name="Lines",
                value=f"Lines: {await self.count_lines('./', '.py'):,}"
                f"\nFunctions: {await self.count_others('./', '.py', 'def '):,}"
                f"\nClasses: {await self.count_others('./', '.py', 'class '):,}",
            )
        except (FileNotFoundError, UnicodeDecodeError):
            pass

        embed.set_footer(
            text=f"Made with discord.py v{discord.__version__}",
            icon_url="https://i.imgur.com/5BFecvA.png",
        )
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

    @app_commands.command(name='clear')
    @app_commands.describe(all='Default\'s to False, removes ALL messages from selected Channel regardless of who sent them.')
    @app_commands.default_permissions(manage_messages=True)
    async def clear(self, interaction: discord.Interaction, channel: Union[discord.VoiceChannel, discord.TextChannel, discord.Thread, None], amount: app_commands.Range[int, 0, 100] = 15, all: bool = False):
        """Cleans up Messages sent by anyone. Limit 100"""
        await interaction.response.defer()

        assert isinstance(
            interaction.channel, (discord.VoiceChannel, discord.TextChannel, discord.Thread))
        channel = channel or interaction.channel  # type:ignore

        if all:
            messages = await channel.purge(limit=amount, bulk=False)
        else:
            messages = await channel.purge(limit=amount, check=self._self_check, bulk=False)

        return await channel.send(f'Cleaned up **{len(messages)} {"messages" if len(messages) > 1 else "message"}**. Wow, look at all this space!', delete_after=self._client._message_timeout)

    @commands.command(name='charinfo')
    async def charinfo(self, context: commands.Context, *, characters: str):
        """Shows you information about a number of characters.
        Only up to 25 characters at a time.
        """

        def to_string(c):
            digit = f'{ord(c):x}'
            name = unicodedata.name(c, 'Name not found.')
            return f'`\\U{digit:>08}`: {name} - `{c}` \N{EM DASH} {c} \N{EM DASH} <http://www.fileformat.info/info/unicode/char/{digit}>'

        msg = '\n'.join(map(to_string, characters))
        if len(msg) > 2000:
            return await context.send('Output too long to display.')
        await context.send(msg)

    @commands.command(name='mimic')
    @commands.is_owner()
    async def mimic(self, context: commands.Context):
        """Invokes the previously run `command` with parameters."""
        await context.send(f'*Kuma Kuma Kuma* `{self._context.command}`')
        await self._context.reinvoke(restart=True)

    @commands.command(name='ping')
    async def ping(self, context: commands.Context):
        """Pong..."""
        self._context = context
        await context.send(f'Pong `{round(self._client.latency * 1000)}ms`', ephemeral=True, delete_after=self._client._message_timeout)

    @commands.command(name='webhooks')
    async def webhooks(self, context: commands.Context, channel: Union[discord.VoiceChannel, discord.TextChannel, discord.StageChannel, discord.ForumChannel, None]):
        """Displays a channels webhooks by `Name` and `ID`"""

        assert isinstance(context.channel, (discord.VoiceChannel, discord.TextChannel, discord.StageChannel, discord.ForumChannel))

        channel = channel or context.channel
        channel_webhooks = "\n".join([f"**{webhook.name}** | ID: `{webhook.id}`" for webhook in await channel.webhooks()])
        await context.send(f'> {channel.mention} Webhooks \n{channel_webhooks}')

    @commands.command(name='hb')
    async def hastebin_me(self, context: commands.Context):
        """Converts a `str` to a Haste bin url"""
        await context.defer()
        await self._auto_on_hastebin(context.message)
        await context.message.delete()

    @commands.command(name='mb')
    async def mystbin_me(self, context: commands.Context):
        """Converts a `str` to a Mystbin url"""
        await context.defer()
        await self._auto_on_mystbin(context.message)
        await context.message.delete()

    @commands.command(name='link')
    async def url_linking(self, context: commands.Context, var: str):
        """Provides a Useful URL based upon the var parameter"""
        listing = {
            # Gatekeeper Github Links
            "gatekeeper": "https://github.com/k8thekat/GatekeeperV2",
            "gk": "https://github.com/k8thekat/GatekeeperV2",

            # Cube Coders Links
            "amp": "https://discord.gg/cubecoders",
            "cubecoders": "https://cubecoders.com/",
            "cc": "https://cubecoders.com/",

            # Discord.py Server Links
            "dpy": "https://discord.gg/dpy",
            "d.py": "https://discord.gg/dpy",
            "discord.py": "https://discord.gg/dpy",
            "dpy_docs": "https://discordpy.readthedocs.io/en/stable/",

            # Gatekeeper Wiki Links
            "wiki": "https://github.com/k8thekat/GatekeeperV2/wiki",
            "commands": "https://github.com/k8thekat/GatekeeperV2/wiki/Commands",
            "perms": "https://github.com/k8thekat/GatekeeperV2/wiki/Permissions",
            "banners": "https://github.com/k8thekat/GatekeeperV2/wiki/Server-Banners",
            "whitelist": "https://github.com/k8thekat/GatekeeperV2/wiki/Auto-Whitelisting",
            "autowl": "https://github.com/k8thekat/GatekeeperV2/wiki/Auto-Whitelisting",
            "wl": "https://github.com/k8thekat/GatekeeperV2/wiki/Auto-Whitelisting",

            # Patreon/Donation Links
            "patreon": "https://www.patreon.com/Gatekeeperv2"}

        var = var.lower()
        if var in listing:
            await context.send(f'{listing[var]}')
        elif var == "?":
            await context.send(f"Possible Entries:\n> {(', ').join([key.title() for key in listing.keys()])}")

    @commands.command(name='source')
    async def source(self, context: commands.Context, *, command: Union[str, None]):
        """Displays my full source code or for a specific command.
        To display the source code of a subcommand you can separate it by
        periods, e.g. tag.create for the create subcommand of the tag command
        or by spaces.
        """

        source_url = 'https://github.com/k8thekat/Kuma_Kuma'
        branch = 'main'
        if command is None:
            return await context.send(source_url)

        if command == 'help':
            src = type(self._client.help_command)
            module = src.__module__
            filename = inspect.getsourcefile(src)

        else:
            obj = self._client.get_command(command.replace('.', ' '))
            if obj is None:
                return await context.send('Could not find command.')

            # since we found the command we're looking for, presumably anyway, let's
            # try to access the code itself
            src = obj.callback.__code__
            module = obj.callback.__module__
            filename = src.co_filename

        lines, firstlineno = inspect.getsourcelines(src)
        if not module.startswith('discord'):
            # not a built-in command
            if filename is None:
                return await context.send('Could not find source for command.')

            location = os.path.relpath(filename).replace('\\', '/')
        else:
            location = module.replace('.', '/') + '.py'
            source_url = 'https://github.com/k8thekat/Kuma_Kuma'
            branch = 'main'

        final_url = f'<{source_url}/blob/{branch}/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>'
        await context.send(final_url)


async def setup(client):
    await client.add_cog(Util(client))
