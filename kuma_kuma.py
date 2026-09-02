#!/usr/bin/env python3
# Reminder to use `nohup ./kuma_kuma.py > /dev/null &`
"""Copyright (C) 2021-2022 Katelynn Cadwallader.

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

__title__ = "Kuma Kuma"
__author__ = "k8thekat"
__license__ = "GNU"
__version__ = "1.0.0"
__credits__ = "Kuma Kuma Bear"

import argparse
import asyncio
import contextlib
import datetime
import importlib
import logging
import os
import signal
import sys
import time
from configparser import ConfigParser
from io import BytesIO
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from sqlite3 import DatabaseError
from threading import Thread, current_thread
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple, Optional, Union

import asqlite
import discord
import mystbin
import sentry_sdk
from aiohttp_client_cache import SQLiteBackend
from aiohttp_client_cache.session import CachedSession
from discord import Intents, Interaction, app_commands
from discord.ext import commands, tasks
from discord.utils import MISSING
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.integrations.asyncio import AsyncioIntegration

from extensions import EXTENSIONS
from utils import (
    PALETTE_FORMATS,
    CodeFormat,
    KumaContext,
    KumaEmojiTable,
    KumaHelpCommand,
    KumaLogFormatter,
    KumaResources,
    MessageHistory,
    colourise_log,
    entry_level,
    parse_levels,
    report_error,
    setup_errors,
    setup_message_history,
    split_log_entries,
)

if TYPE_CHECKING:
    import types
    from collections.abc import Iterable, Sequence
    from sqlite3 import Row

    import aiohttp
    from discord.ext.tasks import Loop
    from discord.state import ConnectionState


class VersionInfo(NamedTuple):
    major: int
    minor: int
    revision: int
    release_level: Literal["release", "development"]


version_info: VersionInfo = VersionInfo(major=1, minor=0, revision=0, release_level="development")


DB_FILENAME = "kuma_kuma.sqlite"
#: The prefix that applies in every guild, on top of whatever the `prefix` table holds. It is stated
#: here rather than seeded into that table so a guild we have never been configured for still answers.
DEFAULT_PREFIX: str = "kuma"
#: Initial seek window for `LogHandler.parse_log`, doubled until enough records are found.
LOG_TAIL_CHUNK = 16 * 1024
#: How far back from EOF `LogHandler.parse_log` will search before giving up.
LOG_TAIL_MAX_BYTES = 4 * 1024 * 1024
CACHE_DIR: Path = Path(__file__).parent.joinpath("cache")
DB_PATH: str = CACHE_DIR.joinpath(DB_FILENAME).as_posix()
LOGGER: logging.Logger = logging.getLogger(__name__)


async def _get_prefix(bot: Kuma_Kuma, message: discord.Message) -> list[str]:
    """Retrieves the prefixes for the current guild.

    Always includes :data:`DEFAULT_PREFIX`, so the documented `kuma` prefix works in a guild whose
    `prefix` table row was never written — which was every guild but my own.

    Parameters
    ----------
    bot: :class:`Kuma_Kuma`
        The Bot class.
    message: :class:`discord.Message`
        The discord.Message invoking the command.

    Returns
    -------
    :class:`list[str]`
        A list of prefixes.

    """
    prefixes: set[str] = set(bot._prefixes)  # noqa: SLF001 # Our own attribute; the copy is so the cache is never mutated.
    if message.guild is not None:
        prefixes.update(await bot.guild_prefixes(guild_id=message.guild.id))

    wmo_func = commands.when_mentioned_or(*prefixes)
    return wmo_func(bot, message)


async def _get_trusted(bot: Kuma_Kuma) -> set[int]:
    """Retrieves all trusted users aka Owners.

    Parameters
    ----------
    bot: :class:`Kuma_Kuma`
        The Bot class.

    Returns
    -------
    :class:`set[int]`
        A set of Owner IDs.

    """
    trusted: set[int] = bot.owner_ids
    async with bot.pool.acquire() as conn:
        res: list[Row] = await conn.fetchall("""SELECT ownerid FROM owners""")
        if len(res) >= 1:
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
    ownerid INTEGER NOT NULL UNIQUE
)"""


class _DiscordReconnectFilter(logging.Filter):
    """Downgrades discord.py reconnect attempts from ERROR to WARNING so Sentry ignores them."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno == logging.ERROR and "Attempting a reconnect" in record.getMessage():
            record.levelno = logging.WARNING
            record.levelname = "WARNING"
        return True


class LogHandler:
    """Kuma Kuma logging.

    .. note::
        Discord Multi-line code block formats:
        - https://github.com/highlightjs/highlight.js/blob/main/SUPPORTED_LANGUAGES.md

    """

    cur_log: Path
    logger: logging.Logger
    code_formats: ClassVar[tuple[CodeFormat, ...]] = PALETTE_FORMATS
    default_code_format: CodeFormat = CodeFormat.POWERSHELL

    def __init__(
        self,
        /,
        sentry: str,
        level: int = logging.INFO,
        webhook_url: str = "",
        local_dev: bool = False,
        *,
        session: aiohttp.ClientSession | CachedSession,
    ) -> None:
        self.logger = logging.getLogger()
        if local_dev is False:
            self.logger.info("Sentry SDK is Enabled -- Flag: %s", local_dev)
            sentry_sdk.init(dsn=sentry, integrations=[AioHttpIntegration(), AsyncioIntegration()])
        else:
            self.logger.warning("Sentry SDK is Disabled -- Flag: %s", local_dev)
        self.webhook_url: str = webhook_url
        self.session = session
        self.path: Path = Path(__file__).parent.joinpath("logs")
        self.cur_log: Path = Path(__file__).parent.joinpath("logs/log.log")

        # Colour goes to the console only. The rotating file stays plain text so
        # `grep` keeps working and `parse_log` does not ship escape bytes into a
        # Discord message that is not fenced as `ansi`.
        stream_handler = logging.StreamHandler(stream=sys.stdout)
        stream_handler.setFormatter(KumaLogFormatter())

        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(threadName)s] [%(levelname)s]  %(message)s",
            datefmt="%m/%d/%Y %I:%M:%S %p",
            handlers=[
                stream_handler,
                TimedRotatingFileHandler(
                    filename=self.path.joinpath("log.log").as_posix(),
                    when="midnight",
                    atTime=datetime.datetime.min.time(),
                    backupCount=8,
                    encoding="utf-8",
                    utc=True,
                ),
            ],
        )
        self.logger.setLevel(level=level)
        logging.getLogger("discord.client").addFilter(_DiscordReconnectFilter())

    def _tail_entries(self, entries: int, *, levels: frozenset[str] | None, max_bytes: int) -> list[str]:
        """Read backwards from EOF until ``entries`` matching records are in hand.

        Log files are append-only and we always want the newest records, so
        reading the whole file to discard all but the last handful is wasted
        I/O. This seeks to a window at the end and doubles it until the window
        holds enough records, the whole file has been read, or ``max_bytes`` is
        reached — the last of which matters when a narrow ``levels`` filter
        would otherwise walk the entire file looking for one CRITICAL.

        Parameters
        ----------
        entries: :class:`int`
            How many matching records are wanted.
        levels: :class:`frozenset[str]` | None
            Level names to keep, or None for all.
        max_bytes: :class:`int`
            Stop widening the window past this many bytes.

        Returns
        -------
        :class:`list[str]`
            Up to ``entries`` records, oldest first.

        """
        size: int = self.cur_log.stat().st_size
        window: int = min(size, LOG_TAIL_CHUNK)

        while True:
            with self.cur_log.open("rb") as fp:
                fp.seek(size - window)
                raw: bytes = fp.read(window)

            # Unless we reached the start of the file, the first line is very
            # likely a fragment. Dropping to the first newline discards it and,
            # because a newline is never inside a multi-byte UTF-8 sequence,
            # also guarantees the remainder decodes cleanly.
            if window < size:
                _, newline, raw = raw.partition(b"\n")
                if not newline:
                    raw = b""

            found: list[str] = split_log_entries(raw.decode("utf-8", errors="replace"))
            if levels is not None:
                found = [entry for entry in found if entry_level(entry) in levels]

            if len(found) >= entries or window >= size or window >= max_bytes:
                return found[-entries:]
            window = min(size, window * 2)

    def parse_log(
        self,
        entries: int = 15,
        *,
        levels: str | Iterable[str] | None = None,
        colour: bool = False,
        max_chars: int = 1900,
        max_bytes: int = LOG_TAIL_MAX_BYTES,
    ) -> str:
        """Read the tail of the current log file.

        Slices by *record*, not by line, so a traceback always arrives attached
        to the message that raised it, and seeks from EOF rather than reading
        the whole file.

        Parameters
        ----------
        entries: :class:`int`, optional
            How many of the most recent records to return, by default 15.
            Values below 1 are clamped to 1.
        levels: :class:`str` | :class:`Iterable[str]` | None, optional
            Keep only these levels, by default None (keep everything). Accepts
            ``"ERROR"``, ``"ERROR,WARNING"``, ``"error warning"`` or a list.
        colour: :class:`bool`, optional
            Re-apply :class:`KumaLogFormatter` colours, by default False. Only
            fence the result as :attr:`CodeFormat.ANSI` when this is True.
        max_chars: :class:`int`, optional
            A hard ceiling on the returned length, by default 1900 — Discord's
            2000 character limit less room for a fence. Whole records are
            dropped from the front to fit; a single oversized record is cut.
        max_bytes: :class:`int`, optional
            How far back from EOF to search before giving up.

        Returns
        -------
        :class:`str`
            The selected records, newline joined.

        Raises
        ------
        FileNotFoundError
            The current log file does not exist.
        ValueError
            The log file could not be read, or an unknown level was requested.

        """
        if not self.cur_log.exists():
            msg = f"The most recent log file is not present. | path: {self.cur_log.resolve()}"
            raise FileNotFoundError(msg)

        wanted: frozenset[str] | None = parse_levels(levels)

        try:
            selected: list[str] = self._tail_entries(max(entries, 1), levels=wanted, max_bytes=max_bytes)
        except OSError as e:
            msg = "We encountered an error executing parse_log."
            self.logger.error(msg=msg, exc_info=e)  # noqa: TRY400
            raise ValueError(msg) from e

        # Walk backwards dropping whole records until the budget is met, so the
        # excerpt never opens mid-traceback. Colour is applied afterwards --
        # trimming coloured text would slice an escape sequence in half.
        kept: list[str] = []
        budget: int = max_chars
        for entry in reversed(selected):
            cost: int = len(entry) + 1
            if cost > budget:
                if not kept:
                    kept.append(entry[-budget:])
                break
            kept.append(entry)
            budget -= cost

        text: str = "\n".join(reversed(kept))
        return colourise_log(text) if colour else text

    async def upload_log(
        self,
    ) -> None:
        """Uploads the most recent log file to a mystbin."""
        if self.cur_log.exists():
            try:
                data: str = self.cur_log.read_text()
            except Exception as e:
                msg = "We encountered an error executing upload_log."
                self.logger.error(msg=msg, exc_info=e)  # noqa: TRY400
                raise ValueError(msg) from e

            await self.create_paste(content=data, session=self.session)
        else:
            msg = f"The most recent log file is not present. | path: {self.cur_log.resolve()}"
            raise FileNotFoundError(msg)

    async def webhook_send_log(
        self,
    ) -> None:
        """Uploads the most recent log file to a webhook."""
        if not self.cur_log.exists():
            msg = f"The most recent log file is not present. | path: {self.cur_log.resolve()}"
            raise FileNotFoundError(msg)

        # size in Mb - to prevent exceeding Discord file attachment limits.
        size: float = self.cur_log.stat().st_size / (1024 * 1024)
        if size >= 10:
            msg = f"The log file size is larger than Discord Webhook limit (10Mb). | size: {size}"
            raise OverflowError(msg)

        # Our own session, not a fresh one. The old call built a `ClientSession` inline and never
        # closed it, so every log upload leaked a connector and an "Unclosed client session" warning.
        webhook: discord.Webhook = discord.Webhook.from_url(url=self.webhook_url, session=self.session)
        await webhook.send(file=discord.File(fp=self.cur_log.resolve()))

    @staticmethod
    async def create_paste(
        *,
        content: Optional[str] = None,
        files: Optional[list[tuple[str, str]]] = None,
        password: Optional[str] = None,
        expires: Optional[datetime.datetime] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> mystbin.Paste:
        if not content and not files:
            msg = "Either `content` or `files` must be provided."
            raise ValueError(msg)

        if content:
            post_files: list[mystbin.File] = [mystbin.File(filename="output.py", content=content)]
        elif files:
            post_files = [mystbin.File(filename=name, content=content) for name, content in files]
        else:
            msg = "An argument for `content` or `files` must be provided."
            raise ValueError(msg)

        return await mystbin.Client(session=session).create_paste(files=post_files, password=password, expires=expires)

    @staticmethod
    def dump_file(data: Union[str, BytesIO], file_name: str) -> None:
        if isinstance(data, BytesIO):
            data = data.read().decode(encoding="utf-8")

        with Path().joinpath(f"{file_name}.dump").open("w+") as dump_file:
            dump_file.write(data)


class ProxyObject(discord.Object):
    __slots__ = ("guild",)

    def __init__(self, guild: Optional[discord.abc.Snowflake], /) -> None:
        super().__init__(id=0)
        self.guild: Optional[discord.abc.Snowflake] = guild


class KumaCommandTree(app_commands.CommandTree):
    """Handles any Application Commands."""

    client: Kuma_Kuma  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._mention_app_commands: dict[int | None, list[app_commands.AppCommand]] = {}

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        command_name = interaction.command.qualified_name if interaction.command else "Unknown"
        cog_name = getattr(interaction.command, "binding", None)
        cog_name = type(cog_name).__name__ if cog_name else "N/A"
        LOGGER.info("%s used %s -> /%s", interaction.user.name, cog_name, command_name)
        return True

    async def sync(self, *, guild: Optional[discord.abc.Snowflake] = None) -> list[app_commands.AppCommand]:
        """Method overwritten to store the commands."""
        ret = await super().sync(guild=guild)
        self._mention_app_commands[guild.id if guild else None] = ret
        return ret

    async def fetch_commands(self, *, guild: Optional[discord.abc.Snowflake] = None) -> list[app_commands.AppCommand]:
        """Method overwritten to store the commands."""
        ret = await super().fetch_commands(guild=guild)
        self._mention_app_commands[guild.id if guild else None] = ret
        return ret

    async def find_mention_for(
        self,
        command: app_commands.Command | app_commands.Group | str,
        *,
        guild: Optional[discord.abc.Snowflake] = None,
    ) -> Optional[str]:
        """Retrieves the mention of an AppCommand given a specific command name, and optionally, a guild.

        Parameters
        ----------
        command: Union[:class:`app_commands.Command`, :class:`app_commands.Group`, str]
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
        LOGGER.exception("Exception occurred in the CommandTree:", exc_info=error)

        # Answered first, and separately from the report below. Discord shows a deferred interaction
        # as "thinking..." until something replies to it, so a command that raised after its `defer`
        # used to hang there forever — and the report is the part that can fail, which would take
        # the reply down with it if the caller were told second.
        await self.answer_caller(interaction=interaction)
        try:
            await report_error(
                pool=self.client.pool,
                owner=self.client.owner,
                error=error,
                command=(interaction.command and interaction.command.name) or "Unknown",
                command_type="app",
                user=interaction.user,
                guild=interaction.guild,
                channel=interaction.channel,
            )
        except Exception:
            LOGGER.exception("Could not report a CommandTree error to the owner:")

    async def answer_caller(self, *, interaction: Interaction) -> None:
        """Closes off a failed command's interaction, so the caller isn't left watching a spinner."""
        content: str = (
            f"That command hit an error, so it didn't finish. It has been logged and Kat has been told. {self.client.emoji_table.kuma_sad}"
        )
        with contextlib.suppress(discord.HTTPException):
            if interaction.response.is_done():
                await interaction.followup.send(content=content, ephemeral=True)
            else:
                await interaction.response.send_message(content=content, ephemeral=True)


def command_names(cog: commands.Cog) -> tuple[str, ...]:
    """Names every command a cog owns, group children excluded.

    `get_commands` and `get_app_commands` both return the top level only — the `walk_` variants are
    the ones that recurse — so a group contributes its own name and none of its children. That is
    what a lookup wants: a hint or a listener belongs to the cog, never to one leaf of a group.

    Parameters
    ----------
    cog: :class:`commands.Cog`
        The cog to read. Needs no `Kuma_Cog`, so a third party cog indexes the same way.

    Returns
    -------
    :class:`tuple[str, ...]`
        Every command name, sorted so the order does not depend on how the cog was built.

    """
    names: set[str] = {command.name for command in cog.get_commands()}
    # A hybrid appears in both lists under the same name; the set is what makes that harmless.
    names.update(command.name for command in cog.get_app_commands())
    return tuple(sorted(names))


class Kuma_Kuma(commands.Bot):  # noqa: N801
    _app_id = 1053576011935129640

    _prefixes: ClassVar[set[str]] = {DEFAULT_PREFIX}
    """The prefixes that apply everywhere, regardless of the `prefix` table"""
    _prefix_cache: dict[int, frozenset[str]]
    """Per guild prefixes off the `prefix` table, keyed by guild ID. See `guild_prefixes`"""

    # The owner_ids is updated via the Trust Add/Remove Command.
    owner_ids: set[int]  # type: ignore - Collections are immutable --
    # My Discord User ID. `owner_ids` grows at runtime via the `trusted` command, so anything that
    # should stay mine alone — eg. the `restart` command — checks against this instead.
    owner_user_id: int = 144462063920611328

    # Set by `restart()`; read by `__main__` once the loop is done to decide whether to re-exec.
    restart_requested: bool = False
    # Strong reference to the deferred close in `restart()`; a bare task can be collected mid flight.
    _restart_close_task: Optional[asyncio.Task[None]] = None

    message_timeout = 120
    start_time: float = time.time()
    _session: CachedSession
    _loops: list[Loop[Any]]
    # These are duplicated and located inside any "Cog" class that inherits "Kuma_Cog"
    emoji_table: KumaEmojiTable = KumaEmojiTable()
    resources: KumaResources = KumaResources()
    command_owners: dict[str, str]
    """Command name to the qualified name of the cog that owns it.

    Maintained a cog at a time by :meth:`add_cog` and :meth:`remove_cog` rather than rebuilt. Every
    load, unload and reload routes through those two — `reload_extension` is remove then add — so
    there is no path by which a cog arrives or leaves without the map hearing about it.
    """
    msg_history: MessageHistory

    if TYPE_CHECKING:
        user: discord.ClientUser

    def __init__(self, config: KumaConfig, loghandler: LogHandler) -> None:
        intents: Intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        self.owner_id = None
        self.config: KumaConfig = config
        self.loghandler: LogHandler = loghandler
        self._prefix_cache = {}
        self._loops = []
        self.command_owners = {}
        super().__init__(
            intents=intents,
            command_prefix=_get_prefix,
            strip_after_prefix=True,
            tree_cls=KumaCommandTree,
            help_command=KumaHelpCommand(),
        )

        self.owner_ids = set()
        self.owner_ids.add(self.owner_user_id)

    @property
    def pool(self) -> asqlite.Pool:
        """ASQLITE Database connection pool."""
        return self._pool

    @pool.setter
    def pool(self, value: asqlite.Pool) -> None:
        self._pool: asqlite.Pool = value

    @property
    def owner(self) -> discord.User:
        return self.bot_app_info.owner

    @property
    def logging_webhook(self) -> discord.Webhook:
        return discord.Webhook.from_url(url=self.config.logging_webhook, session=self.session)

    @property
    def uptime(self) -> datetime.timedelta:
        return datetime.timedelta(seconds=(round(time.time() - self.start_time)))

    @property
    def local_ini(self) -> Path:
        """Path directly to the local.ini file."""
        return Path(__file__).parent.joinpath("local.ini")

    @property
    def task_loops(self) -> list[Loop[Any]]:
        return self._loops

    @property
    def app_emojis(self) -> list[discord.Emoji]:
        return self._app_emojis

    @property
    def session(self) -> CachedSession:
        return self._session

    @property
    def connection_state(self) -> ConnectionState:
        """The internal connection state; gives manually-built objects access to the HTTP client."""
        return self._connection

    async def add_cog(
        self,
        cog: commands.Cog,
        /,
        *,
        override: bool = False,
        guild: Optional[discord.abc.Snowflake] = MISSING,
        guilds: Sequence[discord.abc.Snowflake] = MISSING,
    ) -> None:
        """Adds a cog, then indexes the commands it brought with it.

        Parameters
        ----------
        cog: :class:`commands.Cog`
            The cog being added.
        override: :class:`bool`, optional
            Passed through to :meth:`commands.Bot.add_cog`, by default False.
        guild: :class:`Optional[discord.abc.Snowflake]`, optional
            Passed through to :meth:`commands.Bot.add_cog`, by default `MISSING`.
        guilds: :class:`Sequence[discord.abc.Snowflake]`, optional
            Passed through to :meth:`commands.Bot.add_cog`, by default `MISSING`.

        """
        await super().add_cog(cog, override=override, guild=guild, guilds=guilds)
        # Indexed after `super()`, so `__commands__` describes what actually registered rather than
        # what the class body declared.
        names: tuple[str, ...] = command_names(cog)
        cog.__commands__ = names  # type: ignore[attr-defined] # Our own attribute; `commands.Cog` has no such slot.
        self.command_owners.update(dict.fromkeys(names, cog.qualified_name))

    async def remove_cog(
        self,
        name: str,
        /,
        *,
        guild: Optional[discord.abc.Snowflake] = MISSING,
        guilds: Sequence[discord.abc.Snowflake] = MISSING,
    ) -> Optional[commands.Cog]:
        """Removes a cog, and takes its commands back out of the index.

        Parameters
        ----------
        name: :class:`str`
            The qualified name of the cog to remove.
        guild: :class:`Optional[discord.abc.Snowflake]`, optional
            Passed through to :meth:`commands.Bot.remove_cog`, by default `MISSING`.
        guilds: :class:`Sequence[discord.abc.Snowflake]`, optional
            Passed through to :meth:`commands.Bot.remove_cog`, by default `MISSING`.

        Returns
        -------
        :class:`Optional[commands.Cog]`
            The cog that was removed, or `None` when no cog was registered under that name.

        """
        cog: Optional[commands.Cog] = await super().remove_cog(name, guild=guild, guilds=guilds)
        if cog is not None:
            # Dropped from the cog's own list rather than by scanning the map for its name; the scan
            # would be the one pass over everything in an otherwise incremental path.
            for command_name in getattr(cog, "__commands__", ()):
                self.command_owners.pop(command_name, None)
        return cog

    async def setup_hook(self) -> None:
        self.bot_app_info: discord.AppInfo = await self.application_info()
        self.mb_client = mystbin.Client(session=self.session)
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(PREFIX_SETUP_SQL)
                await conn.execute(OWNER_SETUP_SQL)
            await setup_errors(self.pool)
            await setup_message_history(self.pool)
            self.msg_history = MessageHistory(pool=self.pool)
            self._message_cleanup.start()
            self.owner_ids = await _get_trusted(bot=self)  # type: ignore - As long as it's a set of Ints it's fine.

        except DatabaseError:
            LOGGER.exception("<%s.setup_hook()> | Encountered an error.", __class__.__name__)
            msg = "Unable to connect to the database."
            raise DatabaseError(msg)  # noqa: B904

    async def close(self) -> None:
        if self._message_cleanup.is_running():
            self._message_cleanup.cancel()
        await super().close()

    async def on_ready(self) -> None:
        LOGGER.name = "Kuma Kuma"
        LOGGER.info("Kuma Kuma Bear <3")
        try:
            self._app_emojis: list[discord.Emoji] = await self.fetch_application_emojis()
        except (discord.errors.HTTPException, discord.errors.MissingApplicationID):
            LOGGER.error("<%s.%s> | We encountered an error fetching the application emojis.", __class__.__name__, "on_ready")  # noqa: TRY400
            self._app_emojis = []

    def is_me(self, /, snowflake: discord.Message | discord.Thread) -> bool:
        """Owner/Author check of an object.

        Parameters
        ----------
        snowflake: :class:`discord.Message | discord.Thread`
            An object containing a :class:`discord.Member | discord.User` attribute for comparison against a :class:`discord.ClientUser`.

        Returns
        -------
        :class:`bool`
            If the object belongs to Kuma Kuma or not.

        """
        if isinstance(snowflake, discord.Message):
            return snowflake.author == self.user

        if isinstance(snowflake, discord.Thread):
            # By ID rather than `Thread.owner`, which resolves through the member cache and answers
            # `None` for an uncached member — which would read as "not mine" for a thread that is.
            return snowflake.owner_id == self.user.id
        return False

    async def guild_prefixes(self, *, guild_id: int) -> frozenset[str]:
        """Retrieves a guild's registered prefixes, reading the database only on a cache miss.

        .. note:
            Entries are dropped by the `prefix` commands via :meth:`invalidate_prefixes` rather than
            given a lifetime, so a change is live on the very next message and a quiet guild never
            re-reads.

        Parameters
        ----------
        guild_id: :class:`int`
            The Discord guild to look up.

        Returns
        -------
        :class:`frozenset[str]`
            The guild's prefixes, not including :data:`DEFAULT_PREFIX`. Frozen because it is the
            cached object itself; a caller wanting to add to it should build its own set.

        """
        cached: Optional[frozenset[str]] = self._prefix_cache.get(guild_id)
        if cached is not None:
            return cached

        async with self.pool.acquire() as conn:
            res: list[Row] = await conn.fetchall("""SELECT prefix FROM prefix WHERE serverid = ?""", guild_id)

        prefixes: frozenset[str] = frozenset(entry["prefix"] for entry in res if entry["prefix"])
        self._prefix_cache[guild_id] = prefixes
        return prefixes

    def invalidate_prefixes(self, guild_id: int) -> None:
        """Drops a guild's cached prefixes, so the next message re-reads them from the database.

        Parameters
        ----------
        guild_id: :class:`int`
            The Discord guild whose prefixes changed.

        """
        self._prefix_cache.pop(guild_id, None)
        LOGGER.debug("<%s.%s> | Invalidated the prefix cache. | Guild ID: %s", __class__.__name__, "invalidate_prefixes", guild_id)

    def refresh_help_command(self) -> None:
        """Rebind `help_command` to a freshly imported `KumaHelpCommand`.

        `help_command` holds an *instance*, built once in `__init__`, so reloading `utils.help` puts
        new code in `sys.modules` and changes nothing about the object actually answering `help`.
        Only a full restart picked those edits up. Re-importing and re-instantiating here means
        `reload` does; the property setter detaches the old command and registers the new one.

        .. note::
            This method itself lives in `kuma_kuma.py`, which `reload` never re-executes — so
            changing *this* still needs a restart. Everything in `utils/help.py` no longer does.

        """
        module: types.ModuleType = importlib.reload(importlib.import_module("utils.help"))
        self.help_command = module.KumaHelpCommand()
        LOGGER.info("<%s.%s> | Rebound the help command.", __class__.__name__, "refresh_help_command")

    def commands_enabled_for(self, message: discord.Message) -> bool:
        """Whether this message should be run through `process_commands`.

        Parameters
        ----------
        message: :class:`discord.Message`
            The message being dispatched.

        Returns
        -------
        :class:`bool`
            Whether commands are live for this message.

        """
        checker: Optional[Any] = getattr(self.get_cog("Claude"), "is_session_thread", None)
        if checker is None or not checker(message.channel):
            return True
        if self.user is None:
            return True
        return message.content.lstrip().startswith((f"<@{self.user.id}>", f"<@!{self.user.id}>"))

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return

        if not self.commands_enabled_for(message):
            return

        await super().on_message(message)

    async def on_connect(self) -> None:
        pass

    async def on_command(self, context: KumaContext) -> None:
        cog_name = "N/A" if context.command is None else context.command.cog_name
        LOGGER.info("%s used %s -> %s", context.author.name, cog_name, context.command)
        # Deletes the command invocation message.
        try:
            # A `Repl` session keeps answering the message that started it — every result is sent
            # with it as the reply reference — so deleting the invocation would leave the whole
            # session hanging off "Original message was deleted". The session ends itself instead.
            if context.cog is not None and type(context.cog).__name__ == "Repl":
                return

            await context.message.delete(delay=self.message_timeout)
        except (discord.errors.Forbidden, discord.errors.HTTPException, discord.NotFound):
            pass

    async def on_command_error(self, context: KumaContext, error: commands.CommandError) -> None:
        # --- User-facing ephemeral reply ---
        if context.command is None and isinstance(error, commands.errors.CommandNotFound):
            await context.send(
                content=f"I can't run the command `{context.message.content}` as it doesn't exist! {self.emoji_table.kuma_crying}",
                ephemeral=True,
                delete_after=30,
            )
        elif context.command is not None:
            LOGGER.error("We encountered an error executing: %s", context.command, exc_info=error)
            if isinstance(error, commands.TooManyArguments):
                await context.send(
                    content=f"> You called the `{context.command.name}` command with too many arguments. {self.emoji_table.kuma_rawr}",
                    ephemeral=True,
                    delete_after=30,
                )
            elif isinstance(error, commands.MissingRequiredArgument):
                await context.send(
                    content=(
                        f"> You called `{context.command.name}` command without the required arguments. {self.emoji_table.kuma_head_clench}"
                    ),
                    ephemeral=True,
                    delete_after=30,
                )
            else:
                await context.send(
                    content=f"> We encountered an error executing the command {context.command.name}. {self.emoji_table.kuma_shrug}",
                    ephemeral=True,
                    delete_after=30,
                )
        else:
            LOGGER.error(
                "<%s.%s> | We encountered an error executing a command. | Guild: %s | Type/Error: <%s> / %s",
                __class__.__name__,
                "on_command_error",
                context.guild,
                type(error),
                error,
            )
            await context.send(
                content=f"> We encountered an {type(error)} executing the command. {self.emoji_table.kuma_peak}",
                ephemeral=True,
                delete_after=30,
            )

        # --- Record and report to owner ---
        try:
            await report_error(
                pool=self.pool,
                owner=self.owner,
                error=error,
                command=context.command.qualified_name if context.command else context.message.content,
                command_type="prefix",
                user=context.author,
                guild=context.guild,
                channel=context.channel,
            )
        except Exception:
            LOGGER.exception("<%s.%s> | Could not report a prefix command error to the owner:", __class__.__name__, "on_command_error")

    async def on_command_completion(self, context: commands.Context) -> None:
        # `restart` deletes its own invocation before it asks to be closed, and this races the close
        # it just scheduled; either way the delete below has nothing left to do.
        if self.restart_requested is True:
            return

        # If the command was invoked by a prefix or mention, delete immediately instead of
        # waiting for the `message_timeout` delay set in `on_command`.
        if (
            context.prefix is not None
            and isinstance(context.me, (discord.Member, discord.Role))
            and context.message.channel.permissions_for(context.me).manage_messages
        ):
            try:
                await context.message.delete()

            except discord.errors.NotFound:
                msg = "<%s.%s> | Unable to find the `discord.Message` belonging to this %s object."
                LOGGER.error(msg, __class__.__name__, "on_command_completion", type(context))  # noqa: TRY400 # I don't want to raise an exception as I know the error already.
                return
            except Exception as e:
                msg = "We encountered an error executing: %s"
                LOGGER.exception(msg, context.command, exc_info=e)

    async def on_reaction_add(self, reaction: discord.Reaction, user: Union[discord.Member, discord.User]) -> None:
        """Called when a message has a reaction added to it.

        Similar to `on_message_edit()`,
        if the message is not found in the internal message cache,
        then this event will not be called. Consider using `on_raw_reaction_add()` instead.

        Parameters
        ----------
        reaction: :class:`discord.Reaction`
            The reaction that was added.
        user: :class:`Union[discord.Member, discord.User]`
            The member or user who added the reaction.

        """
        if isinstance(reaction.emoji, str):
            LOGGER.info(
                "Reaction.Emoji Used: %s Unicode: %s by %s",
                reaction.emoji,
                reaction.emoji.encode(encoding="unicode-escape").decode(encoding="ASCII"),
                user.name,
            )

        else:
            LOGGER.info(
                "Emoji Used: %s by %s| Emoji ID: %s | Emoji Name: %s",
                reaction.emoji,
                user.name,
                reaction.emoji.id,
                reaction.emoji.name,
            )

    async def get_context(
        self,
        origin: Union[discord.Interaction, discord.Message],
        /,
        *,
        cls: type[KumaContext] = KumaContext,
    ) -> KumaContext:
        return await super().get_context(origin, cls=cls)

    async def start(self) -> None:
        await super().start(token=self.config.token, reconnect=True)

    async def restart(self) -> None:
        """Shuts the bot down and asks `__main__` to bring the process straight back up.

        The actual re-exec cannot happen here. `os.execv` replaces the process image on the spot, so
        calling it from inside a command would abandon a running event loop — no `cog_unload`, no
        closed pool, no closed session; the very things `Kuma.bash`s SIGTERM handling was added to
        preserve. Instead we flag it, `close()` unwinds `main()` normally, and the exec happens once
        `asyncio.run()` has returned and everything is shut.

        .. note::
            The close is handed to the loop rather than awaited. Awaiting it here closes the client
            from inside the invocation chain that is still running us, and `Bot.invoke` has a
            `command_completion` left to dispatch — `close()` having already set `Client.loop` to
            `MISSING`, that dispatch raises ``'_MissingSentinel' object has no attribute
            'create_task'``. Returning first lets `invoke` finish against a live loop.

        """
        self.restart_requested = True
        self._restart_close_task = asyncio.get_running_loop().create_task(self.close(), name="kuma: restart close")

    @tasks.loop(seconds=60, reconnect=True)
    async def _message_cleanup(self) -> None:
        """Deletes tracked messages whose age exceeds :attr:`message_timeout`."""
        cutoff: datetime.timedelta = datetime.timedelta(seconds=self.message_timeout)

        for guild in self.guilds:
            records = await self.msg_history.recent(limit=self.msg_history.max_entries, guild_id=guild.id)
            expired_ids: list[int] = []

            for record in records:
                if datetime.datetime.now(tz=datetime.UTC) - record.created_at < cutoff:
                    continue

                channel = guild.get_channel_or_thread(record.channel_id)
                if channel is None or not hasattr(channel, "get_partial_message"):
                    # Channel was deleted, lost access, or is not messageable — discard the row.
                    expired_ids.append(record.message_id)
                    continue

                try:
                    partial: discord.PartialMessage = channel.get_partial_message(record.message_id)
                    await partial.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    LOGGER.debug(
                        "<%s.%s> | Unable to delete message %s in channel %s — removing record.",
                        __class__.__name__,
                        "_message_cleanup",
                        record.message_id,
                        record.channel_id,
                    )

                expired_ids.append(record.message_id)

            if expired_ids:
                await self.msg_history.delete(*expired_ids)

    @_message_cleanup.before_loop
    async def _before_message_cleanup(self) -> None:
        await self.wait_until_ready()


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
    """Parse my local ini file."""
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
            msg = "Failed to parse the local.ini"
            LOGGER.exception(msg, exc_info=e)
            raise ValueError(msg) from e
            # raise ValueError(msg)
    else:
        msg = "The <Path> provided was not a regular <File>."
        raise ValueError(msg)
    return _temp


# TODO: Change row factory for sqlite3.Row to use a dict factory. -> https://docs.python.org/3/library/sqlite3.html#sqlite3-howto-row-factory
# Need to test this locally to understand it first.
async def main(*, local_dev: bool = False, log_level: int = logging.INFO) -> bool:
    """Builds and runs the bot, returning whether it asked to be restarted."""
    cur_thread: Thread = current_thread()
    cur_thread.name = "Kuma Kuma Bear"
    config: KumaConfig = ini_load()
    # Ensure the cache directory exists before any SQLite backend opens a file in it.
    CACHE_DIR.mkdir(exist_ok=True)
    # TODO(@k8thekat) Implement -> https://requests-cache.readthedocs.io/en/stable/user_guide/expiration.html
    _cache = SQLiteBackend(
        cache_name=CACHE_DIR.joinpath("kuma_kuma_cache").as_posix(),
        autoclose=True,
        expire_after=86400,
    )

    # TODO - I can make a json serializer if needed but not needed (per Umbra)
    async with (
        CachedSession(cache=_cache) as session,
        Kuma_Kuma(
            config=config,
            loghandler=LogHandler(config.sentry_io, log_level, config.logging_webhook, local_dev, session=session),
        ) as kuma,
        asqlite.create_pool(database=DB_PATH) as pool,
    ):
        kuma.pool = pool
        kuma._session = session  # noqa: SLF001
        for extension in EXTENSIONS:
            await kuma.load_extension(name=extension.name)
            LOGGER.info("Loaded %sextension: %s", "module " if extension.ispkg else "", extension.name)

        await kuma.load_extension(name="kuma_claude")
        LOGGER.info("Loaded packaged extension: %s", "kuma_claude")

        await kuma.start()

    # Reached once the pool and the session have been closed by the `async with`, so the caller can
    # safely hand the process over to something else.
    return kuma.restart_requested


def parse_args() -> argparse.Namespace:
    """Parses the mode flag `Kuma.bash` passes through.

    Live is the default so a bare `python kuma_kuma.py` behaves exactly as it always has — only an
    explicit `--dev` turns Sentry off.

    Returns
    -------
    :class:`argparse.Namespace`
        The parsed arguments.

    """
    parser = argparse.ArgumentParser(prog="kuma_kuma.py", description="Kuma Kuma Bear.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Run in live mode with Sentry enabled (the default).")
    mode.add_argument("--dev", action="store_true", help="Run in local development mode with Sentry disabled.")
    parser.add_argument("--log-level", default="INFO", help="Logging level name, e.g. DEBUG. Defaults to INFO.")
    return parser.parse_args()


def _on_terminate(signum: int, frame: object) -> None:  # noqa: ARG001
    """Turns SIGTERM into the same shutdown path as Ctrl-C.

    Python's default SIGTERM handler kills the process outright — no unwinding, so
    `commands.Bot.close()` never runs and neither does any `cog_unload`. That matters here because
    `ClaudeCog.cog_unload` is what kills in-flight `claude` subprocesses; without this a
    `Kuma.bash -live` restart would leave them orphaned.

    Raising `KeyboardInterrupt` reuses the interrupt path that `__main__` already suppresses, so
    `asyncio.run` cancels tasks and the `async with` block closes the bot cleanly.
    """
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _on_terminate)
    arguments: argparse.Namespace = parse_args()
    level: int = getattr(logging, arguments.log_level.upper(), logging.INFO)
    # Only an in-bot `restart` sets this. A Ctrl-C or a SIGTERM leaves it False, so stopping the bot
    # still stops it.
    restart: bool = False
    with contextlib.suppress(KeyboardInterrupt, RuntimeError, asyncio.CancelledError):
        restart = asyncio.run(main=main(local_dev=arguments.dev, log_level=level))

    if restart is True:
        LOGGER.info("Restarting Kuma Kuma Bear. | argv: %s", sys.orig_argv)
        # `execv` replaces us in place rather than forking, so the PID never changes — which is the
        # whole point. `Kuma.bash` wrote that PID to `kuma_kuma.py.pid`, and a spawned child would
        # leave the file pointing at a corpse, so `-status` and `-stop` would never find the bot
        # again. `sys.orig_argv` is the entire original command line, interpreter flags included, so
        # the replacement comes up with `-u` and the `--live`/`--dev` mode it was started under.
        logging.shutdown()
        os.execv(sys.executable, sys.orig_argv)  # noqa: S606 # No shell is the point; the argv is our own, not user input.
