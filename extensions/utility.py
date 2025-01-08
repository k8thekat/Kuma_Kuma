"""
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

"""

from __future__ import annotations

import inspect
import json
import os
import re
import time
import unicodedata
from datetime import timedelta
from typing import TYPE_CHECKING, Union

import aiofiles
import aiohttp
import discord
import mystbin
import psutil
from discord import app_commands
from discord.ext import commands

from utils.cog import KumaCog as Cog  # need to replace with your own Cog class
from utils.utils import count_lines, count_others

if TYPE_CHECKING:
    from kuma_kuma import Kuma_Kuma
    from utils.context import KumaContext as Context


class Utility(Cog):
    """A class to house useful commands about the bot and it's code."""

    def __init__(self, bot: Kuma_Kuma) -> None:
        super().__init__(bot=bot)

    @commands.command(help="Shows info about the bot", aliases=["botinfo", "info", "bi"])
    async def about(self, ctx: Context) -> None:
        """Tells you information about the bot itself."""
        await ctx.defer()
        assert self.bot.user
        information: discord.AppInfo = await self.bot.application_info()
        embed = discord.Embed()
        # embed.add_field(name="Latest updates:", value=get_latest_commits(limit=5), inline=False)

        embed.set_author(
            name=f"Made by {information.owner.name}",
            icon_url=information.owner.display_avatar.url,
        )
        memory_usage = psutil.Process().memory_full_info().uss / 1024**2
        cpu_usage: float = psutil.cpu_percent()

        embed.add_field(name="Process", value=f"{memory_usage:.2f} MBs \n{cpu_usage:.2f}% CPU")
        embed.add_field(name=f"{self.bot.user.name} info:", value=f"**Uptime:**\n{self.bot.uptime}")
        try:
            embed.add_field(
                name="Lines",
                value=f"Lines: {await count_lines(path='./', filetype='.py'):,}"
                f"\nFunctions: {await count_others(path='./', filetype='.py', file_contains='def '):,}"
                f"\nClasses: {await count_others(path='./', filetype='.py', file_contains='class '):,}",
            )
        except (FileNotFoundError, UnicodeDecodeError):
            pass

        embed.set_footer(
            text=f"Made with discord.py v{discord.__version__}",
            icon_url="https://i.imgur.com/5BFecvA.png",
        )
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

    @commands.command(name="charinfo")
    async def charinfo(self, context: Context, *, characters: str) -> discord.Message | None:
        """Shows you information about a number of characters.
        Only up to 25 characters at a time.
        """

        def to_string(c: str) -> str:
            digit: str = f"{ord(c):x}"
            name: str = unicodedata.name(c, "Name not found.")
            return f"`\\U{digit:>08}`: {name} - `{c}` \N{EM DASH} {c} \N{EM DASH} <http://www.fileformat.info/info/unicode/char/{digit}>"

        msg: str = "\n".join(map(to_string, characters))
        if len(msg) > 2000:
            return await context.send(content="Output too long to display.")
        await context.send(content=msg)

    @commands.command(name="ping")
    async def ping(self, context: Context) -> discord.Message:
        """Pong..."""
        return await context.send(
            content=f"Pong `{round(number=self.bot.latency * 1000)}ms`", ephemeral=True, delete_after=self.message_timeout
        )

    @commands.command(name="get_webhooks", help="Displays a channels webhooks by `Name` and `ID`", aliases=["getwh", "gwh"])
    @commands.guild_only()
    @commands.has_permissions(manage_webhooks=True)
    async def get_webhooks(
        self,
        context: Context,
        channel: Union[discord.VoiceChannel, discord.TextChannel, discord.StageChannel, discord.ForumChannel, None],
    ) -> discord.Message:
        assert isinstance(
            context.channel, (discord.VoiceChannel, discord.TextChannel, discord.StageChannel, discord.ForumChannel)
        )

        channel = channel or context.channel
        channel_webhooks: str = "\n".join([
            f"**{webhook.name}** | ID: `{webhook.id}`" for webhook in await channel.webhooks()
        ])
        return await context.send(content=f"> {channel.mention} Webhooks \n{channel_webhooks}")

    # todo - Improve catalog and alternate words for matching.
    @commands.command(name="link")
    async def url_linking(self, context: Context, var: str) -> None:
        """Provides a Useful URL based upon the var parameter"""
        listing: dict[str, str] = {
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
            "gkwiki": "https://github.com/k8thekat/GatekeeperV2/wiki",
            "gkcommands": "https://github.com/k8thekat/GatekeeperV2/wiki/Commands",
            "gkperms": "https://github.com/k8thekat/GatekeeperV2/wiki/Permissions",
            "gkbanners": "https://github.com/k8thekat/GatekeeperV2/wiki/Server-Banners",
            "gkwl": "https://github.com/k8thekat/GatekeeperV2/wiki/Auto-Whitelisting",
        }

        var = var.lower()
        if var in listing:
            await context.send(f"{listing[var]}")
        elif var == "?":
            await context.send(f"Possible Entries:\n> {(', ').join([key.title() for key in listing])}")

    @commands.command(name="source")
    async def source(self, context: Context, *, command: Union[str, None]) -> discord.Message | None:
        """Displays my full source code or for a specific command.
        To display the source code of a subcommand you can separate it by
        periods, e.g. tag.create for the create subcommand of the tag command
        or by spaces.
        """
        source_url = "https://github.com/k8thekat/Kuma_Kuma"
        branch = "main"
        if command is None:
            return await context.send(source_url)

        if command == "help":
            src = type(self.bot.help_command)
            module = src.__module__
            filename = inspect.getsourcefile(src)

        else:
            obj = self.bot.get_command(command.replace(".", " "))
            if obj is None:
                return await context.send("Could not find command.")

            # since we found the command we're looking for, presumably anyway, let's
            # try to access the code itself
            src = obj.callback.__code__
            module = obj.callback.__module__
            filename = src.co_filename
            code_class = obj._cog

            # Handles my seperate repo URLs. (Could store this as part of the cog class?)
            # This requires you do define `repo_url` per script for files in a different parent directory than your bot.py
            if code_class != None and hasattr(code_class, "repo_url"):
                source_url = getattr(obj._cog, "repo_url")

        lines, firstlineno = inspect.getsourcelines(src)
        if not module.startswith("discord"):
            # not a built-in command
            if filename is None:
                return await context.send("Could not find source for command.")

            location = os.path.relpath(filename).replace("\\", "/")

        else:
            location = module.replace(".", "/") + ".py"
            branch = "main"

        final_url = f"<{source_url}/blob/{branch}/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>"
        await context.send(final_url)

    # todo - Implement a git pull method to be used for cog edits
    # @commands.command(name="gpull", aliases=["gp"])
    # async def git_pull(self, context: Context, repo: str = _default_repo, branch: str = _default_branch):
    #     import git

    #     repo = git.Repo().init()  # type: ignore
    #     res = git.remote.Remote(repo=repo, name="origin")  # type: ignore
    #     res.pull(branch)
    #     # import git
    #     # g = git.Git('git-repo')
    #     # g.pull('origin','branch-name')


async def setup(bot: Kuma_Kuma) -> None:
    await bot.add_cog(Utility(bot))
