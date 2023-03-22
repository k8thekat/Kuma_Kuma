from __future__ import annotations

from typing import TYPE_CHECKING, Union

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from kuma_kuma import Kuma_Kuma

class KumaContext(commands.Context["Kuma_Kuma"]):
    bot: Kuma_Kuma


class KumaGuildContext(KumaContext):
    author: discord.Member
    guild: discord.Guild
    channel: Union[discord.VoiceChannel, discord.TextChannel, discord.Thread]
    me: discord.Member