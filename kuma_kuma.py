#!/usr/bin/env python3
# Reminder to use `nohup ./kuma_kuma.py > /dev/null &`
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
import asyncio
import contextlib
from email import message
import os
from threading import current_thread, Thread
import logger
from dotenv import load_dotenv
import time
import aiohttp


import discord
from discord.ext import commands
from discord import Intents, Message

import loader
import logging
from typing import Union, TYPE_CHECKING, Any

from utils.context import KumaContext


async def _get_prefix(bot: "Kuma_Kuma", message: Message) -> str:
    # TODO - Have a DB store a prefix per server.
    prefix = ""
    return prefix


class Kuma_Kuma(commands.Bot):

    if TYPE_CHECKING:
        user: discord.ClientUser

    def __init__(self):
        self._logger = logging.getLogger()
        intents: Intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        _app_id = 1053576011935129640
        self._prefix = '?'
        owner_ids: list[int] = [144462063920611328]
        super().__init__(intents=intents, command_prefix=self._prefix, strip_after_prefix=True)
        self._message_timeout = 120

        self._context = commands.Context
        self._start_time: float = time.time()

    async def setup_hook(self) -> None:
        # Modular loading of all cogs.
        # self._db = Kuma_DB()
        # self._db_pool: Coroutine[Any, Any, Pool] = self._db._dev_return()
        # self.session = aiohttp.ClientSession()
        self._handler = loader.Handler(self)
        # await self.load_extension("util_cog.py", package="..repose.dpy_cogs.cogs")
        await self._handler.cog_auto_loader()

    async def on_ready(self) -> None:
        self._logger.info('Kuma Kuma Bear <3')

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return
        await super().on_message(message)

    async def on_command(self, context: commands.Context) -> None:
        self._logger.info(f'{context.author.name} used {context.command}...')

    async def on_command_error(self, context: KumaContext, error: commands.CommandError):
        # assert context.command

        if context.command is not None:
            if isinstance(error, commands.TooManyArguments):
                await context.send(content=f'You called the {context.command.name} command with too many arguments.')
            elif isinstance(error, commands.MissingRequiredArgument):
                await context.send(content=f'You called {context.command.name} command without the required arguments')
        else:
            await context.send(content=str(error))

    async def on_command_completion(self, context: commands.Context):
        if context.message.content.startswith(self._prefix):
            if context.message.channel.permissions_for(context.me).manage_messages:  # type: ignore
                await context.message.delete()

    async def on_reaction_add(self, reaction: discord.Reaction, user: Union[discord.Member, discord.User]) -> None:
        """Called when a message has a reaction added to it. Similar to `on_message_edit()`, 
        if the message is not found in the internal message cache, 
        then this event will not be called. Consider using `on_raw_reaction_add()` instead."""
        if isinstance(reaction.emoji, str):
            self._logger.info(
                f'Emoji Used: {reaction.emoji} Unicode: {reaction.emoji.encode("unicode-escape").decode("ASCII")} by {user.name}')
        else:
            self._logger.info(
                f'Emoji Used: ID: {reaction.emoji.id} Name: {reaction.emoji.name} by {user.name}')

    async def get_context(self, origin: Union[discord.Interaction, discord.Message], /, *, cls=KumaContext) -> KumaContext:
        return await super().get_context(origin, cls=cls)


Kuma = Kuma_Kuma()


@Kuma.hybrid_group(name='kuma')
async def kuma(context: commands.Context) -> None:
    print()


@kuma.command(name='reload')
@commands.is_owner()
async def reload(context: commands.Context) -> None:
    """Reloads all cogs inside the cogs folder."""
    # Kuma._logger.info(f'{context.author.name} used {context.command}...')
    try:
        await Kuma._handler.cog_auto_loader(reload=True)
        await context.send(f'**SUCCESS** Reloading All Cogs ', ephemeral=True, delete_after=Kuma._message_timeout)
    except Exception as e:
        await context.send(content=f"We encountered an **Error** - \n{e}", ephemeral=True, delete_after=Kuma._message_timeout)


@kuma.command(name='sync')
@commands.is_owner()
async def sync(context: commands.Context, local: bool = True, reset: bool = False):
    """Syncs Kuma Commands to the current guild this command was used in."""
    # Kuma._logger.info(f'{context.author.name} used {context.command}...')
    await context.defer()
    # This keeps our DB Guild_ID Current.

    if reset == True:
        if local == True:
            # Local command tree reset
            Kuma.tree.clear_commands(guild=context.guild)
            Kuma._logger.info(f'{Kuma.user.name} Commands Reset Locally and Sync\'d: {await Kuma.tree.sync(guild=context.guild)}')
            return await context.send(f'**WARNING** Resetting `{Kuma.user.name}s` Commands Locally...', ephemeral=True, delete_after=Kuma._message_timeout)

        elif context.author.id == 144462063920611328:
            # Global command tree reset
            Kuma.tree.clear_commands(guild=None)
            Kuma._logger.info(f'{Kuma.user.name} Commands Reset Globall and Sync\'d: {await Kuma.tree.sync(guild=None)}')
            return await context.send(f'**WARNING** Resetting `{Kuma.user.name}s` Commands Globally...', ephemeral=True, delete_after=Kuma._message_timeout)
        else:
            return await context.send('**ERROR** You do not have permission to reset the commands.', ephemeral=True, delete_after=Kuma._message_timeout)

    if local == True:
        # Local command tree sync
        Kuma.tree.copy_global_to(guild=context.guild)  # type:ignore
        Kuma._logger.info(f'{Kuma.user.name} Commands Sync\'d Locally: {await Kuma.tree.sync(guild=context.guild)}')
        return await context.send(f'Successfully Sync\'d `{Kuma.user.name}s` Commands to {context.guild}...', ephemeral=True, delete_after=Kuma._message_timeout)

    elif context.author.id == 144462063920611328:
        # Global command tree sync
        Kuma._logger.info(f'{Kuma.user.name} Commands Sync\'d Globally: {await Kuma.tree.sync(guild=None)}')
        await context.send(f'Successfully Sync\'d `{Kuma.user.name}s` Commands Globally...', ephemeral=True, delete_after=Kuma._message_timeout)


async def main() -> None:
    cur_thread: Thread = current_thread()
    cur_thread.name = "Kuma Kuma Bear"
    token: str = os.environ.get("TOKEN", "")

    async with Kuma:
        await Kuma.start(token)


if __name__ == "__main__":
    load_dotenv()
    logger.init()

    with contextlib.suppress(KeyboardInterrupt, RuntimeError, asyncio.CancelledError):
        asyncio.run(main())
