from __future__ import annotations
import textwrap

from discord.ext import commands
from discord.ext.commands import Context #This should point to your custom Context


class CodeBlockConverter(commands.Converter):
    async def convert(self, ctx: commands.Context, arg: str) -> str:
        """Automatically removes code blocks from the code."""
        content = textwrap.dedent(arg).strip()
        if content.startswith('`' * 3) and content.endswith('`' * 3):
            return '\n'.join(content.split('\n')[1:-1])
        # remove `foo`
        return content.strip('` \n')


class Snowflake:
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> int:
        try:
            return int(argument)
        except ValueError:
            param = ctx.current_parameter
            if param:
                raise commands.BadArgument(f'{param.name} argument expected a Discord ID not {argument!r}')
            raise commands.BadArgument(f'expected a Discord ID not {argument!r}')
