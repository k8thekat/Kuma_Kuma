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

import asyncio
import datetime
import io
import logging
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Optional, Union, Unpack

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from lemminflect import getInflection  # pyright: ignore[reportMissingTypeStubs, reportUnknownVariableType]

from .animation import AnimationFrames, AnimationStyle, AnimationTarget, KumaAnimation

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from aiohttp_client_cache.session import _ExpandedRequestOptions as AioHTTPRequestOptions

    from kuma_kuma import Kuma_Kuma

    from ._types import Metrics, Uptime

__all__ = ("ROTATE_WINDOW", "FFXIVResources", "KumaCog", "KumaEmojiTable", "KumaResources")

LOGGER = logging.getLogger(__name__)

#: Seconds a :meth:`KumaCog.rotate_pick` choice holds before it moves on to the next entry.
ROTATE_WINDOW: int = 3600


class PennPOS(StrEnum):
    CC = "CC"  # Coordinating conjunction (and, but, or)
    CD = "CD"  # Cardinal number (1, third)
    DT = "DT"  # Determiner (the, a, some)
    EX = "EX"  # Existential "there" (there is)
    FW = "FW"  # Foreign word
    IN = "IN"  # Preposition or subordinating conjunction (in, of, like, although)
    JJ = "JJ"  # Adjective (big, fast)
    JJR = "JJR"  # Adjective, comparative (bigger, faster)
    JJS = "JJS"  # Adjective, superlative (biggest, fastest)
    LS = "LS"  # List item marker (1., 2., A., B.)
    MD = "MD"  # Modal (could, will, would, should)
    NN = "NN"  # Noun, singular or mass (dog, music)
    NNS = "NNS"  # Noun, plural (dogs, tables)
    NNP = "NNP"  # Proper noun, singular (London, John)
    NNPS = "NNPS"  # Proper noun, plural (Vikings, Smiths)
    PDT = "PDT"  # Predeterminer (all, both, half)
    POS = "POS"  # Possessive ending ('s)
    PRP = "PRP"  # Personal pronoun (I, he, she, they)
    PRP_POSS = "PRP$"  # Possessive pronoun (my, his, her, their)
    RB = "RB"  # Adverb (quickly, never)
    RBR = "RBR"  # Adverb, comparative (faster, better)
    RBS = "RBS"  # Adverb, superlative (fastest, best)
    RP = "RP"  # Particle (up, off, out — as in "give up")
    SYM = "SYM"  # Symbol (%, &, +, =)
    TO = "TO"  # "to" (as infinitive marker or preposition)
    UH = "UH"  # Interjection (uh, well, yes, oops)
    VB = "VB"  # Verb, base form (run, eat)
    VBD = "VBD"  # Verb, past tense (ran, ate)
    VBG = "VBG"  # Verb, gerund or present participle (running, eating)
    VBN = "VBN"  # Verb, past participle (run, eaten)
    VBP = "VBP"  # Verb, non-3rd person singular present (run, eat)
    VBZ = "VBZ"  # Verb, 3rd person singular present (runs, eats)
    WDT = "WDT"  # Wh-determiner (which, that, whatever)
    WP = "WP"  # Wh-pronoun (who, what)
    WP_POSS = "WP$"  # Possessive wh-pronoun (whose)
    WRB = "WRB"  # Wh-adverb (where, when, why, how)


class UnicodeTable:
    """Unicode Library.

    .. note::
    - https://www.vertex42.com/ExcelTips/unicode-symbols.html

    __Current Attributes__
    ```
    middle_dot: str = "\U000030fb"
    em_dash: str = "\U0000fe31"
    double_vertical: str = "\U00002016"  # DOUBLE VERTICAL LINE - ‖ — ‖ — http://www.fileformat.info/info/unicode/char/2016
    right_arrow: str = "\U000021e2"  # RIGHTWARDS DASHED ARROW - ⇢ — ⇢ — http://www.fileformat.info/info/unicode/char/21e2
    colon: str = "\U00002236"  # RATIO - ∶ — ∶ — http://www.fileformat.info/info/unicode/char/2236  # noqa: RUF003
    right_triangle_arrow: str = "\U000022b3"  # CONTAINS AS NORMAL SUBGROUP - ⊳ — ⊳ — http://www.fileformat.info/info/unicode/char/22b3
    right_hook_arrow: str = "\U000021aa" # RIGHTWARDS ARROW WITH HOOK - ↪ — ↪ — http://www.fileformat.info/info/unicode/char/21aa
    star: str = "\U00002b50" # ⭐
    blank: str = "\u200b"
    inbox_tray: str = "\U0001f4e5" # :inbox_tray:
    loud_speaker: str = "\U0001f4e2" # : PUBLIC ADDRESS LOUDSPEAKER - 📢 — 📢 — http://www.fileformat.info/info/unicode/char/1f4e2
    no_entry: str = "\U000026d4" #: NO ENTRY - ⛔ — ⛔ — http://www.fileformat.info/info/unicode/char/26d4
    warning_sign: str = "\U000026a0" # : WARNING SIGN - ⚠ — ⚠ — http://www.fileformat.info/info/unicode/char/26a0
    speed_bubble: str = "\U0001f5e8" #: LEFT SPEECH BUBBLE - 🗨 — 🗨 — http://www.fileformat.info/info/unicode/char/1f5e8
    sun_behind_cloud: str = "\U000026c5"  # SUN BEHIND CLOUD - ⛅ — ⛅ — http://www.fileformat.info/info/unicode/char/26c5
    lotus: str = "\U0001fab7"  # LOTUS - 🪷 — 🪷 — http://www.fileformat.info/info/unicode/char/1fab7
    game_die: str = "\U0001f3b2"  # GAME DIE - 🎲 — 🎲 — http://www.fileformat.info/info/unicode/char/1f3b2
    video_game: str = "\U0001f3ae"  # VIDEO GAME - 🎮 — 🎮 — http://www.fileformat.info/info/unicode/char/1f3ae
    artist_palette: str = "\U0001f3a8"  # ARTIST PALETTE - 🎨 — 🎨 — http://www.fileformat.info/info/unicode/char/1f3a8
    identification_card: str = "\U0001faaa"  # IDENTIFICATION CARD - 🪪 — 🪪 — http://www.fileformat.info/info/unicode/char/1faaa
    bar_chart: str = "\U0001f4ca"  # BAR CHART - 📊 — 📊 — http://www.fileformat.info/info/unicode/char/1f4ca
    no_one_under_eighteen: str = "\U0001f51e"  # NO ONE UNDER EIGHTEEN SYMBOL - 🔞 — 🔞 — http://www.fileformat.info/info/unicode/char/1f51e
    ```
    """

    middle_dot: str = "\U000030fb"
    em_dash: str = "\U0000fe31"
    double_vertical: str = "\U00002016"  # DOUBLE VERTICAL LINE - ‖ — ‖ — http://www.fileformat.info/info/unicode/char/2016
    right_arrow: str = "\U000021e2"  # RIGHTWARDS DASHED ARROW - ⇢ — ⇢ — http://www.fileformat.info/info/unicode/char/21e2
    colon: str = "\U00002236"  # RATIO - ∶ — ∶ — http://www.fileformat.info/info/unicode/char/2236  # noqa: RUF003
    right_triangle_arrow: str = "\U000022b3"  # CONTAINS AS NORMAL SUBGROUP - ⊳ — ⊳ — http://www.fileformat.info/info/unicode/char/22b3
    right_hook_arrow: str = "\U000021aa"  # RIGHTWARDS ARROW WITH HOOK - ↪ — ↪ — http://www.fileformat.info/info/unicode/char/21aa
    star: str = "\U00002b50"  # ⭐
    blank: str = "\u200b"
    inbox_tray: str = "\U0001f4e5"  # :inbox_tray:
    loud_speaker: str = "\U0001f4e2"  # : PUBLIC ADDRESS LOUDSPEAKER - 📢 — 📢 — http://www.fileformat.info/info/unicode/char/1f4e2
    no_entry: str = "\U000026d4"  #: NO ENTRY - ⛔ — ⛔ — http://www.fileformat.info/info/unicode/char/26d4
    warning_sign: str = "\U000026a0"  # : WARNING SIGN - ⚠ — ⚠ — http://www.fileformat.info/info/unicode/char/26a0
    speed_bubble: str = "\U0001f5e8"  #: LEFT SPEECH BUBBLE - 🗨 — 🗨 — http://www.fileformat.info/info/unicode/char/1f5e8
    sun_behind_cloud: str = "\U000026c5"  # SUN BEHIND CLOUD - ⛅ — ⛅ — http://www.fileformat.info/info/unicode/char/26c5
    lotus: str = "\U0001fab7"  # LOTUS - 🪷 — 🪷 — http://www.fileformat.info/info/unicode/char/1fab7
    game_die: str = "\U0001f3b2"  # GAME DIE - 🎲 — 🎲 — http://www.fileformat.info/info/unicode/char/1f3b2
    video_game: str = "\U0001f3ae"  # VIDEO GAME - 🎮 — 🎮 — http://www.fileformat.info/info/unicode/char/1f3ae
    artist_palette: str = "\U0001f3a8"  # ARTIST PALETTE - 🎨 — 🎨 — http://www.fileformat.info/info/unicode/char/1f3a8
    identification_card: str = "\U0001faaa"  # IDENTIFICATION CARD - 🪪 — 🪪 — http://www.fileformat.info/info/unicode/char/1faaa
    bar_chart: str = "\U0001f4ca"  # BAR CHART - 📊 — 📊 — http://www.fileformat.info/info/unicode/char/1f4ca
    no_one_under_eighteen: str = "\U0001f51e"  # NO ONE UNDER EIGHTEEN SYMBOL - 🔞 — 🔞 — http://www.fileformat.info/info/unicode/char/1f51e


class KumaEmojiTable:
    """Usage - <:_:emoji_id>."""

    kuma_rawr = "<:kuma_rawr:1337130200009281559>"
    kuma_crying = "<:kuma_crying:1337130201527750757>"
    kuma_tear = "<:kuma_tear:1337130203096420372>"
    kuma_happy = "<:kuma_happy:1337130204547780648>"
    kuma_star_eye = "<:kuma_star_eye:1337130207462817946>"
    kuma_shy = "<:kuma_shy:1337130209446592513>"
    kuma_peak = "<:kuma_peak:1337130211128377406>"
    kuma_chuckle = "<:kuma_chuckle:1337130212890247199>"
    kuma_heart = "<:kuma_heart:1337130214492475475>"
    kuma_sad = "<:kuma_sad:1337130217071841441>"
    kuma_shrug = "<:kuma_shrug:1337130218963468448>"
    kuma_hmm = "<:kuma_hmm:1337130227788415087>"
    kuma_tea = "<:kuma_tea:1337130230598602792>"
    kuma_shock = "<:kuma_shock:1337130232993288244>"
    kuma_bleh = "<:kuma_bleh:1337130235472117841>"
    kuma_uwu = "<:kuma_uwu:1337130237225603092>"
    kuma_wow = "<:kuma_wow:1337130238878154893>"
    kuma_pout = "<:kuma_pout:1337133347163345019>"
    kuma_head_clench = "<:kuma_head_clench:1337133349612814398>"

    # Retired: every entry is a plain class attribute, so `table.kuma_happy` says the same thing at
    # no cost and without a lookup that could raise. The `int` branch never worked anyway — it
    # compared the ID against the whole `<:name:id>` string, so an ID lookup always raised.
    # Use the attribute directly, or `get_emoji()` when the name is only known at runtime.
    # @staticmethod
    # def to_inline_emoji(emoji: Union[str, int]) -> str | None:
    #     """Converts the emoji provided into a Discord in line str type emoji for usage.
    #
    #     Parameters
    #     ----------
    #     emoji: Union[:class:`str`, :class:`int`]
    #         Either the emoji name or the ID to lookup..
    #
    #     Returns
    #     -------
    #     :class:`str`
    #         Discord in line Emoji string.
    #
    #     Raises
    #     ------
    #     LookupError
    #         If the emoji provided does not exist.
    #
    #     """
    #     if isinstance(emoji, str):
    #         emoji = emoji.lower()
    #
    #     for key, value in KumaEmojiTable.__dict__.items():
    #         if isinstance(emoji, str) and emoji == key:
    #             return value
    #         if isinstance(emoji, int) and emoji == value:
    #             return value
    #     msg = "The Emoji provided does not exist. | %s"
    #     raise LookupError(msg, emoji)

    @classmethod
    def get_emoji(cls, name: str) -> Optional[str]:
        """Get the Application Emoji string by name.

        Parameters
        ----------
        name: :class:`str`
            The name of the emoji to lookup.

        Returns
        -------
        :class:`str`
            The Discord in line emoji string, if able to find the emoji.

        """
        for key, value in cls.__dict__.items():
            if name.lower() == key:
                return value
        return None

    @classmethod
    def to_cdn_url(cls, name: str, *, size: int = 128) -> Optional[str]:
        """Get the CDN image URL for one of our Application Emojis by name.

        Components V2 parts that show an image (:class:`discord.ui.Thumbnail`,
        :class:`discord.MediaGalleryItem`) take either a URL or an `attachment://` reference. An
        attachment has to be re-uploaded on every edit of the message carrying it, which is no good for
        a component that gets re-rendered often. Our emojis are already hosted by Discord and their IDs
        are baked into this table, so pointing at the CDN puts Kuma artwork in a component for free; no
        upload, and no attachment slot spent.

        Parameters
        ----------
        name: :class:`str`
            The name of the emoji to lookup, eg. `kuma_peak`.
        size: :class:`int`, optional
            The requested image width in pixels, by default `128`. Discord serves powers of two from
            16 to 4096 and rounds anything else up.

        Returns
        -------
        :class:`Optional[str]`
            The CDN URL, or `None` when no emoji goes by that name.

        """
        inline: Optional[str] = cls.get_emoji(name)
        if inline is None:
            return None
        # Our entries are all in the `<:name:id>` form, so the ID is the last colon separated field.
        emoji_id: str = inline.rstrip(">").rsplit(sep=":", maxsplit=1)[-1]
        return f"https://cdn.discordapp.com/emojis/{emoji_id}.png?size={size}"


class KumaResources:
    """Attributes related to useful material stored in the `./resources/kuma_kuma_emojis` folder."""

    src: Path = Path(__file__).parent.parent.joinpath("resources")
    banner: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_banner.jpg")
    sticker: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_sticker.jpg")
    sticker2: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_sticker2.jpg")
    smug_large: Path = src.joinpath("kuma_kuma_emojis/kuma_kuma_bear_smug_large.jpg")


class KumaCog(commands.Cog):
    """Our custom Cog class for Discord.py.

    Attributes
    ----------
    bot: Kuma_Kuma
        The discord bot class.
    message_timeout: :class:`int`
        The amount of time in seconds before a message is auto-deleted.
    owner_guild: :class:`discord.Object`
        The owner of the bots guild. aka Neko Neko Cafe`
    emoji_table: :class:`KumaEmojiTable`
        A simple str name attribute to int id value representation for ease of use.
    resources: :class:`KumaResources`
        A base class with pathlib Path attributes for accessing resources.


    Properties
    ----------
    unicode: :class:`UnicodeTable`
        ...
    metrics: :class:`Metrics`
        A dictionary containing useful information regarding metrics, can be expanded.

    """

    bot: Kuma_Kuma
    message_timeout: int
    owner_guild = discord.Object(id=602285328320954378)
    emoji_table: KumaEmojiTable = KumaEmojiTable()
    resources: KumaResources = KumaResources()
    strftime_fmt = "%d/%m | %H:%M(%Z)"
    _timestamp_styles = Literal["F", "f", "D", "d", "T", "t", "R"]

    _metrics: dict[str, Metrics]
    _unicode: UnicodeTable = UnicodeTable()

    @property
    def unicode(self) -> UnicodeTable:
        return self._unicode

    @property
    def metrics(self) -> dict[str, Metrics]:
        """Stores Cog relevant metrics information.

        By default stores the current Cog's uptime.

        Returns
        -------
        :class:`Metrics`
            _description_.

        """
        return self._metrics

    @metrics.setter
    def metrics(self, value: dict[str, Metrics]) -> None:
        self._metrics: dict[str, Metrics] = value

    def __init__(self, bot: Kuma_Kuma) -> None:
        """Builds the base :class:`KumaCog` class object.

        Set's self.bot, self.message_timeout.
        - Set's the Cogs start time using :class:`time.time()`
        """
        self.bot = bot
        self.message_timeout = bot.message_timeout
        # `type(self)`, not `__class__` — the latter is bound to the class the method is *written*
        # in, so every cog keyed its metrics under "KumaCog" and the last one loaded won. A cog
        # reading `self.metrics[<its own name>]` only worked if it overwrote the dict itself.
        self.metrics = {type(self).__name__: {"uptime": {"start": datetime.datetime.now(tz=datetime.UTC)}}}

    async def get_guild(self) -> discord.Guild | None:
        """Returns the owners guild. In this instance `Neko Neko Cafe`.

        Returns
        -------
        :class:`discord.Guild | None`
            A discord Guild object, otherwise None.

        """
        return self.bot.get_guild(self.owner_guild.id)

    # Keys on a settings TypedDict that are storage rather than settings, so they are never offered
    # as something to change.
    settings_excluded_keys: ClassVar[frozenset[str]] = frozenset({"id", "serverid", "userid"})

    # The column every user scoped table keys on. A convention rather than a foreign key into some
    # global users table; a Discord ID is already a stable 64 bit key of its own, so a surrogate would
    # only add an ordering rule to every insert and a JOIN to every read. Repeating this line is the
    # join key, spelled the same way each time so a lookup never has to guess the column name.
    user_id_column: ClassVar[str] = "userid INTEGER NOT NULL"

    @staticmethod
    def settings_choices(settings: type, *, exclude: Iterable[str] = ()) -> list[app_commands.Choice[str]]:
        """Turns a settings TypedDict into the app command choices for changing it.

        Keeps the TypedDict as the single place a setting is declared. Add a column to it and the
        command choice comes along with it, so there is no second list to keep in step.

        .. note::
            A staticmethod on purpose. Choices are handed to `@app_commands.choices(...)`, which runs
            in the class body at import time, long before any cog instance exists; anything needing
            `self` could not be used there at all. Call it as ``Cog.settings_choices(MySettings)``.

        Parameters
        ----------
        settings: :class:`type`
            The TypedDict describing one row. eg. `ModeratorSettings`.
        exclude: :class:`Iterable[str]`, optional
            Keys to leave out on top of :attr:`settings_excluded_keys`, for a column that is not a
            user facing setting.

        Returns
        -------
        :class:`list[app_commands.Choice[str]]`
            A choice per remaining key, with the column name as both the name and the value.

        """
        skipped: frozenset[str] = KumaCog.settings_excluded_keys | frozenset(exclude)
        # `__annotations__` rather than `inspect.get_annotations()`. A TypedDict's own dict is already
        # the field list and it keeps declaration order, which is the order the choices should read in.
        return [app_commands.Choice(name=key, value=key) for key in settings.__annotations__ if key not in skipped]

    @staticmethod
    def rotate_pick[T](entries: Sequence[T], *, offset: int = 0, window: int = ROTATE_WINDOW) -> T:
        """Choose one of several entries, rotating which one on a fixed cadence.

        The wall clock is the seed, so there is nothing to store and the same caller asking twice
        inside one window is handed the same entry — a choice that is reproducible rather than merely
        random.

        .. note::
            A staticmethod on purpose. The clock and the arguments decide everything, so there is
            nothing on the cog to consult. Call it as ``Cog.rotate_pick(entries, offset=user.id)``.

        Parameters
        ----------
        entries: :class:`Sequence[T]`
            What to choose from.
        offset: :class:`int`, optional
            Shifts this caller's place in the rotation, by default 0. Pass a user ID so two people
            asking in the same window are not handed the same entry.
        window: :class:`int`, optional
            Seconds a choice holds before moving on, by default :attr:`ROTATE_WINDOW`.

        Returns
        -------
        :class:`T`
            The entry whose turn it is.

        Raises
        ------
        IndexError
            ``entries`` was empty.

        """
        # Guarded rather than left to `% 0`, which raises ZeroDivisionError and reads as a maths bug
        # rather than as the empty sequence it actually is.
        if not entries:
            err = "rotate_pick() needs at least one entry."
            raise IndexError(err)

        bucket: int = int(datetime.datetime.now(tz=datetime.UTC).timestamp() // window)
        return entries[(bucket + offset) % len(entries)]

    def to_discord_timestamp(self, time: Optional[datetime.datetime] = None, *, style: _timestamp_styles = "F") -> str:
        """Converts a Date Time value into each Discord users local timezone for display.

        .. note::
            Uses discord built in timestamp converter for relative time for user.
            - https://discord.com/developers/docs/reference#message-formatting-timestamp-styles


        Parameters
        ----------
        time: :class:`datetime`
            The datetime object to timestamp.
        style: :class:`_timestamp_styles`, optional
            The Format to display the text in, by default "F".

        Returns
        -------
        :class:`str`
            The markdown text to use in Discord content to display the timestmap.

        """
        if time is None:
            time = datetime.datetime.now(tz=datetime.UTC)
        return f"<t:{int(time.timestamp())}:{style}>"

    def string_inflection(self, word: str, tag: PennPOS = PennPOS.VBD) -> str:
        """Uses Lemminflection to change the inflection of the passed in word depending on the tag provided.

        - By default it uses `Verb, past tense` aka `VBD`.

        Parameters
        ----------
        word: :class:`str`
            The str to inflect upon.
        tag: :class:`PennPOS`, optional
            The Penn TreeBank Tag, by default "PennPOS.VBD".
            - https://www.ling.upenn.edu/courses/Fall_2003/ling001/penn_treebank_pos.html

        Returns
        -------
        :class:`str`
            Returns the Inflected string if possible, otherwise the original word.

        """
        results = getInflection(word, tag)  # pyright: ignore[reportUnknownVariableType]
        if isinstance(results, tuple) and len(results) >= 1:  # pyright: ignore[reportUnknownArgumentType]
            return results[0]  # pyright: ignore[reportUnknownVariableType]
        return word

    def to_progressive(self, label: str, tag: PennPOS = PennPOS.VBG) -> str:
        """Returns a label with only its first word inflected, so a bare verb reads as an action.

        Wraps :meth:`string_inflection` for the common status-line case — `"Read"` becomes
        `"Reading"`, `"Run tests"` becomes `"Running tests"`. The trailing words are left alone
        because they are the object of the verb, not part of it, and the caller's leading capital is
        restored afterwards since lemminflect always returns lowercase.

        Parameters
        ----------
        label: :class:`str`
            The label to inflect, e.g. a tool or action name.
        tag: :class:`PennPOS`, optional
            The Penn TreeBank tag to inflect to, by default :attr:`PennPOS.VBG` (present participle).

        Returns
        -------
        :class:`str`
            The inflected label; unchanged when it is empty or cannot be inflected.

        """
        if not label:
            return label

        head, _, tail = label.partition(" ")
        inflected: str = self.string_inflection(head, tag)
        if head[:1].isupper():
            inflected = inflected[:1].upper() + inflected[1:]
        return f"{inflected} {tail}".rstrip()

    def animate(
        self,
        target: AnimationTarget,
        *,
        label: str = "Working",
        style: Optional[AnimationStyle] = None,
        **kwargs: Any,
    ) -> KumaAnimation:
        """Builds a self-updating status message for a long running action.

        Use it as an async context manager — the background task is cancelled on exit even when the
        body raises, so a failure cannot strand a message animating forever.

        .. code-block:: python

            async with self.animate(message, label="Fetch") as status:
                status.label = "Build"
                status.add_line("✓ 42 items")

        The label is rendered verbatim; inflect it yourself with :meth:`string_inflection` first
        if you want a bare verb to read as an action in progress (`"Read"` → `"Reading"`).

        Parameters
        ----------
        target: :class:`AnimationTarget`
            The message, webhook message or interaction to edit.
        label: :class:`str`, optional
            The leading text, by default `"Working"`.
        style: :class:`Optional[AnimationStyle]`, optional
            A frame style — see :class:`AnimationFrames` for presets and
            :meth:`AnimationFrames.toggle` to cycle your own strings or emoji. Defaults to
            :attr:`AnimationFrames.BRAILLE`.
        **kwargs: :class:`Any`
            Forwarded to :class:`KumaAnimation` — `header`, `footer`, `status_last`,
            `min_interval`, `idle_interval`, `max_interval`, `decay_after`, `max_length`.

        Returns
        -------
        :class:`KumaAnimation`
            The animation, not yet started.

        """
        return KumaAnimation(target, label=label, style=style, **kwargs)

    async def get_request(self, *, url: str, **request_params: Unpack[AioHTTPRequestOptions]) -> bytes | None:
        try:
            async with self.bot.session.get(url=url, **request_params) as session:
                if session.status >= 200 and session.status < 300:
                    return await session.read()
        except (TimeoutError, aiohttp.ClientError) as e:
            LOGGER.warning(
                "<%s.%s> | Connection failed. | URL: %s | Error: %s",
                __class__.__name__,
                "get_request",
                url,
                e,
            )
        return None


# region --- Moogle Intuition / FFXIV
class MooglesIntuitionEmojis:
    """A collection of Application Emojis for Moogles Intuition Module."""

    # Marketboard/Etc Icons/Emojis
    aethernet_icon = "<:aethernet_icon:1441468511481495654>"
    "Aethernet Miniature - Application Emoji"
    alc_icon = "<:alc_icon:1441485708329222174>"
    "Alchemist Job - Application Emoji"
    another_world_icon = "<:another_world_icon:1441637597133672458>"
    "Traveler Icon next to a players name - Application Emoji"
    arm_icon = "<:arm_icon:1441485709335728238>"
    "Armorsmith Job - Application Emoji"
    arricon = "<:arricon:1441468515608690719>"
    "A Real Reborn Expansion - Application Emoji"
    bag_with_exclamation = "<:bag_with_exclamation:1442003581262762157>"
    "A Satchel similar to the inventory icon with an exclamation mark - Application Emoji"
    bait: str = "<:bait:1452081174661824673>"
    "Change Bait Fisher action icon - APplication Emoji"
    bot_icon = "<:bot_icon:1441485719469166734>"
    "Botanist Job - Application Emoji"
    botanist_icon_mini = "<:botanist_icon_mini:1441607824974155858>"
    "Botanist Job Icon mini - Application Emoji"
    bsm_icon = "<:bsm_icon:1441485711114113204>"
    "Blacksmith Job - Application Emoji"
    calendar_star_icon = "<:calendar_star_icon:1441637605702893722>"
    "Calendar with a Star in the middle - Application Emoji"
    chest = "<:chest:1441468512743718954>"
    "Opened Chest with Gold Coins - Application Emoji"
    chest2 = "<:chest2:1441468516254355639>"
    "Closed Chest similar to Treasure Dungeon Icon - Application Emoji"
    clock_icon = "<:clock_icon:1441607831382917251>"
    "Clock Icon with grey background - Application Emoji"
    commedations = "<:commedations:1441637600099176519>"
    "Player commendation from the UI - Application Emoji"
    crafting_log_icon = "<:crafting_log_icon:1441637601218924704>"
    "Crafting log icon from in game UI - Application Emoji"
    crp_icon = "<:crp_icon:1441485712770994369>"
    "Carpenter Job - Application Emoji"
    cul_icon = "<:cul_icon:1441485714071228426>"
    "Culinarian Job - Application Emoji"
    custom_deliveries = "<:custom_deliveries:1441637604444602450>"
    "Custom deliveries icon - Application Emoji"
    dl_icon = "<:dl_icon:1441485697977548943>"
    "Download Icon - Application Emoji"
    doh_icon = "<:doh_icon:1441467943199183093>"
    "Disciple of Hand - Application Emoji"
    dol_icon = "<:dol_icon:1441467944755527700>"
    "Disciple of Land - Application Emoji"
    double_hook = "<:double_hook:1452081168995319849>"
    "Double Hook Fisher action Icon - Application Emoji"
    dticon = "<:dticon:1441468517739135076>"
    "Dawntrail Expansion - Application Emoji"
    dungeon_icon = "<:dungeon_icon:1441485706592518388>"
    "Generic Dungeon Icon - Application Emoji"
    error_icon = "<:error_icon:1441607828552028390>"
    "Caution traingle with exclamation mark - Application Emoji"
    ewicon = "<:ewicon:1441468519202951208>"
    "Endwalker Expansion - Application Emoji"
    fate_icon = "<:fate_icon:1441637603542827028>"
    "FATE Icon from in game UI - Application Emoji"
    fisher_icon = "<:fisher_icon:1442010164281737326>"
    "Fisher job icon grey with drop shadow - Application Emoji"
    fisher_icon_mini = "<:fisher_icon_mini:1441607823950872718>"
    "Fisher Job Icon mini - Application Emoji"
    fishing_log_icon = "<:fishing_log_icon:1442268119937712261>"
    "Fishing Log Icon from in game UI - Application Emoji"
    flame_icon = "<:flame_icon:1441485904580444220>"
    "The Immortal Flames Reputation - Application Emoji"
    fsh_icon = "<:fsh_icon:1441467900450836550>"
    "Fisher Job - Application Emoji"
    gatheringicon = "<:gatheringicon:1441468520461369425>"
    "Miner and Botanisty icons in an X pattern - Application Emoji"
    gathering_log_icon = "<:gathering_log_icon:1442268118230765628>"
    "The Gathering Log icon from in game UI - Application Emoji"
    garlandtools_icon = "<:garlandtoolsicon:1466429637222600952>"
    "The Garland Tools website Icon."
    gear_icon = "<:gear_icon:1441607830493987009>"
    "Gear/Settings style Icon with grey background  - Application Emoji"
    gil = "<:gil:1441468514052608000>"
    "Gil Currency - Application Emoji"
    glittery_gil_satchel = "<:glittery_gil_satchel:1441607832989470821>"
    "Gil Satchel with Sparkles - Application Emoji"
    gsm_icon = "<:gsm_icon:1441485715358617693>"
    "Goldsmith Job - Application Emoji"
    hwicon = "<:hwicon:1441468521384120421>"
    "Heavensward Expansion - Application Emoji"
    hook: str = "<:hook:1452081176096407664>"
    "Fisher Hook action icon - Application Emoji"
    inv_icon = "<:inv_icon:1441485699965653183>"
    "Satchel with an ! on it - Application Emoji"
    inv_icon2 = "<:inv_icon2:1441485701182001162>"
    "Open Bag with Arrow pointing into Satchel - Application Emoji"
    inventory_icon = "<:inventory_icon:1441637602334736456>"
    "Inventory Icon from in game UI - Application Emoji"
    left_arrow_icon = "<:left_arrow_icon:1441607825871863838>"
    "Left Arrow Icon - Application Emoji"
    loop_icon = "<:loop_icon:1441607829671645225>"
    "Loop Swirl Icon - Application Emoji"
    ltw_icon = "<:ltw_icon:1441485716118048830>"
    "Leatherworker Job - Application Emoji"
    mbhistoryicon = "<:mbhistoryicon:1441468522608984194>"
    "Marketboard History - Application Emoji"
    mbicon = "<:mbicon:1441468523527540786>"
    "Marketboard - Application Emoji"
    mb_2_icon = "<:mb_2_icon:1442003582638489790>"
    "Satchel being placed into the Marketboard sign - Application Emoji"
    mbwatchlisticon = "<:mbwatchlisticon:1441468524592632030>"
    "Marketboard Watch List - Application Emoji"
    mgp = "<:mgp:1441485730315636776>"
    "Manderville Gold Saucer Points - Application Emoji"
    mnr_icon = "<:mnr_icon:1441485720454697093>"
    "Miner Job - Application Emoji"
    mooch = "<:mooch:1452081171167969322>"
    "Mooch Fisher action icon - Application Emoji"
    moogle1 = "<:moogle1:1441468526350176326>"
    "Running on all 4 Moogle legs in - Application Emoji"
    moogle2 = "<:moogle2:1441468527843213432>"
    "Running on all 4 Moogle legs out - Application Emoji"
    moogle3 = "<:moogle3:1441468529160491048>"
    "Upright Moogle with large antenna - Application Emoji"
    moogle_selfie_icon = "<:moogle_selfie_icon:1441637607107858522>"
    "Moogle face on colored background - Application Emoji"
    moogle_selfie_grey_icon = "<:moogle_selfie_grey_icon:1441637608642973768>"
    "Moogle face on grey background - Application Emoji"
    miner_icon_mini = "<:miner_icon_mini:1441607822575145033>"
    "Miner Job Icon mini - Application Emoji"
    no_action = "<:no_action:1441607821190889643>"
    "Red X Icon - Application Emoji"
    poi_icon = "<:poi_icon:1441485705598603365>"
    "Point of Interest - Application Emoji"
    powerful_hookset = "<:powerful_hookset:1452081173500264509>"
    "Powerful Hookset Fisher action icon - Application Emoji"
    precision_hookset = "<:precision_hookset:1452081172636106752>"
    "Precision Hookset Fisher action icon - Application Emoji"
    right_arrow_icon = "<:right_arrow_icon:1441607827071172649>"
    "Right Arrow Icon - Application Emoji"
    repair_icon = "<:repair_icon:1441637595938295838>"
    "Repair wrench on a yellow hexagon background Icon - Application Emoji"
    sbicon = "<:sbicon:1441468530288492704>"
    "Stormblood Expansion - Application Emoji"
    shbicon = "<:shbicon:1441468532389838978>"
    "Shadowbringers Expansion - Application Emoji"
    teamcraft_icon = "<:teamcrafticon:1466431486222663919>"
    "The Teamcraft website Icon."
    tales_of_adventure = "<:tales_of_adventure:1442312168534966322>"
    "A FFXIV Colored book, "
    treasurehunt = "<:treasurehunt:1441467786445586472>"
    "Treasure Dungeon Icon - Application Emoji"
    triple_hook = "<:triple_hook:1452081170241032396>"
    "Triple Hook Fisher action Icon - Application Emoji"
    upload_icon = "<:upload_icon:1441485698963210392>"
    "Upload Icon - Application Emoji"
    universalis_icon = "<:universalisicon:1466429635939008778>"
    "The Universalis website Icon"
    vendor_icon = "<:vendor_icon:1441832190819303505>"
    "Vendor Icon from the map UI - Application Emoji"
    world_visit = "<:world_visit:1441637598979297461>"
    "Visit another World in game Icon - Application Emoji"
    wvr_icon = "<:wvr_icon:1441485717707685938>"
    "Weaver Job - Application Emoji."
    xiv_wiki_icon = "<:xivwikiicon:1466431487451726026>"
    "The XIV wiki Icon"

    @classmethod
    def get_emoji(cls, name: str) -> Optional[str]:
        """Get the Application Emoji string by name.

        Parameters
        ----------
        name: :class:`str`
            The name of the emoji to lookup.

        Returns
        -------
        :class:`str`
            The Discord in line emoji string, if able to find the emoji.

        """
        for key, value in cls.__dict__.items():
            if name.lower() == key:
                return value
        return None


class FFXIVResources:
    """FFXIV Resources such as Icons, Banners, Emojis, Items, Locations and much more for easier lookup.

    - All the Emoji's are stored on Neko Neko Cafe` Discord Guild.

    Attributes
    ----------
    resource_path: :class:`Path`
        Parent Path directory to `resources/moogle_intuition` directory.
    emojis: :class:`MooglesIntuitionEmojis`
        A class with mapped attributes related to inline discord Application Emojis.

    """

    resource_path: Path = Path(__file__).parent.parent.joinpath("resources/moogle_intuition")
    emojis: MooglesIntuitionEmojis = MooglesIntuitionEmojis()
    patch_mapping: ClassVar[dict[int, str]] = {
        1: "patch_icon/arr-icon.png",
        2: "patch_icon/hw-icon.png",
        3: "patch_icon/sb-icon.png",
        4: "patch_icon/shb-icon.png",
        5: "patch_icon/ew-icon.png",
        6: "patch_icon/dt-icon.png",
    }

    @classmethod
    def get_patch_icon(cls, patch_id: float, filename: str = "patch-icon.png") -> discord.File:
        """get_patch_icon _summary_.

        .. note::
            - 1: "patch_icon/arr-icon.png",
            - 2: "patch_icon/hw-icon.png",
            - 3: "patch_icon/sb-icon.png",
            - 4: "patch_icon/shb-icon.png",
            - 5: "patch_icon/ew-icon.png",
            - 6: "patch_icon/dt-icon.png",

        Parameters
        ----------
        patch_id: :class:`float`
            The ICON ID in reference to `patch_mapping`.
        filename: :class:`str`
            The filename parameter for the `discord.File` object.

        Returns
        -------
        :class:`discord.File`
            _description_.

        """
        file_path = cls.patch_mapping.get(int(patch_id), cls.patch_mapping[1])
        return discord.File(cls.resource_path.joinpath(file_path), filename=filename)

    @classmethod
    def get_banner(cls) -> discord.File:
        """Get the Final Fantasy 14 Trail Banner from local files.

        - Filename: "ffxiv-banner.png"

        Returns
        -------
        :class:`discord.File`
            A discord.File object of the local banner file.

        """
        return discord.File(fp=cls.resource_path.joinpath("ffxiv-trail-banner.png"), filename="ffxiv-banner.png")

    @classmethod
    def get_universalis_icon(cls, filename: str = "universalis-icon.png") -> discord.File:
        """Get the Universalis Icon from local files.

        Parameters
        ----------
        filename: :class:`str`
            The filename parameter for the `discord.File` object.

        Returns
        -------
        :class:`discord.File`
            A discord.File object of the Universalis icon file.

        """
        return discord.File(fp=cls.resource_path.joinpath("universalis/universalis-icon.png"), filename=filename)

    @classmethod
    def get_garlandtools_icon(cls) -> discord.File:
        """Get the GarlandTools Icon from local files.

        - Filename: "garlandtools-icon.png"

        Returns
        -------
        :class:`discord.File`
            A discord.File object of the GarlandTools icon file.

        """
        return discord.File(fp=cls.resource_path.joinpath("garlandtools-icon.png"), filename="garlandtools-icon.png")

    @classmethod
    def get_aethernet_icon(cls) -> discord.File:
        """Get the Aethernet Icon from local files.

        - Filename: "aethernet-icon.png"

        Returns
        -------
        :class:`discord.File`
            A discord.File object of the Aethernet icon file.

        """
        return discord.File(fp=cls.resource_path.joinpath("aethernet-icon.png"), filename="aethernet-icon.png")

    @classmethod
    def get_moogle_icon(cls, filename: str = "moogle-icon.png") -> discord.File:
        """Get the Moogle Intution Icon from local files.

        Parameters
        ----------
        filename: :class:`str`
            The filename parameter for the `discord.File` object.

        Returns
        -------
        :class:`discord.File`
            A discord.File object of the Moogle Intution icon file.

        """
        return discord.File(fp=cls.resource_path.joinpath("moogle-emoji-1.png"), filename=filename)

    # @classmethod
    # def make_file(cls, path: Path) -> discord.File:
    #     if path.exists():
    #         return discord.File()
