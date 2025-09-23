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

from discord.ext import commands
from discord.ext.commands import Context  # This should point to your custom Context


class CodeBlockConverter(commands.Converter):
    async def convert(self, ctx: commands.Context, arg: str) -> str:
        """Automatically removes code blocks from the code."""
        content = textwrap.dedent(arg).strip()
        if content.startswith("`" * 3) and content.endswith("`" * 3):
            return "\n".join(content.split("\n")[1:-1])
        # remove `foo`
        return content.strip("` \n")


class Snowflake:
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> int:
        try:
            return int(argument)
        except ValueError:
            param = ctx.current_parameter
            if param:
                raise commands.BadArgument(f"{param.name} argument expected a Discord ID not {argument!r}")
            raise commands.BadArgument(f"expected a Discord ID not {argument!r}")
