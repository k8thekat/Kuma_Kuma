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

import os
from functools import partial
from re import Pattern, compile as compile_
from typing import Union

import discord
from discord import Embed, app_commands
from discord.ext import commands

from utils.cog import KumaCog
from utils.ui.view import ReactionRoleView

interaction = discord.Interaction


class AutoRole(KumaCog):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot=bot)
        self._name: str = os.path.basename(__file__).title()
        self.logger.info(f"**SUCCESS** Initializing {self._name}")

    REACTION_ROLES_BUTTON_REGEX: Pattern[str] = compile_(r"RR::BUTTON::(?P<ROLE_ID>\d+)")

    @app_commands.command(name="role_embed")
    async def role_embed(
        self,
        interaction: discord.Interaction,
        channel: Union[discord.TextChannel, None],
        role: discord.Role,
        field_body: str,
        emoji: Union[str, None],
    ) -> None:
        """Displays an Embed in a channel that Users can interact with the button to `Add` or `Remove` a role."""
        embed = Embed(
            title=f"**{role.name} Role**",
            color=role.color,
            description=f"Click the button below if you'd like to subscribe to the {role.mention} role for updates!",
        )
        embed.add_field(name="**What is this for?**", value=field_body)

        role_view = ReactionRoleView(
            timeout=None, custom_id=f"RR::BUTTON::{role.id}", button_label=role.name, button_emoji=emoji
        )

        if channel == None:
            await interaction.response.send_message(embed=embed, view=role_view)
        else:
            await channel.send(embed=embed, view=role_view)

    @commands.Cog.listener("on_interaction")
    async def on_reaction_role(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return

        custom_id = (interaction.data or {}).get("custom_id", "")
        match = self.REACTION_ROLES_BUTTON_REGEX.fullmatch(custom_id)

        if match:
            role_id = int(match.group("ROLE_ID"))
            role = interaction.guild.get_role(role_id)
            if not role:
                return await interaction.response.send_message(
                    "Sorry, that role does not seem to exist anymore...", ephemeral=True
                )

            meth, message = (
                (partial(interaction.user.add_roles, atomic=True), "Gave you the role **{}**")
                if role not in interaction.user.roles
                else (partial(interaction.user.remove_roles, atomic=True), "Removed the role **{}**")
            )
            try:
                await meth(role)
            except discord.HTTPException as e:
                return await interaction.response.send_message(f"Failed to assign role: {e.text}", ephemeral=True)
            await interaction.response.send_message(message.format(role.name), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoRole(bot))
