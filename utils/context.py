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

from typing import TYPE_CHECKING, Any, Optional, Union

from discord.ext import commands

if TYPE_CHECKING:
    import discord
    from aiohttp_client_cache.session import CachedSession

    from kuma_kuma import Kuma_Kuma

    from ._types import EmojiFollowup

__all__ = ("KumaContext", "KumaGuildContext")


def _normalize_emoji_followup(emoji: EmojiFollowup) -> str:
    """Collapse one or more emoji inputs into a single string for message content.

    Strings pass through as-is, :class:`discord.Emoji` and :class:`discord.PartialEmoji` are
    stringified via ``str()``, and sequences are space-joined so Discord still renders them large.

    .. note::
        Discord renders emoji large only when the message contains nothing else. Custom emoji cap
        at 3 per message; unicode emoji are more generous (~27).

    """
    if isinstance(emoji, str):
        return emoji
    # discord.Emoji / discord.PartialEmoji are not iterable; a list or tuple is a multi-emoji input.
    if isinstance(emoji, (list, tuple)):
        return " ".join(str(e) for e in emoji)
    return str(emoji)


class KumaContext(commands.Context["Kuma_Kuma"]):
    bot: Kuma_Kuma

    @property
    def session(self) -> CachedSession:
        """Global bot Session access.

        Returns
        -------
        :class:`CachedSession`
            The bot :class:`CachedSession`.

        """
        return self.bot.session

    @property
    def emoji_followup_message(self) -> Optional[discord.Message]:
        """The most recent emoji followup sent from this context.

        Set automatically by :meth:`send` or :meth:`reply` when an ``emoji_followup`` argument is
        provided. Returns ``None`` when no followup has been sent yet.

        Returns
        -------
        :class:`Optional[discord.Message]`
            The followup message, or ``None``.

        """
        return getattr(self, "_last_emoji_followup", None)

    async def send(
        self,
        content: Any = None,
        *,
        track: bool = False,
        emoji_followup: Optional[EmojiFollowup] = None,
        **kwargs: Any,
    ) -> discord.Message:
        """Send a message, optionally recording it and/or sending a large-emoji followup.

        Parameters
        ----------
        content: :class:`Any`, optional
            The message content.
        track: :class:`bool`, optional
            Whether to record the sent message in ``bot.msg_history``, by default False.
        emoji_followup: :data:`EmojiFollowup`, optional
            One or more emoji to send as a standalone followup message so Discord renders them
            large. Accepts a plain inline string, a :class:`discord.Emoji`, a
            :class:`discord.PartialEmoji`, or a sequence of any of those (space-joined). The
            followup message is accessible via :attr:`emoji_followup_message` afterward.
        **kwargs: :class:`Any`
            Passed through to :meth:`commands.Context.send`.

        Returns
        -------
        :class:`discord.Message`
            The sent message (the primary one, not the followup).

        """
        message: discord.Message = await super().send(content, **kwargs)
        if track and hasattr(self.bot, "msg_history"):
            await self.bot.msg_history.record(message)
        if emoji_followup is not None:
            self._last_emoji_followup: discord.Message = await self.channel.send(_normalize_emoji_followup(emoji_followup))
        return message

    async def reply(
        self,
        content: Any = None,
        *,
        track: bool = False,
        emoji_followup: Optional[EmojiFollowup] = None,
        **kwargs: Any,
    ) -> discord.Message:
        """Reply to the invoking message, optionally recording it and/or sending a large-emoji followup.

        The followup is a plain ``channel.send``, not another reply, so it will not double-ping
        the invoking user.

        Parameters
        ----------
        content: :class:`Any`, optional
            The message content.
        track: :class:`bool`, optional
            Whether to record the sent message in ``bot.msg_history``, by default False.
        emoji_followup: :data:`EmojiFollowup`, optional
            One or more emoji to send as a standalone followup message so Discord renders them
            large. Accepts a plain inline string, a :class:`discord.Emoji`, a
            :class:`discord.PartialEmoji`, or a sequence of any of those (space-joined). The
            followup message is accessible via :attr:`emoji_followup_message` afterward.
        **kwargs: :class:`Any`
            Passed through to :meth:`commands.Context.reply`.

        Returns
        -------
        :class:`discord.Message`
            The sent message (the primary one, not the followup).

        """
        message: discord.Message = await super().reply(content, **kwargs)
        if track and hasattr(self.bot, "msg_history"):
            await self.bot.msg_history.record(message)
        if emoji_followup is not None:
            self._last_emoji_followup: discord.Message = await self.channel.send(_normalize_emoji_followup(emoji_followup))
        return message


class KumaGuildContext(KumaContext):
    author: discord.Member  # pyright: ignore[reportIncompatibleVariableOverride]
    guild: discord.Guild  # pyright: ignore[reportIncompatibleVariableOverride]
    me: discord.Member  # pyright: ignore[reportIncompatibleVariableOverride]
    channel: Union[discord.VoiceChannel, discord.TextChannel, discord.Thread]  # pyright: ignore[reportIncompatibleVariableOverride]
