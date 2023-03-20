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


# Discord Libs
import discord
from discord.ext import commands
from discord import app_commands
from discord.app_commands import Choice

Dependencies = None


class Util(commands.Cog):
    def __init__(self, client=commands.Bot) -> None:
        self._client = client
        self._name = os.path.basename(__file__).title()
        self._logger = logging.getLogger()
        self._logger.info(f'**SUCCESS** Initializing {self._name} ')

    @property
    def _uptime(self) -> timedelta:
        return timedelta(seconds=(round(time.time() - self._client._start_time)))

    @property
    def _message_timeout(self) -> int:
        return self._client._message_timeout

    @property
    def _prefix(self) -> str:
        return self._client._prefix

    @commands.Cog.listener('on_message')
    async def on_message_listener(self, message: discord.Message):
        if str(message.channel.type).lower() not in ['news', 'private'] and str(message.channel.category).lower() not in ['staff', 'test_channels', 'gaming', 'info']:
            if len(message.content) > 1500 and not message.content.startswith(self._prefix):
                await message.reply('Hey your message was a little too long; *Kuma Kuma Bear* moved it to `Mystbin`')
                await self._auto_on_mystbin(message)
                return await message.delete()

    def _self_check(self, message: discord.Message) -> bool:
        return message.author == self._client.user

    async def _auto_on_mystbin(self, message: discord.Message):
        """Converts a `discord.Message` into a Mystbin URL"""
        mb_client = mystbin.Client()
        paste = await mb_client.create_paste(filename=f'{message.author.name}', content=message.content)
        await mb_client.close()
        await message.channel.send(content=f"Here is {message.author.mention} Mystbin `url` \n> {paste.url}")

    async def _auto_on_hastebin(self, message: discord.Message):
        """Converts a `discord.Message` into a Hastebin URL"""
        url = "https://hastebin.com/documents "
        if message.content.startswith(self._prefix):
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

    @commands.command(name='clear')
    @app_commands.choices(all=[Choice(name='True', value=1), Choice(name='False', value=0)])
    @app_commands.describe(all='Default\'s to True, removes ALL commands from selected Channel regardless of who sent them.')
    @commands.is_owner()
    async def clear(self, context: commands.Context, channel: discord.abc.GuildChannel = None, amount: app_commands.Range[int, 0, 100] = 50, all: Choice[int] = 1):
        """Cleans up Messages sent by anyone. Limit 100"""
        self._logger.info(
            f'{context.author.name} used {context.command.name}...')
        self._context = context
        await context.defer()


        if channel == None or type(channel) == str:
            # This should take the numeric value we pass in via ?kuma clear 100 turn into our amount
            amount = channel
            channel = context.channel

        if type(all) == Choice:
            all = all.value

        if all == 1:
            messages = await channel.purge(limit=amount, bulk=False)
        else:
            messages = await channel.purge(limit=amount, check=self._self_check, bulk=False)

        return await channel.send(f'Cleaned up **{len(messages)} {"messages" if len(messages) > 1 else "message"}**. Wow, look at all this space!', delete_after=self._message_timeout)

    @commands.command(name='charinfo')
    async def charinfo(self, context: commands.Context, *, characters: str):
        """Shows you information about a number of characters.
        Only up to 25 characters at a time.
        """
        self._logger.info(
            f'{context.author.name} used {context.command.name}...')

        def to_string(c):
            digit = f'{ord(c):x}'
            name = unicodedata.name(c, 'Name not found.')
            return f'`\\U{digit:>08}`: {name} - {c} \N{EM DASH} <http://www.fileformat.info/info/unicode/char/{digit}>'

        msg = '\n'.join(map(to_string, characters))
        if len(msg) > 2000:
            return await context.send('Output too long to display.')
        await context.send(msg)

    @commands.command(name='mimic')
    @commands.is_owner()
    async def mimic(self, context: commands.Context):
        """Invokes the previously run `command` with parameters."""
        self._logger.info(
            f'{context.author.name} used {context.command.name}...')
        await context.send(f'*Kuma Kuma Kuma* `{self._context.command.name}`')
        await self._context.reinvoke(restart=True)

    @commands.command(name='ping')
    async def ping(self, context: commands.Context):
        """Pong..."""
        self._logger.info(
            f'{context.author.name} used {context.command.name}...')
        self._context = context
        await context.send(f'Pong `{round(self._client.latency * 1000)}ms`', ephemeral=True, delete_after=self._message_timeout)

    @commands.command(name='webhooks')
    async def webhooks(context: commands.Context, channel: discord.abc.GuildChannel = None):
        """Displays a channels webhooks by `Name` and `ID`"""
        if channel == None:
            channel = context.channel
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
    async def url_linking(context: commands.Context, var: str):
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
    async def source(self, context: commands.Context, *, command: str = None):
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
