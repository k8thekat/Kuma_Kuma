"""Timezone lookup helpers, adapted from RoboDanny.

Source: https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/reminder.py.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, NamedTuple, Optional

import aiohttp
import discord
import pytz
from attr import dataclass
from dateutil.zoneinfo import get_zonefile_instance
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context  # This should point to your custom bot context
from lxml import etree  # type: ignore[attr-defined]  # lxml ships no type stubs

import utils.fuzzy as fuzzy

if TYPE_CHECKING:
    from typing import Self


_default_timezones: list[app_commands.Choice[str]] = []
valid_timezones: set[str] = set(get_zonefile_instance().zones)

DEFAULT_POPULAR_TIMEZONE_IDS = (
    # America
    "usnyc",  # America/New_York
    "uslax",  # America/Los_Angeles
    "uschi",  # America/Chicago
    "usden",  # America/Denver
    # India
    "inccu",  # Asia/Kolkata
    # Europe
    "trist",  # Europe/Istanbul
    "rumow",  # Europe/Moscow
    "gblon",  # Europe/London
    "frpar",  # Europe/Paris
    "esmad",  # Europe/Madrid
    "deber",  # Europe/Berlin
    "grath",  # Europe/Athens
    "uaiev",  # Europe/Kyiv
    "itrom",  # Europe/Rome
    "nlams",  # Europe/Amsterdam
    "plwaw",  # Europe/Warsaw
    # Canada
    "cator",  # America/Toronto
    # Australia
    "aubne",  # Australia/Brisbane
    "ausyd",  # Australia/Sydney
    # Brazil
    "brsao",  # America/Sao_Paulo
    # Japan
    "jptyo",  # Asia/Tokyo
    # China
    "cnsha",  # Asia/Shanghai
)

_timezone_aliases: dict[str, str] = {
    "Eastern Time": "America/New_York",
    "Central Time": "America/Chicago",
    "Mountain Time": "America/Denver",
    "Pacific Time": "America/Los_Angeles",
    # (Unfortunately) special case American timezone abbreviations
    "EST": "America/New_York",
    "CST": "America/Chicago",
    "MST": "America/Denver",
    "PST": "America/Los_Angeles",
    "EDT": "America/New_York",
    "CDT": "America/Chicago",
    "MDT": "America/Denver",
    "PDT": "America/Los_Angeles",
}


async def parse_bcp47_timezones() -> list[app_commands.Choice[str]]:
    """Builds the popular-timezone choices from the CLDR BCP47 timezone table.

    Also fills in :data:`_timezone_aliases`, mapping a human description ("Eastern Time") onto the
    IANA name it resolves to, following CLDR's `preferred` entries where one is given.

    Returns
    -------
    :class:`list[app_commands.Choice[str]]`
        One choice per entry in :data:`DEFAULT_POPULAR_TIMEZONE_IDS`; empty when the request fails.

    """
    # The session is the context manager, not just the request. The early `return` on a non-200 used
    # to jump out with the session still open, leaking a connector and an "Unclosed client session"
    # warning; closing it at the bottom only ever ran on the happy path.
    _default_timezones: list[app_commands.Choice[str]] = []
    async with (
        aiohttp.ClientSession() as session,
        session.get("https://raw.githubusercontent.com/unicode-org/cldr/main/common/bcp47/timezone.xml") as resp,
    ):
        if resp.status != 200:
            return _default_timezones

        parser = etree.XMLParser(ns_clean=True, recover=True, encoding="utf-8")
        tree = etree.fromstring(await resp.read(), parser=parser)

        # Build a temporary dictionary to resolve "preferred" mappings
        entries: dict[str, CLDRDataEntry] = {
            node.attrib["name"]: CLDRDataEntry(
                description=node.attrib["description"],
                aliases=node.get("alias", "Etc/Unknown").split(" "),
                deprecated=node.get("deprecated", "false") == "true",
                preferred=node.get("preferred"),
            )
            for node in tree.iter("type")
            # Filter the Etc/ entries (except UTC)
            if not node.attrib["name"].startswith(("utcw", "utce", "unk")) and not node.attrib["description"].startswith("POSIX")
        }

        for entry in entries.values():
            # These use the first entry in the alias list as the "canonical" name to use when mapping the
            # timezone to the IANA database.
            # The CLDR database is not particularly correct when it comes to these, but neither is the IANA database.
            # It turns out the notion of a "canonical" name is a bit of a mess. This works fine for users where
            # this is only used for display purposes, but it's not ideal.
            if entry.preferred is not None:
                preferred = entries.get(entry.preferred)
                if preferred is not None:
                    _timezone_aliases[entry.description] = preferred.aliases[0]
            else:
                _timezone_aliases[entry.description] = entry.aliases[0]

        for key in DEFAULT_POPULAR_TIMEZONE_IDS:
            entry = entries.get(key)
            if entry is not None:
                _default_timezones.append(app_commands.Choice(name=entry.description, value=entry.aliases[0]))

    return _default_timezones


class CLDRDataEntry(NamedTuple):
    description: str
    aliases: list[str]
    deprecated: bool
    preferred: Optional[str]


async def convert_timezones(tz: str) -> datetime.datetime:
    """Returns the current time in the named timezone.

    Parameters
    ----------
    tz: :class:`str`
        An IANA timezone name, eg. `America/New_York`.

    Returns
    -------
    :class:`datetime.datetime`
        Now, as an aware datetime in that zone.

    """
    return datetime.datetime.astimezone(discord.utils.utcnow(), tz=pytz.timezone(tz))
