"""Source: https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/reminder.py"""

from __future__ import annotations
import datetime

from typing import TYPE_CHECKING, NamedTuple, Optional
import aiohttp
from attr import dataclass
from dateutil.zoneinfo import get_zonefile_instance
import discord
import pytz
import utils.fuzzy as fuzzy

from utils.context import KumaContext

from discord.ext import commands
from discord import app_commands

from lxml import etree

if TYPE_CHECKING:
    from typing_extensions import Self


_default_timezones: list[app_commands.Choice[str]] = []
# valid_timezones: set[str] = set(get_zonefile_instance().zones)

DEFAULT_POPULAR_TIMEZONE_IDS = (
    # America
    'usnyc',  # America/New_York
    'uslax',  # America/Los_Angeles
    'uschi',  # America/Chicago
    'usden',  # America/Denver
    # India
    'inccu',  # Asia/Kolkata
    # Europe
    'trist',  # Europe/Istanbul
    'rumow',  # Europe/Moscow
    'gblon',  # Europe/London
    'frpar',  # Europe/Paris
    'esmad',  # Europe/Madrid
    'deber',  # Europe/Berlin
    'grath',  # Europe/Athens
    'uaiev',  # Europe/Kyev
    'itrom',  # Europe/Rome
    'nlams',  # Europe/Amsterdam
    'plwaw',  # Europe/Warsaw
    # Canada
    'cator',  # America/Toronto
    # Australia
    'aubne',  # Australia/Brisbane
    'ausyd',  # Australia/Sydney
    # Brazil
    'brsao',  # America/Sao_Paulo
    # Japan
    'jptyo',  # Asia/Tokyo
    # China
    'cnsha',  # Asia/Shanghai
)

_timezone_aliases: dict[str, str] = {
    'Eastern Time': 'America/New_York',
    'Central Time': 'America/Chicago',
    'Mountain Time': 'America/Denver',
    'Pacific Time': 'America/Los_Angeles',
    # (Unfortunately) special case American timezone abbreviations
    'EST': 'America/New_York',
    'CST': 'America/Chicago',
    'MST': 'America/Denver',
    'PST': 'America/Los_Angeles',
    'EDT': 'America/New_York',
    'CDT': 'America/Chicago',
    'MDT': 'America/Denver',
    'PDT': 'America/Los_Angeles',
}


async def parse_bcp47_timezones():
    _default_timezones: list[app_commands.Choice[str]] = []
    session = aiohttp.ClientSession()
    async with session.get(
        'https://raw.githubusercontent.com/unicode-org/cldr/main/common/bcp47/timezone.xml'
    ) as resp:
        if resp.status != 200:
            return _default_timezones
        # await session.close()

        parser = etree.XMLParser(ns_clean=True, recover=True, encoding='utf-8')
        tree = etree.fromstring(await resp.read(), parser=parser)

        # Build a temporary dictionary to resolve "preferred" mappings
        entries: dict[str, CLDRDataEntry] = {
            node.attrib['name']: CLDRDataEntry(
                description=node.attrib['description'],
                aliases=node.get('alias', 'Etc/Unknown').split(' '),
                deprecated=node.get('deprecated', 'false') == 'true',
                preferred=node.get('preferred'),
            )
            for node in tree.iter('type')
            # Filter the Etc/ entries (except UTC)
            if not node.attrib['name'].startswith(('utcw', 'utce', 'unk'))
            and not node.attrib['description'].startswith('POSIX')
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

    await session.close()
    return _default_timezones


class CLDRDataEntry(NamedTuple):
    description: str
    aliases: list[str]
    deprecated: bool
    preferred: Optional[str]


async def convert_timezones(tz: str) -> datetime.datetime:
    conv_time: datetime.datetime = datetime.datetime.astimezone(discord.utils.utcnow(), tz=pytz.timezone(tz))

    return conv_time
# class TimeZone(NamedTuple):
#     label: str
#     key: str

#     @classmethod
#     async def convert(cls, context: KumaContext, argument: str) -> TimeZone:
#         # assert isinstance(ctx.cog, Reminder)

#         # Prioritise aliases because they handle short codes slightly better
#         if argument in _timezone_aliases:
#             return cls(key=argument, label=_timezone_aliases[argument])

#         if argument in valid_timezones:
#             return cls(key=argument, label=argument)
#         else:
#             raise commands.BadArgument(f'Could not find timezone for {argument!r}')
#         # timezones: list[TimeZone] = find_timezones(argument)

#         # try:
#         #     return await context.disambiguate(timezones, lambda t: t[0], ephemeral=True)
#         # except ValueError:
#         #     raise commands.BadArgument(f'Could not find timezone for {argument!r}')

#     def to_choice(self) -> app_commands.Choice[str]:
#         return app_commands.Choice(name=self.label, value=self.key)


# def find_timezones(query: str) -> list[TimeZone]:
#     # A bit hacky, but if '/' is in the query then it's looking for a raw identifier
#     # otherwise it's looking for a CLDR alias
#     if '/' in query:
#         return [TimeZone(key=a, label=a) for a in fuzzy.finder(query, valid_timezones)]

#     keys = fuzzy.finder(query, _timezone_aliases.keys())
#     return [TimeZone(label=k, key=_timezone_aliases[k]) for k in keys]
