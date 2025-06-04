from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, TypeVar, Union

import discord
from discord.ext import commands
from lemminflect import getInflection

if TYPE_CHECKING:
    from datetime import datetime
    from logging import Logger

    from kuma_kuma import Kuma_Kuma

__all__ = ("KumaCog", "KumaEmojiTable", "KumaResources")


class KumaEmojiTable:
    """
    Usage - <:_:emoji_id>

    """

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
        """
        Converts the emoji provided into a Discord in line str type emoji for usage.


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
        -----------
        emoji: Union[:class:`str`, :class:`int`]
            Either the emoji name or the ID to lookup..

        Returns
        --------
        :class:`str`
            Discord in line Emoji string.

        Raises
        -------
        :class:`LookupError`
            If the emoji provided does not exist.
        """
        if isinstance(emoji, str):
            emoji = emoji.lower()

        for key, value in KumaEmojiTable.__dict__.items():
            if isinstance(emoji, str) and emoji == key:
                return f"<:{key}:{value}>"
            if isinstance(emoji, int) and emoji == value:
                return f"<:{key}:{value}>"

        raise LookupError("The Emoji provided does not exist. | %s", emoji)


class KumaResources:
    """
    Attributes related to useful material stored in the `./resources` folder.
    """

    src: Path = Path(__file__).parent.joinpath("resources")
    banner: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_banner.jpg")
    sticker: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_sticker.jpg")
    sticker2: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_sticker2.jpg")
    smug_large: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_smug_large.jpg")


class KumaCog(commands.Cog):
    """
    Our custom Cog class for Discord.py.


    Attributes
    -----------
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
    _timestamp_styles = Literal["F", "f", "D", "d", "T", "t", "R"]

    def __init__(self, bot: Kuma_Kuma) -> None:
        self.bot = bot
        self.logger = bot.logger
        # self.logger.name = f"{self.__class__.__name__}"
        self.message_timeout = bot.message_timeout

    async def get_guild(self) -> discord.Guild | None:
        return self.bot.get_guild(self.owner_guild.id)

    def to_discord_timestamp(self, time: datetime, style: _timestamp_styles = "F") -> str:
        """
        Converts a Date Time value into each Discord users local timezone for display.

        Parameters
        -----------
        time: :class:`datetime`
            The datetime object to timestamp.
        style: :class:`_timestamp_styles`, optional
            The Format to display the text in, by default "F".

        Returns
        --------
        :class:`str`
            The markdown text to use in Discord content to display the timestmap.

        Raises
        -------
        :exc:`ValueError`
            If the `style` parameter is invalid.
        """
        # https://sesh.fyi/timestamp/
        if style != self._timestamp_styles:
            raise ValueError("You provided an invalid Timestamp Style argument. | Style: %s", style)
        return f"<t:{int(time.timestamp())}:{style}>"

    def string_inflection(self, word: str, tag: str = "VBD") -> str:
        """
        Uses Lemminflection to change the inflection of the passed in word depending on the tag provided.
        - By default it uses `Verb, past tense` aka `VBD`.

        Parameters
        -----------
        word: :class:`str`
            The str to inflect upon.
        tag: :class:`str`, optional
            The Penn TreeBank Tag, by default "VBD".
            - https://www.ling.upenn.edu/courses/Fall_2003/ling001/penn_treebank_pos.html

        Returns
        --------
        :class:`str`
            Returns the Inflected string if possible, otherwise the original word.
        """
        results = getInflection(word, tag)
        if isinstance(results, tuple) and len(results) >= 1:
            return results[0]
        else:
            return word
