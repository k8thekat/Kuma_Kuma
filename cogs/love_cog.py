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

import datetime
from datetime import timezone
import enum
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
    role_switching INT NOT NULL DEFAULT 0,
    role INT NOT NULL,
    position_switching INT NOT NULL DEFAULT 0,
    position INT NOT NULL,
    PRIMARY KEY(discord_id)
)
"""

PARTNERS_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS partners (
    lovers_id INT NOT NULL,
    partner_id INT NOT NULL,
    role_switch INT NOT NULL,
    position_switching INT NOT NULL,
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
    async def create(
        cls,
        *,
        color: int | Colour | None = None,
        title: Any | None = None,
        timestamp: datetime.datetime | None = None,
        lover: LoverEntry,
        interaction: discord.Interaction,
    ):
        self = cls(color=color, title=title, timestamp=timestamp)
        self.set_thumbnail(url=None if interaction.user.avatar == None else interaction.user.avatar.url)
        lover_attrs = ["role", "role_switching", "position", "position_switching"]
        lover_preferences = {}
        for entry in lover_attrs:
            lover_preferences[entry] = getattr(lover, entry)

        self.add_field(
            name="**Preferences**",
            value=lover.role
        )

        assert interaction.guild
        partners = await lover.list_partners()
        if not len(partners):  # or partners is not None:
            self.add_field(name="**Partners**", value="None")
        else:
            members = [interaction.guild.get_member(int(x)) for x in partners]
            self.add_field(
                name="**Partners**",
                value=[member.display_name for member in members if member is not None],
            )

        kinks = await lover.list_kinks()
        if not len(kinks):  # or kinks is not None:
            self.add_field(name="**Kinks**", value="None")
        else:
            for entry in kinks:
                self.add_field(name=entry["name"], value=entry["description"])

        return self


class PartnerEmbed(discord.Embed):
    @classmethod
    async def create(
        cls,
        *,
        color: int | Colour | None = None,
        title: Any | None = None,
        timestamp: datetime.datetime | None = None,
        partner: discord.Member,
    ):
        self = cls(color=color, title=title, timestamp=timestamp)
        self.set_thumbnail(url=None if partner.avatar == None else partner.avatar.url)

        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=partner.id)
        if lover is not None:
            res = await lover.list_kinks()
            if res is not None and len(res):
                kinks = "\n".join([entry["name"] for entry in res])
                self.add_field(name="Kinks", value=kinks)
                # for entry in res:
                # self.add_field(name=entry["name"], value=entry["description"])

        return self


class LoverRoles(enum.Enum):
    dom = 0
    sub = 1


class LoverPositions(enum.Enum):
    top = 0
    bottom = 1


@dataclass(slots=True)
class LoverEntry:
    name: str
    discord_id: int

    role: int
    role_switching: bool

    position: int
    position_switching: bool

    @property
    def get_role(self) -> str:
        """ Possible options see `LoverRoles` """
        pos_roles = ["dom", "sub"]
        return pos_roles[self.role]

    @property
    def get_position(self) -> str:
        """ Possible options see `LoverPositions`"""
        pos_position = ["top", "bottom"]
        return pos_position[self.position]

    # partners: list[dict[int, str]] #{id/owner_id : name}
    # kinks: list[dict[int, str]] #{id/owner_id: name}

    @classmethod
    async def get_or_none(cls, *, discord_id: int) -> LoverEntry | None:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute(
                    """SELECT * FROM lovers WHERE discord_id = ?""", discord_id
                )
                res = await cur.fetchone()

                return cls(**res) if res is not None else None

    @classmethod
    async def add_lover(
        cls,
        *,
        name: str,
        discord_id: int,
        role: int,
        position: int,
        role_switching: bool = False,
        position_switching: bool = False,
    ) -> LoverEntry | None:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute(
                    """INSERT INTO lovers(name, discord_id, role, role_switching, position, position_switching) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(discord_id) DO NOTHING RETURNING *""",
                    name,
                    discord_id,
                    role,
                    position,
                    role_switching,
                    position_switching,
                )
                res = await cur.fetchone()
                await db.commit()

                return cls(**res) if res is not None else None

    async def delete_lover(self) -> int:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute(
                    """DELETE FROM lovers WHERE discord_id = ?""", self.discord_id
                )
                await db.commit()

                return cur.get_cursor().rowcount

    # async def update_lover(self, name: str, role: int, position: int, role_switching: bool = False, position_switching: bool = False) -> LoverEntry:
    async def update_lover(self, args: dict[str, int | bool]) -> LoverEntry:
        SQL = []
        VALUES = []
        for entry in args:
            SQL.append(entry + " = ?")
            VALUES.append(args[entry])

        SQL = ", ".join(SQL)
        VALUES.append(self.discord_id)
        print(SQL)
        print(VALUES)
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                # await cur.execute("""UPDATE lovers SET name = ? WHERE discord_id = ? RETURNING *""", name, self.discord_id)
                await cur.execute(
                    f"""UPDATE lovers SET {SQL} WHERE discord_id = ? RETURNING *""",
                    tuple(VALUES),
                )
                await db.commit()

                res = await cur.fetchone()
                return LoverEntry(**res)

    async def add_partner(
        self,
        # partner_name: str,
        partner_id: int,
        # role: int,
        # position: int,
        role_switching: bool,
        position_switching: bool,
    ) -> LoverEntry | None | bool:
        """
        Partners TABLE SCHEMA
        ----------------------------
            lovers_id `INT NOT NULL`
            partner_id `INT NOT NULL`
            role_switch `INT NOT NULL`
            position_switching `INT NOT NULL`


        RETURNS
        -------------------------
        `False` - partner_id does not exist in `LOVERS` table \n
        `None` - partner_id/lover_id is already in the table as `PRIMARY KEY`.
        """

        partner: LoverEntry | None = await self.get_or_none(discord_id=partner_id)

        # if lover == None:
        #     lover = await self.add_lover(
        #         name=partner_name,
        #         discord_id=partner_id,
        #         role=role,
        #         position=position,
        #         role_switching=role_switching,
        #         position_switching=position_switching,
        #     )

        if partner is not None:
            async with asqlite.connect(DB_FILENAME) as db:
                async with db.cursor() as cur:
                    # await cur.execute("""INSERT INTO partners(lovers_id, partner_id) VALUES (?, ?)
                    # ON CONFLICT(lovers_id, partner_id) DO NOTHING RETURNING *""", lover.discord_id, partner_id)
                    try:
                        await cur.execute(
                            """INSERT INTO partners(lovers_id, partner_id, role_switch, position_switching) VALUES (?, ?, ?, ?)""",
                            self.discord_id,
                            partner_id,
                            role_switching,
                            position_switching
                            # lover.role_switching,
                            # lover.position_switching,
                        )
                        # await cur.execute("""INSERT INTO partners(partner_id) VALUES (?, ?))

                    except sqlite3.IntegrityError as err:
                        if (
                            type(err.args[0]) == str
                            and err.args[0].lower()
                            == "unique constraint failed: partners.lovers_id, partners.partner_id"
                        ):
                            return None

                    # res = await cur.fetchone()
                    await db.commit()
                    return partner
        else:
            return False
        # return lover

    async def remove_partner(self, partner_id: int) -> None | int:
        lover = await self.get_or_none(discord_id=partner_id)

        if lover == None:
            return lover

        else:
            async with asqlite.connect(DB_FILENAME) as db:
                async with db.cursor() as cur:
                    await cur.execute(
                        """DELETE FROM partners WHERE lovers_id = ? and partner_id = ?""",
                        self.discord_id,
                        partner_id,
                    )
                    await db.commit()

                    return cur.get_cursor().rowcount

    async def list_partners(self) -> list:
        """
        Returns a list of Discord IDs for lookup.
        """
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute(
                    """SELECT partner_id FROM partners WHERE lovers_id = ?""",
                    self.discord_id,
                )
                res = await cur.fetchall()
                # partners = []
                if res:
                    # for entry in res:
                    #     if entry["partner_id"] not in partners and entry["partner_id"] != self.discord_id:
                    #         partners.append(entry["partner_id"])
                    #     # if entry["lovers_id"] not in partners and entry["lovers_id"] != self.discord_id:
                    #     #     partners.append(entry["lovers_id"])
                    res = [entry["partner_id"] for entry in res]

                # return partners if len(partners) else None
                return res

    async def add_kink(
        self, name: str, description: Union[str, None] = None
    ) -> str | None:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute(
                    """INSERT INTO kinks(lovers_id, name, description) VALUES (?, ?, ?) 
                ON CONFLICT(lovers_id, name) DO NOTHING RETURNING *""",
                    self.discord_id,
                    name,
                    description,
                )
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

    async def list_kinks(self) -> list:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute(
                    """SELECT * FROM kinks WHERE lovers_id = ?""", self.discord_id
                )
                res = await cur.fetchall()

                return res if not None else None


class Love(commands.Cog):
    love_language = app_commands.Group(
        name="love", description="Love helper commands", nsfw=True
    )

    love_user = app_commands.Group(
        name="user", description="User profile commands",
        parent=love_language,
        nsfw=True,
        guild_only=True
    )

    love_partner = app_commands.Group(
        name="partner",
        description="Partner related commands.",
        parent=love_language,
        nsfw=True,
        guild_only=True
    )

    love_kinks = app_commands.Group(
        name="kinks",
        description="Kink related commands.",
        parent=love_language,
        nsfw=True,
        guild_only=True
    )

    def __init__(self, bot: Kuma_Kuma) -> None:
        self._bot: Kuma_Kuma = bot
        self._name: str = os.path.basename(__file__).title()
        self._logger = logging.getLogger()
        self._logger.info(f"**SUCCESS** Initializing {self._name} ")

    async def cog_load(self) -> None:
        async with asqlite.connect(DB_FILENAME) as db:
            await db.execute(LOVERS_SETUP_SQL)
            await db.execute(PARTNERS_SETUP_SQL)
            await db.execute(KINKS_SETUP_SQL)

    async def partner_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice]:
        assert interaction.guild
        res: list[Choice] = [
            app_commands.Choice(name="No Entries Found...", value="x" * 100)
        ]
        choice_list: list[discord.Member] = []

        lover: LoverEntry | None = await LoverEntry.get_or_none(
            discord_id=interaction.user.id
        )
        if lover is not None:
            partners = await lover.list_partners()

            if partners is None:
                return res
            else:
                for id in partners:
                    # print(type(id["partner_id"]))
                    member: discord.Member | None = interaction.guild.get_member(id)
                    if member is not None:
                        # Choice[name= member.name, value= member.id]
                        choice_list.append(member)

            return [
                app_commands.Choice(name=member.display_name, value=str(member.id))
                for member in choice_list
                if current.lower() in member.name.lower()
            ]
        return res

    async def kinks_autocomplete(self, interaction: discord.Interaction, current: str):
        assert interaction.guild
        res: list[Choice] = []
        choice_list: list[str] = []

        lover: LoverEntry | None = await LoverEntry.get_or_none(
            discord_id=interaction.user.id
        )
        if lover is not None:
            kinks = await lover.list_kinks()

            if kinks is None:
                return res
            else:
                for entry in kinks:
                    choice_list.append(entry["name"])

                return [
                    Choice(name=kink, value=kink)
                    for kink in choice_list
                    if current.lower() in kink.lower()
                ]
        return res

    # @love_language.command(name="reroll")
    # async def love_reroll(self, interaction: discord.Interaction):
    #     print()

    # @love_language.command(name="interval", description="Time values are in `UTC` only")
    # async def love_message_interval(self, interaction: discord.Interaction) -> None:
    #     print()

    @love_user.command(name="add", description="Create your Lover profile.")
    # @app_commands.choices(
    #     action=[
    #         Choice(name="add", value="add"),
    #         #Choice(name="delete", value="delete"),
    #         # Choice(name="update", value="update"),
    #     ]
    # )
    @app_commands.describe(role="Your prefered `role` as a partner")
    @app_commands.describe(position="Your prefered `position` with partners.")
    async def love_user_add(
        self,
        interaction: discord.Interaction,
        # action: Choice[str],
        role: LoverRoles,
        position: LoverPositions,
        role_switching: bool = False,
        position_switching: bool = False,
    ):
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)

        # if action.value == "add":
        if lover is None:
            await LoverEntry.add_lover(
                name=interaction.user.display_name,
                discord_id=interaction.user.id,
                role=role.value,
                position=position.value,
                role_switching=role_switching,
                position_switching=position_switching,
            )

            await interaction.response.send_message(
                content=f"Added *{interaction.user.display_name}*",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                content=f"You are already a *Lover* user, get out there!",
                ephemeral=True
            )

    @love_user.command(name="delete", description="Remove your Lover profile.")
    async def love_user_delete(
        self,
        interaction: discord.Interaction
    ):
        # if action.value == "delete":
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            await lover.delete_lover()
            await interaction.response.send_message(
                content=f"We have removed {interaction.user.display_name} from the database, sad to see you go~",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                content=f"I was unable to find a Lover member by the name of `{interaction.user.display_name}`",
                ephemeral=True
            )

    @love_user.command(name="update", description="Update your Lover profile.")
    async def love_user_update(
        self,
        interaction: discord.Interaction,
        name: str | None = None,
        role: LoverRoles | None = None,
        role_switching: bool | None = None,
        position: LoverPositions | None = None,
        position_switching: bool | None = None
    ):
        func_args = locals()
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)

    #    if action.value == "update":
        if lover is not None:
            # We only want to update vars that are not None
            func_args.pop("self")
            func_args.pop("interaction")
            # func_args.pop("lover")
            results: dict = {}
            for entry in func_args:
                if func_args[entry] is not None:
                    results[entry] = func_args[entry]
            await lover.update_lover(args=results)
            await interaction.response.send_message(
                content=f"We updated your Lover profile!",
                ephemeral=True
            )

    # TODO! -- Add Lover info command..
    @love_user.command(name="info", description="Shows your Lover profile information.")
    async def love_user_info(
        self,
        interaction: discord.Interaction
    ):
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            await interaction.response.send_message(
                embed=await LoverEmbed.create(
                    color=interaction.user.color,
                    title=f"**{interaction.user.display_name}**",
                    timestamp=discord.utils.utcnow(),
                    lover=lover,
                    interaction=interaction),
                ephemeral=True
            )

    @love_partner.command(name="add", description="Add a partner")
    async def love_add_partner(
        self, interaction: discord.Interaction,
        partner: discord.Member,
        role_switching: bool | None = None,
        position_switching: bool | None = None
    ) -> None:
        if partner.id == interaction.user.id:
            return await interaction.response.send_message(
                content=f"You cannot add yourself as a partner... or can you?",
                ephemeral=True,
            )

        lover: LoverEntry | None = await LoverEntry.get_or_none(
            discord_id=interaction.user.id
        )
        if lover is not None:
            # If the lover did not set the role switching or position switching; use theirs as the default.
            if role_switching == None:
                role_switching = lover.role_switching
            if position_switching == None:
                position_switching = lover.position_switching

            result: LoverEntry | bool | None = await lover.add_partner(
                partner_id=partner.id, role_switching=role_switching, position_switching=position_switching
            )
            if result == None:
                await interaction.response.send_message(
                    content=f"Looks like **{partner.display_name}** is already your partner, get out there and have fun!",
                    ephemeral=True
                )
            elif result == False:
                await interaction.response.send_message(
                    content=f"**{partner.display_name}** is not a Lover, ask them to add themselves first!",
                    ephemeral=True
                )

            # Add the lover to the partner
            lover_partner = await LoverEntry.get_or_none(discord_id=partner.id)
            if lover_partner is not None:
                await lover_partner.add_partner(
                    partner_id=interaction.user.id,
                    role_switching=lover_partner.role_switching,
                    position_switching=lover_partner.position_switching
                )

            if type(lover_partner) == LoverEntry:
                await interaction.response.send_message(
                    embed=await LoverEmbed.create(
                        color=partner.color,
                        title=partner.display_name,
                        timestamp=discord.utils.utcnow(),
                        lover=lover_partner,
                        interaction=interaction,
                    ),
                    ephemeral=True,
                )

        else:
            await interaction.response.send_message(
                content=f"It looks like `{interaction.user.display_name}` is not a *lover* user.",
                ephemeral=True,
            )

    @love_partner.command(name="remove", description="Remove a partner.")
    @app_commands.autocomplete(partner=partner_autocomplete)
    async def love_remove_partner(
        self, interaction: discord.Interaction, partner: str
    ) -> None:
        if len(partner) == 100:
            return await interaction.response.send_message(
                content="You don't have any partners! Why did you select that option?",
                ephemeral=True,
            )

        if not partner.isdigit():
            return await interaction.response.send_message(
                content="You must choose from the options prompted to you.",
                ephemeral=True,
            )

        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        assert interaction.guild

        if lover is not None:
            res: int | None = await lover.remove_partner(partner_id=int(partner))
            partner_discord = interaction.guild.get_member(int(partner))
            await interaction.response.send_message(
                content=f"We removed your partner by the name of `{partner_discord.display_name}`."
                if partner_discord is not None
                else f"Unable to remove `{partner}`",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                content=f"It looks like `{interaction.user.display_name}` is not a *lover* user.",
                ephemeral=True,
            )

    @love_partner.command(name="list", description="Lists all your partners")
    async def love_list_partner(self, interaction: discord.Interaction) -> None:
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        assert interaction.guild

        if lover is not None:
            res: list | None = await lover.list_partners()
            if res is not None and len(res):
                embeds = []
                for x in res:
                    partner: discord.Member | None = interaction.guild.get_member(
                        int(x)
                    )
                    if partner is not None:
                        partner_embed = await PartnerEmbed.create(
                            color=partner.color,
                            title=partner.display_name,
                            timestamp=discord.utils.utcnow(),
                            partner=partner,
                        )
                        embeds.append(partner_embed)

                await interaction.response.send_message(
                    content=f"**{interaction.user.display_name}** Partners",
                    embeds=embeds,
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    content=f"You have no partners, consider adding some? Get out there and flirt sexy~<3",
                    ephemeral=True,
                )
        else:
            await interaction.response.send_message(
                content=f"It looks like `{interaction.user.display_name}` is not a *lover* user.",
                ephemeral=True,
            )

    # TODO - Verify operation
    @love_kinks.command(name="add", description="Add a type of Kink/Play you like.")
    async def love_kink_add(
        self,
        interaction: discord.Interaction,
        kink: str,
        description: Union[str, None] = None,
    ) -> None:
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            await lover.add_kink(name=kink, description=description)

            msg_content: str = f"Added `{kink}` to **{interaction.user.display_name}**"
            if description is not None:
                msg_content += "\n **Description**: " + description

            await interaction.response.send_message(content=msg_content, ephemeral=True)
        else:
            await interaction.response.send_message(
                content=f"It looks like `{interaction.user.display_name}` is not a *lover* user.",
                ephemeral=True,
            )

    @love_kinks.command(name="list", description="List all your Kinks.")
    async def love_kink_list(self, interaction: discord.Interaction):
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            kinks = await lover.list_kinks()
            if kinks is not None and len(kinks):
                kink_embed = discord.Embed(
                    title=f"{interaction.user.display_name} `Kinks~`",
                    color=interaction.user.color,
                    timestamp=discord.utils.utcnow(),
                )
                kink_embed.set_thumbnail(
                    url=None
                    if interaction.user.avatar == None
                    else interaction.user.avatar.url
                )

                for entry in kinks:
                    kink_embed.add_field(
                        name=entry["name"], value=entry["description"], inline=False
                    )
                await interaction.response.send_message(
                    embed=kink_embed, ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    content=f"It looks like you do not have any Kinks, consider adding some? *cracks whip*",
                    ephemeral=True,
                )
        else:
            await interaction.response.send_message(
                content=f"It looks like `{interaction.user.display_name}` is not a *lover* user.",
                ephemeral=True,
            )

    @love_kinks.command(name="remove", description="Remove a Kink.")
    @app_commands.autocomplete(kink=kinks_autocomplete)
    async def love_kink_remove(self, interaction: discord.Interaction, kink: str):
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            await lover.remove_kink(name=kink)
            await interaction.response.send_message(
                content=f"Aww no longer into `{kink}` anymore? If that changes just add it back.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                content=f"It looks like `{interaction.user.display_name}` is not a *lover* user.",
                ephemeral=True,
            )

    # @tasks.loop(time=datetime.time(hour=14, tzinfo=timezone.utc))
    # async def love_message_loop() -> None:
    #     print()


async def setup(bot: Kuma_Kuma):
    await bot.add_cog(Love(bot))
