import os
import logging
import aiofiles
import psutil
import re
import time
from datetime import timedelta
    
from discord.ext import commands
import discord

Dependencies = None

class Util(commands.Cog):
    def __init__(self, client: discord.Client) -> None:
        self._client = client
        self._name = os.path.basename(__file__).title()
        self._logger = logging.getLogger()
        self._logger.info(f'**SUCCESS** Initializing {self._name} ')

    @property
    def _uptime(self):
        return timedelta(seconds=(round(time.time() - self._client._start_time)))
    
    
        
    async def count_lines(self, path: str, filetype: str = ".py", skip_venv: bool = True):
        lines = 0
        for i in os.scandir(path):
            if i.is_file():
                if i.path.endswith(filetype):
                    if skip_venv and re.search(r"(\\|/)?venv(\\|/)", i.path):
                        continue
                    lines += len((await (await aiofiles.open(i.path, "r")).read()).split("\n"))
            elif i.is_dir():
                lines += await self.count_lines(i.path, filetype)
        return lines
    
    async def count_others(self, path: str, filetype: str = ".py", file_contains: str = "def", skip_venv: bool = True):
        line_count = 0
        for i in os.scandir(path):
            if i.is_file():
                if i.path.endswith(filetype):
                    if skip_venv and re.search(r"(\\|/)?venv(\\|/)", i.path):
                        continue
                    line_count += len(
                        [line for line in (await (await aiofiles.open(i.path, "r")).read()).split("\n") if file_contains in line]
                    )
            elif i.is_dir():
                line_count += await self.count_others(i.path, filetype, file_contains)
        return line_count

    @commands.command(help="Shows info about the bot", aliases=["botinfo", "info", "bi"])
    async def about(self, ctx: commands.Context):
        """Tells you information about the bot itself."""
        await ctx.defer()
        information = await self._client.application_info()
        embed = discord.Embed()
        #embed.add_field(name="Latest updates:", value=get_latest_commits(limit=5), inline=False)

        embed.set_author(name=f"Made by {information.owner}", icon_url=information.owner.display_avatar.url,)
        memory_usage = psutil.Process().memory_full_info().uss / 1024**2
        cpu_usage = psutil.cpu_percent()

        embed.add_field(name="Process", value=f"{memory_usage:.2f} MiB\n{cpu_usage:.2f}% CPU")
        embed.add_field(
            name= f"{self._client.user.name} info:",
            value=f"**Uptime:**\n{self._uptime}")
        try:
            embed.add_field(
                name="Lines",
                value=f"Lines: {await self.count_lines('./', '.py'):,}"
                f"\nFunctions: {await self.count_others('./', '.py', 'def '):,}"
                f"\nClasses: {await self.count_others('./', '.py', 'class '):,}",
            )
        except (FileNotFoundError, UnicodeDecodeError):
            pass

        embed.set_footer(
            text=f"Made with discord.py v{discord.__version__}",
            icon_url="https://i.imgur.com/5BFecvA.png",
        )
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

async def setup(client):
    await client.add_cog(Util(client))