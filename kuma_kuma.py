#!/usr/bin/env python3
# Reminder to use `nohup ./kuma_kuma.py > /dev/null &`
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

import asyncio
import contextlib
import datetime
import logging
import pathlib
import sys
import time
import traceback
from configparser import ConfigParser
from datetime import timedelta
from io import BytesIO
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from pprint import pformat
from threading import Thread, current_thread
from typing import TYPE_CHECKING, ClassVar, Union

import aiohttp
import asqlite
import colorlog
import discord
import mystbin
import sentry_sdk
from discord import Intents, Interaction, app_commands
from discord.ext import commands

# from discord.utils import _ColourFormatter as ColourFormatter
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.integrations.asyncio import AsyncioIntegration

from extensions import EXTENSIONS
from utils.cog import KumaEmojiTable, KumaResources
from utils.context import KumaContext

if TYPE_CHECKING:
    from sqlite3 import Row

DB_FILENAME = "kuma_kuma.sqlite"
DB_PATH: str = Path(__file__).parent.joinpath(DB_FILENAME).as_posix()


async def _get_prefix(bot: Kuma_Kuma, message: discord.Message) -> list[str]:
    """
    Retrieves the prefixes for the current guild.
    """
    prefixes: set[str] = bot._prefixes
    if message.guild is not None:
        guild: int = message.guild.id

        async with bot.pool.acquire() as conn:
            res: list[Row] = await conn.fetchall("""SELECT prefix FROM prefix WHERE serverid = ?""", guild)
            if res is not None and len(res) >= 1:
                prefixes.update([entry["prefix"] for entry in res if entry["prefix"] not in prefixes])

    wmo_func = commands.when_mentioned_or(*prefixes)
    # bot._prefixes.update(prefixes)
    return wmo_func(bot, message)


async def _get_trusted(bot: Kuma_Kuma) -> set[int]:
    """
    Retrieves all trusted users aka Owners.
    """
    trusted: set[int] = bot.owner_ids
    async with bot.pool.acquire() as conn:
        res: list[Row] = await conn.fetchall("""SELECT ownerid FROM owners""")
        if res is not None and len(res) >= 1:
            trusted.update([entry["ownerid"] for entry in res])
    return trusted


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


class LogHandler:
    """
    Discord Multi-line code block formats:
    - https://github.com/highlightjs/highlight.js/blob/main/SUPPORTED_LANGUAGES.md

    """

    cur_log: Path
    logger: logging.Logger
    code_formats: ClassVar[list[str]] = ["excel", "nc", "ml", " nim", " ps", " prolog", "thor"]
    default_code_format: str = "ps"

    def __init__(self, sentry: str, level: int = logging.INFO, webhook_url: str = "", local_dev: bool = False) -> None:
        self.logger = logging.getLogger()
        if local_dev == False:
            self.logger.info("Sentry SDK is Enabled -- Flag: %s", local_dev)
            sentry_sdk.init(dsn=sentry, integrations=[AioHttpIntegration(), AsyncioIntegration()])
        else:
            self.logger.warning("Sentry SDK is Disabled -- Flag: %s", local_dev)
        self.webhook_url: str = webhook_url
        self.session: aiohttp.ClientSession
        self.path: Path = pathlib.Path(__file__).parent.joinpath("logs")
        self.cur_log: Path = pathlib.Path(__file__).parent.joinpath("logs/log.log")

        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(threadName)s] [%(levelname)s]  %(message)s",
            datefmt="%m/%d/%Y %I:%M:%S %p",
            handlers=[
                logging.StreamHandler(stream=sys.stdout),
                TimedRotatingFileHandler(
                    filename=pathlib.Path.as_posix(self=self.path) + "/log.log",
                    when="midnight",
                    atTime=datetime.datetime.min.time(),
                    backupCount=4,
                    encoding="utf-8",
                    utc=True,
                ),
            ],
        )

    def parse_log(self) -> str:
        if self.cur_log.exists():
            try:
                data: str = self.cur_log.read_text()
            except Exception as e:
                self.logger.error(msg="We encountered an error executing upload_log.", exc_info=e)
                raise ValueError("We encountered an error executing upload_log.")

            return data[-1900:]

        else:
            raise FileNotFoundError("The most recent log file is not present. | path: %s", self.cur_log.resolve())

    async def upload_log(
        self,
    ) -> None:
        """Uploads the most recent log file to a mystbin."""
        if self.cur_log.exists():
            try:
                data: str = self.cur_log.read_text()
            except Exception as e:
                self.logger.error(msg="We encountered an error executing upload_log.", exc_info=e)
                raise ValueError("We encountered an error executing upload_log.")

            await self.create_paste(content=data, session=self.session)
        else:
            raise FileNotFoundError("The most recent log file is not present. | path: %s", self.cur_log.resolve())

    async def webhook_send_log(
        self,
    ) -> None:
        """Uploads the most recent log file to a webhook."""

        if not self.cur_log.exists():
            raise FileNotFoundError("The most recent log file is not present. | path: %s", self.cur_log.resolve())

        # size in Mb - to prevent exceeding Discord file attachment limits.
        size: float = self.cur_log.stat().st_size / (1024 * 1024)
        if size < 10:
            webhook: discord.Webhook = discord.Webhook.from_url(url=self.webhook_url, session=aiohttp.ClientSession())
            await webhook.send(file=discord.File(fp=self.cur_log.resolve()))
        else:
            raise OverflowError("The log file size is larger than Discord Webhook limit (10Mb). | size: %s", size)

    @staticmethod
    async def create_paste(
        *,
        content: str | None = None,
        files: list[tuple[str, str]] | None = None,
        password: str | None = None,
        expires: datetime.datetime | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> mystbin.Paste:
        if not content and not files:
            raise ValueError("Either `content` or `files` must be provided.")

        if content:
            post_files: list[mystbin.File] = [mystbin.File(filename="output.py", content=content)]
        elif files:
            post_files = [mystbin.File(filename=name, content=content) for name, content in files]
        else:
            raise ValueError("An argument for `content` or `files` must be provided.")

        return await mystbin.Client(session=session).create_paste(files=post_files, password=password, expires=expires)

    @staticmethod
    def dump_file(data: str | BytesIO, file_name: str) -> None:
        if isinstance(data, BytesIO):
            data = data.read().decode(encoding="utf-8")

        with Path().joinpath(f"{file_name}.dump").open("w+") as f:
            f.write(data)

        f.close()


class ProxyObject(discord.Object):
    __slots__ = ("guild",)

    def __init__(self, guild: discord.abc.Snowflake | None, /) -> None:
        super().__init__(id=0)
        self.guild: discord.abc.Snowflake | None = guild


class KumaCommandTree(app_commands.CommandTree):
    """This handles any Application Commands"""

    client: Kuma_Kuma  # type: ignore
    _mention_app_commands: dict[int | None, list[app_commands.AppCommand]]

    async def sync(self, *, guild: discord.abc.Snowflake | None = None) -> list[app_commands.AppCommand]:
        """Method overwritten to store the commands."""
        ret = await super().sync(guild=guild)
        self._mention_app_commands[guild.id if guild else None] = ret
        return ret

    async def fetch_commands(self, *, guild: discord.abc.Snowflake | None = None) -> list[app_commands.AppCommand]:
        """Method overwritten to store the commands."""
        ret = await super().fetch_commands(guild=guild)
        self._mention_app_commands[guild.id if guild else None] = ret
        return ret

    async def find_mention_for(
        self,
        command: app_commands.Command | app_commands.Group | str,
        *,
        guild: discord.abc.Snowflake | None = None,
    ) -> str | None:
        """Retrieves the mention of an AppCommand given a specific command name, and optionally, a guild.
        Parameters
        ----------
        name: Union[:class:`app_commands.Command`, :class:`app_commands.Group`, str]
            The command which it's mention we will attempt to retrieve.
        guild: Optional[:class:`discord.abc.Snowflake`]
            The scope (guild) from which to retrieve the commands from. If None is given or not passed,
            only the global scope will be searched, however the global scope will also be searched if
            a guild is passed.
        """

        check_global = self.fallback_to_global is True or guild is not None

        if isinstance(command, str):
            # Try and find a command by that name. discord.py does not return children from tree.get_command, but
            # using walk_commands and utils.get is a simple way around that.
            _command = discord.utils.get(self.walk_commands(guild=guild), qualified_name=command)

            if check_global and not _command:
                _command = discord.utils.get(self.walk_commands(), qualified_name=command)

        else:
            _command = command

        if not _command:
            return None

        if guild:
            try:
                local_commands = self._mention_app_commands[guild.id]
            except KeyError:
                local_commands = await self.fetch_commands(guild=guild)

            app_command_found = discord.utils.get(local_commands, name=(_command.root_parent or _command).name)

        else:
            app_command_found = None

        if check_global and not app_command_found:
            try:
                global_commands = self._mention_app_commands[None]
            except KeyError:
                global_commands = await self.fetch_commands()

            app_command_found = discord.utils.get(global_commands, name=(_command.root_parent or _command).name)

        if not app_command_found:
            return None

        return f"</{_command.qualified_name}:{app_command_found.id}>"

    async def on_error(
        self,
        interaction: Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        assert interaction.command is not None  # typechecking # disable assertions
        self.client.logger.exception("Exception occurred in the CommandTree:\n%s", exc_info=error)

        e = discord.Embed(title="Command Error", colour=0xA32952)
        e.add_field(name="Command", value=(interaction.command and interaction.command.name) or "No command found.")
        e.add_field(name="Author", value=interaction.user, inline=False)
        channel = interaction.channel
        assert channel  # always there
        guild = interaction.guild
        channel_name: str = "In DMs" if isinstance(channel, (discord.DMChannel, discord.PartialMessageable)) else channel.name  # type: ignore - for some reason it thinks channel is None; despite the assertion.

        location_fmt: str = f"Channel: {channel_name} ({channel.id})"
        if guild:
            location_fmt += f"\nGuild: {guild.name} ({guild.id})"
        e.add_field(name="Location", value=location_fmt, inline=True)
        (exc_type, exc, tb) = type(error), error, error.__traceback__
        trace: list[str] = traceback.format_exception(exc_type, exc, tb)
        clean: str = "".join(trace)
        if len(clean) >= 2000:
            # todo - mystbin login info? possibly generate a password for these?
            paste: mystbin.Paste = await self.client.loghandler.create_paste(content=clean, session=self.client.session)
            e.description = f"Error was too long to send in a codeblock, so I have pasted it [here]({paste.url})."
        else:
            e.description = f"```py\n{clean}\n```"

        e.timestamp = datetime.datetime.now(tz=datetime.UTC)
        await self.client.logging_webhook.send(embed=e)
        await self.client.owner.send(embed=e)


class Kuma_Kuma(commands.Bot):
    logger: logging.Logger = logging.getLogger()
    _app_id = 1053576011935129640
    _prefixes: ClassVar[set[str]] = set()
    # The owner_ids is updated via the Trust Add/Remove Command.
    owner_ids: set[int]  # type: ignore - Collections are immutable --
    message_timeout = 120
    start_time: float = time.time()
    pool: asqlite.Pool
    session: aiohttp.ClientSession

    # These are duplicated and located inside any "Cog" class that inherits "Kuma_Cog"
    emoji_table: KumaEmojiTable = KumaEmojiTable()
    resources: KumaResources = KumaResources()
    if TYPE_CHECKING:
        user: discord.ClientUser

    def __init__(self, config: KumaConfig, loghandler: LogHandler) -> None:
        intents: Intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        self.owner_id = None
        self.config: KumaConfig = config
        self.loghandler: LogHandler = loghandler
        self._prefixes.add("kuma")
        super().__init__(intents=intents, command_prefix=_get_prefix, strip_after_prefix=True)

    @property
    def owner(self) -> discord.User:
        return self.bot_app_info.owner

    @property
    def logging_webhook(self) -> discord.Webhook:
        return discord.Webhook.from_url(url=self.config.logging_webhook, session=self.session)

    @property
    def uptime(self) -> timedelta:
        return timedelta(seconds=(round(time.time() - self.start_time)))

    async def setup_hook(self) -> None:
        self.bot_app_info: discord.AppInfo = await self.application_info()
        self.mb_client = mystbin.Client(session=self.session)
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(PREFIX_SETUP_SQL)
                await conn.execute(OWNER_SETUP_SQL)
            self.owner_ids = await _get_trusted(bot=self)  # type: ignore - As long as it's a set of Ints it's fine.
        except Exception as e:
            self.logger.error("We encountered an error executing %s", __name__ + "setup_hook", exc_info=e)
            raise ConnectionError("Unable to connect to the database.")

    async def on_ready(self) -> None:
        self.logger.info(msg="Kuma Kuma Bear <3")

    def is_me(self, message: discord.Message) -> bool:
        return message.author == self.user

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return

        await super().on_message(message)

    async def on_command(self, context: KumaContext) -> None:
        self.logger.info("%s used %s", context.author.name, context.command)

    async def on_command_error(self, context: KumaContext, error: commands.CommandError) -> None:
        if context.command is not None:
            self.logger.error("We encountered an error executing: %s", context.command, exc_info=error)
            if isinstance(error, commands.errors.CommandNotFound):
                await context.send(
                    content=f"{self.emoji_table.to_inline_emoji('kuma_crying')}I can't run the command `{context.command.name}` as it doesn't exist!",
                    ephemeral=True,
                    delete_after=30,
                )
            if isinstance(error, commands.TooManyArguments):
                await context.send(
                    content=f"You called the `{context.command.name}` command with too many arguments.", ephemeral=True, delete_after=30
                )
            elif isinstance(error, commands.MissingRequiredArgument):
                await context.send(
                    content=f"You called `{context.command.name}` command without the required arguments", ephemeral=True, delete_after=30
                )
            else:
                await context.send(
                    content=f"We encountered an error executing the command {context.command.name}.",
                    ephemeral=True,
                    delete_after=30,
                )
                self.logger.debug(pformat(vars(context.command)))
                return

        self.logger.error("We encountered an error executing: %s", context, exc_info=error)
        # await context.send(content="We encountered an error executing the command.", ephemeral=True, delete_after=30)
        self.logger.debug(pformat(vars(context)))

    async def on_command_completion(self, context: commands.Context) -> None:
        if (
            context.message.content.startswith(tuple(self._prefixes)) and context.message.channel.permissions_for(context.me).manage_messages  # type: ignore
        ):
            try:
                await context.message.delete()

            except discord.errors.NotFound:
                return
            except Exception as e:
                self.logger.error("We encountered an error executing: %s", context.command, exc_info=e)

    async def on_reaction_add(self, reaction: discord.Reaction, user: Union[discord.Member, discord.User]) -> None:
        """Called when a message has a reaction added to it. Similar to `on_message_edit()`,
        if the message is not found in the internal message cache,
        then this event will not be called. Consider using `on_raw_reaction_add()` instead."""
        if isinstance(reaction.emoji, str):
            self.logger.info(
                "Reaction.Emoji Used: %s Unicode: %s by %s",
                reaction.emoji,
                reaction.emoji.encode(encoding="unicode-escape").decode(encoding="ASCII"),
                user.name,
            )

        else:
            self.logger.info(
                "Emoji Used: %s by %s| Emoji ID: %s | Emoji Name: %s",
                reaction.emoji,
                user.name,
                reaction.emoji.id,
                reaction.emoji.name,
            )

    async def get_context(self, origin: Union[discord.Interaction, discord.Message], /, *, cls: type[KumaContext] = KumaContext) -> KumaContext:
        return await super().get_context(origin, cls=cls)

    async def start(self) -> None:
        await super().start(token=self.config.token, reconnect=True)


class KumaConfig:
    token: str
    sentry_io: str
    logging_webhook: str
    github_owner: str
    github_token: str

    def __init__(self, token: str, sentry_io: str, logging_webhook: str, github_owner: str, github_token: str) -> None:
        self.token = token
        self.sentry_io = sentry_io
        self.logging_webhook = logging_webhook
        self.github_owner = github_owner
        self.github_token = github_token


def ini_load() -> KumaConfig:
    """
    Parse my local ini file
    """
    logger = logging.getLogger()
    _setting_file: Path = Path("./local.ini")
    if _setting_file.is_file():
        settings = ConfigParser(converters={"list": lambda setting: [value.strip() for value in setting.split(",")]})
        settings.read(filenames=_setting_file.as_posix())
        # login creds
        try:
            _temp = KumaConfig(
                token=settings.get(section="DISCORD", option="token"),
                sentry_io=settings.get(section="SENTRY_IO", option="dsn"),
                logging_webhook=settings.get(section="DISCORD", option="logging_webhook"),
                github_owner=settings.get(section="GITHUB", option="owner"),
                github_token=settings.get(section="GITHUB", option="token"),
            )
        except Exception as e:
            logger.error(msg="Failed to parse the local.ini", exc_info=e)
            raise ValueError("Failed to parse the local.ini")
    else:
        raise ValueError("Failed to load .ini")
    return _temp


async def main(local_dev: bool = False) -> None:
    cur_thread: Thread = current_thread()
    cur_thread.name = "Kuma Kuma Bear"
    config: KumaConfig = ini_load()
    async with (
        Kuma_Kuma(
            config=config,
            loghandler=LogHandler(sentry=config.sentry_io, webhook_url=config.logging_webhook, local_dev=local_dev),
        ) as kuma,
        aiohttp.ClientSession() as session,  # todo - I can make a json serializer if needed but not needed (per Umbra)
        asqlite.create_pool(database=DB_PATH) as pool,
    ):
        kuma.pool = pool
        kuma.session = session
        for extension in EXTENSIONS:
            await kuma.load_extension(name=extension.name)
            kuma.logger.info("Loaded %sextension: %s", "module " if extension.ispkg else "", extension.name)
        await kuma.load_extension(name="extensions.private.work")
        kuma.logger.info("Loaded extension: %s", "private.work")
        await kuma.start()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt, RuntimeError, asyncio.CancelledError):
        asyncio.run(main=main())
