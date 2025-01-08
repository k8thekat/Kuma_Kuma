from __future__ import annotations

from typing import TYPE_CHECKING

from discord.ext import commands

if TYPE_CHECKING:
    from logging import Logger

    from kuma_kuma import Kuma_Kuma


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
    """

    bot: Kuma_Kuma
    logger: Logger
    message_timeout: int

    def __init__(self, bot: Kuma_Kuma) -> None:
        self.bot = bot
        self.logger = bot.logger
        self.message_timeout = bot.message_timeout
