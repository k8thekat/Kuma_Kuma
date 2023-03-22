import textwrap

from discord.ext import commands

class CodeBlockConverter(commands.Converter):
    async def convert(self, ctx: commands.Context, arg: str) -> str:
        """Automatically removes code blocks from the code."""
        content = textwrap.dedent(arg).strip()
        if content.startswith('`'*3) and content.endswith('`'*3):
            return '\n'.join(content.split('\n')[1:-1])
        # remove `foo`
        return content.strip('` \n')