from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Union

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from logging import Logger

    from kuma_kuma import Kuma_Kuma


class KumaEmojiTable:
    """
    Usage - <:_:emoji_id>
    """

    kuma_rawr: int = 1337130200009281559
    kuma_crying: int = 1337130201527750757
    kuma_tear: int = 1337130203096420372
    kuma_happy: int = 1337130204547780648
    kuma_star_eye: int = 1337130207462817946
    kuma_shy: int = 1337130209446592513
    kuma_peak: int = 1337130211128377406
    kuma_chuckle: int = 1337130212890247199
    kuma_heart: int = 1337130214492475475
    kuma_sad: int = 1337130217071841441
    kuma_shrug: int = 1337130218963468448
    kuma_hmm: int = 1337130227788415087
    kuma_tea: int = 1337130230598602792
    kuma_shock: int = 1337130232993288244
    kuma_bleh: int = 1337130235472117841
    kuma_uwu: int = 1337130237225603092
    kuma_wow: int = 1337130238878154893
    kuma_pout: int = 1337133347163345019
    kuma_head_clench: int = 1337133349612814398

    @staticmethod
    def to_inline_emoji(emoji: Union[str, int]) -> str | None:
        """
        Converts the emoji provided into a Discord in line str type emoji for usage.

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

    def __init__(self, bot: Kuma_Kuma) -> None:
        self.bot = bot
        self.logger = bot.logger
        self.message_timeout = bot.message_timeout

    async def get_guild(self) -> discord.Guild | None:
        return self.bot.get_guild(self.owner_guild.id)
