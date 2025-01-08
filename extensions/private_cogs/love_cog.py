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
import enum
import logging
import os
import sqlite3
from datetime import timedelta, timezone
from pprint import pprint
from typing import Any, List, Union

import discord
import pytz
from discord import Member, app_commands
from discord.app_commands import Choice
from discord.ext import commands, tasks

import utils.asqlite as asqlite
import utils.timezones
from cogs.love_cog_utils.db import *
from cogs.love_cog_utils.ui import *

_logger = logging.getLogger()


class LoverRoles(enum.Enum):
    dominant = 0
    submissive = 1


class LoverPositions(enum.Enum):
    top = 0
    bottom = 1


class Love(commands.Cog):
    love_language = app_commands.Group(
        name="love", description="Love helper commands", nsfw=True
    )

    love_user = app_commands.Group(
        name="user",
        description="User profile commands",
        parent=love_language,
        nsfw=True,
        guild_only=True,
    )

    love_partner = app_commands.Group(
        name="partner",
        description="Partner related commands.",
        parent=love_language,
        nsfw=True,
        guild_only=True,
    )

    love_kinks = app_commands.Group(
        name="kinks",
        description="Kink related commands.",
        parent=love_language,
        nsfw=True,
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self._bot: commands.Bot = bot
        self._name: str = os.path.basename(__file__).title()
        self._logger = logging.getLogger()
        self._logger.info(f"**SUCCESS** Initializing {self._name} ")

        self._last_utc_minutes: int = (discord.utils.utcnow().hour * 60 + discord.utils.utcnow().minute)

    async def cog_load(self) -> None:
        # Generate our list of "Choices"
        self._timezones_choices: list[Choice[str]] = await utils.timezones.parse_bcp47_timezones()
        self._timezone_aliases: dict[str, str] = utils.timezones._timezone_aliases
        self._time_table = TimeTable()

        async with asqlite.connect(DB_FILENAME) as db:
            await db.execute(LOVERS_SETUP_SQL)
            await db.execute(PARTNERS_SETUP_SQL)
            await db.execute(KINKS_SETUP_SQL)
            await db.execute(TIMEZONE_SETUP_SQL)

        # await self.love_message_loop.start()

    async def cog_unload(self) -> None:
        if self.love_message_loop.is_running() is True:
            self.love_message_loop.cancel()

    def row_todict(self, lover: LoverEntry, row: list[sqlite3.Row] | None,) -> dict[str, str] | None:
        """ Converts `list[Row]` into dict \n
        Any duplicate keys will append `(lover.name)` to the entry["name"].\n
        All values will contain `entry["name"]:lover.discord_id` for lookup.\n
        RETURNS = `{["name"] + f" ({lover.name})" : ["name"] + f":{lover.discord_id}"}`
        """
        # We will have two list[Row Factory] ideally; from different Lovers
        # There will be a chance of duplicate ["name"] values; so we should append and or add possible the Lover.name to the ["name"] value
        # We need to return the  "name=" ["name"] param of the Choice and "value=" "name" + "lover_id" aka Lovers(discord_id)
        res: dict[str, str] = {}
        if row is None:
            return None

        for values in row:
            res[values["name"]] = values["name"] + f":{lover.discord_id}"
        return res

    def merg_dict(self, dict1: dict[str, str], dict2: dict[str, str], lover: LoverEntry) -> dict[str, str]:

        for entry in dict2:
            if entry in dict1:
                name = entry + f" ({lover.name})"
                dict1[name] = entry + f":{lover.discord_id}"
            else:
                dict1[entry] = dict2[entry]

        return dict1

    async def lover_handler(self, interaction: discord.Interaction, lover_id: int | str):
        if isinstance(lover_id, str):
            if len(lover_id) == 100:
                return await interaction.response.send_message(content="You don't have any partners! Why did you select that option?", ephemeral=True)

            if not lover_id.isdigit():
                return await interaction.response.send_message(content="You must choose from the options prompted to you.", ephemeral=True)
        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=int(lover_id))
        if lover is None:
            return await interaction.response.send_message(content=f"It appears {interaction.user.display_name} is not a *Lover* user.", ephemeral=True)
        return lover

    async def partner_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice]:
        assert interaction.guild
        res: list[Choice] = [app_commands.Choice(name="No Entries Found...", value="x" * 100)]
        choice_list: list[discord.Member] = []

        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=interaction.user.id)
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

            return [app_commands.Choice(name=member.display_name, value=str(member.id)) for member in choice_list if current.lower() in member.name.lower()]
        return res

    async def kinks_autocomplete(self, interaction: discord.Interaction, current: str):
        assert interaction.guild
        res: list[Choice] = [app_commands.Choice(name="No Entries Found...", value="x" * 100)]
        choice_list: list[str] = []

        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            kinks = await lover.list_kinks()

            if kinks is None:
                return res
            else:
                for entry in kinks:
                    choice_list.append(entry["name"])

                return [Choice(name=kink, value=kink)for kink in choice_list if current.lower() in kink.lower()]
        return res

    # TODO Need to possible validate logic.
    async def partners_kinks_autocomplete(self, interaction: discord.Interaction, current: str):
        # Would like to possible know the kink name along with the description..
        assert interaction.guild
        kinks: dict[str, str] = {}
        res: list[Choice] = [app_commands.Choice(name="No Entries Found...", value="x" * 100)]
        lover: LoverEntry | None = await LoverEntry.get_or_none(
            discord_id=interaction.user.id)

        if lover is None:
            return res

        lover_partners = await lover.list_partners()
        lover_kinks: dict[str, str] | None = self.row_todict(lover=lover, row=await lover.list_kinks() if not None else None)
        if lover_kinks is not None:
            kinks = self.merg_dict(dict1=kinks, dict2=lover_kinks, lover=lover)

        if len(lover_partners) and lover_partners is not None:
            # TODO - Turn this into list comp??
            # partners = [for partner   (await LoverEntry.get_or_none(discord_id=int(id)) for id in partners)]
            # partners: list[LoverEntry | None] = [await LoverEntry.get_or_none(discord_id=int(id)) for id in partner_res]
            for id in lover_partners:
                partner: LoverEntry | None = await LoverEntry.get_or_none(discord_id=int(id))
                if partner is None:
                    continue
                partners_kinks: dict[str, str] | None = self.row_todict(lover=partner, row=await partner.list_kinks() if not None else None)
                if partners_kinks is None:
                    continue
                else:
                    kinks = self.merg_dict(dict1=kinks, dict2=partners_kinks, lover=partner)

            return [Choice(name=key, value=value) for key, value in kinks.items() if current.lower() in key.lower()][:25]
        else:
            return res

    async def timezone_set_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        cur_choices = list(self._timezones_choices)
        for key, value in self._timezone_aliases.items():
            if current.lower() in key.lower():
                cur_choices.append(app_commands.Choice(name=key, value=value))

        return [tz for tz in cur_choices if current.lower() in tz.name.lower()][:25]

        # if not argument:
        #     return timezones._default_timezones
        # matches: list[TimeZone] = timezones.find_timezones(argument)

    async def times_autocomplete(self, interaction: discord.Interaction, current: str):
        choice_list = [app_commands.Choice(name=x, value=x) for x in self._time_table.create_table()]
        return [time for time in choice_list if current.lower() in time.name.lower()][:25]

    # TODO - Figure out why this loop is blocking inside of `cog_load` and test DB lookup/results
    @tasks.loop(seconds=60)
    async def love_message_loop(self) -> None:
        # The goal of this loop is to look at all partner's suggested time (aka s_time) and use the minute value stored in the database
        # as a UTC offset from midnight against the current UTC time as for when to fire.
        cur_utc_minutes: int = (discord.utils.utcnow().hour * 60 + discord.utils.utcnow().minute)
        # res = await get_suggestion_time(value1=self.last_utc_minutes + 1, value2=cur_utc_minutes)
        res = await get_range_suggestion_time(value1=0, value2=9999)  # spoof test
        self.last_utc_minutes = cur_utc_minutes
        if res is not None:
            for partners in res:
                print(partners["partner_id"])
                print(partners["lovers_id"])
        print("finished loop")

    # @love_message_loop.before_loop
    # async def before_message_loop(self) -> None:
    #     await self._bot.wait_until_ready()

    # @tasks.loop(seconds=60)
    # async def love_dev_loop(self):
    #     print("Dev loop fired")
    #     time_format = '%x %I:%M %p'
    #     # My LoverEntry profile
    #     lover = await LoverEntry.get_or_none(discord_id=144462063920611328)
    #     assert lover
    #     time_test = await self._time_table.time_converter(time="6:56PM", lover=lover)

    #     today = datetime.date.today()
    #     utc_time = datetime.time(hour=0, minute=0)
    #     utc_midnight = pytz.timezone('UTC').localize(datetime.datetime.combine(today, utc_time))

    #     print("minutes", time_test)
    #     s_time = utc_midnight + timedelta(minutes=time_test)
    #     var1 = datetime.datetime.now(tz=pytz.timezone("UTC"))
    #     var2 = s_time
    #     print("UTC Now == TimeStamp", datetime.datetime.now(tz=pytz.timezone("UTC")).strftime(time_format), s_time.strftime(time_format))
    #     if var1 == var2 or var1 > var2:
    #         print("success")
    #     return

    # @love_language.command(name="reroll")
    # async def love_reroll(self, interaction: discord.Interaction):
    #     print()

    @love_user.command(name="add", description="Create your Lover profile.")
    @app_commands.describe(role="Your prefered `role` as a partner")
    @app_commands.describe(position="Your prefered `position` with partners.")
    async def love_user_add(self, interaction: discord.Interaction, role: LoverRoles, position: LoverPositions, role_switching: bool = False, position_switching: bool = False):

        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=interaction.user.id)
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

            await interaction.response.send_message(content=f"Added *{interaction.user.display_name}*", ephemeral=True)
        else:
            await interaction.response.send_message(content=f"You are already a *Lover* user, get out there!", ephemeral=True,)

    # TODO Verify Operation
    @love_user.command(name="delete", description="Remove your Lover profile.")
    async def love_user_delete(self, interaction: discord.Interaction):
        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            await lover.delete_lover()
            await interaction.response.send_message(content=f"We have removed {interaction.user.display_name} from the database, sad to see you go~", ephemeral=True)
        else:
            await interaction.response.send_message(content=f"I was unable to find a Lover member by the name of `{interaction.user.display_name}`", ephemeral=True)

    @love_user.command(name="update", description="Update your Lover profile.")
    @app_commands.autocomplete(tz=timezone_set_autocomplete)
    @app_commands.describe(tz="Set your timezone")
    async def love_user_update(self, interaction: discord.Interaction, name: str | None = None, role: LoverRoles | None = None,
                               role_switching: bool | None = None, position: LoverPositions | None = None, position_switching: bool | None = None, tz: str | None = None):
        func_args = locals()
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)

        #    if action.value == "update":
        if lover is not None:
            # We only want to update vars that are not None
            func_args.pop("self")
            func_args.pop("interaction")
            func_args.pop("role")
            func_args.pop("position")
            func_args.pop("tz")
            # func_args.pop("lover")
            results: dict = {}

            # This is what is known as ooga booga 3 am code :D This is technically the most efficient way to do this because we don't remember any other way of doing this
            if role is not None:
                results["role"] = role.value
            if position is not None:
                results["position"] = position.value

            for entry in func_args:
                # if entry == "role" and role is not None:
                #     results["role"] = role.value
                # if entry == "position" and position is not None:
                #     results["position"] = position.value
                if func_args[entry] is not None:
                    results[entry] = func_args[entry]

            if len(results):
                await lover.update_lover(args=results)

            if tz is not None:
                try:
                    res = await utils.timezones.convert_timezones(tz=tz)
                except:
                    return await interaction.response.send_message(content=f"You provided an improper Timezone; please pick from the provided selection only.", ephemeral=True)
                await lover.set_timezone(tz=tz)

            await interaction.response.send_message(content=f"We updated your *Lover* profile!", ephemeral=True)

    @love_user.command(name="info", description="Shows a Lovers profile information.")
    @app_commands.autocomplete(lover=partner_autocomplete)
    async def love_user_info(self, interaction: discord.Interaction, lover: str | None = None):
        assert interaction.guild
        # This is for Partner lookup..
        if lover is not None:
            if len(lover) == 100:
                return await interaction.response.send_message(content="You don't have any partners! Why did you select that option?", ephemeral=True)

            if not lover.isdigit():
                return await interaction.response.send_message(content="You must choose from the options prompted to you.", ephemeral=True)

            res: LoverEntry | None = await LoverEntry.get_or_none(discord_id=int(lover))
            member: discord.Member | None = interaction.guild.get_member(int(lover))
            if res is not None and member is not None:
                return await interaction.response.send_message(
                    embed=await LoverEmbed.create(color=member.color,
                                                  title=f"**{member.display_name}**",
                                                  timestamp=discord.utils.utcnow(),
                                                  lover=res, interaction=interaction,
                                                  member=member,), ephemeral=True,)

        # This is for self lookup...
        if lover is None:
            res = await LoverEntry.get_or_none(discord_id=interaction.user.id)
            if res is not None:
                return await interaction.response.send_message(
                    embed=await LoverEmbed.create(color=interaction.user.color,
                                                  title=f"**{interaction.user.display_name}**",
                                                  timestamp=discord.utils.utcnow(),
                                                  lover=res, interaction=interaction,), ephemeral=True)

            else:
                return await interaction.response.send_message(content=f"**{interaction.user.display_name}** is not a Lover, ask them to add themselves first!", ephemeral=True,)

    @love_user.command(name="timezone", description="Set your timezone for misc. bot interactions.")
    @app_commands.describe(tz="Set your timezone")
    @app_commands.autocomplete(tz=timezone_set_autocomplete)
    async def love_user_timezone(self, interaction: discord.Interaction, tz: str):
        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            # This will error out if someone provides a timezone that does not exist acting as a "hacky" validation.
            try:
                res = await utils.timezones.convert_timezones(tz=tz)
            except:
                return await interaction.response.send_message(content=f"You provided an improper Timezone; please pick from the provided selection only.", ephemeral=True)

            await lover.set_timezone(tz=tz)
            return await interaction.response.send_message(content=f"You timezone has been set to **{tz}** \n > Current date/time is: **{res.strftime('%x %I:%M %p')}**.", ephemeral=True)
        if lover is None:
            return await interaction.response.send_message(content=f"It looks like `{interaction.user.display_name}` is not a *lover* user.", ephemeral=True)

    @love_partner.command(name="add", description="Add a partner")
    async def love_partner_add(self, interaction: discord.Interaction, partner: discord.Member,) -> None:
        assert interaction.guild

        if partner.id == interaction.user.id:
            # cloning intensifies
            return await interaction.response.send_message(content=f"You cannot add yourself as a partner... or can you?", ephemeral=True)

        # Verify the possible partner is a Lover.
        partner_lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=partner.id)
        if partner_lover is None:
            return await interaction.response.send_message(content=f"**{partner.display_name}** is not a *Lover*, ask them to add themselves first!", ephemeral=True)

        # Verify the user is a Lover.
        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            # Lets verify the partner is not already a partner
            results = await lover.list_partners()
            if interaction.user.id in results:
                return await interaction.response.send_message(content=f"Looks like **{partner.display_name}** is already your partner, get out there and have fun!", ephemeral=True)
            else:
                await LoverPartnerView.request(sender=interaction.user, maybe_partner=partner, guild=interaction.guild)
                return await interaction.response.send_message(content=f"Lover request message sent to {partner.mention}", ephemeral=True)

        # If the user is not a Lover; fail.
        if lover is None:
            return await interaction.response.send_message(content=f"It looks like `{interaction.user.display_name}` is not a *lover* user.", ephemeral=True)
        #     # Add the lover to the partner
        #     lover_partner = await LoverEntry.get_or_none(discord_id=partner.id)
        #     if lover_partner is not None:
        #         await lover_partner.add_partner(
        #             partner_id=interaction.user.id,
        #             role_switching=lover_partner.role_switching,
        #             position_switching=lover_partner.position_switching,
        #         )

        #     if type(lover_partner) == LoverEntry:
        #         await interaction.response.send_message(
        #             embed=await LoverEmbed.create(
        #                 color=partner.color,
        #                 title=partner.display_name,
        #                 timestamp=discord.utils.utcnow(),
        #                 lover=lover_partner,
        #                 interaction=interaction,
        #                 member=partner,
        #             ),
        #             ephemeral=True,
        #         )

    @love_partner.command(name="remove", description="Remove a partner.")
    @app_commands.autocomplete(partner=partner_autocomplete)
    async def love_partner_remove(self, interaction: discord.Interaction, partner: str) -> None:
        if len(partner) == 100:
            return await interaction.response.send_message(content="You don't have any partners! Why did you select that option?", ephemeral=True,)

        if not partner.isdigit():
            return await interaction.response.send_message(content="You must choose from the options prompted to you.", ephemeral=True,)

        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        assert interaction.guild

        if lover is not None:
            res: int | None = await lover.remove_partner(partner_id=int(partner))
            partner_discord = interaction.guild.get_member(int(partner))
            await interaction.response.send_message(content=f"We removed your partner by the name of `{partner_discord.display_name}`."if partner_discord is not None else f"Unable to remove `{partner}`", ephemeral=True)
        else:
            await interaction.response.send_message(content=f"It looks like `{interaction.user.display_name}` is not a *Lover* user.", ephemeral=True)

    @love_partner.command(name="update", description="Update your relationship with a partner.")
    @app_commands.autocomplete(suggestion_time=times_autocomplete)
    @app_commands.autocomplete(partner=partner_autocomplete)
    async def love_partner_update(self, interaction: discord.Interaction, partner: str, role_switching: bool | None = None, position_switching: bool | None = None, suggestion_time: str | None = None):

        func_args: dict[str, Any] = locals()
        func_args.pop("self")
        func_args.pop("interaction")
        func_args.pop("partner")
        func_args.pop("suggestion_time")

        s_time: float | None = None
        results: dict = {}

        for entry in func_args:
            if func_args[entry] is not None:
                results[entry] = func_args[entry]

        lover: LoverEntry | None = await self.lover_handler(interaction=interaction, lover_id=interaction.user.id)

        if lover is not None:
            if suggestion_time is not None:
                s_time = await self._time_table.suggestion_time_diff(time=suggestion_time, lover=lover)
                results["s_time"] = s_time
            if role_switching is None:
                role_switching = lover.role_switching
            if position_switching is None:
                position_switching = lover.position_switching
           # print("results", results)
            if len(results):
                # add our partners discord ID as our last value. We add the discord_id inside `update_partner`
                results["partner_id"] = int(partner)
                await lover.update_partner(args=results)
                pprint(results)
                # TODO Format reply displaying changed values as an embed?
                # for key, value in results:
                # example: {'partner_id': 479429344213860372, 's_time': 1680}
                await interaction.response.send_message(content=f"Updated user placeholder..")

    @love_partner.command(name="list", description="Lists all your partners")
    async def love_partner_list(self, interaction: discord.Interaction) -> None:
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        assert interaction.guild

        if lover is None:
            return await interaction.response.send_message(content=f"It looks like `{interaction.user.display_name}` is not a *lover* user.", ephemeral=True)

        res: list | None = await lover.list_partners()
        if res is not None and len(res):
            embeds: list[discord.Embed] = []
            rem_partner: int = 0

            for partner_id in res:
                partner: discord.Member | None = interaction.guild.get_member(int(partner_id))
                lover_partner: LoverEntry | None = await LoverEntry.get_or_none(discord_id=partner_id)

                # If we cannot find them at all (our DB or in the Guild)
                if partner is None and lover_partner is None:
                    await lover.remove_partner(partner_id=partner_id)
                    rem_partner += 1
                    await interaction.response.send_message(content="We are unable to find one of your partners; removing them as your partner.", ephemeral=True)
                    continue

                # if we cannot find them in the DB but they exists in the guild.
                if lover_partner is None and partner is not None:
                    await lover.remove_partner(partner_id=partner_id)
                    rem_partner += 1
                    await interaction.response.send_message(content=f"It appears {partner.display_name} does not have a *Lover* profile anymore. Removing them as your partner.", ephemeral=True)
                    continue

                # if we cannot find them in the guild but they are in the DB.
                if partner is None and lover_partner is not None:
                    await lover.remove_partner(partner_id=partner_id)
                    rem_partner += 1
                    await interaction.response.send_message(content=f"{lover_partner.name} is no longer a member of this guild, removing them as your partner.", ephemeral=True)
                    continue

                if partner is not None and lover_partner is not None:
                    partner_embed: PartnerEmbed = await PartnerEmbed.create(color=partner.color, title=partner.display_name, timestamp=discord.utils.utcnow(), lover_id=interaction.user.id, partner=partner)
                    embeds.append(partner_embed)

            await interaction.response.send_message(content=f"**{interaction.user.display_name}**'s Partners {f'(Removed {rem_partner})' if rem_partner > 0 else ''}", embeds=embeds, ephemeral=True)
        else:
            await interaction.response.send_message(content=f"You have no partners, consider adding some? Get out there and flirt sexy~<3", ephemeral=True)

    @love_kinks.command(name="add", description="Add a type of Kink/Play you like.")
    async def love_kink_add(self, interaction: discord.Interaction, kink: str, description: Union[str, None] = None) -> None:
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            await lover.add_kink(name=kink, description=description)

            msg_content: str = f"Added `{kink}` to **{interaction.user.display_name}**"
            if description is not None:
                msg_content += "\n **Description**: " + description

            await interaction.response.send_message(content=msg_content, ephemeral=True)
        else:
            await interaction.response.send_message(content=f"It looks like `{interaction.user.display_name}` is not a *lover* user.", ephemeral=True)

    @love_kinks.command(name="list", description="List all your Kinks.")
    @app_commands.autocomplete(lover=partner_autocomplete)
    async def love_kink_list(self, interaction: discord.Interaction, lover: str | None = None):
        assert interaction.guild
        res: LoverEntry | None = None
        member: discord.Member | None | discord.User = None
        # This is for Partner lookup..
        if lover is not None:
            if len(lover) == 100:
                return await interaction.response.send_message(content="You don't have any partners! Why did you select that option?", ephemeral=True)

            if not lover.isdigit():
                return await interaction.response.send_message(content="You must choose from the options prompted to you.", ephemeral=True)

            res = await LoverEntry.get_or_none(discord_id=int(lover))
            member = interaction.guild.get_member(int(lover))

        if lover is None:
            res: LoverEntry | None = await LoverEntry.get_or_none(discord_id=interaction.user.id)
            member = interaction.user

        if res is not None and member is not None:
            kinks = await res.list_kinks()
            if kinks is not None and len(kinks):
                kink_embed = discord.Embed(title=f"{member.display_name} **Kinks~**", color=member.color, timestamp=discord.utils.utcnow())

                kink_embed.set_thumbnail(url=None if member.avatar == None else member.avatar.url)

                # Kinks Embed Field Generator
                kink_results: list = await res.list_kinks()
                if not len(kink_results):  # or kinks is not None:
                    kink_embed.add_field(name="**__Kinks__**", value="*Currently no Kinks*")
                else:
                    display_kinks: list = [f"- **{entry['name']}**" for entry in kink_results]
                    kink_embed.add_field(name="**__Kinks__**", value="\n".join(display_kinks), inline=False)
                await interaction.response.send_message(embed=kink_embed, ephemeral=True)
            else:
                await interaction.response.send_message(content=f"It looks like you do not have any Kinks, consider adding some? *cracks whip*", ephemeral=True,)
        if res is None:
            await interaction.response.send_message(content=f"It looks like the {member.display_name if member is not None else 'User'} specified is not a *Lover* user.", ephemeral=True)
        if member is None:
            await interaction.response.send_message(content=f"It appears this {res.name if res is not None else 'User'} is no longer apart of the guild.", ephemeral=True)

    @love_kinks.command(name="remove", description="Remove a Kink.")
    @app_commands.autocomplete(kink=kinks_autocomplete)
    async def love_kink_remove(self, interaction: discord.Interaction, kink: str):
        lover = await LoverEntry.get_or_none(discord_id=interaction.user.id)
        if lover is not None:
            await lover.remove_kink(name=kink)
            await interaction.response.send_message(content=f"Aww no longer into `{kink}` anymore? If that changes just add it back.", ephemeral=True)
        else:
            await interaction.response.send_message(content=f"It looks like `{interaction.user.display_name}` is not a *lover* user.", ephemeral=True)

    # TODO Need to validate logic and test for errors.
    @love_kinks.command(name="info", description="Look up the description of a Kink.")
    @app_commands.autocomplete(kink=partners_kinks_autocomplete)
    async def love_kink_info(self, interaction: discord.Interaction, kink: str):
        assert interaction.guild
        # convert our kink str into parts we need
        # should come in as `name:discord_id`
        kink_info: list = kink.split(":")
        kink_owner_id: int = int(kink_info[1])
        kink_name: str = kink_info[0]

        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=kink_owner_id)
        if lover is None:
            return interaction.response.send_message(content=f"I was unable to find the owner of that kink..they appear to no longer be a *Lover*", ephemeral=True)
        member = interaction.guild.get_member(kink_owner_id)
        if member is None:
            return await interaction.response.send_message(content=f"Looks like {lover.name} is no longer apart of this server", ephemeral=True)

        if lover is not None:
            res = await lover.get_kink(name=kink_name)
            kink_description = res["description"]
            kink_embed = discord.Embed(title=f"**{lover.name}'s** Kink", color=member.color, timestamp=discord.utils.utcnow())
            kink_embed.set_thumbnail(url=None if member.avatar is None else member.avatar.url)
            kink_embed.add_field(name=f"**{kink_name}**", value=kink_description)

            await interaction.response.send_message(content=f"{kink.split(':')}", embed=kink_embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Love(bot))
