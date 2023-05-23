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
from __future__ import annotations

import datetime
from datetime import timezone
from dis import disco
import discord
import os
import logging
from dataclasses import dataclass

from discord import Interaction, app_commands
from discord.app_commands import Choice
from discord.ext import commands, tasks
import asqlite

from typing import Union

from kuma_kuma import Kuma_Kuma

DB_FILENAME = "lovers.sqlite"

LOVERS_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS lovers(
    name TEXT NOT NULL,
    discord_id BIGINT NOT NULL,
)"""

PARTNERS_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS partners(
    lovers_id INT NOT NULL,
    partner_id INT NOT NULL,
    FOREIGN KEY (lovers_id) references lovers(discord_id)
    FOREIGN KEY (partner_id) references lovers(discord_id)
)"""

KINKS_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS kinks(
    lovers_id INT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    FOREIGN KEY (lovers_id) references lovers(discord_id)
)"""

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LoverEntry:
    name: str
    discord_id: int
    # partners: list[dict[int, str]] #{id/owner_id : name}
    # kinks: list[dict[int, str]] #{id/owner_id: name}

    @classmethod
    async def get_or_none(cls, *, discord_id: int) -> LoverEntry | None:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""SELECT * FROM lovers WHERE discord_id = ?""", discord_id)
                res = await cur.fetchone()

                return cls(**res) if res is not None else None

    @classmethod
    async def add_lover(cls, *, name: str, discord_id: int) -> LoverEntry | None:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""INSERT INTO lovers(name, discord_id) VALUES (?, ?)
                ON CONFLICT(name, discord_id) DO NOTHING RETURNING *""", name, discord_id)
                res = await cur.fetchone()
                await db.commit()

                return cls(**res) if res is not None else None

    async def delete_lover(self) -> int:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""DELETE FROM lovers WHERE discord_id = ?""", self.discord_id)
                await db.commit()

                return cur.get_cursor().rowcount

    async def update_lover(self, name: str) -> LoverEntry:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""UPDATE lovers SET name = ? WHERE discord_id = ? RETURNING *""", name, self.discord_id)
                await db.commit()

                res = await cur.fetchone()
                return LoverEntry(**res)

    async def add_partner(self, partner_name: str, partner_id: int) -> LoverEntry | None:
        lover: LoverEntry | None = await self.get_or_none(discord_id=partner_id)

        if lover == None:
            lover = await self.add_lover(name=partner_name, discord_id=partner_id)

        else:
            async with asqlite.connect(DB_FILENAME) as db:
                async with db.cursor() as cur:
                    await cur.execute("""INSERT INTO partners(lovers_id, partner_id) VALUES (?, ?)
                    ON CONFLICT(lovers_id, partner_id) DO NOTHING RETURNING *""", lover.discord_id, partner_id)
                    res = await cur.fetchone()
                    await db.commit()

        # return lover

    async def remove_partner(self, partner_id: int) -> None | int:
        lover = await self.get_or_none(discord_id=partner_id)

        if lover == None:
            return lover

        else:
            async with asqlite.connect(DB_FILENAME) as db:
                async with db.cursor() as cur:
                    await cur.execute("""DELETE FROM partners WHERE lovers_id = ? and partner_id = ?""", self.discord_id, partner_id)
                    await db.commit()

                    return cur.get_cursor().rowcount

    async def list_partners(self) -> list | None:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""SELECT partner_id FROM partners WHERE lovers_id = ?""", self.discord_id)
                res = await cur.fetchall()

                return res if not None else None

    async def add_kink(self, name: str, description: Union[str, None] = None) -> str | None:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""INSERT INTO kinks(lovers_id, name, description) VALUES (?, ?, ?) 
                ON CONFLICT(lovers_id, name) DO NOTHING RETURNING *""", self.discord_id, name, description)
                res = await cur.fetchone()
                await db.commit()

                return name if res is not None else None

    async def remove_kink(self, name: str) -> int:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""DELETE FROM kinks WHERE name = ?""", name)
                res = await cur.fetchone()
                await db.commit()

                return cur.get_cursor().rowcount

    async def list_kinks(self) -> list | None:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""SELECT * FROM kinks WHERE lovers_id = ?""", self.discord_id)
                res = await cur.fetchall()

                return res if not None else None


class Love(commands.Cog):
    love_language = app_commands.Group(name="love",
                                       description="Love helper commands",
                                       nsfw=True)

    love_partner = app_commands.Group(name="partner", description="Partner related commands.",
                                      parent=love_language,
                                      nsfw=True, guild_only=True)

    love_kinks = app_commands.Group(name="kinks", description="Kink related commands.",
                                    parent=love_language,
                                    nsfw=True)

    def __init__(self, bot: Kuma_Kuma) -> None:
        self._bot: Kuma_Kuma = bot
        self._name: str = os.path.basename(__file__).title()
        self._logger = logging.getLogger()
        self._logger.info(f'**SUCCESS** Initializing {self._name} ')

    async def partner_autocomplete(self, interaction: discord.Interaction, current: str) -> list[Choice[str]]:
        assert interaction.guild
        res: list[Choice] = []
        choice_list: list[discord.Member] = []

        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            partners = await lover.list_partners()

            if partners is None:
                return res
            else:
                for id in partners:
                    member: discord.Member | None = interaction.guild.get_member(id["partner_id"])
                    if member is not None:
                        # Choice[name= member.name, value= member.id]
                        choice_list.append(member)

                return [Choice(name=member.name, value=str(member.id)) for member in choice_list if current.lower() in member.name.lower()]
        return res

    # @love_language.command(name="reroll")
    # async def love_reroll(self, interaction: discord.Interaction):
    #     print()

    # @love_language.command(name="interval", description="Time values are in `UTC` only")
    # async def love_message_interval(self, interaction: discord.Interaction) -> None:
    #     print()

    @love_partner.command(name="add", description="Add a partner")
    async def love_add_partner(self, interaction: discord.Interaction, partner: Union[discord.User, discord.Member]) -> None:
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)

        if lover is not None:
            await lover.add_partner(partner_name=partner.name, partner_id=partner.id)

        await interaction.response.send_message(content=f"You added **{partner.name}** as a partner.")

    @love_partner.command(name="remove", description="Remove a partner.")
    @app_commands.autocomplete(partner=partner_autocomplete)
    async def love_remove_partner(self, interaction: discord.Interaction, partner: Union[discord.User, discord.Member]) -> None:
        # TODO - Create a Discord User list of the Partner IDs of the lover from the DB.
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)

        if lover is not None:
            res: int | None = await lover.remove_partner(partner_id=partner.id)

        await interaction.response.send_message(content={f"We removed {res} partner." if res is not None else f"Unable to remove {partner.name}"})

    @love_partner.command(name="list", description="Lists all your partners")
    async def love_list_partner(self, interaction: discord.Interaction) -> None:
        print()

    @love_kinks.command(name="add", description="Add a type of Kink/Play you like.")
    async def love_kink_add(self, interaction: discord.Interaction, kink: str, description: Union[str, None] = None) -> None:

        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            await lover.add_kink(name=kink, description=description)

            msg_content: str = f"Added {kink} to {interaction.user.name}"
            if description is not None:
                msg_content += "\n **Description**:" + description

            await interaction.response.send_message(content=msg_content)

    # @tasks.loop(time=datetime.time(hour=14, tzinfo=timezone.utc))
    # async def love_message_loop() -> None:
    #     print()


async def setup(bot: Kuma_Kuma):
    await bot.add_cog(Love(bot))
