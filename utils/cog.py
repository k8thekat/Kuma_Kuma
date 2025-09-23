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

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, Union

import discord
from discord.ext import commands
from lemminflect import getInflection  # pyright: ignore[reportMissingTypeStubs, reportUnknownVariableType]

if TYPE_CHECKING:
    from datetime import datetime
    from logging import Logger

    from kuma_kuma import Kuma_Kuma

__all__ = ("FFXIVResources", "KumaCog", "KumaEmojiTable", "KumaResources")


class KumaEmojiTable:
    """Usage - <:_:emoji_id>."""

    kuma_rawr: ClassVar[int] = 1337130200009281559
    kuma_crying: ClassVar[int] = 1337130201527750757
    kuma_tear: ClassVar[int] = 1337130203096420372
    kuma_happy: ClassVar[int] = 1337130204547780648
    kuma_star_eye: ClassVar[int] = 1337130207462817946
    kuma_shy: ClassVar[int] = 1337130209446592513
    kuma_peak: ClassVar[int] = 1337130211128377406
    kuma_chuckle: ClassVar[int] = 1337130212890247199
    kuma_heart: ClassVar[int] = 1337130214492475475
    kuma_sad: ClassVar[int] = 1337130217071841441
    kuma_shrug: ClassVar[int] = 1337130218963468448
    kuma_hmm: ClassVar[int] = 1337130227788415087
    kuma_tea: ClassVar[int] = 1337130230598602792
    kuma_shock: ClassVar[int] = 1337130232993288244
    kuma_bleh: ClassVar[int] = 1337130235472117841
    kuma_uwu: ClassVar[int] = 1337130237225603092
    kuma_wow: ClassVar[int] = 1337130238878154893
    kuma_pout: ClassVar[int] = 1337133347163345019
    kuma_head_clench: ClassVar[int] = 1337133349612814398

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


class KumaResources:
    """Attributes related to useful material stored in the `./resources` folder."""

    src: Path = Path(__file__).parent.parent.joinpath("resources")
    banner: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_banner.jpg")
    sticker: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_sticker.jpg")
    sticker2: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_sticker2.jpg")
    smug_large: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_smug_large.jpg")


class FFXIVResources:
    """FFXIV Resources such as Icons, Banners, Emojis, Items, Locations and much more for easier lookup.

    - All the Emoji's are stored on Neko Neko Cafe` Discord Guild.

    Attributes
    ----------
    resource_path: :class:`Path`
        Parent Path directory to `resources/moogle_intuition` directory.

    """

    resource_path: Path = Path(__file__).parent.parent.joinpath("resources/moogle_intuition")

    patch_mapping: ClassVar[dict[int, discord.File]] = {
        1: discord.File(resource_path.joinpath("patch_icon/arr-icon.png"), filename="patch-icon.png"),
        2: discord.File(resource_path.joinpath("patch_icon/hw-icon.png"), filename="patch-icon.png"),
        3: discord.File(resource_path.joinpath("patch_icon/sb-icon.png"), filename="patch-icon.png"),
        4: discord.File(resource_path.joinpath("patch_icon/shb-icon.png"), filename="patch-icon.png"),
        5: discord.File(resource_path.joinpath("patch_icon/ew-icon.png"), filename="patch-icon.png"),
        6: discord.File(resource_path.joinpath("patch_icon/dt-icon.png"), filename="patch-icon.png"),
    }

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
    def get_universalis_icon(cls) -> discord.File:
        """Get the Universalis Icon from local files.

        - Filename: "universalis-icon.png"

        Returns
        -------
        :class:`discord.File`
            A discord.File object of the Universalis icon file.

        """
        return discord.File(fp=cls.resource_path.joinpath("universalis/universalis-icon.png"), filename="universalis-icon.png")

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

    @classmethod
    def get_patch_icon(cls, patch_id: float) -> discord.File:
        return cls.patch_mapping.get(
            int(patch_id),
            cls.patch_mapping[1],
        )

    @property
    def gil_emoji(self) -> str:
        return f"<:gil:{self.gil}>"

    # Misc Emojis
    moogleemoji1: int = 1360791416007295097
    moogleemoji2: int = 1360791377679745107
    # moogle3: int = 23
    # moogle4: int = 24
    aetherneticon: int = 1360791343189983325
    gil: int = 1359719462550507680

    # Marketboard/Etc Icons/Emojis
    mbicon: int = 1360791262910873840
    mbhistoryicon: int = 1360791284695961731
    mbwatchlisticon: int = 1360791304841334815


class KumaCog(commands.Cog):
    """Our custom Cog class for Discord.py.

    Attributes
    ----------
    bot: Kuma_Kuma
        The discord bot class.
    logger: :class:`logging.Logger`
        The root logger for the discord bot.
    message_timeout: :class:`int`
        The amount of time in seconds before a message is auto-deleted.
    owner_guild: :class:`discord.Object`
        The owner of the bots guild. aka Neko Neko Cafe`
    emoji_table: :class:`KumaEmojiTable`
        A simple str name attribute to int id value representation for ease of use.
    resources: :class:`KumaResources`
        A base class with pathlib Path attributes for accessing resources.

    """

    bot: Kuma_Kuma
    logger: Logger
    message_timeout: int
    owner_guild = discord.Object(id=602285328320954378)
    emoji_table: KumaEmojiTable = KumaEmojiTable()
    resources: KumaResources = KumaResources()
    strftime_fmt = "%d/%m | %H:%M(%Z)"
    _timestamp_styles = Literal["F", "f", "D", "d", "T", "t", "R"]

    # Unicode Library -> https://www.vertex42.com/ExcelTips/unicode-symbols.html
    middle_dot: str = "\U000030fb"
    em_dash: str = "\U0000fe31"
    double_vertical: str = "\U00002016"  # DOUBLE VERTICAL LINE - ‖ — ‖ — http://www.fileformat.info/info/unicode/char/2016
    right_arrow: str = "\U000021e2"  # RIGHTWARDS DASHED ARROW - ⇢ — ⇢ — http://www.fileformat.info/info/unicode/char/21e2
    colon: str = "\U00002236"  # RATIO - ∶ — ∶ — http://www.fileformat.info/info/unicode/char/2236  # noqa: RUF003
    right_triangle_arrow: str = "\U000022b3"  # CONTAINS AS NORMAL SUBGROUP - ⊳ — ⊳ — http://www.fileformat.info/info/unicode/char/22b3

    def __init__(self, bot: Kuma_Kuma) -> None:
        """Make the Cog class."""
        self.bot = bot
        self.logger = bot.logger
        # self.logger.name = f"{self.__class__.__name__}"
        self.message_timeout = bot.message_timeout

    async def get_guild(self) -> discord.Guild | None:
        """Returns the owners guild. In this instance `Neko Neko Cafe`.

        Returns
        -------
        :class:`discord.Guild | None`
            A discord Guild object, otherwise None.

        """
        return self.bot.get_guild(self.owner_guild.id)

    def to_discord_timestamp(self, time: datetime, style: _timestamp_styles = "F") -> str:
        """Converts a Date Time value into each Discord users local timezone for display.

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
        # https://sesh.fyi/timestamp/

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
