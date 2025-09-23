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

from typing import Self

from discord import Embed

__all__ = ("KumaEmbed",)


class KumaEmbed(Embed):
    def add_blank_field(self, *, inline: bool = True) -> Self:
        """Adds a blank field to the embed object.

        This function returns the class instance to allow for fluent-style
        chaining. Can only be up to 25 fields.

        Parameters
        ----------
        inline: :class:`bool`
            Whether the field should be displayed inline.

        """
        field = {
            "inline": inline,
            "name": "\u200b",
            "value": "\u200b",
        }

        try:
            self._fields.append(field)
        except AttributeError:
            self._fields = [field]

        return self
