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

from typing import Optional, Self, Unpack

from discord import Embed
from discord.ext.commands import Cog

from ._types import EmbedParams
from .cog import KumaCog

__all__ = ("KumaEmbed",)

class KumaEmbed(Embed):
    cog: KumaCog
    def __init__(self, *, cog: KumaCog, **kwargs: Unpack[EmbedParams]) -> None:
        self.cog = cog
        if kwargs.get("author") is not None:
            kwargs.pop("author")
        super().__init__(**kwargs) # pyright: ignore[reportCallIssue] # We pop the author key earlier.

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

    def add_seperator(self, *, index: Optional[int] = None, inline:bool = False) -> Self:
        """..."""
        if index is None:
            self.add_field(name="_________________________", value="test", inline=inline)
            self.add_field(name=self.cog.unicode.double_vertical, value=self.cog.unicode.double_vertical, inline=inline)
            return self

        self.insert_field_at(name="=========================", value="\u200b", index=index, inline=inline)
        return self
