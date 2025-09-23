import datetime
from typing import Any, NotRequired, Optional, TypedDict, Union

import discord
from discord import Colour
from discord.types.embed import EmbedType

__all__ = ("ButtonParams", "EmbedParams", "GitHubIssueSubmissionResponse")

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
    colour: NotRequired[Optional[int | Colour]]
    color: NotRequired[Optional[int | Colour]]
    title: NotRequired[Optional[Any]]
    type: NotRequired[EmbedType]
    url: NotRequired[Optional[Any]]
    description: NotRequired[Optional[Any]]
    timestamp: NotRequired[Optional[datetime.datetime]]


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
