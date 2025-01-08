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

import asyncio
import inspect
import io
import logging
import os
import traceback
from contextlib import redirect_stdout

import discord
from discord.ext import commands

from utils import cog


class Repl(cog.KumaCog):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot=bot)
        self._name: str = os.path.basename(__file__).title()
        self.logger.info(f"**SUCCESS** Initializing {self._name}")

    async def cog_load(self) -> None:
        self._sessions: set[int] = set()

    @commands.Cog.listener("on_message")
    async def on_message_listener(self, message: discord.Message):
        # This is for our `REPL` sessions.
        if message.channel.id in self._sessions:
            return

    @commands.command(hidden=True)
    @commands.is_owner()
    async def repl(self, ctx: commands.Context):
        """Launches an interactive REPL session."""
        variables = {
            "ctx": ctx,
            "bot": self.bot,
            "message": ctx.message,
            "guild": ctx.guild,
            "channel": ctx.channel,
            "author": ctx.author,
            "_": None,
        }

        if ctx.channel.id in self._sessions:
            await ctx.send("Already running a REPL session in this channel. Exit it with `quit`.")
            return

        self._sessions.add(ctx.channel.id)
        await ctx.send("Enter code to execute or evaluate. `exit()` or `quit` to exit.")

        def check(message: discord.Message):
            return (
                message.author.id == ctx.author.id
                and message.channel.id == ctx.channel.id
                and message.content.startswith("`")
            )

        while True:
            try:
                response = await self.bot.wait_for("message", check=check, timeout=10.0 * 60.0)
            except asyncio.TimeoutError:
                await ctx.send("Exiting REPL session.")
                self._sessions.remove(ctx.channel.id)
                break

            cleaned = self.cleanup_code(response.content)

            if cleaned in ("quit", "exit", "exit()"):
                await ctx.send("Exiting.")
                self._sessions.remove(ctx.channel.id)
                return

            if cleaned in ("?"):
                await ctx.send(f"{variables.keys()}")

            executor = exec
            code = ""
            if cleaned.count("\n") == 0:
                # single statement, potentially 'eval'
                try:
                    code = compile(cleaned, "<repl session>", "eval")
                except SyntaxError:
                    pass
                else:
                    executor = eval

            if executor is exec:
                try:
                    code = compile(cleaned, "<repl session>", "exec")
                except SyntaxError as e:
                    await ctx.send(self.get_syntax_error(e))
                    continue

            variables["message"] = response

            fmt = None
            stdout = io.StringIO()

            try:
                with redirect_stdout(stdout):
                    result = executor(code, variables)
                    if inspect.isawaitable(result):
                        result = await result
            except Exception:
                value = stdout.getvalue()
                fmt = f"```py\n{value}{traceback.format_exc()}\n```"
            else:
                value = stdout.getvalue()
                if result is not None:
                    fmt = f"```py\n{value}{result}\n```"
                    variables["_"] = result
                elif value:
                    fmt = f"```py\n{value}\n```"

            try:
                if fmt is not None:
                    if len(fmt) > 2000:
                        await ctx.send("Content too big to be printed.")
                    else:
                        await ctx.send(fmt)
            except discord.Forbidden:
                pass
            except discord.HTTPException as e:
                await ctx.send(f"Unexpected error: `{e}`")

    def cleanup_code(self, content: str) -> str:
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith("```") and content.endswith("```"):
            return "\n".join(content.split("\n")[1:-1])

        # remove `foo`
        return content.strip("` \n")

    def get_syntax_error(self, e: SyntaxError) -> str:
        if e.text is None:
            return f"```py\n{e.__class__.__name__}: {e}\n```"
        return f"```py\n{e.text}{'^':>{e.offset}}\n{e.__class__.__name__}: {e}```"


async def setup(bot: commands.Bot):
    await bot.add_cog(Repl(bot))
