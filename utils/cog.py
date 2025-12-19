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

import datetime
import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, Optional, Union

import discord
from discord.ext import commands
from lemminflect import getInflection  # pyright: ignore[reportMissingTypeStubs, reportUnknownVariableType]

if TYPE_CHECKING:
    from logging import Logger

    from kuma_kuma import Kuma_Kuma

    from ._types import Metrics, Uptime

__all__ = ("FFXIVResources", "KumaCog", "KumaEmojiTable", "KumaResources")

LOGGER = logging.getLogger()


class UnicodeTable:
    # Unicode Library -> https://www.vertex42.com/ExcelTips/unicode-symbols.html
    middle_dot: str = "\U000030fb"
    em_dash: str = "\U0000fe31"
    double_vertical: str = "\U00002016"  # DOUBLE VERTICAL LINE - ‖ — ‖ — http://www.fileformat.info/info/unicode/char/2016
    right_arrow: str = "\U000021e2"  # RIGHTWARDS DASHED ARROW - ⇢ — ⇢ — http://www.fileformat.info/info/unicode/char/21e2
    colon: str = "\U00002236"  # RATIO - ∶ — ∶ — http://www.fileformat.info/info/unicode/char/2236  # noqa: RUF003
    right_triangle_arrow: str = "\U000022b3"  # CONTAINS AS NORMAL SUBGROUP - ⊳ — ⊳ — http://www.fileformat.info/info/unicode/char/22b3


class KumaEmojiTable:
    """Usage - <:_:emoji_id>."""

    kuma_rawr = "<:kuma_rawr:1337130200009281559>"
    kuma_crying = "<:kuma_crying:1337130201527750757>"
    kuma_tear = "<:kuma_tear:1337130203096420372>"
    kuma_happy = "<:kuma_happy:1337130204547780648>"
    kuma_star_eye = "<:kuma_star_eye:1337130207462817946>"
    kuma_shy = "<:kuma_shy:1337130209446592513>"
    kuma_peak = "<:kuma_peak:1337130211128377406>"
    kuma_chuckle = "<:kuma_chuckle:1337130212890247199>"
    kuma_heart = "<:kuma_heart:1337130214492475475>"
    kuma_sad = "<:kuma_sad:1337130217071841441>"
    kuma_shrug = "<:kuma_shrug:1337130218963468448>"
    kuma_hmm = "<:kuma_hmm:1337130227788415087>"
    kuma_tea = "<:kuma_tea:1337130230598602792>"
    kuma_shock = "<:kuma_shock:1337130232993288244>"
    kuma_bleh = "<:kuma_bleh:1337130235472117841>"
    kuma_uwu = "<:kuma_uwu:1337130237225603092>"
    kuma_wow = "<:kuma_wow:1337130238878154893>"
    kuma_pout = "<:kuma_pout:1337133347163345019>"
    kuma_head_clench = "<:kuma_head_clench:1337133349612814398>"

    @staticmethod
    def to_inline_emoji(emoji: Union[str, int]) -> str | None:
        """Converts the emoji provided into a Discord in line str type emoji for usage.

        Emojis
        -------
        - kuma_rawr
        - kuma_crying
        - kuma_tear
        - kuma_happy
        - kuma_star_eye
        - kuma_shy
        - kuma_peak
        - kuma_chuckle
        - kuma_heart
        - kuma_sad
        - kuma_shrug
        - kuma_hmm
        - kuma_tea
        - kuma_shock
        - kuma_bleh
        - kuma_uwu
        - kuma_wow
        - kuma_pout
        - kuma_head_clench


        Parameters
        ----------
        emoji: Union[:class:`str`, :class:`int`]
            Either the emoji name or the ID to lookup..

        Returns
        -------
        :class:`str`
            Discord in line Emoji string.

        Raises
        ------
        LookupError
            If the emoji provided does not exist.

        """
        if isinstance(emoji, str):
            emoji = emoji.lower()

        for key, value in KumaEmojiTable.__dict__.items():
            if isinstance(emoji, str) and emoji == key:
                return f"<:{key}:{value}>"
            if isinstance(emoji, int) and emoji == value:
                return f"<:{key}:{value}>"
        msg = "The Emoji provided does not exist. | %s"
        raise LookupError(msg, emoji)

    @classmethod
    def get_emoji(cls, name: str) -> Optional[str]:
        """Get the Application Emoji string by name.

        Parameters
        ----------
        name: :class:`str`
            The name of the emoji to lookup.

        Returns
        -------
        :class:`str`
            The Discord in line emoji string, if able to find the emoji.

        """
        for key, value in cls.__dict__.items():
            if name.lower() == key:
                return value
        return None


class KumaResources:
    """Attributes related to useful material stored in the `./resources/kuma_kuma_emojis` folder."""

    src: Path = Path(__file__).parent.parent.joinpath("resources")
    banner: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_banner.jpg")
    sticker: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_sticker.jpg")
    sticker2: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_sticker2.jpg")
    smug_large: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_smug_large.jpg")


class KumaCog(commands.Cog):
    """Our custom Cog class for Discord.py.

    Attributes
    ----------
    bot: Kuma_Kuma
        The discord bot class.
    message_timeout: :class:`int`
        The amount of time in seconds before a message is auto-deleted.
    owner_guild: :class:`discord.Object`
        The owner of the bots guild. aka Neko Neko Cafe`
    emoji_table: :class:`KumaEmojiTable`
        A simple str name attribute to int id value representation for ease of use.
    resources: :class:`KumaResources`
        A base class with pathlib Path attributes for accessing resources.


    Properties
    ----------
    unicode: :class:`UnicodeTable`
        ...
    metrics: :class:`Metrics`
        A dictionary containing useful information regarding metrics, can be expanded.

    """

    bot: Kuma_Kuma
    message_timeout: int
    owner_guild = discord.Object(id=602285328320954378)
    emoji_table: KumaEmojiTable = KumaEmojiTable()
    resources: KumaResources = KumaResources()
    strftime_fmt = "%d/%m | %H:%M(%Z)"
    _timestamp_styles = Literal["F", "f", "D", "d", "T", "t", "R"]

    _metrics: dict[str, Metrics]
    _unicode: UnicodeTable = UnicodeTable()

    @property
    def unicode(self) -> UnicodeTable:
        return self._unicode

    @property
    def metrics(self) -> dict[str, Metrics]:
        """Stores Cog relevant metrics information.

        By default stores the current Cog's uptime.

        Returns
        -------
        :class:`Metrics`
            _description_.

        """
        return self._metrics

    @metrics.setter
    def metrics(self, value: dict[str, Metrics]) -> None:
        self._metrics: dict[str, Metrics] = value

    def __init__(self, bot: Kuma_Kuma) -> None:
        """Builds the base :class:`KumaCog` class object.

        Set's self.bot, self.message_timeout.
        - Set's the Cogs start time using :class:`time.time()`
        """
        self.bot = bot
        self.message_timeout = bot.message_timeout
        self.metrics = {__class__.__name__: {"uptime": {"start": datetime.datetime.now(tz=datetime.UTC)}}}

    async def get_guild(self) -> discord.Guild | None:
        """Returns the owners guild. In this instance `Neko Neko Cafe`.

        Returns
        -------
        :class:`discord.Guild | None`
            A discord Guild object, otherwise None.

        """
        return self.bot.get_guild(self.owner_guild.id)

    def to_discord_timestamp(self, time: Optional[datetime.datetime] = None, *, style: _timestamp_styles = "F") -> str:
        """Converts a Date Time value into each Discord users local timezone for display.

        .. note::
            Uses discord built in timestamp converter for relative time for user.
            - https://discord.com/developers/docs/reference#message-formatting-timestamp-styles


        Parameters
        ----------
        time: :class:`datetime`
            The datetime object to timestamp.
        style: :class:`_timestamp_styles`, optional
            The Format to display the text in, by default "F".

        Returns
        -------
        :class:`str`
            The markdown text to use in Discord content to display the timestmap.

        """
        if time is None:
            time = datetime.datetime.now(tz=datetime.UTC)
        return f"<t:{int(time.timestamp())}:{style}>"

    def string_inflection(self, word: str, tag: str = "VBD") -> str:
        """Uses Lemminflection to change the inflection of the passed in word depending on the tag provided.

        - By default it uses `Verb, past tense` aka `VBD`.

        Parameters
        ----------
        word: :class:`str`
            The str to inflect upon.
        tag: :class:`str`, optional
            The Penn TreeBank Tag, by default "VBD".
            - https://www.ling.upenn.edu/courses/Fall_2003/ling001/penn_treebank_pos.html

        Returns
        -------
        :class:`str`
            Returns the Inflected string if possible, otherwise the original word.

        """
        results = getInflection(word, tag)  # pyright: ignore[reportUnknownVariableType]
        if isinstance(results, tuple) and len(results) >= 1:  # pyright: ignore[reportUnknownArgumentType]
            return results[0]  # pyright: ignore[reportUnknownVariableType]
        return word


class MooglesIntuitionEmojis:
    """A collection of Application Emojis for Moogles Intuition Module."""

    # Marketboard/Etc Icons/Emojis
    aethernet_icon = "<:aethernet_icon:1441468511481495654>"
    "Aethernet Miniature - Application Emoji"
    alc_icon = "<:alc_icon:1441485708329222174>"
    "Alchemist Job - Application Emoji"
    another_world_icon = "<:another_world_icon:1441637597133672458>"
    "Traveler Icon next to a players name - Application Emoji"
    arm_icon = "<:arm_icon:1441485709335728238>"
    "Armorsmith Job - Application Emoji"
    arricon = "<:arricon:1441468515608690719>"
    "A Real Reborn Expansion - Application Emoji"
    bag_with_exclamation = "<:bag_with_exclamation:1442003581262762157>"
    "A Satchel similar to the inventory icon with an exclamation mark - Application Emoji"
    bot_icon = "<:bot_icon:1441485719469166734>"
    "Botanist Job - Application Emoji"
    botanist_icon_mini = "<:botanist_icon_mini:1441607824974155858>"
    "Botanist Job Icon mini - Application Emoji"
    bsm_icon = "<:bsm_icon:1441485711114113204>"
    "Blacksmith Job - Application Emoji"
    calendar_star_icon = "<:calendar_star_icon:1441637605702893722>"
    "Calendar with a Star in the middle - Application Emoji"
    chest = "<:chest:1441468512743718954>"
    "Opened Chest with Gold Coins - Application Emoji"
    chest2 = "<:chest2:1441468516254355639>"
    "Closed Chest similar to Treasure Dungeon Icon - Application Emoji"
    clock_icon = "<:clock_icon:1441607831382917251>"
    "Clock Icon with grey background - Application Emoji"
    commedations = "<:commedations:1441637600099176519>"
    "Player commendation from the UI - Application Emoji"
    crafting_log_icon = "<:crafting_log_icon:1441637601218924704>"
    "Crafting log icon from in game UI - Application Emoji"
    crp_icon = "<:crp_icon:1441485712770994369>"
    "Carpenter Job - Application Emoji"
    cul_icon = "<:cul_icon:1441485714071228426>"
    "Culinarian Job - Application Emoji"
    custom_deliveries = "<:custom_deliveries:1441637604444602450>"
    "Custom deliveries icon - Application Emoji"
    dl_icon = "<:dl_icon:1441485697977548943>"
    "Download Icon - Application Emoji"
    doh_icon = "<:doh_icon:1441467943199183093>"
    "Disciple of Hand - Application Emoji"
    dol_icon = "<:dol_icon:1441467944755527700>"
    "Disciple of Land - Application Emoji"
    dticon = "<:dticon:1441468517739135076>"
    "Dawntrail Expansion - Application Emoji"
    dungeon_icon = "<:dungeon_icon:1441485706592518388>"
    "Generic Dungeon Icon - Application Emoji"
    error_icon = "<:error_icon:1441607828552028390>"
    "Caution traingle with exclamation mark - Application Emoji"
    ewicon = "<:ewicon:1441468519202951208>"
    "Endwalker Expansion - Application Emoji"
    fate_icon = "<:fate_icon:1441637603542827028>"
    "FATE Icon from in game UI - Application Emoji"
    fisher_icon = "<:fisher_icon:1442010164281737326>"
    "Fisher job icon grey with drop shadow - Application Emoji"
    fisher_icon_mini = "<:fisher_icon_mini:1441607823950872718>"
    "Fisher Job Icon mini - Application Emoji"
    fishing_log_icon = "<:fishing_log_icon:1442268119937712261>"
    "Fishing Log Icon from in game UI - Application Emoji"
    flame_icon = "<:flame_icon:1441485904580444220>"
    "The Immortal Flames Reputation - Application Emoji"
    fsh_icon = "<:fsh_icon:1441467900450836550>"
    "Fisher Job - Application Emoji"
    gatheringicon = "<:gatheringicon:1441468520461369425>"
    "Miner and Botanisty icons in an X pattern - Application Emoji"
    gathering_log_icon = "<:gathering_log_icon:1442268118230765628>"
    "The Gathering Log icon from in game UI - Application Emoji"
    gear_icon = "<:gear_icon:1441607830493987009>"
    "Gear/Settings style Icon with grey background  - Application Emoji"
    gil = "<:gil:1441468514052608000>"
    "Gil Currency - Application Emoji"
    glittery_gil_satchel = "<:glittery_gil_satchel:1441607832989470821>"
    "Gil Satchel with Sparkles - Application Emoji"
    gsm_icon = "<:gsm_icon:1441485715358617693>"
    "Goldsmith Job - Application Emoji"
    hwicon = "<:hwicon:1441468521384120421>"
    "Heavensward Expansion - Application Emoji"
    inv_icon = "<:inv_icon:1441485699965653183>"
    "Satchel with an ! on it - Application Emoji"
    inv_icon2 = "<:inv_icon2:1441485701182001162>"
    "Open Bag with Arrow pointing into Satchel - Application Emoji"
    inventory_icon = "<:inventory_icon:1441637602334736456>"
    "Inventory Icon from in game UI - Application Emoji"
    left_arrow_icon = "<:left_arrow_icon:1441607825871863838>"
    "Left Arrow Icon - Application Emoji"
    loop_icon = "<:loop_icon:1441607829671645225>"
    "Loop Swirl Icon - Application Emoji"
    ltw_icon = "<:ltw_icon:1441485716118048830>"
    "Leatherworker Job - Application Emoji"
    mbhistoryicon = "<:mbhistoryicon:1441468522608984194>"
    "Marketboard History - Application Emoji"
    mbicon = "<:mbicon:1441468523527540786>"
    "Marketboard - Application Emoji"
    mb_2_icon = "<:mb_2_icon:1442003582638489790>"
    "Satchel being placed into the Marketboard sign - Application Emoji"
    mbwatchlisticon = "<:mbwatchlisticon:1441468524592632030>"
    "Marketboard Watch List - Application Emoji"
    mgp = "<:mgp:1441485730315636776>"
    "Manderville Gold Saucer Points - Application Emoji"
    mnr_icon = "<:mnr_icon:1441485720454697093>"
    "Miner Job - Application Emoji"
    moogle1 = "<:moogle1:1441468526350176326>"
    "Running on all 4 Moogle legs in - Application Emoji"
    moogle2 = "<:moogle2:1441468527843213432>"
    "Running on all 4 Moogle legs out - Application Emoji"
    moogle3 = "<:moogle3:1441468529160491048>"
    "Upright Moogle with large antenna - Application Emoji"
    moogle_selfie_icon = "<:moogle_selfie_icon:1441637607107858522>"
    "Moogle face on colored background - Application Emoji"
    moogle_selfie_grey_icon = "<:moogle_selfie_grey_icon:1441637608642973768>"
    "Moogle face on grey background - Application Emoji"
    miner_icon_mini = "<:miner_icon_mini:1441607822575145033>"
    "Miner Job Icon mini - Application Emoji"
    no_action = "<:no_action:1441607821190889643>"
    "Red X Icon - Application Emoji"
    poi_icon = "<:poi_icon:1441485705598603365>"
    "Point of Interest - Application Emoji"
    right_arrow_icon = "<:right_arrow_icon:1441607827071172649>"
    "Right Arrow Icon - Application Emoji"
    repair_icon = "<:repair_icon:1441637595938295838>"
    "Repair wrench on a yellow hexagon background Icon - Application Emoji"
    sbicon = "<:sbicon:1441468530288492704>"
    "Stormblood Expansion - Application Emoji"
    shbicon = "<:shbicon:1441468532389838978>"
    "Shadowbringers Expansion - Application Emoji"
    treasurehunt = "<:treasurehunt:1441467786445586472>"
    "Treasure Dungeon Icon - Application Emoji"
    tales_of_adventure = "<:tales_of_adventure:1442312168534966322>"
    "A FFXIV Colored book, "
    upload_icon = "<:upload_icon:1441485698963210392>"
    "Upload Icon - Application Emoji"
    vendor_icon = "<:vendor_icon:1441832190819303505>"
    "Vendor Icon from the map UI - Application Emoji"
    world_visit = "<:world_visit:1441637598979297461>"
    "Visit another World in game Icon - Application Emoji"
    wvr_icon = "<:wvr_icon:1441485717707685938>"
    "Weaver Job - Application Emoji."

    @classmethod
    def get_emoji(cls, name: str) -> Optional[str]:
        """Get the Application Emoji string by name.

        Parameters
        ----------
        name: :class:`str`
            The name of the emoji to lookup.

        Returns
        -------
        :class:`str`
            The Discord in line emoji string, if able to find the emoji.

        """
        for key, value in cls.__dict__.items():
            if name.lower() == key:
                return value
        return None


class FFXIVResources:
    """FFXIV Resources such as Icons, Banners, Emojis, Items, Locations and much more for easier lookup.

    - All the Emoji's are stored on Neko Neko Cafe` Discord Guild.

    Attributes
    ----------
    resource_path: :class:`Path`
        Parent Path directory to `resources/moogle_intuition` directory.
    emojis: :class:`MooglesIntuitionEmojis`
        A class with mapped attributes related to inline discord Application Emojis.

    """

    resource_path: Path = Path(__file__).parent.parent.joinpath("resources/moogle_intuition")
    emojis: MooglesIntuitionEmojis = MooglesIntuitionEmojis()
    patch_mapping: ClassVar[dict[int, str]] = {
        1: "patch_icon/arr-icon.png",
        2: "patch_icon/hw-icon.png",
        3: "patch_icon/sb-icon.png",
        4: "patch_icon/shb-icon.png",
        5: "patch_icon/ew-icon.png",
        6: "patch_icon/dt-icon.png",
    }

    @classmethod
    def get_patch_icon(cls, patch_id: float, filename: str = "patch-icon.png") -> discord.File:
        """get_patch_icon _summary_.

        .. note::
            - 1: "patch_icon/arr-icon.png",
            - 2: "patch_icon/hw-icon.png",
            - 3: "patch_icon/sb-icon.png",
            - 4: "patch_icon/shb-icon.png",
            - 5: "patch_icon/ew-icon.png",
            - 6: "patch_icon/dt-icon.png",

        Parameters
        ----------
        patch_id: :class:`float`
            The ICON ID in reference to `patch_mapping`.
        filename: :class:`str`
            The filename parameter for the `discord.File` object.

        Returns
        -------
        :class:`discord.File`
            _description_.

        """
        file_path = cls.patch_mapping.get(int(patch_id), cls.patch_mapping[1])
        return discord.File(cls.resource_path.joinpath(file_path), filename=filename)

    @classmethod
    def get_banner(cls) -> discord.File:
        """Get the Final Fantasy 14 Trail Banner from local files.

        - Filename: "ffxiv-banner.png"

        Returns
        -------
        :class:`discord.File`
            A discord.File object of the local banner file.

        """
        return discord.File(fp=cls.resource_path.joinpath("ffxiv-trail-banner.png"), filename="ffxiv-banner.png")

    @classmethod
    def get_universalis_icon(cls, filename: str = "universalis-icon.png") -> discord.File:
        """Get the Universalis Icon from local files.

        Parameters
        ----------
        filename: :class:`str`
            The filename parameter for the `discord.File` object.

        Returns
        -------
        :class:`discord.File`
            A discord.File object of the Universalis icon file.

        """
        return discord.File(fp=cls.resource_path.joinpath("universalis/universalis-icon.png"), filename=filename)

    @classmethod
    def get_garlandtools_icon(cls) -> discord.File:
        """Get the GarlandTools Icon from local files.

        - Filename: "garlandtools-icon.png"

        Returns
        -------
        :class:`discord.File`
            A discord.File object of the GarlandTools icon file.

        """
        return discord.File(fp=cls.resource_path.joinpath("garlandtools-icon.png"), filename="garlandtools-icon.png")

    @classmethod
    def get_aethernet_icon(cls) -> discord.File:
        """Get the Aethernet Icon from local files.

        - Filename: "aethernet-icon.png"

        Returns
        -------
        :class:`discord.File`
            A discord.File object of the Aethernet icon file.

        """
        return discord.File(fp=cls.resource_path.joinpath("aethernet-icon.png"), filename="aethernet-icon.png")

    @classmethod
    def get_moogle_icon(cls, filename: str = "moogle-icon.png") -> discord.File:
        """Get the Moogle Intution Icon from local files.

        Parameters
        ----------
        filename: :class:`str`
            The filename parameter for the `discord.File` object.

        Returns
        -------
        :class:`discord.File`
            A discord.File object of the Moogle Intution icon file.

        """
        return discord.File(fp=cls.resource_path.joinpath("moogle-emoji-1.png"), filename=filename)

    # @classmethod
    # def make_file(cls, path: Path) -> discord.File:
    #     if path.exists():
    #         return discord.File()
