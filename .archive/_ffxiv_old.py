from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Self, TypedDict, Union

import discord
from discord.ext import commands

from utils.cog import KumaCog as Cog

from .universalis_data._enums import DataCenterEnum, LocalizationEnum, WorldEnum
from .universalis_data.modules import FFXIVItem, FFXIVResource, FFXIVUser, GarlandAPIWrapper, UniversalisAPIWrapper

if TYPE_CHECKING:
    # from universalis_data._types import APIResponseAliases

    from kuma_kuma import Kuma_Kuma
    from utils.context import KumaContext as Context

    # from .universalis_data._types import FFXIVUserDBTyped, FFXIVWatchListDBTyped, UniversalisAPI_CurrentTyped
    # from universalis_data.modules import ModulesDataTableAlias

    # F = TypeVar("F", bound=ModulesDataTableAlias)
    # X = TypeVar("X", bound=APIResponseAliases)


class UniversalisMarketboardButton(discord.ui.Button):
    view: GarlandToolsItemView

    def __init__(
        self,
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        label: str = "Market Board",
        emoji: str | None = None,
    ) -> None:
        super().__init__(style=style, label=label, emoji=emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        # TODO - May need to edit the message early and remove all attachments before dispatching the new View + Embed.
        await self.view.get_marketboard(interaction=interaction)
        await interaction.response.defer()


class UniversalisHistoryButton(discord.ui.Button):
    def __init__(
        self,
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        label: str = "Sale History...",
        emoji: str | None = None,
    ) -> None:
        super().__init__(style=style, label=label, emoji=emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        # TODO - Edit the MarketBoard Embed to Show History Data too? Or generate a new Embed IDK yet.
        await interaction.response.send_message("Getting Sales History...")


class UniversalisWatchListButton(discord.ui.Button):
    def __init__(
        self,
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.red,
        label: str = "Add to Watch List",
        emoji: str | None = None,
    ) -> None:
        super().__init__(style=style, label=label, emoji=emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        # TODO - This should prompt a modal for min/max price thresholds to monitor.
        await interaction.response.send_message("Added Item to your Watchlist!")


class UniversalisMarketEmbed(discord.Embed):
    """Universalis Embed requires Market Board Information."""

    icons: list[discord.File]
    cog: FFXIV

    def __init__(
        self,
        info: discord.AppInfo,
        item: FFXIVItem,
        world_or_dc: DataCenterEnum | WorldEnum,
        cog: FFXIV,
        color: discord.Color = discord.Color.og_blurple(),
        localization: LocalizationEnum = LocalizationEnum.en,
    ) -> None:
        title: str = f"**__{getattr(item, f'{localization.value}_name', item.en_name)}__** | [{item.item_id}]"
        self.item: FFXIVItem = item
        self.icons = []
        self.cog = cog
        description: str = f"*{item.description}*"
        super().__init__(color=color, title=title, description=description, timestamp=discord.utils.utcnow())
        # Our Author Icon Setup.
        self.icons.append(cog.ffxiv_resources.get_universalis_icon())
        self.set_author(name="Universalis Marketboard Lookup", icon_url="attachment://uni-icon.png")
        self.add_field(
            name="Marketboard Information:",
            value=f"{'- World:' if isinstance(world_or_dc, WorldEnum) else '- Data Center'} =={world_or_dc.name}==\n-------------",
        )
        # TODO - Decide on which keys and values to display on the embed.
        # Iterate through the listings looking for the homeworld name, if it matches place that at the top under it's own section.
        #
        self.item.universalis_current.get("averagePrice")
        self.item.universalis_current.get("currentAveragePrice")
        self.item.universalis_current.get("listings")
        self.item.universalis_current.get("recentHistory")
        self.item.universalis_current.get("minPrice")
        self.item.universalis_current.get("maxPrice")
        self.add_field(name="", value="")

        # Embed Thumbnail attachment.
        self.icons.append(item.get_icon())
        self.set_thumbnail(url="attachment://item-icon.png")

        self.icons.append(self.cog.ffxiv_resources.item_banner)
        self.set_image(url="attachment://ffxiv-banner.png")
        print("FOOTER ICON", self.item.patch.name)
        self.set_footer(
            text=f"Universalis Marketboard integration made by {info.owner.name}",
            icon_url=f"attachment://{self.item.patch.name}.png",
        )

    def get_attachments(self) -> list[discord.File]:
        """Returns a list of icons/files used for this Instance of the embed.

        Returns
        -------
        :class:`list[discord.File]`
            A list of images to be used on our Embed..

        """
        return self.icons


class UniversalisMarketView(discord.ui.View):
    cog: FFXIV
    interacton_user: Union[discord.Member, discord.User]
    item: FFXIVItem
    history_button: UniversalisHistoryButton
    watch_list_button: UniversalisWatchListButton

    def __init__(self, cog: FFXIV, item: FFXIVItem, interaction_user: Union[discord.Member, discord.User]) -> None:
        self.cog = cog
        self.item = item
        self.interacton_user = interaction_user
        self.history_button = UniversalisHistoryButton()
        self.watch_list_button = UniversalisWatchListButton()
        super().__init__()
        self.add_item(item=self.history_button)
        self.add_item(item=self.watch_list_button)

    async def get_history(self, interaction: discord.Interaction) -> None:
        pass


class GarlandToolsItemMoreInfoButton(discord.ui.Button):
    view: GarlandToolsItemView

    def __init__(self, style: discord.ButtonStyle = discord.ButtonStyle.green, label: str = "More Info...") -> None:
        super().__init__(style=style, label=label)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user == self.view.interaction_user:
            # self.disabled = True
            await self.view.more_info_embed()
        await interaction.response.defer()


class GarlandToolsItemView(discord.ui.View):
    cog: FFXIV
    item_embed: GarlandToolsItemInfoEmbed
    interaction_user: Union[discord.Member, discord.User]
    mb_button: UniversalisMarketboardButton
    more_info: GarlandToolsItemMoreInfoButton
    original_message: discord.Message

    def __init__(
        self,
        cog: FFXIV,
        item_embed: GarlandToolsItemInfoEmbed,
        interaction_user: Union[discord.Member, discord.User],
        orig_message: discord.Message,
    ) -> None:
        self.cog = cog
        self.item_embed = item_embed
        self.interaction_user = interaction_user
        self.mb_button = UniversalisMarketboardButton()
        self.more_info = GarlandToolsItemMoreInfoButton()
        self.original_message = orig_message
        super().__init__()

        self.add_item(item=self.more_info)
        # Marketboard Button.
        # self.add_item(item=self.mb_button)

    async def more_info_embed(self) -> discord.Message:
        """Edits the original Embed MSG with the full Item Details and removes the "more info" button"""
        self.remove_item(item=self.more_info)
        # Update our Embed Fields and redispatch.
        embed: GarlandToolsItemInfoEmbed = self.item_embed.get_full_details()
        # TODO - See about a way to add the ffxiv-banner as an attachment and not lose the previous message attachments.
        return await self.original_message.edit(content=None, embed=embed, view=self)

    async def get_marketboard(self, interaction: discord.Interaction) -> None:
        if self.interaction_user == interaction.user:
            # Remove all buttons on our View
            self.clear_items()
            # Generate our Marketboard Embed
            embed: UniversalisMarketEmbed = await self.item_embed.get_market_info_dc(interaction=interaction)
            # Generate our Marketboard View
            view = UniversalisMarketView(cog=self.cog, item=self.item_embed.item, interaction_user=interaction.user)
            # We dispatch our new View and Embed while adding our new attachments.
            await self.original_message.edit(embed=embed, view=view, attachments=embed.get_attachments())
        return


class GarlandToolsItemInfoEmbed(discord.Embed):
    """This Embed is for Garland Tools API information related to an `FFXIVItem`"""

    icons: list[discord.File]
    cog: FFXIV
    info: discord.AppInfo
    item: FFXIVItem

    def __init__(
        self,
        info: discord.AppInfo,
        item: FFXIVItem,
        cog: FFXIV,
        color: discord.Color = discord.Color.og_blurple(),
        localization: LocalizationEnum = LocalizationEnum.en,
    ) -> None:
        title: str = f"**{getattr(item, f'{localization.value}_name', item.en_name)}** || [{item.item_id}]"
        self.info = info
        self.item = item
        self.item = item.get_garland_info()
        self.cog = cog
        self.icons = []
        # TODO - Possibly host the file online to prevent file IO issues?
        # self.icons.append(self.cog.ffxiv_emojitable.get_banner())
        description: str | None = f"*{item.description[:1020] + ' ...'}*" if item.description is not None else None

        super().__init__(color=color, title=title, description=description, timestamp=discord.utils.utcnow())
        # Our Author Icon Setup.
        self.icons.append(cog.ffxiv_resources.get_garlandtools_icon())
        self.set_author(name="Garland Data Lookup", icon_url="attachment://gt-icon.png")
        # Item Links to various websites.
        self.add_field(name="**__Links__**:", value=item.get_hyper_links(), inline=False)

        # Embed Thumbnail attachment.
        item_icon: discord.File = item.get_icon()
        self.icons.append(item_icon)
        # print("ICON ARGS", self.icons, self.item.item_id)
        self.set_thumbnail(url=f"attachment://{item_icon.filename}")

        # print("FOOTER IMAGE: ", self.item.patch.name, "-icon.png")
        self.icons.append(self.item.get_patch_icon())
        self.set_footer(text=f"Garland Tools integration made by {info.owner.name}", icon_url=f"attachment://{self.item.patch.name}.png")

    def get_attachments(self) -> list[discord.File]:
        """Returns a list of icons/files used for this Instance of the embed.

        Returns
        -------
        :class:`list[discord.File]`
            A list of images to be used on our Embed..

        """
        return self.icons

    def get_full_details(self) -> Self:
        """Retrieves more information regarding the FFXIV Item supplied and returns an updated Embed.

        Returns
        -------
        :class:`discord.Embed`
            The updated Self object.

        """
        # This should remove the original "Links" field.
        self.remove_field(index=0)

        # Our item information.
        stats: list[str] = []
        stats.append(f"- *Sell:* {self.item.sell_price} {self.cog.ffxiv_resources.to_inline_emoji(emoji='gil')}")
        stats.append(f"- *Buy:* {self.item.price} {self.cog.ffxiv_resources.to_inline_emoji(emoji='gil')}")
        stats.append(f"- *Stack Size:* {self.item.stackSize}")
        self.add_field(name="**__Stats__**:", value="\n".join(stats), inline=False)

        # Unique Information
        self.add_field(name="**__Dyeable__**:", value=self.item.dyecount)

        ventures = bool(len(self.item.ventures) if self.item.ventures is not None else 0)
        self.add_field(name="**__Ventures__**:", value=ventures, inline=False)
        drops: str | None = self.item.get_drops()
        if drops is not None:
            self.add_field(name="**__Drops__**:", value=drops, inline=False)
        vendors: str | None = self.item.get_vendor_information()
        if vendors is not None:
            self.add_field(name="**__Shops__ **:", value=vendors, inline=False)
        crafts: str | None = self.item.get_craft_information()
        if crafts is not None:
            self.add_field(name="**__Craftable by__**:", value=crafts, inline=False)

        fishing: str | None = self.item.get_fish_guide()
        if fishing is not None:
            self.add_field(name="**__Fish Guide__**:", value=fishing, inline=False)
        # Item Links to various websites.
        self.add_field(name="**__Links__**:", value=self.item.get_hyper_links(), inline=False)
        # TODO - fix the banner image
        # self.icons.append(self.cog.ffxiv_emojitable.get_banner())
        # self.set_image(url="attachment://ffxiv-banner.png")
        return self

    async def get_market_info_dc(self, interaction: discord.Interaction) -> UniversalisMarketEmbed:
        """Collect the Universalis Marketboard Information and updates our `FFXIVItem` attributes and our new Embed to display~

        Parameters
        ----------
        interaction: :class:`discord.Interaction`
            The Discord Interaction from the Button interaction.

        Returns
        -------
        :class:`UniversalisItemEmbed`
            A updated Embed with market information related to the item.

        """
        uni_user: FFXIVUser | None = await self.cog.add_or_get_ffxiv_user(user=interaction.user, guild=interaction.guild)
        if uni_user is None:
            self.cog.logger.warning(
                "Failed to find the Universalis User: %s | Guild: %s -- Defaulting to %s",
                interaction.user,
                interaction.guild,
                DataCenterEnum.Crystal,
            )
            world_or_dc: WorldEnum | DataCenterEnum = DataCenterEnum.Crystal
        else:
            world_or_dc = uni_user.home_world

        item: FFXIVItem = self.item.set_marketboard_current(
            data=await self.cog.get_universalis_current_mb_data(items=self.item, world_or_dc=world_or_dc),
        )
        return UniversalisMarketEmbed(info=self.info, item=item, cog=self.cog, world_or_dc=world_or_dc)


class GarlandToolsItemSelect(discord.ui.Select):
    """Related to `GarlandToolsItemSelectionView` to handle the options and return the results."""

    view: GarlandToolsItemSelectionView
    result: str
    is_done: bool = False

    def __init__(self, options: list[discord.SelectOption], placeholder: str) -> None:
        super().__init__(options=options, placeholder=placeholder)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user == self.view.interaction_user:
            self.result = self.values[0]
            self.is_done = True

        if await self.view.check_results(interaction=interaction) is True:
            return
        await interaction.response.defer()


class GarlandToolsItemSelectionView(discord.ui.View):
    """This is used to handle multiple Items having very similar names and isolating the item the user is searching for."""

    cog: FFXIV
    item_list: list[FFXIVItem]
    item_select: GarlandToolsItemSelect
    interaction_user: Union[discord.Member, discord.User]

    def __init__(
        self,
        cog: FFXIV,
        item_list: list[FFXIVItem],
        interaction_user: discord.User | discord.Member,
        localization: LocalizationEnum = LocalizationEnum.en,
    ) -> None:
        self.cog = cog
        self.interaction_user = interaction_user
        self.item_list = item_list

        super().__init__()
        choices: list[discord.SelectOption] = [
            discord.SelectOption(label=getattr(item, f"{localization.name}_name", "UNK"), value=str(item.item_id)) for item in item_list
        ]

        self.item_select = GarlandToolsItemSelect(placeholder="Possible Items...", options=choices)
        self.add_item(item=self.item_select)

    async def check_results(self, interaction: discord.Interaction) -> bool:
        if self.item_select.is_done:
            information: discord.AppInfo = await self.cog.bot.application_info()
            await interaction.response.send_message(
                embed=GarlandToolsItemInfoEmbed(
                    info=information,
                    item=self.get_item_from_list(item_id=self.item_select.result),
                    cog=self.cog,
                ),
                ephemeral=True,
            )
            return True
        return False

    def get_item_from_list(self, item_id: str) -> FFXIVItem:
        """Returns the FFXIV Item by the provided `item_id`, if it exists in our `self.item_list`."""
        for item in self.item_list:
            if item.item_id == item_id:
                return item
        self.cog.bot.logger.error("Failed to find the selected Item inside MarketBoardView.get_item_from_list(%s)", item_id)
        raise IndexError("Failed to find the selected Item: %s", item_id)


class FFXIV(Cog):
    """FFXIV Unversalis Cog for Discord."""

    ffxiv_resources: FFXIVResource
    garland_api: GarlandAPIWrapper
    universalis_api: UniversalisAPIWrapper

    def __init__(self, bot: Kuma_Kuma) -> None:
        self.universalis_api = UniversalisAPIWrapper(bot=bot)
        # My GarlandTools API Wrapper to handle return types.
        self.garland_api = GarlandAPIWrapper(cache_location=Path(__file__).parent.joinpath("universalis_data/garland_tools"))

        self.ffxiv_resources = FFXIVResource(bot=bot, garland_api=self.garland_api)
        super().__init__(bot=bot)

    async def cog_load(self) -> None:
        async with self.bot.pool.acquire() as conn:
            await conn.execute(FFXIVUSER_SETUP_SQL)
            await conn.execute(WATCH_LIST_SETUP_SQL)

        # self.bot.loop.call_later(5, self.get_items())

    async def add_or_get_ffxiv_user(
        self,
        user: discord.User | discord.Member,
        home_world: WorldEnum = WorldEnum.Balmung,
        guild: discord.Guild | None = None,
        localization: LocalizationEnum = LocalizationEnum.en,
    ) -> FFXIVUser | None:
        print("ADD OR GET DATA", user, home_world, guild, localization)
        async with self.bot.pool.acquire() as conn:
            # Try to find our user first..
            res: FFXIVUserDBTyped | None = await conn.fetchone(
                """SELECT * FROM ffxivuser WHERE discord_id = ? and guild_id = ?""",
                user.id,
                (guild.id if guild is not None else 0),
            )  # type: ignore - I know the dataset because of above.
            if res is not None:
                return FFXIVUser(data=res, db_pool=self.bot.pool)

            # No user...
            self.logger.info(
                "Adding Name: %s | ID: %s | Guild ID: %s | Home World: %s | Localization: %s to the Database.",
                user.name,
                user.id,
                (guild.id if guild is not None else guild),
                home_world.value,
                localization,
            )
            res: FFXIVUserDBTyped | None = await conn.fetchone(
                """INSERT INTO ffxivuser(discord_id, guild_id, home_world, loc) VALUES(?, ?, ?, ?) RETURNING *""",
                user.id,
                (guild.id if guild is not None else 0),
                home_world.value,
                localization.value,
            )  # type: ignore - I know the dataset because of above.

            if res is None:
                self.logger.error(
                    "We encountered an error inserting a Row into the database via Unversalis.add_or_get_user_datacenter(). | GuildID: %s | UserID: %s | Home World: %s",
                    (guild.id if guild is not None else 0),
                    user.id,
                    home_world.value,
                )
                raise sqlite3.DataError(
                    "We encountered an error inserting a Row into the database via Unversalis.add_or_get_user_datacenter().",
                )

            return FFXIVUser(data=res, db_pool=self.bot.pool) if res is not None else res

    @commands.command(help="", aliases=["fxtest"])
    @commands.is_owner()
    async def ffxiv_test_func(self, context: Context, item_id: str) -> None:
        pass

    # @commands.command(help="", aliases=["isearch", "xlitem"])
    # async def ffxiv_item_lookup(self, context: Context, *, item: str) -> discord.Message:
    #     universalis_user: FFXIVUser | None = await self.add_or_get_ffxiv_user(user=context.author, guild=context.guild)
    #     if universalis_user is None:
    #         return await context.send(content="Failed to Find User...")

    #     items: list[FFXIVItem] = self.convert_item_name_to_ids(item_name=item, limit_results=1)
    #     information: discord.AppInfo = await self.bot.application_info()
    #     # TODO - Validate the Selection View works and albe to select an Option, etc..
    #     # if we have multiple items; let's prompt a View with a select to find the specific item they are after.
    #     if len(items) > 1:
    #         return await context.send(
    #             view=GarlandToolsItemSelectionView(item_list=items, interaction_user=context.author, localization=universalis_user.loc, cog=self)
    #         )

    #     msg: discord.Message = await context.send(content=f"Looking up your Item: {item}", ephemeral=True)
    #     embed = GarlandToolsItemInfoEmbed(info=information, item=items[0], cog=self)
    #     view = GarlandToolsItemView(cog=self, item_embed=embed, interaction_user=context.author, orig_message=msg)
    #     return await msg.edit(content=None, embed=embed, view=view, attachments=embed.get_attachments())

    # @commands.command(help="", aliases=["pc", "pricecheck", "price"])
    # @commands.is_owner()
    # async def ffxiv_price_check(self, context: Context, *, item: str) -> discord.Message:
    #     item_list: list[FFXIVItem] | list[str] = [item] if item.isnumeric() else self.convert_item_name_to_ids(item_name=item)

    #     user_dc: FFXIVUser | None = await self.add_or_get_ffxiv_user(user=context.author, guild=context.guild)
    #     data_center: DataCenterEnum = DataCenterEnum.Crystal if user_dc is None else DataCenterEnum(value=user_dc.datacenter_id)
    #     if data_center.value == 0:
    #         try:
    #             res: UniversalisAPI_CurrentTyped = await self.get_universalis_current_mb_data(items=item_list)
    #         except Exception:
    #             temp: list[str] = [e.item_id if isinstance(e, FFXIVItem) else e for e in item_list]
    #             return await context.send(
    #                 content=f"Oh no.. {self.emoji_table.to_inline_emoji(emoji=self.emoji_table.kuma_head_clench)} \nWe failed to find the items: {','.join(temp)}"
    #             )
    #     else:
    #         res: UniversalisAPI_CurrentTyped = await self.get_universalis_current_mb_data(items=item, world_or_dc=data_center)

    #     return await context.send(
    #         content=f"Checking marketboard pricing...\nItem Name Searched: {item}\nDataCenter: {data_center}\nItem IDs: {' | '.join([e.item_id if isinstance(e, FFXIVItem) else e for e in item_list])}",
    #         ephemeral=True,
    #     )

    @commands.command()
    @commands.is_owner()
    async def ffxiv_get_price(self, context: Context, item: str) -> None:
        await context.send(content=context.message.content)

    @commands.command(help="Parse Allagon Tools Export.csv", aliases=["imarket", "atoolsmarket", "invmarket"])
    @commands.is_owner()
    async def inventory_price_check(self, context: Context) -> None:
        pass

    @commands.Cog.listener(name="on_message")
    async def on_message_listener(self, message: discord.Message) -> None:
        pass


async def setup(bot: Kuma_Kuma) -> None:
    await bot.add_cog(FFXIV(bot=bot))
