import asyncio
import hashlib
import io
import json
import os
import struct
import sys
import time
from collections.abc import Coroutine
from configparser import ConfigParser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Row
from typing import Literal, TypedDict, Union

import aiohttp
import asyncpraw
import discord
import pytz
import tzlocal
import xy_binfind
from aiohttp import ClientResponse
from asyncpraw.models import Subreddit
from discord import app_commands
from discord.ext import commands, tasks
from fake_useragent import UserAgent
from PIL import Image as IMG
from PIL.Image import Image
from typing_extensions import Any

import utils.asqlite as asqlite
from utils import ImageComp, cog

script_loc: Path = Path(__file__).parent
DB_FILENAME = "reddit_scrape.sqlite"
DB_PATH: str = script_loc.joinpath(DB_FILENAME).as_posix()

SUBREDDIT_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS subreddit (
    id INTEGER PRIMARY KEY NOT NULL,
    name TEXT COLLATE NOCASE NOT NULL UNIQUE,
    webhook_id INTEGER,
    FOREIGN KEY (webhook_id) references webhook(id)
)"""

WEBHOOK_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS webhook (
    id INTEGER PRIMARY KEY NOT NULL,
    name TEXT COLLATE NOCASE NOT NULL UNIQUE,
    url TEXT NOT NULL UNIQUE
)"""


class Webhook(TypedDict):
    name: str
    url: str
    id: int


@dataclass
class ImageInfo:
    width: int = field(default=0)
    height: int = field(default=0)
    edge_res: bool = field(default=True)


async def _get_subreddit(name: str) -> Row | None:
    """
    Get a Row from the Subreddit Table.

    Args:
        name (str): Name of Subreddit

    Returns:
        Row['id', 'name', 'webhook_id'] | None
    """
    async with asqlite.connect(DB_PATH) as db:
        async with db.cursor() as cur:
            await cur.execute("""SELECT id, name, webhook_id FROM subreddit where name = ?""", name)
            res: Row | None = await cur.fetchone()
            await cur.close()
            return res if not None else None


async def _add_subreddit(name: str) -> Row | None:
    """
    Add a Row to the Subreddit Table.

    Args:
        name (str): Name of Subreddit

    Returns:
        Row['id','name','webhook_id'] | None
    """
    res = await _get_subreddit(name=name)
    if res is not None:
        return None
    else:
        async with asqlite.connect(DB_PATH) as db:
            async with db.cursor() as cur:
                await cur.execute("""INSERT INTO subreddit(name) VALUES(?) ON CONFLICT(name) DO NOTHING RETURNING *""", name)
                await db.commit()
                res: Row | None = await cur.fetchone()
                await cur.close()
                return res if not None else None


async def _del_subreddit(name: str) -> int | None:
    """
    Delete a Row from the Subreddit Table

    Args:
        name (str): Name of Subreddit

    Returns:
        int | None: Row count
    """
    res: Row | None = await _get_subreddit(name=name)
    if res is None:
        return None
    else:
        async with asqlite.connect(DB_PATH) as db:
            async with db.cursor() as cur:
                await cur.execute("""DELETE FROM subreddit WHERE name = ?""", name)
                res = await cur.fetchone()
                await db.commit()
                count: int = cur.get_cursor().rowcount
                await cur.close()
                return count
                # return res if not None else None


async def _get_all_subreddits(webhooks: bool = True) -> list[str | Union[Any, str, dict[str, str | None]]]:
    """
    Gets all Row entries of the Subreddit Table. Typically including the webhook IDs.

    Args:
        webhooks(bool): Set to False to not include webhook IDs when getting subreddits. Defaults True.
    Returns:
        list[Union[Any, dict[str, str]]]: An empty list if no entries in the Subreddit table.

        Otherwise a list of dictionaries structured as `[{"subreddit", "webhook url"}]`
    """
    _subreddits: list[dict[str, Union[str, None]]] = []
    async with asqlite.connect(DB_PATH) as db:
        async with db.cursor() as cur:
            await cur.execute("""SELECT name, webhook_id FROM subreddit""")
            res = await cur.fetchall()
            if res is None or len(res) == 0:
                return []

            if webhooks == False:
                _subs: list[str] = [entry["name"] for entry in res]
                return _subs
            else:
                for entry in res:
                    res_webhook = await _get_webhook(arg=entry["webhook_id"])
                    if res_webhook is not None:
                        res_webhook = res_webhook["url"]
                    _subreddits.append({entry["name"]: res_webhook})

        return _subreddits


async def _update_subreddit(name: str, webhook: Union[int, str]) -> Row | None:
    """
    Update a Subreddit Row `webhook_id` value.

    Args:
        name (str): Subreddit name
        webhook (int, str): Webhook name, url, ID or None.

    Returns:
        Row['id', 'name', 'url'] | None
    """
    # Check if the subreddit exists
    res: Row | None = await _get_subreddit(name=name)
    if res is None:
        return None
    else:
        # Check if the webhook exists
        if type(webhook) == str and webhook.lower() == "none":
            webhook_id = None
        else:
            res = await _get_webhook(webhook)
            if res is None:
                return None
            else:
                webhook_id = res["ID"]

    async with asqlite.connect(DB_PATH) as db:
        async with db.cursor() as cur:
            await cur.execute("""UPDATE subreddit SET webhook_id = ? WHERE name = ?  RETURNING *""", webhook_id, name)
            await db.commit()
            res = await cur.fetchone()
            return res if not None else None


async def _get_webhook(arg: Union[str, int, None]) -> Row | None:
    """
    Lookup a Row in the Webhook Table

    Args:
        arg (Union[str, int, None]): Supports webhook name, id or url queries.

    Returns:
        Row["name", "id", "url"] | None
    """
    async with asqlite.connect(DB_PATH) as db:
        async with db.cursor() as cur:
            if arg is None:
                return None
            elif type(arg) == int:
                await cur.execute("""SELECT name, id, url FROM webhook where id = ?""", arg)
            elif type(arg) == str and arg.startswith("http"):
                await cur.execute("""SELECT name, id, url FROM webhook where url = ?""", arg)
            else:
                await cur.execute("""SELECT name, id, url FROM webhook where name = ?""", arg)
            res: Row | None = await cur.fetchone()
            await cur.close()
            return res if not None else None


async def _add_webhook(name: str, url: str) -> Row | None:
    """
    Add a Row to the Webhook Table.

    Args:
        name (str): A string to represent the Webhook URL in the table.
        url (str): Discord webhook URL

    Returns:
        Row['id','name','url'] | None
    """
    res: Row | None = await _get_webhook(arg=url)
    if res is not None:
        return None
    else:
        async with asqlite.connect(DB_PATH) as db:
            async with db.cursor() as cur:
                await cur.execute(
                    """INSERT INTO webhook(name, url) VALUES(?, ?) ON CONFLICT(url) DO NOTHING RETURNING *""", name, url
                )
                await db.commit()
                res = await cur.fetchone()
                await cur.close()
                return res if not None else None


async def _del_webhook(arg: Union[int, str]) -> int | None:
    """
    Delete a Row matching the args from the Webhook Table.\n

    Converts string numbers into `ints` if they are digits.

    Args:
        arg (Union[int, str]): Supports Webhook ID, Name or URL.

    Returns:
        int | None: Row Count
    """
    if isinstance(arg, str) and arg.isdigit():
        arg = int(arg)
    res = await _get_webhook(arg=arg)
    if res is None:
        return None
    else:
        async with asqlite.connect(DB_PATH) as db:
            async with db.cursor() as cur:
                await cur.execute(
                    """UPDATE subreddit SET webhook_id = ? WHERE webhook_id = ?  RETURNING *""", None, res["id"]
                )
                await cur.execute("""DELETE FROM webhook WHERE id = ?""", res["id"])
                await db.commit()
                await db.close()
                return cur.get_cursor().rowcount


async def _get_all_webhooks() -> list[None | Webhook]:
    """
    Gets all Webhook Table Rows.

    Returns:
        list[Any] | list[dict[str, str | int]]: Structure `[{"name": str , "url": str , "id": int}]`
    """
    _webhooks: list[None | Webhook] = []
    async with asqlite.connect(DB_PATH) as db:
        async with db.cursor() as cur:
            await cur.execute("""SELECT name, id, url FROM webhook""")
            res: list[Row] = await cur.fetchall()
            if res is None or len(res) == 0:
                return []
            for entry in res:
                webhook: Webhook = {"name": entry["name"], "url": entry["url"], "id": entry["id"]}
                _webhooks.append(webhook)
                # _webhooks.append({"name": entry["name"], "url": entry["url"], "id": entry["id"]})
        await db.close()
        return _webhooks


# TODO - Create a JUMP URL to the last string of messages posted.


class Reddit_IS(cog.KumaCog):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot=bot)
        self._name: str = os.path.basename(__file__).title()
        self.logger.info(f"**SUCCESS** Initializing {self._name}")
        self._file_dir: Path = Path(__file__).parent
        self._message_timeout = 120

        self._interrupt_loop: bool = False
        self._delay_loop: float = 1
        self._loop_iterations: int = 0

        self._sessions = aiohttp.ClientSession()

        # Used to possible keep track of edits to the DB to prevent un-needed DB lookups on submissions.
        self._recent_edit: bool = False

        # Image hash and Url storage
        self._json: Path = self._file_dir.joinpath("reddit.json")
        self._url_list: list = []  # []  # Recently sent URLs
        self._hash_list: list = []  # []  # Recently hashed images
        self._url_prefixes: tuple[str, ...] = ("http://", "https://")

        # Edge Detection comparison.
        self._array_bin: Path = self._file_dir.joinpath("reddit_array.bin")
        self._pixel_cords_array: list[bytes] = []
        self.IMAGE_COMP = ImageComp.Image_Comparison()

        # This is how many posts in each subreddit the script will look back.
        # By default the subreddit script looks at subreddits in `NEW` listing order.
        self._submission_limit = 30
        self._subreddits: list[Union[Any, dict[str, Union[str, None]]]] = []  # {"subreddit" : "webhook_url"}

        self._last_check: datetime = datetime.now(tz=timezone.utc)

        # This forces the timezone to change based upon your OS for better readability in your prints()
        # This script uses `UTC` for functionality purposes.
        self._system_tz = tzlocal.get_localzone()

        # Need to use the string representation of the time zone for `pytz` eg. `America/Los_Angeles`
        self._pytz = pytz.timezone(str(self._system_tz))

        # Purely used to fill out the user_agent parameter of PRAW
        self._sys_os: str = sys.platform.title()
        # Used by URL lib for Hashes.
        self._User_Agent = UserAgent().chrome

        # Default value; change in `reddit_cog.ini`
        self._user_name: Union[str, None] = "Reddit Scrapper"

        # Comparison placeholders through Emojis
        self._reaction_compare_urls: list[str] = []

    @commands.Cog.listener("on_reaction_add")
    async def on_reaction_compare(self, reaction: discord.Reaction, user: discord.User):
        """
        Using the :heavy_check_mark: as a reaction on two images will compare them.

        Args:
            reaction (discord.Reaction): Discord Reaction object
            user (discord.User): Discord user object
        """
        self.logger.info(self._reaction_compare_urls)
        if isinstance(reaction.emoji, str):
            if reaction.emoji == "\u2714\ufe0f":
                res: list[str] = reaction.message.content.split("\n")
                self._reaction_compare_urls.append(res[-1])
                await reaction.message.remove_reaction(emoji="\u2714\ufe0f", member=user)

        if len(self._reaction_compare_urls) >= 2:
            await self._compare_urls(url_one=self._reaction_compare_urls[0], url_two=self._reaction_compare_urls[1])
            await reaction.message.channel.send(
                content=f"{user.mention}\n**URL One**:{self._reaction_compare_urls[0]}\n**URL Two**:{self._reaction_compare_urls[1]}\n**Results:**{self.IMAGE_COMP.results}",
                delete_after=15,
            )
            self._reaction_compare_urls = []

    async def cog_load(self) -> None:
        """Creates Sqlite Database if not present.

        Gets settings from`reddit_cog.ini`

        Creates`reddit.json`if not present and Gets URL and Hash lists.

        Creates our _subreddit list.
        """
        # self._logger.setLevel("DEBUG")
        # Setup our DB tables
        async with asqlite.connect(DB_PATH) as db:
            await db.execute(SUBREDDIT_SETUP_SQL)
            await db.execute(WEBHOOK_SETUP_SQL)
        # Grab our PRAW settings

        await self._ini_load()
        # Grab our hash/url DB
        try:
            self._last_check = self.json_load()
        except:
            self._last_check: datetime = datetime.now(tz=timezone.utc)

        self._subreddits = await _get_all_subreddits()
        # Load our array data.
        await self._read_array()
        # If for some reason our session is closed on cog load.
        # lets re-open a new session.
        if self._sessions.closed:
            self._sessions = aiohttp.ClientSession()

        if self.check_loop.is_running() is False:
            self.check_loop.start()

    async def cog_unload(self) -> None:
        """
        Saves our URL and hash list.\n
        Stops our scrape loop if running and closes any open connections.
        """
        self.json_save()
        await self._save_array()
        await self._reddit.close()
        await self._sessions.close()
        if self.check_loop.is_running():
            self.check_loop.cancel()

    async def _ini_load(self) -> None:
        """
        Gets the Reddit login information and additional settings.
        """
        _setting_file: Path = self._file_dir.joinpath("reddit_cog.ini")
        if _setting_file.is_file():
            settings = ConfigParser(converters={"list": lambda setting: [value.strip() for value in setting.split(",")]})
            settings.read(_setting_file.as_posix())
            # PRAW SETTINGS
            _reddit_secret: str = settings.get("PRAW", "reddit_secret")
            _reddit_client_id: str = settings.get("PRAW", "reddit_client_id")
            _reddit_username: str = settings.get("PRAW", "reddit_username")
            _reddit_password: str = settings.get("PRAW", "reddit_password")

            # CONFIG
            _temp_name = settings.get("CONFIG", "username")
            if len(_temp_name):
                self._user_name = _temp_name

            self._reddit = asyncpraw.Reddit(
                client_id=_reddit_client_id,
                client_secret=_reddit_secret,
                password=_reddit_password,
                user_agent=f"by /u/{_reddit_username})",
                username=_reddit_username,
            )
        else:
            raise ValueError("Failed to load .ini")

    def json_load(self) -> datetime:
        """
        Loads our last_check, url_list and hash_list from a json file; otherwise creates the file and sets our intial last_check time.

        Returns:
            datetime: The last check unix timestamp value from the json file.
        """
        last_check: datetime = datetime.now(tz=timezone.utc)

        if self._json.is_file() is False:
            with open(self._json, "x") as jfile:
                jfile.close()
                self.json_save()
                return last_check

        else:
            with open(self._json) as jfile:
                data = json.load(jfile)
                # self._logger.info('Loaded our settings...')

        if "last_check" in data:
            if data["last_check"] == "None":
                last_check = datetime.now(tz=timezone.utc)
            else:
                last_check = datetime.fromtimestamp(data["last_check"], tz=timezone.utc)
            # self._logger.info('Last Check... Done.')

        if "url_list" in data:
            self._url_list = list(data["url_list"])
            # self._logger.info('URL List... Done.')

        if "hash_list" in data:
            self._hash_list = list(data["hash_list"])
            # self._logger.info('Hash List... Done.')

        jfile.close()
        return last_check

    def json_save(self) -> None:
        """
        I generate an upper limit of the list based upon the subreddits times the submission search limit;
        this allows for configuration changes and not having to scale the limit of the list
        """
        _temp_url_list: list[str] = []
        _temp_hash_list: list[str] = []

        limiter: int = (len(self._subreddits) * self._submission_limit) * 3

        # Turn our set into a list, truncate it via indexing then replace our current set.
        if len(self._url_list) > limiter:
            # 'Trimming down url list...'
            _temp_url_list = self._url_list
            _temp_url_list = _temp_url_list[len(self._url_list) - limiter :]
            self._url_list = _temp_url_list

        if len(self._hash_list) > limiter:
            # 'Trimming down hash list...
            _temp_hash_list = self._hash_list
            _temp_hash_list = _temp_hash_list[len(self._hash_list) - limiter :]
            self._hash_list = _temp_hash_list

        data = {"last_check": self._last_check.timestamp(), "url_list": self._url_list, "hash_list": self._hash_list}
        with open(self._json, "w") as jfile:
            json.dump(data, jfile)
            # 'Saving our settings...'
            jfile.close()

    async def autocomplete_subreddit(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        res: list[str | Any | dict[str, str | None]] = await _get_all_subreddits(webhooks=False)
        return [
            app_commands.Choice(name=subreddit, value=subreddit)
            for subreddit in res
            if type(subreddit) == str and current.lower() in subreddit.lower()
        ][:25]

    async def autocomplete_webhook(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        res: list[None | Webhook] = await _get_all_webhooks()
        return [
            app_commands.Choice(name=webhook["name"], value=webhook["name"])
            for webhook in res
            if webhook is not None and current.lower() in webhook["name"].lower()
        ][:25]

    async def autocomplete_submission_type(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        types: list[str] = ["New", "Top", "Hot"]
        return [app_commands.Choice(name=entry, value=entry) for entry in types if current.lower() in entry.lower()]

    @tasks.loop(minutes=5, reconnect=True)
    async def check_loop(self) -> None:
        if self._recent_edit:
            self._subreddits = []
            self._subreddits = await _get_all_subreddits()
            self._recent_edit = False

        if self._subreddits == None:
            self.logger.warning("No Subreddits found...")
            return
        try:
            # cur_loop = asyncio.get_event_loop()
            # cur_loop.run_in_executor
            # asyncio.run_coroutine_threadsafe(coro=self.subreddit_media_handler(last_check=self._last_check), loop= asyncio.get_event_loop())
            result: Coroutine[Any, Any, int] = await asyncio.to_thread(self.subreddit_media_handler, self._last_check)
            (count,) = await asyncio.gather(result)
            # count: int = await self.subreddit_media_handler(last_check=self._last_check)
        except Exception as e:
            count = 0
            self.logger.error(f"{type(e)} -> {e}")

        self._last_check = datetime.now(tz=timezone.utc)
        self.json_save()
        await self._save_array()
        if count >= 1:
            self.logger.info(f"Finished Sending {str(count) + ' Images' if count > 1 else str(count) + ' Image'}")
        else:
            self.logger.info("No new Images to send.")

    async def process_subreddit_submissions(
        self, sub: str, order_type: str, count: None | int = None, last_check: None | datetime = None
    ) -> list[dict[asyncpraw.reddit.Submission, str]]:
        img_url_to_send: list[dict[asyncpraw.reddit.Submission, str]] = []
        res = None

        if count == None:
            count = self._submission_limit

        cur_subreddit: Subreddit = await self._reddit.subreddit(display_name=sub, fetch=True)
        # limit - controls how far back to go (true limit is 100 entries)
        self.logger.debug(f"order_type: {order_type} | count: {count} | sub: {sub}")
        if order_type.lower() == "new":
            res = cur_subreddit.new(limit=count)
        elif order_type.lower() == "hot":
            res = cur_subreddit.hot(limit=count)
        elif order_type.lower() == "top":
            res = await cur_subreddit.top(limit=count)

        if res is None:
            return img_url_to_send

        # async for submission in cur_subreddit.new(limit=count):
        async for submission in res:
            post_time: datetime = datetime.fromtimestamp(submission.created_utc, tz=timezone.utc)
            if last_check != None and post_time < last_check:
                continue

            self.logger.info(
                f"Checking subreddit {sub} {submission} -> submission title: {submission.title} url: {submission.url} submission post_time: {post_time.astimezone(self._pytz).ctime()} last_check: {last_check.astimezone(self._pytz).ctime() if last_check is not None else 'None'}"
            )
            if hasattr(submission, "url") and getattr(submission, "url").lower().find("gallery") != -1:
                self.logger.info(f"- {submission.title} -> Found a gallery url, getting the image urls.")
                # img_url_to_send.extend({submission: await self._convert_gallery_submissions(submission=submission)})
                img_url_to_send.extend([
                    {submission: entry} for entry in await self._convert_gallery_submissions(submission=submission)
                ])

            # Usually submissions with multiple images will be using this `attr`
            elif hasattr(submission, "media_metadata"):
                self.logger.info(f"- {submission.title} -> Found a media_metadata attribute.")
                # img_url_to_send.extend(await self._get_media_metadata_urls(submission=submission))
                img_url_to_send.extend([
                    {submission: entry} for entry in await self._get_media_metadata_urls(submission=submission)
                ])

            elif hasattr(submission, "url_overridden_by_dest"):
                self.logger.info(f"- {submission.title} has attribute url_overridden_by_dest, getting the url.")
                if submission.url_overridden_by_dest.startswith(self._url_prefixes):
                    img_url_to_send.append({submission: submission.url_overridden_by_dest})

        return img_url_to_send

    async def subreddit_media_handler(self, last_check: datetime) -> int:
        """Iterates through the subReddits Submissions and sends media_metadata"""
        count = 0
        img_url: Union[str, None] = None

        if self._sessions.closed:
            self._sessions = aiohttp.ClientSession()

        self.logger.debug(
            msg=f"subreddit_media_handler starting.. | Delay {self._delay_loop} | # of subreddits {len(self._subreddits)}"
        )
        # We check self._subreddits in `check_loop`
        for entry in self._subreddits:
            for sub, webhook_url in entry.items():
                self.logger.debug(msg=f"Looking @ {sub} with {webhook_url}")
                if webhook_url == None:
                    self.logger.warning(msg=f"No Webhook URL for {sub}, skipping...")
                    continue

                if self._interrupt_loop == True:
                    self._interrupt_loop = False
                    self.logger.warning(msg="The Media Handler loop was interrupted.")
                    return count

                res: int = await self.check_subreddit(subreddit=sub)
                self.logger.debug(msg=f"Check Subreddit | Status Code {res}")
                if res != 200:
                    self.logger.warning(msg=f"Failed to find the subreddit /r/{sub}, skipping entry. Value: {res}")
                    continue

                submissions: list[dict[asyncpraw.reddit.Submission, str]] = await self.process_subreddit_submissions(
                    sub=sub, last_check=last_check, order_type="New"
                )
                self.logger.debug(msg=f"# of possible image submissions {len(submissions)}")
                for sub_entry in submissions:
                    for submission, img_url in sub_entry.items():
                        if img_url in self._url_list:
                            continue

                        if img_url.lower().find("gifs") != -1:
                            self.logger.info(msg=f"Found a gif URL -> {img_url}")
                            continue

                        img_res: ClientResponse | Literal[False] = await self._get_url_req(img_url=img_url)
                        if isinstance(img_res, ClientResponse):
                            img_data: bytes = await img_res.read()
                        else:
                            continue

                        self.logger.debug(msg=f"Checking the hash of {img_url}")
                        hash_res: bool = await self.hash_process(data=img_data)
                        self.logger.debug(msg=f"Checking Image Edge Array...{img_url}")
                        #
                        img_info: ImageInfo = await self._partial_edge_comparison(img_url=img_url, img_data=img_data)

                        if hash_res or img_info.edge_res == True:
                            self.logger.debug(msg=f"Duplicate check failed | hash - {hash_res} | edge - {img_info.edge_res}")
                            continue

                        else:
                            self._url_list.append(img_url)
                            count += 1
                            # TODO - Use markdown formatting to turn img URL into a shorter clickable link.
                            formatted_content: str = f"**r/{sub}** ->  __[{submission.title}]({submission.url})__\n[{img_info.width}x{img_info.height}]\n"
                            await self.webhook_send(url=webhook_url, content=formatted_content)
                            # Soft buffer delay between sends to prevent rate limiting.
                            await asyncio.sleep(delay=self._delay_loop)

        self.logger.debug("subreddit_media_handler ending")
        return count

    async def _get_submission_by_id(self, id: str):
        """
        Test function for getting individual submissions. Used for dev.

        Args:
            id (str): _description_
        """
        res = await self._reddit.submission(id=id)
        # print(type(res), dir(res))
        # pprint(res.__dict__["crosspost_parent_list"][0]["media_metadata"])
        # for entry in dir(res):
        #     pprint(f"attr:{entry} -> {getattr(res, entry)}")

    async def _partial_edge_comparison(self, img_url: str, img_data: bytes) -> ImageInfo:
        """
        Uses aiohttp to `GET` the image url, we modify/convert the image via PIL and get the edges as a binary string.\n
        We take a partial of the edges binary string and see if the blobs exists in `_pixel_cords_array`. \n

        If a partial match is found; we run `full_edge_comparison`. If no full match found; we add the edges to `_pixel_cords_array`.

        Args:
            img_url (str): Web url to Image.
            img_data (bytes): Image data to check.

        Returns:
            ImageInfo: Returns an ImageInfo dataclass to access the image properties and edge results.
        """
        img_info: ImageInfo = ImageInfo()
        stime: float = time.time()
        source: Image = IMG.open(fp=io.BytesIO(initial_bytes=img_data))
        img_info.width = source.width
        img_info.height = source.height
        source = self.IMAGE_COMP._convert(image=source)
        res_image: tuple[Image, Image | None] = self.IMAGE_COMP._image_resize(source=source)
        source = self.IMAGE_COMP._filter(image=res_image[0])
        edges: list[tuple[int, int]] | None = self.IMAGE_COMP._edge_detect(image=source)
        if edges == None:
            self.logger.error(msg=f"Found no edges for url -> {img_url}")
            return img_info

        b_edges: bytes = xy_binfind.struct_pack(edges=edges)
        # We set our max match req to do a full comparison via len of edges. eg. 500 * 10% (50 points)
        # We also set our min match req before we "abort"             eg. (50 * 90%) / 100 (40 points)
        # Track our failures and break if we exceed the diff.                    eg. 50-40 (10 points)
        match_req: int = int((len(b_edges) / self.IMAGE_COMP.sample_percent))
        min_match_req: int = int((match_req * self.IMAGE_COMP.match_percent) / 100)
        for (
            array
        ) in self._pixel_cords_array:  # list[b'\x07\x00\x08\x00\x02\x00\x02\x00', b'\x07\x00\x08\x00\x02\x00\x02\x00']
            sample: int = xy_binfind.find(haystack=array, needles=b_edges, skip=40, failcount=(match_req - min_match_req))
            if sample == -1:
                self.logger.debug(
                    msg=f"{img_url} Match count {sample}/{min_match_req} | Failure"
                )  # count {failures}/{match_req-min_match_req}")  # \n Looking for {b_edges[cords:cords + 4]} in {array}")
                break
            self.logger.debug(msg=f"--> Running full edge comparison on {img_url} | {len(b_edges)}")
            match: int = await self._full_edge_comparison(array=array, edges=b_edges)
            if match == False:
                break
            else:
                return img_info

        self._pixel_cords_array.append(b_edges)
        self._etime: float = time.time() - stime
        img_info.edge_res = False
        return img_info

    async def _full_edge_comparison(self, array: bytes, edges: bytes) -> bool:
        """
        Similar to `partial_edge_comparison` but we use the full list of the image edges against our array

        Args:
            array (bytes): A binary representation of pixel cords.
            edges (bytes): A binary representation of pixel cords.

        Returns:
            bool: Returns `False` if the binary blob is not in the array else `True` if the binary blob is in the array.
        """
        # failures: int = 0
        # match_count: int = 0
        match_req: int = int((len(edges) / self.IMAGE_COMP.match_percent) / 100)
        min_match_req: int = len(edges) - match_req
        matched = xy_binfind.find(haystack=array, needles=edges, failcount=min_match_req)
        # for pos in range(2, len(edges), 4):
        #    if match_count >= min_match_req:
        #        return True
        #    elif failures > min_match_req:
        #        return False
        #    elif array.find(edges[pos:pos + 4]) == -1:
        #        failures += 1
        #    else:
        #        match_count += 1
        if matched != -1:
            return True
        return False

    async def _get_media_metadata_urls(self, submission: asyncpraw.reddit.Submission) -> list[str]:
        """
        Checks the asyncpraw reddit Submission object for the "media_metadata" attribute; validates it is a dictionary and iterates through the url keys returning a list of urls to send.

        Args:
            submission (asyncpraw.reddit.Submission): The subreddit Submission.

        Returns:
            list(str): Image urls.
        """
        _urls: list[str] = []
        self.logger.info(f"-- {submission.title} has attribute media_metadata, getting urls")
        if hasattr(submission, "media_metadata"):
            res = getattr(submission, "media_metadata")
            if isinstance(res, dict):
                for key, img in res.items():
                    # for key, img in submission.media_metadata.items():
                    # example {'status': 'valid', 'e': 'Image', 'm': 'image/jpg', 'p': [lists of random resolution images], 's': See below..}
                    # This allows us to only get Images.
                    if "e" in img and img["e"] == "Image":
                        # example 's': {'y': 2340, 'x': 1080, 'u': 'https://preview.redd.it/0u8xnxknijha1.jpg?width=1080&format=pjpg&auto=webp&v=enabled&s=04e505ade5889f6a5f559dacfad1190446607dc4'}, 'id': '0u8xnxknijha1'}
                        # img_url = img["s"]["u"]
                        _urls.append(img["s"]["u"])
                        continue
        return _urls

    async def _convert_gallery_submissions(self, submission: asyncpraw.reddit.Submission) -> list[str]:
        """
        Take's a gallery url asyncpraw's Submission object and checks its `__dict__` attribute as it contains the "media_metadata" key we need to get all the urls, just nested in another dictionary under "crosspost_parent_list".
        We use a setattr to add `__dict__["crosspost_parent_list"][0]["media_metadata"]` as an attribute to Submission as `Submission.media_metadata`

        Args:
            img_url (str): The URL to parse.

        Returns:
            list[str]: Returns a list of IMG urls it finds inside the `Gallery`.
        """
        _urls: list[str] = []
        if hasattr(submission, "url") and getattr(submission, "url").lower().find("gallery") != -1:
            res: dict = getattr(submission, "__dict__")
            if isinstance(res, dict) and "crosspost_parent_list" in res:
                data: dict = res["crosspost_parent_list"][0]["media_metadata"]
                # ["crosspost_parent_list"][0]["media_metadata"]
                # We know inside the __dict__["crosspost_parent_list"][0] attribute that it may have a key value "media_metadata"
                # we are going to "spoof" the objects attribute to use in the _get_media_metadata_urls() by setting it from the __dict__ key.
                setattr(submission, "media_metadata", data)
                _urls = await self._get_media_metadata_urls(submission=submission)

            # Edge case; some gallery's apparently have a proper "media_metadata" url.
            elif hasattr(submission, "media_metadata"):
                _urls = await self._get_media_metadata_urls(submission=submission)
        # self._logger.info(len(_urls))
        return _urls

    async def _get_url_req(self, img_url: str, ignore_validation: bool = False) -> ClientResponse | Literal[False]:
        """
        Calls a `.get()` method to get the image data.

        Args:
            img_url (str): Web URL for the image.
            ignore_validation (bool): Ignore header check on web request for `image`. Defaults to `False`

        Returns:
            ClientResponse | Literal[False]: Returns `ClientResponse` if the url returns a status code between `200-299`. Otherwise `False` on any other case.
        """
        req: ClientResponse = await self._sessions.get(url=img_url)

        if 199 >= req.status > 299:
            self.logger.error(f"Unable to handle {img_url} ||  status code: {req.status}")
            return False

        # Ignore all further validation as we just want the web request.
        if ignore_validation == True:
            return req

        # if 'image' in req_open.headers.get_content_type():
        if "Content-Type" not in req.headers:
            self.logger.error(f"Unable to find the Content-Type for {img_url}")
            return False

        if "image" in req.headers["Content-Type"]:
            return req

        else:  # Failed to find a 'image'
            self.logger.warning(f"URL: {img_url} is not an image -> {req.headers}")
            return False

    async def _struct_pack(self, array: list[tuple[int, int]]) -> bytes:
        """
        Converts our list of tuples into a binary blob.

        Args:
            array (list[tuple[int, int]]): The list of (X,Y) cords as tuples.

        Returns:
            bytes: Returns a binary representation of all the edges as a blob. \n
            `list[tuple(7,8),tuple(2,2)]` -> list[b'(\x07\x00,\x08\x00),(\x02\x00,\x02\x00)']
        """
        var: bytes = b""
        # We need to store our array len, we multiple it by two since we are storing tuples.
        # list[b'(\x07\x00,\x08\x00),(\x02\x00,\x02\x00)'] = list[tuple(7,8),tuple(2,2)] # 4 bytes per tuple()
        # len of the array would be 2, but times by 2 to decern total number of values (2 values per tuple())
        # len in b"(\x04\x00)" for our comment tuple example.
        # var += struct.pack("<H", (len(array) * 2))
        var += struct.pack("<I", (len(array) * 2))
        for tuple in array:
            # 0x makes it a Hex
            # < = little endian
            # > = big endian
            # H = 2 byte integer (aka half-word)
            # I = 4 byte integer
            # Up to 32 bits in INT (Each value is 8 bits.))
            var += struct.pack("<HH", tuple[0], tuple[1])
        return var

    async def _save_array(self) -> None:
        """
        Saves the list of bytes to `reddit_array.bin`\n
        We truncate the list depending on the length of subreddits and submission limits.

        """
        limiter: int = (len(self._subreddits) * self._submission_limit) * 3
        self.logger.debug(
            f"The length of our pixel cords array before limiting {len(self._pixel_cords_array)} | limiter: {limiter}"
        )
        while len(self._pixel_cords_array) > limiter:
            self._pixel_cords_array.pop(0)

        self.logger.debug(f"pixel cords array {len(self._pixel_cords_array)}")
        for entry in self._pixel_cords_array:
            self.logger.debug(f"length of bytes {len(entry)}")

        data: bytes = b"".join(self._pixel_cords_array)
        self.logger.debug(f"Writing our Pixel Array to `reddit_array.bin` || bytes {len(data)}")

        my_file: io.BufferedWriter = open(self._array_bin, "wb")
        my_file.write(data)
        my_file.close()

    async def _read_array(self) -> None:
        """
        Reads from `reddit_array.bin`\n
        Makes the file if it doesn't exist in `file_dir`
        """
        if self._array_bin.is_file() is False:
            cur_file: io.BufferedWriter = open(self._array_bin, "xb")
            cur_file.close()

        b_file: io.BufferedReader = open(self._array_bin, "rb")
        data: bytes = b_file.read()
        self.logger.debug(f"Loading array from file | bytes {len(data)}")
        b_file.close()
        # We unpack our array len from the previous comment resulting in 4 or b"(\x04\x00)"
        # we *2 to account for 2 bytes per value stored, 2 values go into a single tuple() or tuple(b"\x04\x00", b"\x04\x02")
        self._pixel_cords_array = []
        total_pos = 0
        while total_pos < len(data):
            # struct.unpack returns a tuple of the unpacked data, we are using the first 4 bytes to store an array count.
            # we multiple by 2 to get true length as we are storing 2 bytes per value.
            cord_len: int = struct.unpack("<I", data[total_pos : total_pos + 4])[0] * 2
            self.logger.debug(f"cord length {cord_len}")

            # We increment total_pos +4 to pass our array len blob.
            self._pixel_cords_array.append(data[total_pos : total_pos + cord_len + 4])
            total_pos += cord_len + 4
        self.logger.debug(f"Reading our Array File... | total entries {len(self._pixel_cords_array)}")

    async def hash_process(self, data: bytes) -> bool:
        """
        Checks the sha256 of the supplied image data against our `_hash_list`.

        Args:
            data (bytes): The image data.

        Returns:
            bool: Returns `False` if the sha256 results DO NOT exist in `_hash_list`.\n
            Otherwise returns `True` if the sha256 results already exist in our `_hash_list` or it failed to hash the `img_url` parameter.
        """

        my_hash: str = hashlib.sha256(string=data).hexdigest()

        if my_hash not in self._hash_list:
            self._hash_list.append(my_hash)
            return False

        else:
            return True

    async def webhook_send(self, url: str, content: str) -> None:
        """
        Sends content to the Discord Webhook url provided. \n
        Implements a dynamic delay based upon the status code when sending a webhook.\n
            - If status code is `429` the delay between posts will be incremented by `.1` seconds.
            - After 10 successful sends it will remove `.1` seconds from the delay.

        Args:
            url(str): The Webhook URL to use.
            content(str): The content to send to the url.
        Returns:
            None: Returns `None`
        """
        # None: Returns `False` due to status code not in range `200 <= result.status < 300` else returns `None`
        # Could check channels for NSFW tags and prevent NSFW subreddits from making it to those channels/etc.
        data = {"content": content, "username": self._user_name}
        result: ClientResponse = await self._sessions.post(url, json=data)
        if 200 <= result.status < 300:
            return

        else:
            # Attempting to dynamically adjust the delay in sending messages.
            if result.status == 429:
                self._delay_loop += 0.1

            self._loop_iterations += 1
            if self._loop_iterations > 10:
                self._delay_loop -= 0.1
                self._loop_iterations = 0

            self.logger.warning(
                f"Webhook not sent with {result.status} - delay {self._delay_loop}s, response:\n{result.json()}"
            )
            return

    async def check_subreddit(self, subreddit: str) -> int:
        """
        Attempts a `HEAD` request of the passed in subreddit.

        Args:
            subreddit(str): The subreddit to check. Do not include the `/r/`. eg `NoStupidQuestions`
        Returns:
            int: Returns status code
        Except:
            TimeoutError: Returns status code `400` if the aiohttp.ClientResponse takes longer than 5 seconds to respond.
            Exception(Any): Returns status code `400` if the aiohttp.ClientResponse fails.
        """
        # If for some reason our session is closed.
        # lets re-open a new session.
        if self._sessions.closed:
            self._sessions = aiohttp.ClientSession()

        self.logger.debug(f"Checking subreddit {subreddit}")
        _url: str = "https://www.reddit.com/r/"
        if subreddit.startswith(_url):
            temp_url = subreddit
        else:
            temp_url: str = _url + subreddit

        try:
            # We are having issues with aiohttp.ClientResponse when using `.head()`; we are going to forcibly time it out after 5 seconds.
            async with asyncio.timeout(delay=5):
                check: ClientResponse = await self._sessions.head(url=temp_url, allow_redirects=True)

        except TimeoutError:
            self.logger.error(f"Timed out checking {subreddit} | Returning status code 400")
            return 400

        except Exception as e:
            self.logger.error(f"Failed to check {subreddit} | Response returned {e}")
            return 400

        return check.status

    async def _compare_urls(self, url_one: str, url_two: str):
        """
        Takes two Image URLs and turns them into PIL Images for comparison.
        See `self.IMAGE_COMP`

        Args:
            url_one (str): image url
            url_two (str): image url
        """
        res_one: ClientResponse | Literal[False] = await self._get_url_req(img_url=url_one)
        res_two: ClientResponse | Literal[False] = await self._get_url_req(img_url=url_two)
        if res_one and res_two != False:
            img_one: Image = IMG.open(io.BytesIO(await res_one.read()))
            img_two: Image = IMG.open(io.BytesIO(await res_two.read()))
            self.IMAGE_COMP.compare(source=img_one, comparison=img_two)

    @commands.hybrid_command(help="Retrieves a subreddits X number of Submissions")
    @app_commands.describe(sub="The subreddit name.")
    @app_commands.describe(count="The number of submissions to retrieve, default is 5.")
    @app_commands.describe(order_type="Either `New, Hot, Best or Top`")
    @app_commands.autocomplete(order_type=autocomplete_submission_type)
    async def get_subreddit(
        self, context: commands.Context, sub: str, order_type: str = "new", count: app_commands.Range[int, 0, 100] = 5
    ):
        status: int = await self.check_subreddit(subreddit=sub)
        if status != 200:
            return await context.send(
                content=f"Unable to find the subreddit `/r/{sub}`.\n *Status code: {status}*",
                delete_after=self._message_timeout,
            )

        res: list[dict[asyncpraw.reddit.Submission, str]] = await self.process_subreddit_submissions(
            sub=sub, order_type=order_type, count=count
        )
        self.logger.info(len(res))
        for entry in res:
            for submission, url in entry.items():
                await context.send(content=f"**r/{sub}** ->  __[{submission.title}]({submission.url})__\n{url}\n")
        return await context.send(content=f"Finished sending {len(res)} submissions from **r/{sub}**")

    @commands.hybrid_command(help="Add a subreddit to the DB", aliases=["rsadd", "rsa"])
    @app_commands.describe(sub="The subreddit name or full url https://www.reddit.com/r/subreddit_here")
    @app_commands.default_permissions(manage_guild=True)
    async def add_subreddit(self, context: commands.Context, sub: str):
        status: int = await self.check_subreddit(subreddit=sub)
        if status != 200:
            return await context.send(
                content=f"Unable to find the subreddit `{sub if sub.startswith('https://www.reddit.com/r/') else f'/r/{sub}'}`.\n *Status code: {status}*",
                delete_after=self._message_timeout,
            )

        res: Row | None = await _add_subreddit(name=sub)

        if res is not None:
            self._recent_edit = True
            return await context.send(
                content=f"Added `{sub if sub.startswith('https://www.reddit.com/r/') else f'/r/{sub}'}` to our database.",
                delete_after=self._message_timeout,
            )
        else:
            return await context.send(
                content=f"Unable to add `{sub if sub.startswith('https://www.reddit.com/r/') else f'/r/{sub}'}` to the database.",
                delete_after=self._message_timeout,
            )

    @commands.hybrid_command(help="Remove a subreddit from the DB", aliases=["rsdel", "rsd"])
    @app_commands.describe(sub="The sub Reddit name.")
    @app_commands.autocomplete(sub=autocomplete_subreddit)
    @app_commands.default_permissions(manage_guild=True)
    async def del_subreddit(self, context: commands.Context, sub: str):
        res: int | None = await _del_subreddit(name=sub)
        self._recent_edit = True
        await context.send(content=f"Removed {res} {'subreddit' if res else ''}", delete_after=self._message_timeout)

    @commands.hybrid_command(help="Update a subreddit with a Webhook", aliases=["rsupdate", "rsu"])
    @app_commands.describe(sub="The sub Reddit name.")
    @app_commands.autocomplete(sub=autocomplete_subreddit)
    @app_commands.autocomplete(webhook=autocomplete_webhook)
    @app_commands.default_permissions(manage_guild=True)
    async def update_subreddit(self, context: commands.Context, sub: str, webhook: str):
        res: Row | None = await _update_subreddit(name=sub, webhook=webhook)
        if res is None:
            return await context.send(content=f"Unable to update `/r/{sub}`.", delete_after=self._message_timeout)
        else:
            self._recent_edit = True
            return await context.send(content=f"Updated `/r/{sub}` in our database.", delete_after=self._message_timeout)

    @commands.hybrid_command(help="List of subreddits", aliases=["rslist", "rsl"])
    async def list_subreddit(self, context: commands.Context):
        res: list[Any | dict[str, str]] = await _get_all_subreddits()
        temp_list = []
        for entry in res:
            for name, webhook in entry.items():
                emoji = "\U00002705"  # White Heavy Check Mark
                if webhook is None:
                    emoji = "\U0000274c"  # Negative Squared Cross Mark
                sub_entry = f"{emoji} - **/r/**`{name}`"
                temp_list.append(sub_entry)

        return await context.send(
            content=f"**__Current Subreddit List__**(total: {len(temp_list)}):\n" + "\n".join(temp_list)
        )

    @commands.hybrid_command(help="Info about a subreddit", aliases=["rsinfo", "rsi"])
    @app_commands.describe(sub="The sub Reddit name.")
    @app_commands.autocomplete(sub=autocomplete_subreddit)
    async def info_subreddit(self, context: commands.Context, sub: str):
        res: Row | None = await _get_subreddit(name=sub)
        if res is not None:
            wh_res: Row | None = await _get_webhook(arg=res["webhook_id"])
            if wh_res is not None:
                return await context.send(
                    content=f"**Info on Subreddit /r/`{sub}`**\n> __Webhook Name__: {wh_res['name']}\n> __Webhook ID__: {wh_res['id']}\n> {wh_res['url']}",
                    delete_after=self._message_timeout,
                )
            else:
                return await context.send(
                    content=f"**Info onSubreddit /r/`{sub}`**\n`No webhook assosciated with this subreddit`",
                    delete_after=self._message_timeout,
                )
        else:
            return await context.send(
                content=f"Unable to find /r/`{sub} in the database.", delete_after=self._message_timeout
            )

    @commands.hybrid_command(help="Add a webhook to the database.", aliases=["rswhadd", "rswha"])
    @app_commands.default_permissions(manage_webhooks=True)
    async def add_webhook(self, context: commands.Context, webhook_name: str, webhook_url: str):
        data: str = f"Testing webhook {webhook_name}"
        success: bool | None = await self.webhook_send(url=webhook_url, content=data)
        if success == False:
            return await context.send(content=f"Failed to send a message to `{webhook_name}` via url. \n{webhook_url}")

        res: Row | None = await _add_webhook(name=webhook_name, url=webhook_url)
        if res is not None:
            self._recent_edit = True
            return await context.send(
                content=f"Added **{webhook_name}** to the database. \n> `{webhook_url}`", delete_after=self._message_timeout
            )
        else:
            return await context.send(
                content=f"Unable to add {webhook_url} to the database.", delete_after=self._message_timeout
            )

    @commands.hybrid_command(help="Remove a webhook from the database.", aliases=["rswhdel", "rswhd"])
    @app_commands.autocomplete(webhook=autocomplete_webhook)
    @app_commands.default_permissions(manage_webhooks=True)
    async def del_webhook(self, context: commands.Context, webhook: str):
        res: int | None = await _del_webhook(arg=webhook)
        self._recent_edit = True
        await context.send(content=f"Removed {res} {'webhook' if res else ''}", delete_after=self._message_timeout)

    @commands.hybrid_command(help="List all webhook in the database.", aliases=["rswhlist", "rswhl"])
    @app_commands.default_permissions(manage_webhooks=True)
    async def list_webhook(self, context: commands.Context):
        res: list[Webhook | None] = await _get_all_webhooks()
        return await context.send(
            content="**Webhooks**:\n"
            + "\n".join([
                f"**{entry['name']}** ({entry['id']})\n> `{entry['url']}`\n" for entry in res if entry is not None
            ]),
            delete_after=self._message_timeout,
        )

    @commands.hybrid_command(help="Start/Stop the Scrapper loop", aliases=["rsloop"])
    @app_commands.default_permissions(administrator=True)
    async def scrape_loop(self, context: commands.Context, util: Literal["start", "stop", "restart"]):
        start: list[str] = ["start", "on", "run"]
        end: list[str] = ["stop", "off", "end"]
        restart: list[str] = ["restart", "reboot", "cycle"]
        status: str = ""

        if util in start:
            if self.check_loop.is_running():
                status = "already running."
            else:
                self.check_loop.start()
                status = "starting."

        elif util in end:
            if self.check_loop.is_running():
                self._interrupt_loop = True
                self.check_loop.stop()
                status = "stopping."
            else:
                status = "not currently running."

        elif util in restart:
            # Force cancel and start the loop. Clean up any open sessions.
            # Re open a session and start the loop again.
            self.check_loop.cancel()
            await self._sessions.close()
            self.check_loop.start()
            status = "restarting."

        else:
            return await context.send(
                content=f"You must use these words {','.join(start)} | {','.join(end)}", delete_after=self._message_timeout
            )
        return await context.send(content=f"The Scrapper loop is {status}", delete_after=self._message_timeout)

    @commands.hybrid_command(help="Sha256 comparison of two URLs", aliases=["sha256", "hash"])
    async def hash_comparison(self, context: commands.Context, url_one: str, url_two: str | None):
        failed: bool = False
        res: ClientResponse | Literal[False] = await self._get_url_req(img_url=url_one)
        if res == False:
            failed = True

        elif url_two is not None:
            res_two: ClientResponse | Literal[False] = await self._get_url_req(img_url=url_two)
            if res_two == False:
                failed = True
            elif res == res_two:
                return await context.send(content="The Images match!", delete_after=self._message_timeout)
            elif res != res_two:
                return await context.send(
                    content=f"The Images do not match.\n `{res}` \n `{res_two}`", delete_after=self._message_timeout
                )
        else:
            return await context.send(content=f"Hash: `{res}`", delete_after=self._message_timeout)

        if failed:
            return await context.send(content="Unable to hash the URLs provided.", delete_after=self._message_timeout)

    @commands.hybrid_command(help="Edge comparison of two URLs", aliases=["edge"])
    async def edge_comparison(self, context: commands.Context, url_one: str, url_two: str):
        await self._compare_urls(url_one=url_one, url_two=url_two)
        return await context.send(
            content=f"**URL One**:{url_one}\n**URL Two**:{url_two}\n**Results:**{self.IMAGE_COMP.results}"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Reddit_IS(bot))
