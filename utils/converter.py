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

import textwrap
from typing import TYPE_CHECKING

from discord.ext import commands

if TYPE_CHECKING:
    from discord.ext.commands import Context

__all__ = ("CodeBlockConverter", "Snowflake")


class CodeBlockConverter(commands.Converter):
    """Strips code block fencing from a command argument."""

    async def convert(self, ctx: commands.Context, argument: str) -> str:  # noqa: ARG002 — ctx is part of the Converter protocol
        """Automatically removes code blocks from the code."""
        content: str = textwrap.dedent(argument).strip()
        if content.startswith("`" * 3) and content.endswith("`" * 3):
            return "\n".join(content.split("\n")[1:-1])
        # Remove inline backtick wrapping.
        return content.strip("` \n")


class Snowflake:
    """Converts a string argument to a Discord snowflake (integer ID)."""

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> int:
        """Convert the argument to an integer snowflake ID.

        Raises
        ------
        :exc:`commands.BadArgument`
            The argument is not a valid integer.

        """
        try:
            return int(argument)
        except ValueError:
            parameter = ctx.current_parameter
            if parameter:
                msg: str = f"{parameter.name} argument expected a Discord ID not {argument!r}"
                raise commands.BadArgument(msg) from None
            msg = f"expected a Discord ID not {argument!r}"
            raise commands.BadArgument(msg) from None
