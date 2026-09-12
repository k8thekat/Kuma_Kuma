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

from typing import TYPE_CHECKING, Any, NotRequired, Optional, TypedDict, Union, Unpack

if TYPE_CHECKING:
    import datetime
    from collections.abc import Sequence

    import discord
    from discord import Colour
    from discord.types.embed import EmbedType

    from .cog import KumaCog
    from .embeds import KumaEmbed
    from .ui import KumaContainer, KumaLayoutView, KumaView

    EmojiInput = Union[str, discord.Emoji, discord.PartialEmoji]
    EmojiFollowup = Union[EmojiInput, Sequence[EmojiInput]]

__all__ = ("ButtonParams", "ContainerParams", "EmbedParams", "GitHubIssueSubmissionResponse", "LayoutViewParams", "SelectParams")


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
    user: dict[str, Union[str, int, bool]]
    labels: list[dict[str, Union[str, int, bool]]]
    assignee: dict[str, Union[str, int, bool]]
    assignees: dict[str, Union[str, int, bool]]
    milestone: dict[str, Union[str, int, bool]]
    locked: bool
    active_lock_reason: str
    comments: int
    pull_request: dict[str, Any]
    closed_at: Optional[str]
    "ISO format datetime"
    created_at: Optional[str]
    "ISO format datetime"
    updated_at: Optional[str]
    "ISO format datetime"
    closed_by: Optional[str]
    "ISO format datetime"
    author_association: str
    state_reason: str


class EmbedParams(TypedDict):
    """discord.Embed parameters.

    Keys
    ----
    - colour: :class:`NotRequired[Optional[Union[int, Colour]]]`
    - color: :class:`NotRequired[Optional[Union[int, Colour]]]`
    - title: :class:`NotRequired[Optional[Any]]`
    - type: :class:`NotRequired[EmbedType]`
    - url: :class:`NotRequired[Optional[Any]]`
    - description: :class:`NotRequired[Optional[Any]]`
    - timestamp: :class:`NotRequired[Optional[datetime.datetime]]`
    - author: :class:`NotRequired[Optional[str]]`

    """

    colour: NotRequired[Optional[Union[int, Colour]]]
    color: NotRequired[Optional[Union[int, Colour]]]
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



class ViewParams(TypedDict):
    """:class:`KumaView` base parameters.

    Params
    ------
    cog : :class:`KumaCog`
        The Cog that dispatched the view.
    owner : :class:`Union[discord.Member, discord.User]`
        The Member or User who dispatched the view/interaction.
    embeds : :class:`Optional[Sequence[KumaEmbed]]`
        The Embeds associated with the view, if applicable.
    recent_interaction : :class:`NotRequired[Optional[discord.Interaction]]`
        The most recent :class:`discord.Interaction` that sent content.
    components : :class:`NotRequired[list[discord.ui.Item]]`
        Any Items to pre-append to the View and display during ``__init__``.
    dispatched_by : :class:`Optional[Union[KumaView, discord.ui.Button[KumaView]]]`
        The Object that dispatched the View.
    timeout : :class:`NotRequired[Optional[float]]`
        Default View timeout parameter.
    """

    cog: KumaCog
    "The Cog that dispatched the view."
    recent_interaction: NotRequired[Optional[discord.Interaction]]
    "The most recent :class:`discord.Interaction` that sent content.."
    components: NotRequired[list[discord.ui.Item]]
    "Any Items to pre-append to the View and display during `__init__`"
    owner: Union[discord.Member, discord.User]
    "The Member or User who dispatched the view/interaction."
    embeds: Optional[Sequence[KumaEmbed]]
    "The Embeds associated with the view, if applicable."
    dispatched_by: NotRequired[Optional[Union[KumaView, discord.ui.Button[KumaView]]]]
    "Who dispatched the View..."
    timeout: NotRequired[Optional[float]]
    "Default View timeout parameter."


class ViewParamsPartial(TypedDict):
    """Similar to :class:`ViewParams`, but only ``cog`` and ``owner`` are required.

    Params
    ------
    cog : :class:`KumaCog`
        The Cog that dispatched the view.
    owner : :class:`Union[discord.Member, discord.User]`
        The Member or User who dispatched the view/interaction.
    recent_interaction : :class:`NotRequired[Optional[discord.Interaction]]`
        The most recent :class:`discord.Interaction` that sent content.
    components : :class:`NotRequired[list[discord.ui.Item]]`
        Any Items to pre-append to the View and display during ``__init__``.
    embeds : :class:`NotRequired[Optional[Sequence[KumaEmbed]]]`
        The Embeds associated with the view, if applicable.
    dispatched_by : :class:`NotRequired[Optional[Union[KumaView, discord.ui.Button[KumaView]]]]`
        The Object that dispatched the View.
    timeout : :class:`NotRequired[Optional[float]]`
        Default View timeout parameter.

    """

    cog: KumaCog
    "The Cog that dispatched the view."
    owner: Union[discord.Member, discord.User]
    "The Member or User who dispatched the view/interaction."
    recent_interaction: NotRequired[Optional[discord.Interaction]]
    "The most recent :class:`discord.Interaction` that sent content.."
    components: NotRequired[list[discord.ui.Item]]
    "Any Items to pre-append to the View and display during `__init__`"
    embeds: NotRequired[Optional[Sequence[KumaEmbed]]]
    "The Embeds associated with the view, if applicable."
    dispatched_by: NotRequired[Optional[Union[KumaView, discord.ui.Button[KumaView]]]]
    "Who dispatched the View..."
    timeout: NotRequired[Optional[float]]
    "Default View timeout parameter."


class LayoutViewParams(TypedDict):
    cog: NotRequired[Optional[KumaCog]]
    owner: Union[discord.Member, discord.User]
    container: KumaContainer
    containers: NotRequired[Optional[list[KumaContainer]]]
    include_footer: NotRequired[Optional[bool]]


class ContainerParams(TypedDict, total=False):
    accent_color: Optional[Union[discord.Colour, int]]
    accent_colour: Optional[Union[discord.Colour, int]]
    spoiler: bool
    id: Optional[int]
