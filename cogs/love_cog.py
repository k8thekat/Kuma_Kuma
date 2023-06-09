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
from pydoc import describe
import sqlite3
import discord
import os
import logging
from dataclasses import dataclass
from pprint import pprint

from discord import Embed, Interaction, app_commands
from discord.app_commands import Choice
from discord.colour import Colour
from discord.ext import commands, tasks
import asqlite

from typing import Any, List, Optional, Union

from kuma_kuma import Kuma_Kuma

DB_FILENAME = "lovers.sqlite"

LOVERS_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS lovers (
    name TEXT NOT NULL,
    discord_id BIGINT NOT NULL,
    PRIMARY KEY(discord_id)
)
"""

PARTNERS_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS partners (
    lovers_id INT NOT NULL,
    partner_id INT NOT NULL,
    FOREIGN KEY (lovers_id) references lovers(discord_id),
    FOREIGN KEY (partner_id) references lovers(discord_id)
    PRIMARY KEY(lovers_id, partner_id)
)
"""

KINKS_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS kinks (
    lovers_id INT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    FOREIGN KEY (lovers_id) references lovers(discord_id)
    PRIMARY KEY(lovers_id, name)
)
"""

_logger = logging.getLogger()


class LoverEmbed(discord.Embed):
    @classmethod
    async def create(cls, *, color: int | Colour | None = None, title: Any | None = None, timestamp: datetime.datetime | None = None, lover: LoverEntry, interaction: discord.Interaction):
        self = cls(color=color, title=title, timestamp=timestamp)

        assert interaction.guild
        partners = await lover.list_partners()
        if partners == None:
            partners = "None"
        self.add_field(name="**Partners**", value=[interaction.guild.get_member(int(x)) for x in partners])

        kinks = await lover.list_kinks()
        if kinks == None:
            kinks = "None"
        self.add_field(name="**Kinks**", value=kinks, inline=True)

        return self


class PartnerEmbed(discord.Embed):
    @classmethod
    async def create(cls, *, color: int | Colour | None = None, title: Any | None = None, timestamp: datetime.datetime | None = None, partner: discord.Member):
        self = cls(color=color, title=title, timestamp=timestamp)
        self.set_thumbnail(url=None if partner.avatar == None else partner.avatar.url)

        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=partner.id)
        if lover is not None:
            res = await lover.list_kinks()
            if res is not None:
                for entry in res:
                    self.add_field(name=entry["name"], value=entry["description"])

        return self


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
                ON CONFLICT(discord_id) DO NOTHING RETURNING *""", name, discord_id)
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

        if lover != None:
            async with asqlite.connect(DB_FILENAME) as db:
                async with db.cursor() as cur:
                    # await cur.execute("""INSERT INTO partners(lovers_id, partner_id) VALUES (?, ?)
                    # ON CONFLICT(lovers_id, partner_id) DO NOTHING RETURNING *""", lover.discord_id, partner_id)
                    try:
                        await cur.execute("""INSERT INTO partners(lovers_id, partner_id) VALUES (?, ?)""", self.discord_id, partner_id)

                    except sqlite3.IntegrityError as err:
                        if type(err.args[0]) == str and err.args[0].lower() == "unique constraint failed: partners.lovers_id, partners.partner_id":
                            return None

                    # res = await cur.fetchone()
                    await db.commit()
                    return lover
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
                if res:
                    res = [entry["partner_id"] for entry in res]
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

    # async def update_kink(self, name: str, new_name: str | None = None, new_description: str | None = None) -> int | None:
    #     async with asqlite.connect(DB_FILENAME) as db:
    #         async with db.cursor() as cur:
    #             await cur.execute("""SELECT * FROM kinks WHERE name = ?""", name)
    #             res = await cur.fetchone()
    #             if res is not None:
    #                 name = name if new_name == None else new_name
    #                 description = res["description"] if new_description == None else new_description
    #                 await cur.execute("""UPDATE kinks SET name = ?, description = ? WHERE name = ?""", name, description)

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

    async def cog_load(self) -> None:
        async with asqlite.connect(DB_FILENAME) as db:
            await db.execute(LOVERS_SETUP_SQL)
            await db.execute(PARTNERS_SETUP_SQL)
            await db.execute(KINKS_SETUP_SQL)

    async def partner_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice]:
        assert interaction.guild
        res: list[Choice] = [app_commands.Choice(name="No Entries Found...", value='x' * 100)]
        choice_list: list[discord.Member] = []

        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            partners = await lover.list_partners()

            if partners is None:
                return res
            else:
                for id in partners:
                    # print(type(id["partner_id"]))
                    member_id = id["partner_id"]
                    member: discord.Member | None = interaction.guild.get_member(member_id)
                    if member is not None:
                        # Choice[name= member.name, value= member.id]
                        choice_list.append(member)

            return [app_commands.Choice(name=member.name, value=str(member.id)) for member in choice_list if current.lower() in member.name.lower()]
        return res

    async def kinks_autocomplete(self, interaction: discord.Interaction, current: str):
        assert interaction.guild
        res: list[Choice] = []
        choice_list: list[str] = []

        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            kinks = await lover.list_kinks()

            if kinks is None:
                return res
            else:
                for entry in kinks:
                    choice_list.append(entry["name"])

                return [Choice(name=kink, value=kink) for kink in choice_list if current.lower() in kink.lower()]
        return res

    # @love_language.command(name="reroll")
    # async def love_reroll(self, interaction: discord.Interaction):
    #     print()

    # @love_language.command(name="interval", description="Time values are in `UTC` only")
    # async def love_message_interval(self, interaction: discord.Interaction) -> None:
    #     print()

    @love_language.command(name="user", description="Manage your user profile")
    @app_commands.choices(action=[Choice(name="add", value="add"), Choice(name="delete", value="delete"), Choice(name="update", value="update")])
    async def love_user_lover(self, interaction: discord.Interaction, action: Choice[str]):
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)

        if action.value == "add":
            if lover is None:
                await LoverEntry.add_lover(name=interaction.user.name, discord_id=interaction.user.id)
                await interaction.response.send_message(content=f"Added *{interaction.user.name}*", ephemeral=True)
            else:
                await interaction.response.send_message(content=f"You are already a Lover user, get out there!", ephemeral=True)

        if action.value == "delete":
            if lover is not None:
                await lover.delete_lover()
                await interaction.response.send_message(content=f"We have removed {interaction.user.name} from the database, sad to see you go~", ephemeral=True)
            else:
                await interaction.response.send_message(content=f"I was unable to find a Lover by the name of `{interaction.user.name}`", ephemeral=True)

        if action.value == "update":
            if lover is not None:
                await lover.update_lover(name=interaction.user.name)
                await interaction.response.send_message(content=f"We updated your Lover name!", ephemeral=True)

    # TODO - Verify operation
    @love_partner.command(name="add", description="Add a partner")
    async def love_add_partner(self, interaction: discord.Interaction, partner: discord.Member) -> None:

        if partner.id == interaction.user.id:
            return await interaction.response.send_message(content=f"You cannot add yourself as a partner... or can you?", ephemeral=True)

        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            result: LoverEntry | None = await lover.add_partner(partner_name=partner.name, partner_id=partner.id)
            if result == None:
                await interaction.response.send_message(content=f"Looks like **{partner.name}** is already your partner, get out there and have fun!", ephemeral=True)
            if type(result) == LoverEntry:
                # await interaction.response.send_message(content=f"You added **{partner.name}** as a partner.", ephemeral=True)
                await interaction.response.send_message(embed=await LoverEmbed.create(color=interaction.user.color, timestamp=discord.utils.utcnow(), lover=lover, interaction=interaction), ephemeral=True)

        else:
            await interaction.response.send_message(content=f"It looks like `{interaction.user.name}` is not a *lover* user.", ephemeral=True)

     # TODO - Verify operation
    @love_partner.command(name="remove", description="Remove a partner.")
    @app_commands.autocomplete(partner=partner_autocomplete)
    async def love_remove_partner(self, interaction: discord.Interaction, partner: str) -> None:
        if len(partner) == 100:
            return await interaction.response.send_message(content='You don\'t have any partners! Why did you select that option?', ephemeral=True)

        if not partner.isdigit():
            return await interaction.response.send_message(content='You must choose from the options prompted to you.', ephemeral=True)

        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        assert interaction.guild

        if lover is not None:
            res: int | None = await lover.remove_partner(partner_id=int(partner))
            partner_name = interaction.guild.get_member(int(partner))
            await interaction.response.send_message(content={f"We removed {res} as a partner," if res is not None else f"Unable to remove {partner_name}"}, ephemeral=True)
        else:
            await interaction.response.send_message(content=f"It looks like `{interaction.user.name}` is not a *lover* user.", ephemeral=True)

     # TODO - Verify operation
    @love_partner.command(name="list", description="Lists all your partners")
    async def love_list_partner(self, interaction: discord.Interaction) -> None:
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        assert interaction.guild

        if lover is not None:
            res: list | None = await lover.list_partners()
            # TODO - Embed each user with Avatar icons?
            if res is not None:
                embeds = []
                for x in res:
                    partner: discord.Member | None = interaction.guild.get_member(int(x))
                    if partner:
                        partner_embed = await PartnerEmbed.create(color=partner.color, title=partner.name, timestamp=discord.utils.utcnow(), partner=partner)
                        embeds.append(partner_embed)
                await interaction.response.send_message(content=f"**{interaction.user.name}** Partner list..", embeds=embeds, ephemeral=True)
            else:
                await interaction.response.send_message(content=f"You have no partners, consider adding some? Get out there and flirt sexy~<3", ephemeral=True)
        else:
            await interaction.response.send_message(content=f"It looks like `{interaction.user.name}` is not a *lover* user.", ephemeral=True)

     # TODO - Verify operation
    @love_kinks.command(name="add", description="Add a type of Kink/Play you like.")
    async def love_kink_add(self, interaction: discord.Interaction, kink: str, description: Union[str, None] = None) -> None:

        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            await lover.add_kink(name=kink, description=description)

            msg_content: str = f"Added `{kink}` to **{interaction.user.name}**"
            if description is not None:
                msg_content += "\n **Description**: " + description

            await interaction.response.send_message(content=msg_content, ephemeral=True)
        else:
            await interaction.response.send_message(content=f"It looks like `{interaction.user.name}` is not a *lover* user.", ephemeral=True)

    @love_kinks.command(name="list", description="List all your Kinks.")
    async def love_kink_list(self, interaction: discord.Interaction):
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            kinks = await lover.list_kinks()
            if kinks is not None:
                kink_embed = discord.Embed(title=f"{interaction.user.name} Kinks list.", color=interaction.user.color, timestamp=discord.utils.utcnow())
                kink_embed.set_thumbnail(url=None if interaction.user.avatar == None else interaction.user.avatar.url)
                for entry in kinks:
                    kink_embed.add_field(name=entry["name"], value=entry["description"], inline=False)
                await interaction.response.send_message(embed=kink_embed, ephemeral=True)
            else:
                await interaction.response.send_message(content=f"It looks like you do not have any Kinks, consider adding some? *cracks whip*", ephemeral=True)
        else:
            await interaction.response.send_message(content=f"It looks like `{interaction.user.name}` is not a *lover* user.", ephemeral=True)

    @love_kinks.command(name="remove", description="Remove a Kink.")
    @app_commands.autocomplete(kink=kinks_autocomplete)
    async def love_kink_remove(self, interaction: discord.Interaction, kink: str):
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            await lover.remove_kink(name=kink)
            await interaction.response.send_message(content=f"Aww no longer into `{kink}` anymore? If that changes just add it back.", ephemeral=True)
        else:
            await interaction.response.send_message(content=f"It looks like `{interaction.user.name}` is not a *lover* user.", ephemeral=True)
    # @tasks.loop(time=datetime.time(hour=14, tzinfo=timezone.utc))
    # async def love_message_loop() -> None:
    #     print()


async def setup(bot: Kuma_Kuma):
    await bot.add_cog(Love(bot))
