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
import logging
import os
import time
from pathlib import Path
from sqlite3 import Row
from threading import Thread, current_thread
from typing import TYPE_CHECKING, Any, Union

import discord
from discord import Intents, Message, app_commands
from discord.app_commands import Choice
from discord.ext import commands
from dotenv import load_dotenv

import asqlite
import loader
import logger
from utils.context import KumaContext

script_loc: Path = Path(__file__).parent
DB_FILENAME = "kuma_kuma.sqlite"
DB_PATH: str = script_loc.joinpath(DB_FILENAME).as_posix()

PREFIX_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS prefix (
    id INTEGER PRIMARY KEY NOT NULL,
    serverid INTEGER NOT NULL,
    prefix TEXT
)"""

OWNER_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS owners (
    id INTEGER PRIMARY KEY NOT NULL,
    ownerid INTEGER NOT NULL
)"""


async def _get_prefix(bot: "Kuma_Kuma", message: Message):
    prefixes = [bot._prefix]
    if message.guild is not None:
        _guild: int = message.guild.id

        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""SELECT prefix FROM prefix WHERE serverid = ?""", _guild)
                res: list[Row] = await cur. fetchall()
                if res is not None and len(res) >= 1:
                    prefixes: list[str] = [entry["prefix"] for entry in res]

    wmo_func = commands.when_mentioned_or(*prefixes)
    return wmo_func(bot, message)


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
        self._trusted_users: set[int] = {144462063920611328}
        # owner_id: int = 144462063920611328
        self.owner_id = None
        self.owner_ids: set[int] = {144462063920611328}
        super().__init__(intents=intents, command_prefix=_get_prefix, strip_after_prefix=True)
        self._message_timeout = 120

        self._context = commands.Context
        self._start_time: float = time.time()

    async def setup_hook(self) -> None:
        async with asqlite.connect(DB_FILENAME) as db:
            await db.execute(PREFIX_SETUP_SQL)

        self._handler = loader.Handler(self)
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
        if context.command is not None:
            if isinstance(error, commands.TooManyArguments):
                await context.send(content=f'You called the {context.command.name} command with too many arguments.')
            elif isinstance(error, commands.MissingRequiredArgument):
                await context.send(content=f'You called {context.command.name} command without the required arguments')

    async def on_command_completion(self, context: commands.Context):
        if context.message.content.startswith(self._prefix):
            if context.message.channel.permissions_for(context.me).manage_messages:  # type: ignore
                try:
                    await context.message.delete()
                except discord.errors.NotFound:
                    return
                except Exception as e:
                    self._logger.error(f"We encountered an **Error** - \n{e}")

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


@kuma.command(name='reload', help="Reload all cogs.")
@commands.is_owner()
async def reload(context: commands.Context) -> None:
    """Reloads all cogs inside the cogs folder."""
    await context.typing(ephemeral=True)

    try:
        await Kuma._handler.cog_auto_loader(reload=True)
    except Exception as e:
        await context.send(content=f"We encountered an **Error** - \n{e}", ephemeral=True, delete_after=Kuma._message_timeout)

    await context.send(content=f'**SUCCESS** Reloading All Cogs ', ephemeral=True, delete_after=Kuma._message_timeout)


@kuma.command(name='sync', help="Sync the bot commands to the guild.")
@commands.is_owner()
async def sync(context: commands.Context, local: bool = True, reset: bool = False):
    """Syncs Kuma Commands to the current guild this command was used in."""
    await context.typing(ephemeral=True)
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
            Kuma._logger.info(f'{Kuma.user.name} Commands Reset Globally and Sync\'d: {await Kuma.tree.sync(guild=None)}')
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


@Kuma.hybrid_group(name='prefix')
async def prefix(context: commands.Context) -> None:
    print()


@prefix.command(name="add", help="Add a prefix to Kuma Kuma", aliases=["prea", "pa"])
@commands.is_owner()
async def add_prefix(context: commands.Context, prefix: str):
    if context.guild is not None:
        _guild = context.guild
    else:
        return await context.send(content=f"This command must be used inside a guild", delete_after=Kuma._message_timeout)

    async with asqlite.connect(DB_FILENAME) as db:
        async with db.cursor() as cur:
            await cur.execute("""INSERT INTO prefix(serverid, prefix) VALUES(?, ?)""", _guild.id, prefix.lstrip())
            await db.commit()
            return await context.send(content=f"Added the prefix `{prefix}` for {_guild.name}", delete_after=Kuma._message_timeout)


@prefix.command(name="delete", help="Delete a prefix from Kuma Kuma for a guild.", aliases=["pred", "pd"])
@commands.is_owner()
async def delete_prefix(context: commands.Context, prefix: str):
    if context.guild is not None:
        _guild = context.guild
    else:
        return await context.send(content=f"This command must be used inside a guild", delete_after=Kuma._message_timeout)
    async with asqlite.connect(DB_FILENAME) as db:
        async with db.cursor() as cur:
            await cur.execute("""DELETE FROM prefix WHERE serverid = ? and prefix = ?""", _guild.id, prefix.lstrip())
            await db.commit()
            return await context.send(content=f"Removed the prefix - `{prefix}`", delete_after=Kuma._message_timeout)


@prefix.command(name="clear", help="Clear all prefixes for Kuma Kuma in a guild.", aliases=["prec", "pc"])
@commands.is_owner()
async def clear_prefix(context: commands.Context):
    if context.guild is not None:
        _guild = context.guild
    else:
        return await context.send(content=f"This command must be used inside a guild", delete_after=Kuma._message_timeout)
    async with asqlite.connect(DB_FILENAME) as db:
        async with db.cursor() as cur:
            await cur.execute("""DELETE FROM prefix WHERE serverid = ?""", _guild.id)
            await db.commit()
            return await context.send(content=f"Removed all prefixs for {_guild.name}", delete_after=Kuma._message_timeout)


@prefix.command(name="list", help="List a guilds prefixes")
@commands.is_owner()
async def list_prefix(context: commands.Context) -> Message:
    if context.guild is not None:
        _guild = context.guild
    else:
        return await context.send(content=f"This command must be used inside a guild", delete_after=Kuma._message_timeout)
    async with asqlite.connect(DB_FILENAME) as db:
        async with db.cursor() as cur:
            await cur.execute("""SELECT prefix FROM prefix WHERE serverid = ?""", _guild.id)
            res = await cur.fetchall()
            if res is not None:
                _prefixes = '\n'.join([entry['prefix'] for entry in res])
                return await context.send(content=f"**Current Prefixes:** \n{_prefixes}", delete_after=Kuma._message_timeout)
            else:
                return await context.send(content=f"It appears you do not have any prefix's set", delete_after=Kuma._message_timeout)


@commands.command(name="trusted", help="Add/Remove and list Kuma Kuma Trusted Users.")
@commands.is_owner()
@commands.guild_only()
@app_commands.choices(option=[Choice(name="add", value="add"), Choice(name="remove", value="remove"), Choice(name="list", value="list")])
async def trusted_users(context: KumaContext, option: Choice, member: Union[discord.Member, discord.User]) -> Message | None:
    assert context.guild
    if option == "add":
        if member.id not in Kuma._trusted_users:
            async with asqlite.connect(DB_FILENAME) as db:
                async with db.cursor() as cur:
                    await cur.execute("""INSERT INTO owners(ownerid) VALUES(?)""", member.id)
                    await db.commit()
                    return await context.send(content=f"Added {member.mention} as an owner", ephemeral=True, delete_after=Kuma._message_timeout)
        else:
            return await context.send(content=f"You are already an owner", ephemeral=True, delete_after=Kuma._message_timeout)

    elif option == "remove":
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""DELETE FROM owners WHERE ownerid = ?""", member.id)
                await db.commit()
                res = cur.get_cursor().rowcount
                return await context.send(content=f"Removed {res} Users as an owner", ephemeral=True, delete_after=Kuma._message_timeout)

    elif option == "list":
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""SELECT ownderid FROM owners""")
                res = await cur.fetchall()
                _owners: list[discord.Member] = [await context.guild.fetch_member(entry['id']) for entry in res]
                f_owners = '\n'.join([entry.display_name for entry in _owners])
                return await context.send(content=f"**Current Owners:** \n{f_owners}", ephemeral=True, delete_after=Kuma._message_timeout)


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
