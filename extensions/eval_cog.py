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

import io
import os
import sys
import time
from io import StringIO
from textwrap import indent
from traceback import format_exc as geterr
from typing import Annotated

import aiohttp
import discord
import import_expression
from discord.ext import commands

from utils import cog
from utils.converter import CodeBlockConverter

Dependencies = None


class Eval(cog.KumaCog):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot=bot)
        self._name: str = os.path.basename(__file__).title()
        self.logger.info(f"**SUCCESS** Initializing {self._name}")

    # def CharConvertor(self, char: Union[discord.Emoji, str]) -> Union[discord.Emoji, str]:
    #     if isinstance(char, str):
    #         return char.encode("unicode_escape").decode("ASCII")
    #     else:
    #         return char

    @commands.command(
        invoke_without_command=True,
        name="eval",
        aliases=["```py", "```", "py", "python", "run", "exec", "execute"],
        description="Evaluates the given code",
    )
    @commands.is_owner()
    async def eval(self, context: commands.Context, *, code: Annotated[str, CodeBlockConverter]):
        self.logger.info(f"{context.author.name} used {context.command}...")
        await context.channel.typing()
        env = {
            "context": context,
            "kuma": self.bot,
            "message": context.message,
            "author": context.author,
            "guild": context.guild,
            "channel": context.channel,
            "discord": discord,
            "commands": commands,
            "os": os,
            "io": io,
            "sys": sys,
            "aiohttp": aiohttp,
        }

        function = "async def func():\n" + indent(code, "    ")
        function = function.splitlines()
        x = function[-1].removeprefix("    ")
        if (
            not x.startswith("print")
            and not x.startswith("return")
            and not x.startswith(" ")
            and not x.startswith("yield")
            and not x.startswith("import")
        ):
            function.pop(function.index(function[-1]))
            function.append(f"    return {x}")
        function = "\n".join(function)
        await self._handle_eval(env, context, function)

    async def _handle_eval(self, env, context: commands.Context, function, as_generator=False):
        """Handles the code snippet inside an eval"""
        with RedirectedStdout() as otp:
            try:
                import_expression.exec(function, env)
                func = env["func"]
                ping = time.monotonic()
                if not as_generator:
                    res = await func()
                else:
                    res = None
                    async for x in func():
                        print(x)
            except Exception as e:
                if str(e) == "object async_generator can't be used in 'await' expression":
                    return await self._handle_eval(env, context, function, True)

                err = geterr()
                try:
                    err = err.split("return compile(source, filename, mode, flags,")[1]
                except:
                    try:
                        err = err.split("res = await func()")[1]
                    except:
                        pass
                msg = f"n```py\n{err}\n```"
                return await context.send(content=msg)

            ping = time.monotonic() - ping
            ping = ping * 1000

            if res:
                msg = f"```py\n{res}\n{otp}\n```"
                await context.send(content=msg)

            else:
                msg = f"```py\n{otp}\n```"
                await context.send(content=msg)


async def setup(bot: commands.Bot):
    await bot.add_cog(Eval(bot))


class RedirectedStdout:
    def __init__(self):
        self._stdout = None
        self._string_io = None

    def __enter__(self):
        self._stdout = sys.stdout
        sys.stdout = self._string_io = StringIO()
        return self

    def __exit__(self, type, value, traceback):
        sys.stdout = self._stdout

    def __str__(self):
        if self._string_io:
            return self._string_io.getvalue()
        return ""
