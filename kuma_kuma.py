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

import discord
import logging

import logger
import tokens
import utils

from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

import sys
import io
import aiohttp
import os
import re
from textwrap import indent


class Kuma_Kuma(commands.Bot):
    def __init__(self):
        logger.init()
        self._logger = logging.getLogger()
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        self._prefix = '?'
        super().__init__(intents=intents, command_prefix=self._prefix, strip_after_prefix=True)
        self._message_timeout = 120

        #!TODO! See about setting up `context.reinvoke()`
        self._context = ''

    async def on_ready(self):
        self._logger.info('Kuma Kuma Bear <3')

    async def on_interaction(self, interaction: discord.Interaction):
        self._logger.info('Interaction Occured')
        # print(interaction.command.name)

    def self_check(self, message: discord.Message) -> bool:
        return message.author == client.user


client = Kuma_Kuma()


@utils.author_check(user_id=144462063920611328)
@client.hybrid_command(name='eval')
async def util_eval(context: commands.Context, code: str):
    result = eval(code)
    await context.send(f'```{result}```')


def CodeBlockConvertor(code: str):
    code = re.match("(```py)(.*)(```)", code).group(2).strip()


@client.group(invoke_without_command=True, name="eval", aliases=['```py', '```', 'py', 'python', 'run', 'exec', 'execute'], description="Evaluates the given code")
@utils.author_check(user_id=144462063920611328)
async def util_eval(self, ctx: commands.Context, *, code: CodeBlockConvertor):
    await ctx.channel.typing()
    env = {
        "ctx": ctx,
        "bot": self.bot,
        "message": ctx.message,
        "author": ctx.author,
        "guild": ctx.guild,
        "channel": ctx.channel,
        "discord": discord,
        "commands": commands,
        "os": os,
        "io": io,
        "sys": sys,
        "aiohttp": aiohttp
    }

    function = "async def func():\n" + indent(code, "    ")
    function = function.splitlines()
    x = function[-1].removeprefix("    ")
    if not x.startswith("print") and not x.startswith("return") and not x.startswith(" ") and not x.startswith("yield") and not x.startswith("import"):
        function.pop(function.index(function[-1]))
        function.append(f"    return {x}")
    function = '\n'.join(function)
    await self._handle_eval(env, ctx, function)


@client.hybrid_command(name='clear')
@app_commands.choices(all=[Choice(name='True', value=1), Choice(name='False', value=0)])
@utils.author_check(user_id=144462063920611328)
async def clear(context: commands.Context, channel: discord.abc.GuildChannel, amount: app_commands.Range[int, 0, 100] = 25, all: Choice[int] = 1):
    """Cleans up Messages sent by the Bot. Limit 100"""
    client._logger.info(f'{context.author.name} used Bot Utils Clear...')
    await context.defer()

    if type(all) == Choice:
        all = all.value
    if all == 1:
        messages = await channel.purge(limit=amount, bulk=False)
    else:
        messages = await channel.purge(limit=amount, check=client.self_check, bulk=False)

    return await channel.send(f'Cleaned up **{len(messages)} message(s)**. Wow, look at all this space!', delete_after=client._message_timeout)


@client.hybrid_command(name='sync')
@utils.author_check(user_id=144462063920611328)
@app_commands.choices(local=[Choice(name='True', value=1), Choice(name='False', value=0)])
@app_commands.choices(reset=[Choice(name='True', value=1), Choice(name='False', value=0)])
async def sync(context: commands.Context, local: Choice[int] = True, reset: Choice[int] = False):
    """Syncs Bot Commands to the current guild this command was used in."""
    client._logger.info(f'{context.author.name} used Bot Sync Function...')
    await context.defer()
    # This keeps our DB Guild_ID Current.

    if type(reset) == bool and reset == True or type(reset) == Choice and reset.value() == 1:
        if type(local) == bool and local == True or type(local) == Choice and local.value() == 1:
            # Local command tree reset
            client.tree.clear_commands(guild=context.guild)
            client._logger.info(f'Bot Commands Reset Locally and Sync\'d: {await client.tree.sync(guild=context.guild)}')
            return await context.send(f'**WARNING** Resetting {context.author.name}s Commands Locally...', ephemeral=True, delete_after=client._message_timeout)

        elif context.author.id == 144462063920611328:
            # Global command tree reset
            client.tree.clear_commands(guild=None)
            client._logger.info(f'Bot Commands Reset Globall and Sync\'d: {await client.tree.sync(guild=None)}')
            return await context.send(f'**WARNING** Resetting {context.author.name}s Commands Globally...', ephemeral=True, delete_after=client._message_timeout)
        else:
            return await context.sned('**ERROR** You do not have permission to reset the commands.', ephemeral=True, delete_after=client._message_timeout)

    if type(local) == bool and local == True or type(local) == Choice and local.value() == 1:
        # Local command tree sync
        client.tree.copy_global_to(guild=context.guild)
        client._logger.info(f'Bot Commands Sync\'d Locally: {await client.tree.sync(guild=context.guild)}')
        return await context.send(f'Successfully Sync\'d {context.author.name}s Commands to {context.guild.name}...', ephemeral=True, delete_after=client._message_timeout)

    elif context.author.id == 144462063920611328:
        # Global command tree sync
        client._logger.info(f'Bot Commands Sync\'d Globally: {await client.tree.sync(guild=None)}')
        await context.send(f'Successfully Sync\'d {context.author.name}s Commands Globally...', ephemeral=True, delete_after=client._message_timeout)


# def client_run():
client.run(tokens.token, reconnect=True, log_handler=None)
