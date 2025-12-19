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

from typing import TYPE_CHECKING, Any, TypeVar

from discord.ext import commands

if TYPE_CHECKING:
    import discord
    from aiohttp import ClientSession
    from aiohttp_client_cache.session import CachedSession

    from kuma_kuma import Kuma_Kuma

T = TypeVar("T")

__all__ = ("KumaContext", "KumaGuildContext")


# class DisambiguatorView(discord.ui.View, Generic[T]):
#     message: discord.Message
#     selected: T

#     def __init__(self, ctx: KumaContext, data: list[T], entry: Callable[[T], Any]):
#         super().__init__()
#         self.ctx: KumaContext = ctx
#         self.data: list[T] = data

#         options = []
#         for i, x in enumerate(data):
#             opt = entry(x)
#             if not isinstance(opt, discord.SelectOption):
#                 opt = discord.SelectOption(label=str(opt))
#             opt.value = str(i)
#             options.append(opt)

#         select = discord.ui.Select(options=options)

#         select.callback = self.on_select_submit
#         self.select = select
#         self.add_item(select)

#     async def interaction_check(self, interaction: discord.Interaction) -> bool:
#         if interaction.user.id != self.ctx.author.id:
#             await interaction.response.send_message('This select menu is not meant for you, sorry.', ephemeral=True)
#             return False
#         return True

#     async def on_select_submit(self, interaction: discord.Interaction):
#         index = int(self.select.values[0])
#         self.selected = self.data[index]
#         await interaction.response.defer()
#         if not self.message.flags.ephemeral:
#             await self.message.delete()

#         self.stop()


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

    # async def disambiguate(self, matches: list[T], entry: Callable[[T], Any], *, ephemeral: bool = False) -> T:
    #     if len(matches) == 0:
    #         raise ValueError('No results found.')

    #     if len(matches) == 1:
    #         return matches[0]

    #     if len(matches) > 25:
    #         raise ValueError('Too many results... sorry.')

    #     view = DisambiguatorView(self, matches, entry)
    #     view.message = await self.send(
    #         'There are too many matches... Which one did you mean?', view=view, ephemeral=ephemeral
    #     )
    #     await view.wait()
    #     return view.selected


class KumaGuildContext(KumaContext):
    author: discord.Member  # pyright: ignore[reportIncompatibleVariableOverride]
    guild: discord.Guild  # pyright: ignore[reportIncompatibleVariableOverride]
    # channel: Union[discord.VoiceChannel, discord.TextChannel, discord.Thread]
    me: discord.Member  # pyright: ignore[reportIncompatibleVariableOverride]
    channel: discord.VoiceChannel | discord.TextChannel | discord.Thread  # pyright: ignore[reportIncompatibleVariableOverride]
