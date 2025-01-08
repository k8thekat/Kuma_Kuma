import discord
from typing import Any
import datetime

from utils.timetable import TimeTable
from cogs.love_cog_utils.db import LoverEntry

from discord.colour import Colour
from discord.enums import ButtonStyle
from discord import Member


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
        guild: discord.Guild | None = None,
        member: discord.Member | discord.User | None = None,
    ):
        self = cls(color=color, title=title, timestamp=timestamp)
        if member is None:
            member = interaction.user

        self.set_thumbnail(url=None if member.avatar == None else member.avatar.url)
        # We are generating an embed via DM's we need to pass in a guild object prior for partner list generator.
        if guild is None:
            assert interaction.guild
            guild = interaction.guild

        # Generates the preferences for the Lover User under a single Embed Field
        lover_attrs: list[str] = [
            "role",
            "position",
            "position_switching",
            "role_switching",
        ]
        lover_preferences: list = []
        for entry in lover_attrs:
            if entry == "role_switching" or entry == "position_switching":
                lover_preferences.append(
                    f"- **{entry.title().replace('_', ' ')}**: {bool(getattr(lover, entry))}"
                )
            elif entry == "role":
                lover_preferences.append(
                    f"- **{entry.title()}**: {lover.get_role.title()}"
                )
            elif entry == "position":
                lover_preferences.append(
                    f"- **{entry.title()}**: {lover.get_position.title()}"
                )
        self.add_field(name="**__Preferences__**", value="\n".join(lover_preferences))

        # Partner Embed Field Generator
        partner_results: list = await lover.list_partners()
        if not len(partner_results):  # or partners is not None:
            self.add_field(
                name="**__Partners__**", value="*Currently no Partners*", inline=False
            )
        else:
            members: list[str] = [f"- **{member.display_name}**" for member in (guild.get_member(int(x)) for x in partner_results) if member]

            self.add_field(
                name="**__Partners__**",
                value="\n".join(members),
                inline=False,
            )

        # Kinks Embed Field Generator
        kink_results: list = await lover.list_kinks()
        if not len(kink_results):  # or kinks is not None:
            self.add_field(name="**__Kinks__**", value="*Currently no Kinks*")
        else:
            display_kinks: list = [f"- **{entry['name']}**" for entry in kink_results]
            self.add_field(
                name="**__Kinks__**", value="\n".join(display_kinks), inline=False
            )
        tz = await lover.get_timezone()
        if tz is not None:
            self.add_field(name="**__Timezone__**", value=tz["timezone"], inline=False)
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
        lover_id: int
    ):

        self = cls(color=color, title=title, timestamp=timestamp)
        self.set_thumbnail(url=None if partner.avatar == None else partner.avatar.url)

        lover_partner: LoverEntry | None = await LoverEntry.get_or_none(discord_id=partner.id)
        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=lover_id)
        assert lover

        if lover_partner is not None:
            lover_attrs: list[str] = [
                "role",
                "position",
                "position_switching",
                "role_switching",
            ]
            lover_preferences: list = []
            for entry in lover_attrs:
                if entry == "role_switching" or entry == "position_switching":
                    lover_preferences.append(
                        f"- **{entry.title().replace('_', ' ')}**: {bool(getattr(lover_partner, entry))}"
                    )
                elif entry == "role":
                    lover_preferences.append(
                        f"- **{entry.title()}**: {lover_partner.get_role.title()}"
                    )
                elif entry == "position":
                    lover_preferences.append(
                        f"- **{entry.title()}**: {lover_partner.get_position.title()}"
                    )
            self.add_field(name="**__Preferences__**", value="\n".join(lover_preferences), inline=False)

            # Suggestion time field
            res = await lover.get_partner_suggestion_time(partner_id=partner.id)
            lover_cur_time_inTZ = await TimeTable().localize_suggestion_time(suggestion_time=int(res[0]), lover=lover)
            self.add_field(name="** Suggestion Time **", value=lover_cur_time_inTZ.strftime("%I:%M %p"))

            # Kinks Embed Field Generator
            kink_results: list = await lover_partner.list_kinks()
            if not len(kink_results):  # or kinks is not None:
                self.add_field(name="**__Kinks__**", value="*Currently no Kinks*", inline=False)
            else:
                display_kinks: list = [f"- **{entry['name']}**" for entry in kink_results]
                self.add_field(
                    name="**__Kinks__**", value="\n".join(display_kinks), inline=False
                )
        return self


class LoverApproveButton(discord.ui.Button):
    def __init__(
        self,
        *,
        style: ButtonStyle = ButtonStyle.green,
        label: str = "Approve",
        custom_id: str = "approve_button"
    ):
        self.view: LoverPartnerView
        super().__init__(style=style, label=label, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        # return await super().callback(interaction)
        # Both Lover entries were validated prior; but just in case someone removes themselves we need to validate they still exist in the DB.
        # We only care if both are not none; because if one person "removes" themselves the partnership should fail.
        lover: LoverEntry | None = await LoverEntry.get_or_none(discord_id=self.view.sender.id)
        partner: LoverEntry | None = await LoverEntry.get_or_none(discord_id=self.view.maybe_partner.id)

        if lover is None:
            await self.view.maybe_partner.send(content=f"It appears {self.view.sender.display_name} is no longer a *Lover* member...")
            return await self.view.orig_msg.delete()

        elif partner is None:
            await self.view.sender.send(content=f"It appears {self.view.maybe_partner.display_name} is no longer a *Lover* member...")
            return await self.view.orig_msg.delete()

        # Add each other as partners
        if lover is not None and partner is not None:
            # Create our time offset
            cur_time = datetime.datetime.now()

            await lover.add_partner(
                partner_id=partner.discord_id,
                role_switching=lover.role_switching,
                position_switching=lover.position_switching,
                s_time=(cur_time.hour * 60 + cur_time.minute))
            await partner.add_partner(
                partner_id=lover.discord_id,
                role_switching=partner.role_switching,
                position_switching=partner.position_switching,
                s_time=(cur_time.hour * 60 + cur_time.minute))

        embed: LoverEmbed = await LoverEmbed.create(
            color=self.view.sender.color,
            title=self.view.sender.display_name,
            timestamp=discord.utils.utcnow(),
            lover=lover,
            interaction=interaction,
            guild=self.view.guild,
            member=self.view.sender,
        )
        await self.view.orig_msg.edit(
            content=f"You have approved **{self.view.sender.display_name}** to be your partner.",
            view=None,
            embed=embed)
        await self.view.sender.send(
            content=f"**{self.view.maybe_partner.display_name}** has __Approved__ your request to be their partner.")
        # self.view.res = True


class LoverDenyButton(discord.ui.Button):
    def __init__(
        self,
        *,
        style: ButtonStyle = ButtonStyle.red,
        label: str = "Deny",
        custom_id: str = "deny_button"
    ):
        self.view: LoverPartnerView
        super().__init__(style=style, label=label, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        # return await super().callback(interaction)
        await self.view.orig_msg.edit(content=f"You have denied **{self.view.sender.display_name}** to be your partner.", view=None)
        await self.view.sender.send(content=f"**{self.view.maybe_partner.display_name}** has __Denied__ your request to be their partner.")


class LoverPartnerView(discord.ui.View):
    @classmethod
    async def request(
        cls,
        *,
        sender: discord.Member | discord.User,
        maybe_partner: discord.Member | discord.User,
        guild: discord.Guild
    ):
        self = cls(timeout=None)
        cls.sender: discord.Member | discord.User = sender
        cls.guild: discord.Guild = guild
        cls.maybe_partner: Member | discord.User = maybe_partner
        self.add_item(LoverApproveButton(custom_id=f"approve_button.{maybe_partner.id}"))
        self.add_item(LoverDenyButton(custom_id=f"deny_button.{maybe_partner.id}"))
        # cls.res: bool = False
        cls.orig_msg = await maybe_partner.send(
            content=f"You have been requested to be a partner of {sender.mention}.",
            view=self)
