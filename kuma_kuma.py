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
import logger
import tokens
import loader

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands


import logging
from traceback import format_exc as geterr
from typing import Union
from textwrap import indent

import time


class Kuma_Kuma(commands.Bot):
    def __init__(self):
        logger.init()
        self._logger = logging.getLogger()
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        self._prefix = '?'
        owner_ids = [144462063920611328]
        super().__init__(intents=intents, command_prefix=self._prefix, strip_after_prefix=True)
        self._message_timeout = 120

        self._context = commands.Context
        # This is for REPL Sessions
        self.sessions: set[int] = set()

        self._start_time = time.time()

    async def setup_hook(self) -> None:
        # Modular loading of all cogs.
        self._handler = loader.Handler(self)
        await self._handler.cog_auto_loader()
        # return await super().setup_hook()

    async def on_ready(self):
        self._logger.info('Kuma Kuma Bear <3')

    async def on_interaction(self, interaction: discord.Interaction):
        self._logger.info('Interaction Occured')
        # print(interaction.command.name)

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        self._logger.info(f'On Message: {message.content}')
        await super().on_message(message)

    async def on_reaction_add(self, reaction: discord.Reaction, user: Union[discord.Member, discord.User]):
        """Called when a message has a reaction added to it. Similar to `on_message_edit()`, 
        if the message is not found in the internal message cache, 
        then this event will not be called. Consider using `on_raw_reaction_add()` instead."""
        if type(reaction.emoji) == str:
            self._logger.info(
                f'Emoji Used: {reaction.emoji} Unicode: {reaction.emoji.encode("unicode-escape").decode("ASCII")} by {user.name}')
        else:
            self._logger.info(
                f'Emoji Used: ID: {reaction.emoji.id} Name: {reaction.emoji.name} by {user.name}')


Kuma = Kuma_Kuma()


@Kuma.hybrid_group(name='kuma')
async def kuma(interaction: discord.Interaction):
    print()


@kuma.command(name='sync')
@commands.is_owner()
@app_commands.choices(local=[Choice(name='True', value=1), Choice(name='False', value=0)])
@app_commands.choices(reset=[Choice(name='True', value=1), Choice(name='False', value=0)])
async def sync(context: commands.Context, local: Choice[int] = True, reset: Choice[int] = False):
    """Syncs Kuma Commands to the current guild this command was used in."""
    Kuma._logger.info(f'{context.author.name} used {context.command.name}...')
    await context.defer()
    # This keeps our DB Guild_ID Current.

    if ((type(reset) == bool) and (reset == True)) or ((type(reset)) == Choice and (reset.value() == 1)):
        if ((type(local)) == bool and (local == True)) or ((type(local) == Choice) and (local.value() == 1)):
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

    if ((type(local) == bool) and (local == True)) or ((type(local) == Choice) and (local.value() == 1)):
        # Local command tree sync
        Kuma.tree.copy_global_to(guild=context.guild)
        Kuma._logger.info(f'{Kuma.user.name} Commands Sync\'d Locally: {await Kuma.tree.sync(guild=context.guild)}')
        return await context.send(f'Successfully Sync\'d `{Kuma.user.name}s` Commands to {context.guild.name}...', ephemeral=True, delete_after=Kuma._message_timeout)

    elif context.author.id == 144462063920611328:
        # Global command tree sync
        Kuma._logger.info(f'{Kuma.user.name} Commands Sync\'d Globally: {await Kuma.tree.sync(guild=None)}')
        await context.send(f'Successfully Sync\'d `{Kuma.user.name}s` Commands Globally...', ephemeral=True, delete_after=Kuma._message_timeout)

Kuma.run(tokens.token, reconnect=True, log_handler=None)
