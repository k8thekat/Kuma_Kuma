"""Copyright (C) 2021-2025 Katelynn Cadwallader.

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

Error recording, deduplication, and reporting.

Two tables keep errors searchable without letting the database grow unbounded:

- ``errors`` stores each unique traceback once, keyed by a SHA-256 hash, with a lifetime
  occurrence count and first/last seen timestamps.
- ``error_occurrences`` stores every individual event with its context (who, where, which command).
  Rows older than seven days are pruned at startup.

The CV2 report is a :class:`ErrorReport` LayoutView sent silently to the bot owner's DMs.
"""

from __future__ import annotations

import datetime
import logging
import traceback as tb_module
from hashlib import sha256
from io import BytesIO
from typing import Any, Optional, Union

import discord
from asqlite import Pool

from .cog import KumaEmojiTable

__all__ = ("cleanup_old_occurrences", "report_error", "setup_error_tables", "setup_errors")

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_ERRORS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY,
    hash TEXT UNIQUE NOT NULL,
    traceback TEXT NOT NULL,
    error_type TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    first_seen TIMESTAMP NOT NULL,
    last_seen TIMESTAMP NOT NULL
)
"""

_OCCURRENCES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS error_occurrences (
    id INTEGER PRIMARY KEY,
    error_id INTEGER NOT NULL REFERENCES errors(id) ON DELETE CASCADE,
    timestamp TIMESTAMP NOT NULL,
    command_name TEXT NOT NULL,
    command_type TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    user_name TEXT NOT NULL,
    guild_id INTEGER,
    guild_name TEXT,
    channel_id INTEGER,
    channel_name TEXT
)
"""


# ---------------------------------------------------------------------------
# CV2 report
# ---------------------------------------------------------------------------

_REPORT_RESERVE: int = 800
_VIEW_BUDGET: int = 4000
_TRACEBACK_DISPLAY_LIMIT: int = _VIEW_BUDGET - _REPORT_RESERVE
_ERROR_COLOUR: discord.Color = discord.Color(0xA32952)


class ErrorReport(discord.ui.LayoutView):
    """The CV2 view DM'd to the owner when a command raises."""

    def __init__(
        self,
        *,
        command_name: str,
        command_type: str,
        user_name: str,
        user_id: int,
        guild_name: Optional[str],
        guild_id: Optional[int],
        channel_name: Optional[str],
        channel_id: Optional[int],
        traceback_text: str,
        error_count: int,
        first_seen: datetime.datetime,
        occurred_at: datetime.datetime,
    ) -> None:
        super().__init__(timeout=None)

        container: discord.ui.Container = discord.ui.Container(accent_colour=_ERROR_COLOUR)

        # Header with kuma_sad thumbnail when available.
        header: str = "## ⚠ Command Error"
        kuma_url: Optional[str] = KumaEmojiTable.to_cdn_url("kuma_sad")
        if kuma_url:
            container.add_item(discord.ui.Section(header, accessory=discord.ui.Thumbnail(media=kuma_url)))
        else:
            container.add_item(discord.ui.TextDisplay(header))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Context.
        container.add_item(discord.ui.TextDisplay(f"**Command** · `{command_name}` · {command_type}"))
        container.add_item(discord.ui.TextDisplay(f"**Author** · {user_name} (`{user_id}`)"))

        if guild_name is not None and channel_name is not None:
            location: str = f"**Location** · #{channel_name} (`{channel_id}`) · {guild_name} (`{guild_id}`)"
        elif channel_name is not None:
            location = f"**Location** · #{channel_name} (`{channel_id}`) · DMs"
        else:
            location = "**Location** · Unknown"
        container.add_item(discord.ui.TextDisplay(location))
        container.add_item(discord.ui.Separator())

        # Traceback, truncated to budget.
        display: str = traceback_text[:_TRACEBACK_DISPLAY_LIMIT]
        block: str = f"```py\n{display}\n```"
        if len(traceback_text) > _TRACEBACK_DISPLAY_LIMIT:
            over: int = len(traceback_text) - _TRACEBACK_DISPLAY_LIMIT
            block += f"\n-# …{over:,} more characters; full traceback attached."
        container.add_item(discord.ui.TextDisplay(block))

        # Footer timestamp and occurrence count.
        ts: str = f"<t:{int(occurred_at.timestamp())}:F>"
        if error_count > 1:
            first: str = f"<t:{int(first_seen.timestamp())}:D>"
            footer: str = f"-# {ts} · Seen {error_count}× since {first}"
        else:
            footer = f"-# {ts}"
        container.add_item(discord.ui.TextDisplay(footer))

        self.add_item(container)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


async def setup_error_tables(pool: Pool) -> None:
    """Creates the error tables if they do not exist."""
    async with pool.acquire() as conn:
        await conn.execute(_ERRORS_TABLE_SQL)
        await conn.execute(_OCCURRENCES_TABLE_SQL)


async def cleanup_old_occurrences(pool: Pool, *, days: int = 7) -> int:
    """Deletes occurrence rows older than *days*, returning how many were removed.

    The ``errors`` table is left alone; its ``count`` and ``first_seen`` are lifetime stats.
    """
    cutoff: str = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=days)).isoformat()
    async with pool.acquire() as conn:
        cursor = await conn.execute("""DELETE FROM error_occurrences WHERE timestamp < ?""", cutoff)
        removed: int = cursor.get_cursor().rowcount
    if removed:
        LOGGER.info("<%s> | Pruned %s error occurrence(s) older than %s day(s).", "cleanup_old_occurrences", removed, days)
    return removed


async def setup_errors(pool: Pool, *, prune_days: int = 7) -> None:
    """One-call startup initializer: creates tables and prunes stale occurrences.

    Called from :meth:`KumaKuma.setup_hook` so the bot file doesn't need to know
    about individual table-creation or cleanup steps.

    Parameters
    ----------
    pool: :class:`Pool`
        The database connection pool.
    prune_days: :class:`int`, optional
        Occurrence rows older than this many days are deleted, by default ``7``.

    """
    await setup_error_tables(pool)
    await cleanup_old_occurrences(pool, days=prune_days)


async def _record(
    *,
    pool: Pool,
    traceback_text: str,
    error_type: str,
    command_name: str,
    command_type: str,
    user_id: int,
    user_name: str,
    guild_id: Optional[int],
    guild_name: Optional[str],
    channel_id: Optional[int],
    channel_name: Optional[str],
) -> tuple[int, int, datetime.datetime]:
    """Inserts the error and its occurrence, returning ``(error_id, count, first_seen)``."""
    now: str = datetime.datetime.now(tz=datetime.UTC).isoformat()
    tb_hash: str = sha256(traceback_text.encode()).hexdigest()

    async with pool.acquire() as conn:
        row = await conn.fetchone(
            """INSERT INTO errors (hash, traceback, error_type, count, first_seen, last_seen)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT(hash) DO UPDATE
                   SET count = count + 1,
                       last_seen = excluded.last_seen
               RETURNING id, count, first_seen""",
            tb_hash,
            traceback_text,
            error_type,
            now,
            now,
        )
        error_id: int = row["id"]
        count: int = row["count"]
        first_seen: datetime.datetime = datetime.datetime.fromisoformat(row["first_seen"])

        await conn.execute(
            """INSERT INTO error_occurrences
               (error_id, timestamp, command_name, command_type, user_id, user_name,
                guild_id, guild_name, channel_id, channel_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            error_id,
            now,
            command_name,
            command_type,
            user_id,
            user_name,
            guild_id,
            guild_name,
            channel_id,
            channel_name,
        )

    return error_id, count, first_seen


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _error_type_name(error: BaseException) -> str:
    """Returns the most specific exception class name, unwrapping discord.py wrappers."""
    original: Optional[BaseException] = getattr(error, "original", None)
    return type(original).__name__ if original is not None else type(error).__name__


def _channel_name(channel: Any) -> Optional[str]:
    """Extracts a channel name defensively."""
    if channel is None:
        return None
    if isinstance(channel, (discord.DMChannel, discord.PartialMessageable)):
        return "DMs"
    return getattr(channel, "name", None)


async def report_error(
    *,
    pool: Pool,
    owner: discord.User,
    error: BaseException,
    command: str,
    command_type: str,
    user: Union[discord.User, discord.Member],
    guild: Optional[discord.Guild] = None,
    channel: Any = None,
) -> None:
    """Records an error to the database and DMs the owner a CV2 report.

    Failures are logged and swallowed; error reporting must never crash the error handler.

    Parameters
    ----------
    pool: :class:`Pool`
        The database connection pool.
    owner: :class:`discord.User`
        The bot owner to DM.
    error: :class:`BaseException`
        The exception that was raised.
    command: :class:`str`
        The command name.
    command_type: :class:`str`
        ``"prefix"`` or ``"app"``.
    user: :class:`discord.User | discord.Member`
        Who triggered the command.
    guild: :class:`discord.Guild | None`, optional
        The guild, if any.
    channel: optional
        The channel.

    """
    now: datetime.datetime = datetime.datetime.now(tz=datetime.UTC)
    trace: list[str] = tb_module.format_exception(type(error), error, error.__traceback__)
    traceback_text: str = "".join(trace)

    g_id: Optional[int] = guild.id if guild else None
    g_name: Optional[str] = guild.name if guild else None
    ch_id: Optional[int] = getattr(channel, "id", None)
    ch_name: Optional[str] = _channel_name(channel)

    count: int = 1
    first_seen: datetime.datetime = now
    try:
        _, count, first_seen = await _record(
            pool=pool,
            traceback_text=traceback_text,
            error_type=_error_type_name(error),
            command_name=command,
            command_type=command_type,
            user_id=user.id,
            user_name=str(user),
            guild_id=g_id,
            guild_name=g_name,
            channel_id=ch_id,
            channel_name=ch_name,
        )
    except Exception:
        LOGGER.exception("<%s> | Could not record an error to the database:", "report_error")

    report: ErrorReport = ErrorReport(
        command_name=command,
        command_type=command_type,
        user_name=str(user),
        user_id=user.id,
        guild_name=g_name,
        guild_id=g_id,
        channel_name=ch_name,
        channel_id=ch_id,
        traceback_text=traceback_text,
        error_count=count,
        first_seen=first_seen,
        occurred_at=now,
    )

    send_kwargs: dict[str, Any] = {"view": report, "silent": True}
    if len(traceback_text) > _TRACEBACK_DISPLAY_LIMIT:
        send_kwargs["file"] = discord.File(fp=BytesIO(traceback_text.encode()), filename="traceback.py")

    try:
        await owner.send(**send_kwargs)
    except discord.HTTPException:
        LOGGER.exception("<%s> | Could not DM the owner an error report:", "report_error")
