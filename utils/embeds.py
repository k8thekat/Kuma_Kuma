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
from typing import Literal, Optional, Self, Unpack

import discord
from discord import Embed
from discord.ext.commands import Cog

from utils._types import EmbedParams
from utils.cog import KumaCog, KumaResources

__all__ = ("KumaEmbed",)


class KumaEmbed(Embed):
    """Kuma Kuma Bears default Embed structure.

    - Default ``color`` is set to :class:`discord.Color.og_blurple()`.
    - Default ``title`` is set to ``__Kuma Kuma Bear__``.
    - Default ``timestamp`` is set to  :class:`datetime.datetime.now(tz=datetime.UTC)``.

    .. note::
        There is a default footer URL pointing to the Python logo;
        otherwise overwrite ``set_footer`` call and point to ``footer_icon`` for the default.

    .. note::
        - Will call ``set_footer`` and ``set_author`` with in-place default content.

    .. warning::
        Make sure to use the ``attachments`` property inside your ``message.send()`` if you want the default content.


    Attributes
    ----------
    cog: :class:`KumaCog`
        A :class:`discord.Cog` type class.
    footer_icon: :class:`discord.File | str | None`
        Set the Footer Icon for a :class:`discord.Embed`.
    thumbnail_icon: :class:`discord.File | str | None`
        Set the Thumbnail Icon for a :class:`discord.Embed`.
    avatar_icon: :class:`discord.File | str | None`
        Set the Avatar Icon for a :class:`discord.Embed`.
    field_image: :class:`discord.File | str | None`
        Set the Field Image for a :class:`discord.Embed`.

    """

    cog: KumaCog
    "My internal cog class."
    _footer_icon: discord.File | str | None = None
    _thumbnail_icon: discord.File | str | None = None
    _avatar_icon: discord.File | str | None = None
    _field_image: discord.File | str | None = None

    @property
    def attachments(self) -> Sequence[discord.File]:
        """Holds all the inline attachment URLS to pass along.

        Returns
        -------
        :class:`Sequence[discord.File]`
            _description_.

        """
        attrs: list[str] = ["thumbnail_icon", "avatar_icon", "footer_icon", "field_image"]
        icons: list[discord.File] = []
        # This should allow us to del the property/attributes when not using defaults thus preventing the attachments from being included.
        for attr in attrs:
            try:
                res: discord.File | str | None = getattr(self, attr)
                # We only care about discord.Files as they will be in-line attachments (by design).
                if isinstance(res, discord.File):
                    icons.append(res)
            except AttributeError:
                continue

        # Since we allow people to set the icon values to ``None``
        # (say you want to use defaults but not all of them.)
        return [entry for entry in icons if not None]

    @property
    def thumbnail_icon(self) -> discord.File | str | None:
        """Set the Thumbnail Icon for a :class:`discord.Embed`.

        - Supports ``URL's``.
        - Setting to ``None`` will remove any existing thumbnail icon.

        .. note::
            If supplied a :class:`discord.File` object, updates the "filename" attribute to be used as an inline attachment.

        Returns
        -------
        :class:`discord.File | str | None`

        """
        return self._thumbnail_icon

    @thumbnail_icon.setter
    def thumbnail_icon(self, value: Optional[discord.File | str]) -> None:
        if isinstance(value, discord.File):
            value.filename = "thumbnail-icon.png"
        self._thumbnail_icon = value

    @property
    def avatar_icon(self) -> discord.File | str | None:
        """Set the Avatar Icon for a :class:`discord.Embed`.

        - Supports ``URL's``.
        - Setting to ``None`` will remove any existing avatar icon.

        .. note::
            If supplied a :class:`discord.File` object, updates the "filename" attribute to be used as an inline attachment.

        Returns
        -------
        :class:`discord.File | str | None`

        """
        return self._avatar_icon

    @avatar_icon.setter
    def avatar_icon(self, value: Optional[discord.File | str]) -> None:
        if isinstance(value, discord.File):
            value.filename = "avatar-icon.png"
        self._avatar_icon = value

    @property
    def field_image(self) -> discord.File | str | None:
        """Set the Field Image for a :class:`discord.Embed`.

        - Supports ``URL's``
        - Setting to ``None`` will remove any existing field images.

        .. note::
            If supplied a :class:`discord.File` object, updates the "filename" attribute to be used as an inline attachment.

        Returns
        -------
        :class:`discord.File | str | None`

        """
        return self._field_image

    @field_image.setter
    def field_image(self, value: None | discord.File | str) -> None:
        if isinstance(value, discord.File):
            value.filename = "field-image.png"
        self._field_image = value

    @property
    def footer_icon(self) -> discord.File | str | None:
        """The Footer Icon for a :class:`discord.Embed`.

        - Supports ``URL's``.
        - Setting to ``None`` will remove any existing footer icon.

        .. note::
            If supplied a :class:`discord.File` object, updates the "filename" attribute to be used as an inline attachment.

        Returns
        -------
        :class:`discord.File | str | None`

        """
        return self._footer_icon

    @footer_icon.setter
    def footer_icon(self, value: Optional[discord.File | str]) -> None:
        if isinstance(value, discord.File):
            value.filename = "footer-icon.png"
        self._footer_icon = value

    def __init__(self, *, cog: KumaCog, defaults: bool = False, **kwargs: Unpack[EmbedParams]) -> None:
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

        if defaults:
            # self.footer_icon = discord.File(self.cog.resources.sticker)
            self.avatar_icon = discord.File(self.cog.resources.sticker2)
            # self.thumbnail_icon = discord.File(self.cog.resources.sticker)
            self.set_footer(text=f"Kuma Kuma Bear | by {self.cog.bot.owner.name}", img=discord.File(self.cog.resources.sticker))
            self.set_author(author=self.cog.bot.user)
            self.set_thumbnail(img=discord.File(self.cog.resources.sticker))
            self.set_image(img=discord.File(self.cog.resources.banner))

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
        img: Optional[discord.File] = None,
        icon_url: Optional[str] = "attachment://footer-icon.png",
    ) -> Self:
        """Set the footer of the Embed.

        Parameters
        ----------
        text: :class:`Optional[str]`, optional
            The text parameter for `super().set_footer(text, ...)`, by default "text = Kuma Kuma Bear".
        img: :class:`Optional[discord.File]`, optional
            A pre-built discord.File object that will be set as :property:`self.footer_icon` and the URL set to `attachment://footer-icon.png`,
            by default `None`.
        icon_url: :class:`Optional[str]`, optional
            The icon url parameter for `super().set_footer(..., icon_url)`, by default "icon_url = attachment://footer-icon.png".

        Returns
        -------
        :class:`Self`
            Returns a :class:`Self` for fluent code typing.

        """
        if img is not None:
            self.footer_icon = img
            icon_url = "attachment://footer-icon.png"

        return super().set_footer(text=text, icon_url=icon_url)

    def set_image(self, *, img: Optional[discord.File] = None, url: Optional[str] = "attachment://field-image.png") -> Self:
        """Set the Field Image of the Embed.

        Parameters
        ----------
        img: :class:`Optional[discord.File]`, optional
            A pre-built discord.File object that will be set as :property:`self.field_image` and the URL set to `attachment://field-image.png`,
            by default `None`.
        url: :class:`Optional[str]`, optional
            The icon url parameter for `super().set_image(url)`, by default "url = attachment://field-image.png".

        Returns
        -------
        :class:`Self`
            Returns a :class:`Self` for fluent code typing.

        """
        # Update our property. Renaming happens internally.
        # Forced return as mutually exclusive you'd either use a URL or an IMG. Not both...
        if img is not None:
            self.field_image = img
            return super().set_image(url="attachment://field-image.png")

        return super().set_image(url=url)

    def set_author(
        self,
        *,
        author: Optional[discord.Member | discord.ClientUser] = None,
        name: Optional[str] = "Kuma Kuma Bear",
        url: Optional[str] = None,
        icon_url: Optional[str] = "attachment://avatar-icon.png",
    ) -> Self:
        """Set the Author Image of the Embed.

        Parameters
        ----------
        author: :class:`Optional[discord.Member]`, optional
            The author of the embed, if applicable. Will use the object to populate ``name`` and either ``icon_url`` and or ``icon``
        name: :class:`Optional[str]`, optional
            The name of the author. Can only be up to 256 characters. Default is "Kuma Kuma Bear".
        url: :class:`Optional[str]`, optional
            The URL for the author, if ``author`` is ``None``.
        icon_url: :class:`Optional[str]`, optional
            The icon url parameter for `super().set_author(icon_url)`, by default "icon_url = attachment://field-image.png".

        Returns
        -------
        :class:`Self`
            Returns a :class:`Self` for fluent code typing.

        """
        if author is not None:
            if icon_url is None:
                icon_url = author.display_avatar.url
            name = author.display_name

        return super().set_author(name=name, url=url, icon_url=icon_url)

    def set_thumbnail(
        self, *, img: Optional[discord.File] = None, url: Optional[str | discord.Asset] = "attachment://thumbnail-icon.png"
    ) -> Self:
        """Set the Thumbnail Image of the Embedd.

        Parameters
        ----------
        img: :class:`Optional[discord.File]`, optional
            A pre-built discord.File object that will be set as :property:`self.thumbnail_icon` and the URL set to `attachment://thumbnail-icon.png`,
            by default `None`.
        url: :class:`_type_`, optional
            The icon url parameter for `super().set_thumbnail(url)`, by default "attachment://thumbnail-icon.png".

        Returns
        -------
        :class:`Self`
            Returns a :class:`Self` for fluent code typing.

        """
        if img is not None:
            self.thumbnail_icon = img

        return super().set_thumbnail(url=url)
