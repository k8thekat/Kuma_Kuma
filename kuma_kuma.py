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
import utils

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands


import logging
import sys
import io
import aiohttp
import os
import json
from traceback import format_exc as geterr
from typing import Union
from textwrap import indent
import import_expression
import time
from io import StringIO
import mystbin


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

        self._context = commands.Context

    async def on_ready(self):
        self._logger.info('Kuma Kuma Bear <3')

    async def on_interaction(self, interaction: discord.Interaction):
        self._logger.info('Interaction Occured')
        # print(interaction.command.name)

    async def on_message(self, message: discord.Message):
        if message.author != self.user:
            self._logger.info(f'On Message: {message.content}')

        if str(message.channel.type).lower() not in ['news'] and str(message.channel.category).lower() not in ['staff', 'test_channels', 'gaming', 'info']:
            if len(message.content) > 1500 and not message.content.startswith(self._prefix):
                await message.reply('Hey your message was a little too long; *Kuma Kuma* I moved it to `Mystbin`')
                await self.auto_on_mystbin(message)
                return await message.delete()

        await super().on_message(message)

    async def auto_on_mystbin(self, message: discord.Message):
        """Converts a `discord.Message` into a Mystbin URL"""
        mb_client = mystbin.Client()
        paste = await mb_client.create_paste(filename=f'{message.author.name}', content=message.content)
        await mb_client.close()
        await message.channel.send(content=f"Here is your Mystbin `url` \n> {paste.url}")

    async def auto_on_hastebin(self, message: discord.Message):
        """Converts a `discord.Message` into a Hastebin URL"""
        url = "https://hastebin.com/documents "
        if message.content.startswith(self._prefix):
            message.content = message.content[8:]
        async with aiohttp.ClientSession() as session:
            session_post = await session.post(url=url, data=message.content)
            response = json.loads(await session_post.text())
        await message.channel.send(content=f"Here is your Hastebin `url` \n> {url[:-10]}raw/{response['key']}")

    async def on_reaction_add(self, reaction: discord.Reaction, user: Union[discord.Member, discord.User]):
        """Called when a message has a reaction added to it. Similar to `on_message_edit()`, if the message is not found in the internal message cache, then this event will not be called. Consider using `on_raw_reaction_add()` instead."""
        if type(reaction.emoji) == str:
            self._logger.info(
                f'Emoji Used: {reaction.emoji} Unicode: {reaction.emoji.encode("unicode-escape").decode("ASCII")} by {user.name}')
        else:
            self._logger.info(
                f'Emoji Used: ID: {reaction.emoji.id} Name: {reaction.emoji.name} by {user.name}')

    def self_check(self, message: discord.Message) -> bool:
        return message.author == self.user

    async def _handle_eval(self, env, context: commands.Context, function, as_generator=False):
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
                    err = err.split(
                        "return compile(source, filename, mode, flags,")[1]
                except:
                    try:
                        err = err.split("res = await func()")[1]
                    except:
                        pass
                msg = f"n```py\n{err}\n```"
                #msg = filterTxt(msg)
                errorEm = discord.Embed(
                    title="Eval Error", description=msg, color=discord.Color.red())
                await context.send(embed=errorEm)
                return
            ping = time.monotonic() - ping
            ping = ping * 1000
            if res:
                msg = f"```py\n{res}\n{otp}\n```"
                #msg = filterTxt(msg)
                returnedEm = discord.Embed(
                    title="Returned", description=msg, color=discord.Color.green())
                returnedEm.set_footer(text=f"Finished in {ping}ms")
                await context.send(embed=returnedEm)
            else:
                msg = f"```py\n{otp}\n```"
                #msg = filterTxt(msg)
                outputEm = discord.Embed(
                    title="Output", description=msg, color=discord.Color.green())
                outputEm.set_footer(text=f"Finished in {ping}ms")
                await context.send(embed=outputEm)


Kuma = Kuma_Kuma()


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
        return self._string_io.getvalue()


def CodeBlockConvertor(code: str):
    if code.startswith("```py") and code.endswith("```"):
        code = code.replace("```py", "").replace("```", "")
    return code


def CharConvertor(char: Union[discord.Emoji, str]) -> Union[discord.Emoji, str]:
    if type(char) == str:
        return char.encode("unicode_escape").decode("ASCII")
    if type(char) == discord.Emoji:
        return char


@Kuma.hybrid_group(name='kuma')
async def kuma(interaction: discord.Interaction):
    print()


@kuma.command(invoke_without_command=True, name="eval", aliases=['```py', '```', 'py', 'python', 'run', 'exec', 'execute'], description="Evaluates the given code")
@utils.author_check(user_id=144462063920611328)
async def eval(context: commands.Context, *, code: CodeBlockConvertor):
    Kuma._logger.info(f'{context.author.name} used {context.command.name}...')
    await context.channel.typing()
    env = {
        "context": context,
        "Kuma": Kuma,
        "message": context.message,
        "author": context.author,
        "guild": context.guild,
        "channel": context.channel,
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
    await Kuma._handle_eval(env, context, function)


@kuma.command(name='clear')
@app_commands.choices(all=[Choice(name='True', value=1), Choice(name='False', value=0)])
@app_commands.describe(all='Default\'s to True, removes ALL commands from selected Channel regardless of who sent them.')
@utils.author_check(user_id=144462063920611328)
async def clear(context: commands.Context, channel: discord.abc.GuildChannel = None, amount: app_commands.Range[int, 0, 100] = 50, all: Choice[int] = 1):
    """Cleans up Messages sent by the Kuma. Limit 100"""
    Kuma._logger.info(f'{context.author.name} used {context.command.name}...')
    Kuma._context = context
    await context.defer()

    if channel == None:
        channel = context.channel

    if type(all) == Choice:
        all = all.value

    if all == 1:
        messages = await channel.purge(limit=amount, bulk=False)
    else:
        messages = await channel.purge(limit=amount, check=Kuma.self_check, bulk=False)

    return await channel.send(f'Cleaned up **{len(messages)} {"messages" if len(messages) > 1 else "message"}**. Wow, look at all this space!', delete_after=Kuma._message_timeout)


@kuma.command(name='sync')
@utils.author_check(user_id=144462063920611328)
@app_commands.choices(local=[Choice(name='True', value=1), Choice(name='False', value=0)])
@app_commands.choices(reset=[Choice(name='True', value=1), Choice(name='False', value=0)])
async def sync(context: commands.Context, local: Choice[int] = True, reset: Choice[int] = False):
    """Syncs Kuma Commands to the current guild this command was used in."""
    Kuma._logger.info(f'{context.author.name} used {context.command.name}...')
    await context.defer()
    # This keeps our DB Guild_ID Current.

    if type(reset) == bool and reset == True or type(reset) == Choice and reset.value() == 1:
        if type(local) == bool and local == True or type(local) == Choice and local.value() == 1:
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

    if type(local) == bool and local == True or type(local) == Choice and local.value() == 1:
        # Local command tree sync
        Kuma.tree.copy_global_to(guild=context.guild)
        Kuma._logger.info(f'{Kuma.user.name} Commands Sync\'d Locally: {await Kuma.tree.sync(guild=context.guild)}')
        return await context.send(f'Successfully Sync\'d `{Kuma.user.name}s` Commands to {context.guild.name}...', ephemeral=True, delete_after=Kuma._message_timeout)

    elif context.author.id == 144462063920611328:
        # Global command tree sync
        Kuma._logger.info(f'{Kuma.user.name} Commands Sync\'d Globally: {await Kuma.tree.sync(guild=None)}')
        await context.send(f'Successfully Sync\'d `{Kuma.user.name}s` Commands Globally...', ephemeral=True, delete_after=Kuma._message_timeout)


@kuma.command(name='charinfo')
@utils.author_check(user_id=144462063920611328)
async def unicode(context: commands.Context, var: CharConvertor):
    """Displays `unicode` information for the provided `emoji/character`"""
    Kuma._logger.info(f'{context.author.name} used {context.command.name}...')
    await context.send(f'> `{var}`')


@kuma.command(name='mimic')
@utils.author_check(user_id=144462063920611328)
async def mimic(context: commands.Context):
    """Invokes the previously run `command` with parameters."""
    Kuma._logger.info(f'{context.author.name} used {context.command.name}...')
    await context.send(f'*Kuma Kuma Kuma* `{Kuma._context.command.name}`')
    await Kuma._context.reinvoke(restart=True)


@kuma.command(name='ping')
@utils.author_check(user_id=144462063920611328)
async def ping(context: commands.Context):
    """Pong..."""
    Kuma._logger.info(f'{context.author.name} used {context.command.name}...')
    Kuma._context = context
    await context.send(f'Pong `{round(Kuma.latency * 1000)}ms`', ephemeral=True, delete_after=Kuma._message_timeout)


@kuma.command(name='webhooks')
@utils.author_check(user_id=144462063920611328)
async def webhooks(context: commands.Context, channel: discord.abc.GuildChannel = None):
    """Displays a channels webhooks by `Name` and `ID`"""
    if channel == None:
        channel = context.channel
    channel_webhooks = "\n".join([f"**{x.name}** | ID: `{x.id}`" for x in await channel.webhooks()])
    await context.send(f'> {channel.mention} Webhooks \n{channel_webhooks}')


@kuma.command(name='hb')
@utils.author_check(user_id=144462063920611328)
async def hastebin_me(context: commands.Context):
    """Converts a `str` to a Haste bin url"""
    await context.defer()
    await Kuma.auto_on_hastebin(context.message)
    await context.message.delete()


@kuma.command(name='mb')
@utils.author_check(user_id=144462063920611328)
async def mystbin_me(context: commands.Context):
    """Converts a `str` to a Mystbin url"""
    await context.defer()
    await Kuma.auto_on_mystbin(context.message)
    await context.message.delete()

Kuma.run(tokens.token, reconnect=True, log_handler=None)
