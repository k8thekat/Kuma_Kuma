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

import logging
from typing import TYPE_CHECKING, Any, Unpack

import discord

from ._types import ButtonParams

LOGGER = logging.getLogger()

__all__ = ()


class GenericView(discord.ui.View):
    user: discord.User

    def __init__(self, *, timeout: float | None = 180.0, user: discord.User) -> None:
        super().__init__(timeout=timeout)
        self.user = user

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        LOGGER.info("<%s.%s>", __class__.__name__, "interaction_check")
        if interaction.user != self.user:
            await interaction.response.send_message(
                "This interaction isn't for you!",
                ephemeral=True,
            )
            return False
        return True


class GenericButton(discord.ui.Button):
    view: GenericView

    def __init__(self, **kwargs: Unpack[ButtonParams]) -> None:
        if kwargs.get("style") is None:
            kwargs["style"] = discord.ButtonStyle.primary
        if kwargs.get("label") is None:
            kwargs["label"] = "Generic"

        label = kwargs.get("label")
        if kwargs.get("custom_id") is None and label is not None:
            kwargs["custom_id"] = label.lower().replace(" ", "_")

        super().__init__(**kwargs)

    async def callback(self, interaction: discord.Interaction) -> None:
        LOGGER.info("<%s.%s>", __class__.__name__, "callback")
        if interaction.user == self.view.user:
            await interaction.response.defer()
        return
