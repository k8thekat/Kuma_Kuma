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

from typing import TYPE_CHECKING, Any, NotRequired, Optional, TypedDict, Union

if TYPE_CHECKING:
    import datetime

    import discord
    from discord import Colour
    from discord.types.embed import EmbedType

__all__ = ("ButtonParams", "EmbedParams", "GitHubIssueSubmissionResponse", "SelectParams")

class GitHubIssueSubmissionResponse(TypedDict):
    id: int
    node_id: str
    url: str
    repository_url: str
    labels_url: str
    comments_url: str
    events_url: str
    html_url: str
    number: int
    state: str
    title: str
    body: str
    user: dict[str, str | int | bool]
    labels: list[dict[str, str | int | bool]]
    assignee: dict[str, str | int | bool]
    assignees: dict[str, str | int | bool]
    milestone: dict[str, str | int | bool]
    locked: bool
    active_lock_reason: str
    comments: int
    pull_request: dict
    closed_at: str | None
    "ISO format datetime"
    created_at: str | None
    "ISO format datetime"
    updated_at: str | None
    "ISO format datetime"
    closed_by: str | None
    "ISO format datetime"
    author_association: str
    state_reason: str


class EmbedParams(TypedDict):
    """discord.Embed parameters.

    Keys
    ----
    - colour: :class:`NotRequired[Optional[int | Colour]]`
    - color: :class:`NotRequired[Optional[int | Colour]]`
    - title: :class:`NotRequired[Optional[Any]]`
    - type: :class:`NotRequired[EmbedType]`
    - url: :class:`NotRequired[Optional[Any]]`
    - description: :class:`NotRequired[Optional[Any]]`
    - timestamp: :class:`NotRequired[Optional[datetime.datetime]]`
    - author: :class:`NotRequired[Optional[str]]`


    """

    colour: NotRequired[Optional[int | Colour]]
    color: NotRequired[Optional[int | Colour]]
    title: NotRequired[Optional[Any]]
    type: NotRequired[EmbedType]
    url: NotRequired[Optional[Any]]
    description: NotRequired[Optional[Any]]
    timestamp: NotRequired[Optional[datetime.datetime]]
    author: NotRequired[Optional[str]]


class ButtonParams(TypedDict):
    style: NotRequired[discord.ButtonStyle]
    custom_id: NotRequired[Optional[str]]
    url: NotRequired[Optional[str]]
    disabled: NotRequired[bool]
    label: NotRequired[Optional[str]]
    emoji: NotRequired[Optional[Union[discord.PartialEmoji, discord.Emoji, str]]]
    row: NotRequired[Optional[int]]
    sku_id: NotRequired[Optional[int]]
    id: NotRequired[Optional[int]]

class SelectParams(TypedDict):
    custom_id: NotRequired[str]
    placeholder: NotRequired[Optional[str]]
    min_values: NotRequired[int]
    max_values: NotRequired[int]
    options: list[discord.SelectOption]
    disabled: NotRequired[bool]
    required: NotRequired[bool]
    row: NotRequired[int]
    id: NotRequired[int]


class Metrics(TypedDict):
    uptime: Uptime


class Uptime(TypedDict):
    start: datetime.datetime
