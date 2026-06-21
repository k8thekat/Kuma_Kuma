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

import datetime
import platform
from collections.abc import Sequence
from pathlib import Path
from typing import Optional, Self, Unpack

import discord
from discord import Embed
from discord.ext.commands import Cog

from utils._types import EmbedParams
from utils.cog import KumaCog, KumaResources

__all__ = ("KumaEmbed",)


# ?SUGGESTION: Consider setting up a default ``set_thumbnail`` overwrite function.
class KumaEmbed(Embed):
    """Kuma Kuma Bears default Embed structure.

    - Default ``color`` is set to :class:`discord.Color.og_blurple()`.
    - Default ``title`` is set to ``__Kuma Kuma Bear__``.
    - Default ``timestamp`` is set to  :class:`datetime.datetime.now(tz=datetime.UTC)``.

    .. note::
        There is a default footer URL pointing to the Python logo;
        otherwise overwrite ``set_footer`` call and point to ``footer_icon`` for the default.

    .. note::
        - Will call ``set_footer``, ``set_thumbnail`` and ``set_author`` with in-place default content.

    .. warning::
        Make sure to use the ``attachments`` property inside your ``message.send()`` if you want the default content.
    """

    cog: KumaCog
    "My internal cog class."
    # _thumbnail_icon: discord.File | None = discord.File(KumaResources().sticker2, filename="thumbnail.png")

    @property
    def attachments(self) -> Sequence[discord.File]:
        icons: list[discord.File | None] = [self.thumbnail_icon, self.avatar_icon, self.footer_icon, self.field_image]
        return [entry for entry in icons if entry is not None]

    @property
    def thumbnail_icon(self) -> discord.File:
        """Our Kuma Kuma Bear thumbnail icon - aka ``sticker`` #1.

        Returns
        -------
        :class:`discord.File`
            A discord File object of the Kuma Kuma Bear footer icon.

        """
        return discord.File(fp=KumaResources().sticker, filename="thumbnail-icon.png")

    @property
    def avatar_icon(self) -> discord.File:
        """Our Kuma Kuma Bear avatar icon - aka ``sticker2``.

        Returns
        -------
        :class:`discord.File`
            A discord File object of the Kuma Kuma Bear avatar icon..

        """
        return discord.File(fp=KumaResources().sticker2, filename="avatar-icon.png")

    @property
    def field_image(self) -> None:
        pass

    @property
    def footer_icon(self) -> discord.File:
        """Our Kuma Kuma Bear footer icon - aka `sticker`.

        Returns
        -------
        :class:`discord.File`
            A discord File object of the Kuma Kuma Bear footer icon.

        """
        return discord.File(fp=KumaResources().sticker, filename="footer-icon.png")

    def __init__(self, *, cog: KumaCog, info: Optional[discord.AppInfo] = None, **kwargs: Unpack[EmbedParams]) -> None:
        self.cog = cog

        if kwargs.get("author") is not None:
            kwargs.pop("author")

        # Set our default color (love my blurple)
        if kwargs.get("color") is None:
            kwargs["color"] = discord.Color.og_blurple()

        if kwargs.get("title") is None:
            kwargs["title"] = "__Kuma Kuma Bear__"

        # Attempt to set our Timestamp, default to UTC
        timestamp: datetime.datetime | None = kwargs.get("timestamp")
        if timestamp is None:
            kwargs["timestamp"] = datetime.datetime.now(tz=datetime.UTC)

        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue] # We pop the author key earlier.

        # All our default settings overwriting an Embeds defaults post init.
        if info is not None:
            self.set_footer(text=f"Kuma Kuma Bear made by {info.owner.name}")
        self.set_author(name="Kuma Kuma Bear", icon_url="attachment://avatar-icon.png")
        self.set_thumbnail(url="attachment://thumbnail-icon.png")
        self.set_footer(
            text=f"Made with discord.py v{discord.__version__}, Running {platform.python_implementation()} v{platform.python_version()}",
            icon_url="https://i.imgur.com/5BFecvA.png",
        )

    def add_blank_field(self, *, index: Optional[int] = -1, inline: bool = False) -> Self:
        """Adds a blank field to the embed object.

        This function returns the class instance to allow for fluent-style
        chaining. Can only be up to 25 fields.

        Parameters
        ----------
        inline: :class:`bool`, optional
            Whether the field should be displayed inline, default is False
        index: :class:`Optional[int]`, optional
            To insert the field at a specific index, typically at the end.
            - If `None` will insert the field via `Self.add_field()`.

        """
        if index is None:
            self.add_field(name="\u200b", value="\u200b", inline=inline)
            return self

        self.insert_field_at(index=index, name="\u200b", value="\u200b", inline=inline)
        return self

    def add_seperator(self, *, index: Optional[int] = None, inline: bool = False) -> Self:
        """..."""
        if index is None:
            self.add_field(name="_________________________", value="test", inline=inline)
            self.add_field(name=self.cog.unicode.double_vertical, value=self.cog.unicode.double_vertical, inline=inline)
            return self

        self.insert_field_at(name="=========================", value="\u200b", index=index, inline=inline)
        return self

    def set_footer(
        self,
        *,
        text: Optional[str] = "Kuma Kuma Bear",
        icon_url: Optional[str] = "attachment://footer-icon.png",
        # timestamp: bool = False,
    ) -> Self:
        """Set the footer of the Embed.

        Parameters
        ----------
        text: :class:`Optional[str]`, optional
            The text parameter for `super().set_footer()`, by default "Kuma Kuma Bear".
        icon_url: :class:`_type_`, optional
            The icon url parameter for `super().set_footer()`, by default "attachment://footer-icon.png".
        timestamp: :class:`bool`, optional
            Add a `discord timestamp` of when the embed was sent to the end of the `text` parameter, by default False.

        Returns
        -------
        :class:`Self`
            Returns a :class:`Self` for fluent code typing.

        """
        # if timestamp is True and text is not None:
        #     text += f" | {datetime.datetime.now(tz=datetime.UTC).strftime('%d/%m | %H:%M (%Z)')}"
        return super().set_footer(text=text, icon_url=icon_url)
