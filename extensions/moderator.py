import re
from typing import TYPE_CHECKING, Union

import discord
from discord import Member, Message, User, app_commands
from discord.app_commands import Choice
from discord.ext import commands

from extensions import EXTENSIONS
from kuma_kuma import Kuma_Kuma
from utils.cog import KumaCog as Cog  # need to replace with your own Cog class
from utils.context import KumaContext as Context

if TYPE_CHECKING:
    from sqlite3 import Row

    import mystbin
    from asqlite import Cursor


BOT_NAME = ""


async def _get_prefix(bot: Kuma_Kuma, message: Message) -> list[str]:
    prefixes = [bot._prefix]
    if message.guild is not None:
        _guild: int = message.guild.id

        async with bot.pool.acquire() as conn:
            res: list[Row] = await conn.fetchall("""SELECT prefix FROM prefix WHERE serverid = ?""", _guild)
            if res is not None and len(res) >= 1:
                prefixes: list[str] = [entry["prefix"] for entry in res]

    wmo_func = commands.when_mentioned_or(*prefixes)
    return wmo_func(bot, message)


async def _get_trusted(bot: Kuma_Kuma) -> set[int]:
    _trusted: set[int] = bot.owner_ids
    async with bot.pool.acquire() as conn:
        res: list[Row] = await conn.fetchall("""SELECT ownerid FROM owners""")
        if res is not None and len(res) >= 1:
            _trusted.update([entry["ownerid"] for entry in res])
    return _trusted


PREFIX_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS prefix (
    id INTEGER PRIMARY KEY NOT NULL,
    serverid INTEGER NOT NULL,
    prefix TEXT
)"""

OWNER_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS owners (
    id INTEGER PRIMARY KEY NOT NULL,
    ownerid INTEGER NOT NULL
)"""


class Moderator(Cog):
    """
    Moderator _summary_
    """

    CODEBLOCK_PATTERN: re.Pattern[str] = re.compile(
        pattern=r"`{3}(?P<LANG>\w+)?\n?(?P<CODE>(?:(?!`{3}).)+)\n?`{3}", flags=re.DOTALL | re.MULTILINE
    )

    def __init__(self, bot: Kuma_Kuma) -> None:
        super().__init__(bot=bot)
        global BOT_NAME
        BOT_NAME: str = bot.user.name

    # todo - Need to verify this triggers `on_error()` of Kuma Kuma.
    async def cog_load(self) -> None:
        try:
            async with self.bot.pool.acquire() as conn:
                await conn.execute(PREFIX_SETUP_SQL)
                await conn.execute(OWNER_SETUP_SQL)
        except Exception as e:
            self.logger.error("We encountered an error executing %s", __file__ + ".cog_load", exc_info=e)
            raise ConnectionError("Unable to connect to the database.")

    @commands.Cog.listener("on_message")
    async def on_message_listener(self, message: discord.Message) -> None:
        if message.author == self.bot.user:
            return

        # So if our message is over 1k char length and doesn't use our prefix; Lets push it to a mystbin URL.
        # todo - validate the tuple return of _get_prefix is correct.
        if (
            isinstance(message.channel, discord.abc.GuildChannel)
            and message.channel.type is not discord.ChannelType.news
            and len(message.content) > 1000
            and not message.content.startswith(tuple(await _get_prefix(bot=self.bot, message=message)))
        ):
            await self._auto_on_mystbin(message=message)

    async def _auto_on_mystbin(self, message: Message) -> None:
        """
        Converts a `discord.Message` into a Mystbin URL

        Parameters
        -----------
        message: :class:`Message`
            The Discord Message to be converted.
        """
        content: str = message.content
        assert message.guild  # we are force checking this function earlier to make sure we are in a guild only.
        files: list[tuple[str, str]] = []

        should_upload_to_bin: bool = False

        for idx, match in enumerate(iterable=self.CODEBLOCK_PATTERN.finditer(string=content), start=1):
            language: str = match.group("LANG") or "python"
            filename: str = f"File-{idx}.{language}"
            file_content: str = match.group("CODE")

            files.append((filename, file_content))
            content = content.replace(match.group(), f"`[{filename}]`")

            should_upload_to_bin = should_upload_to_bin or len(file_content) > 1100
        if should_upload_to_bin:
            paste: mystbin.Paste = await self.bot.loghandler.create_paste(files=files, session=self.bot.session)

            # todo - Possibly use an embed for moving large codeblocks to Mystbin.
            await message.channel.send(
                content=f"{content}\n\nHey {message.author.mention}, {BOT_NAME} moved your codeblock(s) to `Mystbin` here is the link: {paste.url}"
            )
            if message.channel.permissions_for(message.guild.me).manage_messages:
                await message.delete()

    @commands.command(name="reload", help="Reload all extensions.")
    @commands.is_owner()
    async def reload(self, context: Context) -> None:
        """Reloads all extensions inside the extensions folder."""
        await context.typing(ephemeral=True)

        try:
            for extension in EXTENSIONS:
                await self.bot.load_extension(name=extension.name)
                self.logger.info("Loaded %sextension: %s", "module " if extension.ispkg else "", extension.name)
        except Exception as e:
            self.logger.error("We encountered an error executing %s", context.command, exc_info=e)
            await context.send(
                content=f"We encountered an **Error** - \n{e}", ephemeral=True, delete_after=self.message_timeout
            )

        await context.send(
            content="**SUCCESS** Reloading All extensions ", ephemeral=True, delete_after=self.message_timeout
        )

    @commands.command(name="sync", help=f"Sync the {BOT_NAME} commands to the guild.")
    @commands.is_owner()
    @commands.guild_only()
    async def sync(self, context: Context, local: bool = True, reset: bool = False) -> Message | None:
        """Syncs Kuma Commands to the current guild this command was used in."""
        await context.typing(ephemeral=True)
        assert context.guild is not None  # Since we limit the command to guild_only()

        if reset == True and local == True:
            # Local command tree reset
            self.bot.tree.clear_commands(guild=context.guild)
            self.bot.logger.info(
                "%s Commands Reset Locally and Sync'd: %s by %s",
                self.bot.user.name,
                await self.bot.tree.sync(guild=context.guild),
                self.bot.user.name,
            )
            return await context.send(
                f"**WARNING** Resetting `{self.bot.user.name}s` Commands Locally...",
                ephemeral=True,
                delete_after=self.message_timeout,
            )

        if local == True:
            # Local command tree sync
            self.bot.tree.copy_global_to(guild=context.guild)
            self.bot.logger.info(
                "%s Commands Sync'd Locally: %s", self.bot.user.name, await self.bot.tree.sync(guild=context.guild)
            )
            return await context.send(
                f"Successfully Sync'd `{self.bot.user.name}s` Commands to {context.guild}...",
                ephemeral=True,
                delete_after=self.message_timeout,
            )

    @commands.group(name="prefix", invoke_without_command=True)
    @commands.guild_only()
    async def prefix(self, context: Context) -> Message:
        """Returns a list of the current prefixes for the current guild"""
        assert context.guild is not None
        async with self.bot.pool.acquire() as conn:
            res: list[Row] = await conn.fetchall("""SELECT prefix FROM prefix WHERE serverid = ?""", context.guild.id)
            if len(res) > 0:
                _prefixes = "\n".join([entry["prefix"] for entry in res])
                return await context.send(
                    content=f"**Current Prefixes:** \n{_prefixes}", delete_after=self.message_timeout, ephemeral=True
                )
            else:
                return await context.send(
                    content="It appears you do not have any prefix's set", delete_after=self.message_timeout, ephemeral=True
                )

    @prefix.command(name="add", help=f"Add a prefix to {BOT_NAME}", aliases=["prea", "pa"])
    @commands.is_owner()
    @commands.guild_only()
    async def add_prefix(self, context: Context, prefix: str) -> Message:
        assert context.guild is not None  # We know because guild_only() decorator.
        async with self.bot.pool.acquire() as conn:
            await conn.execute("""INSERT INTO prefix(serverid, prefix) VALUES(?, ?)""", context.guild.id, prefix.lstrip())
            return await context.send(
                content=f"Added the prefix `{prefix}` for {context.guild.name}",
                delete_after=self.message_timeout,
                ephemeral=True,
            )

    @prefix.command(name="delete", help=f"Delete a prefix from {BOT_NAME} for a guild.", aliases=["pred", "pd"])
    @commands.is_owner()
    @commands.guild_only()
    async def delete_prefix(self, context: Context, prefix: str) -> Message:
        assert context.guild is not None
        async with self.bot.pool.acquire() as conn:
            await conn.execute("""DELETE FROM prefix WHERE serverid = ? AND prefix = ?""", context.guild.id, prefix.lstrip())
            return await context.send(content=f"Removed the prefix - `{prefix}`", delete_after=self.message_timeout)

    @prefix.command(name="clear", help=f"Clear all prefixes for {BOT_NAME}in a guild.", aliases=["prec", "pc"])
    @commands.is_owner()
    async def clear_prefix(self, context: Context) -> Message:
        assert context.guild is not None
        async with self.bot.pool.acquire() as conn:
            await conn.execute("""DELETE FROM prefix WHERE serverid = ?""", context.guild.id)
            return await context.send(
                content=f"Removed all prefix's for {context.guild.name}", delete_after=self.message_timeout, ephemeral=True
            )

    @commands.command(name="trusted", help=f"Add/Remove and list {BOT_NAME} Owner IDs.")
    @commands.is_owner()
    @commands.guild_only()
    @app_commands.choices(
        option=[Choice(name="add", value="add"), Choice(name="remove", value="remove"), Choice(name="list", value="list")]
    )
    async def trust(self, context: Context, option: Choice, member: Union[Member, User]) -> Message | None:
        assert context.guild
        if option == "add":
            if member.id not in self.bot.owner_ids:
                async with self.bot.pool.acquire() as conn:
                    await conn.execute("""INSERT INTO owners(ownerid) VALUES(?)""", member.id)
                    return await context.send(
                        content=f"Added {member.mention} to the owner list",
                        ephemeral=True,
                        delete_after=self.message_timeout,
                    )
            else:
                return await context.send(
                    content="You are already an owner", ephemeral=True, delete_after=self.message_timeout
                )

        elif option == "remove":
            async with self.bot.pool.acquire() as conn:
                cur: Cursor = await conn.execute("""DELETE FROM owners WHERE ownerid = ?""", member.id)
                return await context.send(
                    content=f"Removed {cur.get_cursor().rowcount} Users as an owner",
                    ephemeral=True,
                    delete_after=self.message_timeout,
                )

        elif option == "list":
            async with self.bot.pool.acquire() as conn:
                res: list[Row] = await conn.fetchall("""SELECT ownderid FROM owners""")
                _owners: list[Member] = [await context.guild.fetch_member(entry["id"]) for entry in res]
                f_owners: str = "\n".join([entry.display_name for entry in _owners])
                return await context.send(
                    content=f"**Current Owners:** \n{f_owners}", ephemeral=True, delete_after=self.message_timeout
                )

    @commands.command(name="clear", help="Removes Member and Bot messages from a channel.")
    @app_commands.default_permissions(manage_messages=True)
    @commands.guild_only()
    @app_commands.describe(
        bot_only=f"Default's to True, removes ALL messages from selected Channel that were sent by {BOT_NAME}."
    )
    async def clear(
        self,
        context: Context,
        channel: Union[discord.VoiceChannel, discord.TextChannel, discord.Thread, None],
        amount: app_commands.Range[int, 0, 100] = 15,
        bot_only: bool = True,
    ) -> Message:
        """Cleans up Messages sent by anyone. Limit 100"""
        if isinstance(context, discord.Interaction):
            await context.response.send_message(content="Removing messages...", delete_after=self.message_timeout)

        assert isinstance(context.channel, (discord.VoiceChannel, discord.TextChannel, discord.Thread))
        channel = channel or context.channel

        messages: list[discord.Message] = []
        if bot_only:
            messages = await channel.purge(limit=amount, check=self.bot.is_me, bulk=False)
        elif context.author.id in self.bot.owner_ids:
            messages = await channel.purge(limit=amount, bulk=False)

        return await channel.send(
            content=f"Cleaned up **{len(messages)} {'messages' if len(messages) > 1 else 'message'}**. Wow, look at all this space!",
            delete_after=self.message_timeout,
        )
