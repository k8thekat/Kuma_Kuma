"""Copyright (C) 2021-2025 Katelynn Cadwallader.

This file is part of Kuma Kuma.

Kuma Kuma is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 3, or (at your option)
any later version.

Kuma Kuma is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public
License for more details.

You should have received a copy of the GNU General Public License
along with Kuma Kuma; see the file COPYING.  If not, write to the Free
Software Foundation, 51 Franklin Street - Fifth Floor, Boston, MA
02110-1301, USA.
"""

from __future__ import annotations

import datetime
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Coroutine

    import discord
    from asqlite import Pool

__all__ = ("MessageHistory", "SentMessageRecord", "setup_message_history")

LOGGER = logging.getLogger(__name__)

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sent_messages (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    guild_id INTEGER,
    created_at TIMESTAMP NOT NULL
)
"""

# Keeps the table bounded; oldest rows past this limit are pruned after each insert.
DEFAULT_MAX_ENTRIES: int = 100


class SentMessageRecord:
    """A lightweight record of a sent :class:`discord.Message`.

    Attributes
    ----------
    message_id: :class:`int`
        The Discord message snowflake.
    channel_id: :class:`int`
        The channel the message was sent in.
    guild_id: :class:`Optional[int]`
        The guild the message belongs to, or ``None`` for DMs.
    created_at: :class:`datetime.datetime`
        When the message was sent (UTC).

    """

    __slots__ = ("channel_id", "created_at", "guild_id", "message_id")

    def __init__(
        self,
        *,
        message_id: int,
        channel_id: int,
        guild_id: Optional[int],
        created_at: datetime.datetime,
    ) -> None:
        self.message_id: int = message_id
        self.channel_id: int = channel_id
        self.guild_id: Optional[int] = guild_id
        self.created_at: datetime.datetime = created_at

    def __repr__(self) -> str:
        return f"<SentMessageRecord message_id={self.message_id} channel_id={self.channel_id} guild_id={self.guild_id}>"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SentMessageRecord):
            return self.message_id == other.message_id
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.message_id)


class MessageHistory:
    """Tracks the bot's most recently sent messages in SQLite.

    Intended to live on the bot instance as ``bot.msg_history``. Records are
    capped at *max_entries*; older rows are pruned automatically on insert.

    Parameters
    ----------
    pool: :class:`asqlite.Pool`
        The shared database pool.
    max_entries: :class:`int`, optional
        Maximum rows to keep, by default :data:`DEFAULT_MAX_ENTRIES`.

    """

    __slots__ = ("_max_entries", "_pool")

    def __init__(self, pool: Pool, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._pool: Pool = pool
        self._max_entries: int = max_entries

    @property
    def max_entries(self) -> int:
        """The maximum number of rows kept in the table."""
        return self._max_entries

    async def record(self, message: discord.Message) -> SentMessageRecord:
        """Store a sent message's identifiers and prune past the cap.

        Parameters
        ----------
        message: :class:`discord.Message`
            The message object returned by a send call.

        Returns
        -------
        :class:`SentMessageRecord`
            The persisted record.

        """
        now: datetime.datetime = message.created_at or datetime.datetime.now(tz=datetime.UTC)
        guild_id: Optional[int] = message.guild.id if message.guild is not None else None

        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO sent_messages (message_id, channel_id, guild_id, created_at)
                   VALUES (?, ?, ?, ?)""",
                message.id,
                message.channel.id,
                guild_id,
                now.isoformat(),
            )
            # Prune oldest rows beyond the cap.
            await conn.execute(
                """DELETE FROM sent_messages
                   WHERE id NOT IN (
                       SELECT id FROM sent_messages ORDER BY id DESC LIMIT ?
                   )""",
                self._max_entries,
            )

        LOGGER.debug(
            "<%s.%s> | Recorded | message: %s channel: %s guild: %s",
            __class__.__name__,
            "record",
            message.id,
            message.channel.id,
            guild_id,
        )
        return SentMessageRecord(
            message_id=message.id,
            channel_id=message.channel.id,
            guild_id=guild_id,
            created_at=now,
        )

    @asynccontextmanager
    async def capture(self, coro: Coroutine[Any, Any, discord.Message]) -> AsyncGenerator[discord.Message, None]:
        """Await a send coroutine, record the result, and yield it.

        Wraps any endpoint that returns a :class:`discord.Message` so the
        caller does not need to manually call :meth:`record`::

            async with bot.msg_history.capture(channel.send("hello")) as message:
                ...  # *message* is already persisted

        Parameters
        ----------
        coro: :class:`Coroutine[Any, Any, discord.Message]`
            An unawaited send/reply coroutine.

        Yields
        ------
        :class:`discord.Message`
            The message returned by the coroutine, after recording.

        """
        message: discord.Message = await coro
        await self.record(message)
        yield message

    async def recent(
        self,
        limit: int = 10,
        *,
        guild_id: Optional[int] = None,
    ) -> list[SentMessageRecord]:
        """Fetch the most recent tracked messages, newest first.

        Parameters
        ----------
        limit: :class:`int`, optional
            How many records to return, by default 10.
        guild_id: :class:`Optional[int]`, optional
            Narrow to a single guild. ``None`` returns all guilds.

        Returns
        -------
        :class:`list[SentMessageRecord]`
            The matching records, newest first.

        """
        if guild_id is not None:
            query: str = """SELECT message_id, channel_id, guild_id, created_at
                            FROM sent_messages
                            WHERE guild_id = ?
                            ORDER BY id DESC LIMIT ?"""
            params: tuple[Any, ...] = (guild_id, limit)
        else:
            query = """SELECT message_id, channel_id, guild_id, created_at
                       FROM sent_messages
                       ORDER BY id DESC LIMIT ?"""
            params = (limit,)

        async with self._pool.acquire() as conn:
            rows = await conn.fetchall(query, *params)

        return [
            SentMessageRecord(
                message_id=row["message_id"],
                channel_id=row["channel_id"],
                guild_id=row["guild_id"],
                created_at=datetime.datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    async def delete(self, *message_ids: int) -> int:
        """Remove one or more rows by their Discord message snowflake.

        Accepts raw :class:`int` IDs.  To pass a :class:`SentMessageRecord`
        directly, use its :attr:`message_id`::

            await history.delete(record.message_id)
            await history.delete(rec_a.message_id, rec_b.message_id)

        Parameters
        ----------
        *message_ids: :class:`int`
            One or more Discord message snowflakes to remove.

        Returns
        -------
        :class:`int`
            The number of rows actually deleted.

        """
        if not message_ids:
            return 0

        placeholders: str = ", ".join("?" for _ in message_ids)
        query: str = f"DELETE FROM sent_messages WHERE message_id IN ({placeholders})"  # noqa: S608

        async with self._pool.acquire() as conn:
            cursor = await conn.execute(query, *message_ids)
            deleted: int = cursor.get_cursor().rowcount

        LOGGER.debug(
            "<%s.%s> | Deleted %s row(s) | message_ids: %s",
            __class__.__name__,
            "delete",
            deleted,
            message_ids,
        )
        return deleted


async def setup_message_history(pool: Pool) -> None:
    """Create the ``sent_messages`` table if it does not exist.

    Called from :meth:`Kuma_Kuma.setup_hook` alongside the other table
    creation helpers.

    Parameters
    ----------
    pool: :class:`asqlite.Pool`
        The shared database pool.

    """
    async with pool.acquire() as conn:
        await conn.execute(_TABLE_SQL)
