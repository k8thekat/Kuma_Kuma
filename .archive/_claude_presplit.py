"""Copyright (C) 2021-2025 Katelynn Cadwallader.

This file is part of Kuma Kuma Bear, a Discord Bot.

Kuma Kuma Bear is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 3, or (at your option)
any later version.

Kuma Kuma Bear is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public
License for more details.

You should have received a copy of the GNU General Public License
along with Kuma Kuma Bear; see the file COPYING.  If not, write to the Free
Software Foundation, 51 Franklin Street - Fifth Floor, Boston, MA
02110-1301, USA.

"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import gzip
import io
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Optional, TypedDict, Union, cast

import discord
import psutil
from discord import app_commands
from discord.ext import commands, tasks

from extensions.hints import Hint, HintsCog
from extensions.preferences import Preference, PreferenceChoice, Preferences
from utils import KumaAnimation, KumaCog as Cog, KumaEmojiTable, KumaRollingAnimation, TranscriptBlock

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from sqlite3 import Row

    from discord.channel import ThreadWithMessage

    from kuma_kuma import Kuma_Kuma

LOGGER = logging.getLogger()
__VERSION__ = "2.1.0"

# ---------------------------------------------------------------------------
# CLI transport
#
# The whole point of this module: one long-lived `claude` process per session, fed on stdin and read
# on stdout, instead of a fresh one-shot run per message.
#
# `-p` is in the argument list and that reads wrong at first glance, so: `--input-format stream-json`
# *only works with* `-p`, and `-p` in that combination is not one-shot. Verified against
# `claude 2.1.220` — the process stays up across turns (`returncode` stays `None`), keeps one
# `session_id`, and needs no `--resume` between messages. Dropping `-p` starts the interactive TUI,
# which only speaks ANSI to a PTY and cannot be driven from here at all.
# ---------------------------------------------------------------------------

# Where the CLI installer drops its launcher. A bot started off `.venv/bin/python` from a service or a
# detached shell inherits a PATH without `~/.local/bin` on it, so we resolve this ourselves rather
# than trust whatever PATH we were handed.
CLAUDE_FALLBACK_PATHS: tuple[Path, ...] = (
    Path.home().joinpath(".local", "bin", "claude"),
    Path.home().joinpath(".claude", "local", "claude"),
    Path("/usr/local/bin/claude"),
)

# The flags that turn the CLI into a live session. `--permission-prompt-tool stdio` is the important
# one and is undocumented — it is absent from `--help` entirely. Without it the CLI answers its own
# permission questions and Discord never hears about them; with it, every approval arrives on stdout
# as a `can_use_tool` control request for us to put in front of the user.
LIVE_ARGS: tuple[str, ...] = (
    "-p",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
    "--permission-prompt-tool",
    "stdio",
)

# How long to wait on the CLI answering our `initialize` handshake before giving up on the process.
HANDSHAKE_TIMEOUT: float = 30.0
# How long to wait on any other control request. Short: these are answered locally with no model call,
# so a slow one means something is wrong rather than something is busy.
CONTROL_TIMEOUT: float = 15.0
# A turn has no ceiling. It used to have one, and a wall clock is simply the wrong instrument: the
# turns that ran past it were the long *working* ones — a big refactor, a slow test suite — and
# abandoning those is abandoning exactly the work worth waiting for, while a turn that dies in the
# first ten seconds sat there for the full fifteen minutes regardless.
#
# What replaces it watches the process instead. The turn ends when the CLI says so, when the process
# is gone, or when it has been silent for long enough to call it hung — and "silent" excludes the
# time a permission prompt is on screen, since a turn waiting on a person is not a turn in trouble.
#
# How often the watchdog looks. This is also how quickly a dead process is noticed, which is the
# case the old ceiling handled worst: a crash used to hang the turn for the rest of the fifteen.
TURN_HEARTBEAT: float = 15.0
# Silence, with the process alive and nothing waiting on a human, before the turn is called hung.
# Generous on purpose — a single long tool call (a build, a test suite, a big search) is silent for
# its whole duration and is not stuck. This is the backstop for a wedged process, not a pace setter.
TURN_SILENCE_LIMIT: float = 1800.0
# A live process holds a few hundred MB of node, so a session nobody is talking to gets reaped. The
# next message respawns it with `--resume`, which restores the conversation in full — verified; a
# codeword planted before a kill came back after one. See `LiveSession.spawn`.
IDLE_REAP_MINUTES: int = 30
REAP_INTERVAL_MINUTES: int = 5
# How long teardown will wait for in-flight turns to seal their own status displays before giving up
# on them. A reload that hangs on a Discord edit is worse than a status message left mid-spin.
SEAL_TIMEOUT: float = 5.0
# Closing lines for a turn that ended without an answer. `STOPPED_NOTE` is `.stop`; `RELOAD_NOTE` is
# the cog going away underneath a turn, which is recoverable in a way `.stop` is not — the session
# and its transcript are untouched, so the same prompt sent again resumes exactly where it was.
STOPPED_NOTE: str = "Stopped. Anything already written to your workspace is still there."
RELOAD_NOTE: str = "Interrupted by a reload — the session itself is fine. Send that again to pick it back up."

DEFAULT_MODEL: str = "claude-sonnet-5"

# Keyed by the short name a user picks; the value reaches the CLI's `--model`.
MODELS: dict[str, str] = {
    "sonnet": DEFAULT_MODEL,
    "opus": "claude-opus-5",
    "haiku": "claude-haiku-4-5",
    "fable": "claude-fable-5",
}

MODEL_CHOICES: list[app_commands.Choice[str]] = [app_commands.Choice(name=name.capitalize(), value=name) for name in MODELS]


@dataclass(frozen=True)
class PermissionMode:
    """One entry in the mode table; what the CLI does when a tool wants permission.

    These are the CLI's own `--permission-mode` values one-for-one, no translation layer. Every user
    who reaches this cog is already trusted, so a mode is a *convenience* — how often you want to be
    asked — and not a security boundary. There are no tool allowlists or deny rules anywhere in this
    module for the same reason.

    Attributes
    ----------
    value: :class:`str`
        The `--permission-mode` value, and what `set_permission_mode` is given at runtime.
    description: :class:`str`
        The one line shown beside the mode on the panel's select.

    """

    value: str
    description: str


# `default` is deliberately first and is the one that actually prompts. It is accepted by the CLI but
# absent from `--help`'s choice list, so do not "fix" it by removing it; tested against 2.1.220.
MODES: dict[str, PermissionMode] = {
    "default": PermissionMode(value="default", description="Asks before each tool. The usual choice."),
    "plan": PermissionMode(value="plan", description="Read-only. Plans, then asks to leave plan mode."),
    "edits": PermissionMode(value="acceptEdits", description="Auto-approves file edits, asks for the rest."),
    "auto": PermissionMode(value="auto", description="Decides for itself when to ask."),
    "dontask": PermissionMode(value="dontAsk", description="Runs without asking, but still refuses the risky."),
    # "manual": PermissionMode(value="manual", description="Denies anything ungranted instead of asking."),
    "bypass": PermissionMode(value="bypassPermissions", description="No approval gate on any tool. Use with care."),
}

DEFAULT_MODE: str = MODES["default"].value

# The name a user picks at `.effort` is the value the CLI takes, so this is a tuple rather than a
# mapping; there is no short name to translate.
#
# `auto` is real but is *command only*. Tested against 2.1.220: `/effort auto` is accepted, while
# `--effort auto` warns "Unknown --effort value 'auto' — ignoring it" and silently falls back to the
# default. See `EFFORT_FLAG_VALUES` and `LiveSession.spawn`, which is where that asymmetry is handled.
EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max", "auto")
# The subset `--effort` will actually accept at spawn. Anything outside this is applied by slash
# command after the handshake instead.
# TODO: Understand why these don't align with the EFFORTS key.
EFFORT_FLAG_VALUES: frozenset[str] = frozenset({"low", "medium", "high", "xhigh", "max"})
DEFAULT_EFFORT: str = "medium"

EFFORT_DESCRIPTIONS: dict[str, str] = {
    "low": "Answers directly; fewest tool calls.",
    "medium": "Balanced - a good default for chat.",
    "high": "Explores more before answering.",
    "xhigh": "Best for coding and agentic work.",
    "max": "Deepest reasoning; slowest and priciest.",
    "auto": "Lets Claude Code pick the level per turn.",
}

# Slash commands the CLI handles itself, sent as ordinary user messages. Tested: `/effort high` comes
# back `num_turns: 0` and `total_cost_usd: 0` — it is intercepted locally, never reaching the model,
# so it costs nothing and needs no respawn.
EFFORT_COMMAND: str = "/effort {level}"

# ---------------------------------------------------------------------------
# Context and compaction
#
# A Discord session can stay open for days, so filling the context window is the normal end of a long
# conversation rather than an edge case. The CLI compacts by itself at `autoCompactThreshold` and says
# so on a `system` event, which is the only warning anyone gets that earlier turns have been summarised
# away. In a terminal that is visible; in a thread it would be silent, and a session that quietly
# forgot the first half of its own conversation is the sort of thing that wastes an afternoon.
# ---------------------------------------------------------------------------

# The `system` subtype the CLI emits when a conversation has been compacted, carrying `compactMetadata`
# with `preservedSegment` / `preservedMessages`. Displayed as "Conversation compacted".
COMPACT_EVENT: str = "compact_boundary"
# The CLI withdrawing a `can_use_tool` it already sent, carrying only the `request_id` it retires.
# Seen when a turn ends before its prompt was answered, which an interrupt does every time.
CANCEL_EVENT: str = "control_cancel_request"
# The `model` the CLI stamps on an assistant message it wrote itself instead of getting one back from
# the API. Verified against 2.1.220.
SYNTHETIC_MODEL: str = "<synthetic>"
# The synthetic bodies that are filler rather than an answer, and are dropped instead of posted.
# `/clear` is a local command, so the turn `.clear` runs as never reaches the model — and the CLI
# fills the silence with a synthetic `(no content)`, which was arriving in the thread looking like
# something the model had said. The interrupt notices are the same thing from the other end: a stopped
# turn already says so in our own words, via :meth:`TurnContext.interrupted_note`. Only these exact
# strings are dropped; anything else synthetic — an API error, say — is worth reading and still goes
# through.
SYNTHETIC_NOISE: frozenset[str] = frozenset({"(no content)", "[Request interrupted by user]", "[Request interrupted by user for tool use]"})
# Warn once a session is this far towards its own auto-compact threshold. Early enough that `.new` or
# a deliberate `/compact` is still a choice rather than something that already happened.
CONTEXT_WARN_RATIO: float = 0.85
# How wide the usage bar on `.context` is drawn.
CONTEXT_BAR_WIDTH: int = 24


@dataclass(frozen=True)
class ContextUsage:
    """A snapshot of one session's context window, from the `get_context_usage` control request.

    Read out of band, so it is free, costs no turn and can be taken while the session is working.

    Attributes
    ----------
    total: :class:`int`
        Tokens currently in the window.
    maximum: :class:`int`
        The window's size.
    percentage: :class:`float`
        How full it is, as the CLI itself reports it.
    threshold: :class:`Optional[int]`
        Where the CLI will compact on its own, when it is set to.
    auto_enabled: :class:`bool`
        Whether it will compact on its own at all.
    categories: :class:`list[tuple[str, int]]`
        What is taking up the room, largest first.

    """

    total: int
    maximum: int
    percentage: float
    threshold: Optional[int] = field(default=None)
    auto_enabled: bool = field(default=True)
    categories: list[tuple[str, int]] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict) -> ContextUsage:
        """Builds a snapshot from the control response's body."""
        raw: Any = payload.get("categories")
        categories: list[tuple[str, int]] = []
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict) and isinstance(entry.get("tokens"), int):
                    # "Free space" is what is *left*, not what is used; listing it beside the real
                    # consumers reads as the biggest one of them.
                    if str(entry.get("name")) == "Free space":
                        continue
                    categories.append((str(entry.get("name") or "?"), int(entry["tokens"])))
        categories.sort(key=lambda item: item[1], reverse=True)

        total: int = int(payload.get("totalTokens") or 0)
        maximum: int = int(payload.get("maxTokens") or 0)
        raw_percentage: Any = payload.get("percentage")
        return cls(
            total=total,
            maximum=maximum,
            percentage=float(raw_percentage) if isinstance(raw_percentage, (int, float)) else (total / maximum * 100 if maximum else 0.0),
            threshold=payload.get("autoCompactThreshold") if isinstance(payload.get("autoCompactThreshold"), int) else None,
            auto_enabled=bool(payload.get("isAutoCompactEnabled", True)),
            categories=categories,
        )

    @property
    def nearing_compact(self) -> bool:
        """Returns whether the session is close enough to auto-compaction to be worth mentioning."""
        if not self.auto_enabled or self.threshold is None or self.threshold <= 0:
            return False
        return self.total >= self.threshold * CONTEXT_WARN_RATIO

    @property
    def bar(self) -> str:
        """Returns the usage drawn as a bar, so the number has something to sit against."""
        filled: int = 0 if self.maximum <= 0 else min(CONTEXT_BAR_WIDTH, round(self.total / self.maximum * CONTEXT_BAR_WIDTH))
        return f"`{'█' * filled}{'░' * (CONTEXT_BAR_WIDTH - filled)}`"

    def summary(self) -> str:
        """Returns the one line shown when a session is asked how full it is."""
        line: str = f"{self.bar} {self.percentage:.0f}% — {self.total:,} / {self.maximum:,} tokens"
        if self.threshold is not None and self.auto_enabled:
            line += f"\n-# Compacts on its own at {self.threshold:,}."
        elif not self.auto_enabled:
            line += "\n-# Auto-compaction is off for this session."
        return line


# The rate-limit windows worth showing, in the order they matter, keyed by their name in the
# `get_usage` response. There are a dozen more in there and almost all are `null` on any given plan.
USAGE_WINDOWS: tuple[tuple[str, str], ...] = (("five_hour", "Session (5h)"), ("seven_day", "Week"))
# Flag a window at this utilisation. The account is shared, so one session burning the budget is
# everybody's problem, and the reset time is the only useful thing to say about it.
USAGE_WARN_PERCENT: int = 80
# Refuse to start a turn at this utilisation; the account has nothing left to spend and the turn would
# only fail on the far side of a status message.
USAGE_BLOCK_PERCENT: int = 100
# How long a usage reading stays good for. Measured at roughly 275ms a call — free in tokens, but not
# free in latency, and the windows it reports are five hours and seven days wide, so re-reading one per
# turn would buy nothing. A stale reading can only ever be as wrong as having no check at all was.
USAGE_CACHE_SECONDS: float = 120.0
# The CLI pushes a `rate_limit_event` whenever the account's standing changes, unasked and mid-turn.
# `blocked_by_usage` only ever looks before a turn starts, off a reading up to `USAGE_CACHE_SECONDS`
# old, so this is the only notice that an account went spent while a turn was already running.
RATE_LIMIT_EVENT: str = "rate_limit_event"
# `rate_limit_info.status`, as seen in the binary. `allowed` fires on a perfectly healthy turn and is
# the routine case; the rest all mean the cached reading is now wrong in the direction that matters.
RATE_LIMIT_OK: str = "allowed"


@dataclass(frozen=True)
class RateLimit:
    """One usage window from the `get_usage` control request.

    Attributes
    ----------
    label: :class:`str`
        The window's display name.
    percent: :class:`int`
        How much of it has been spent.
    resets_at: :class:`Optional[datetime.datetime]`
        When it rolls over.

    """

    label: str
    percent: int
    resets_at: Optional[datetime.datetime] = field(default=None)

    @property
    def strained(self) -> bool:
        """Returns whether this window is far enough along to be worth mentioning."""
        return self.percent >= USAGE_WARN_PERCENT

    @property
    def exhausted(self) -> bool:
        """Returns whether this window has nothing left, so a turn started now would only fail."""
        return self.percent >= USAGE_BLOCK_PERCENT

    def line(self) -> str:
        """Returns this window rendered for a reply."""
        text: str = f"- {self.label}: {self.percent}% used"
        if self.resets_at is not None:
            text += f", resets <t:{int(self.resets_at.timestamp())}:R>"
        return text


@dataclass(frozen=True)
class UsageSnapshot:
    """The host account's usage, from the `get_usage` control request.

    Worth having structured rather than scraped: the previous cog could only learn about a spent
    budget by pattern-matching the CLI's error text *after* a run had already failed, whereas this is
    out of band, free, and can be read before anything is spent.

    Attributes
    ----------
    subscription: :class:`str`
        The plan the shared account is on.
    session_cost: :class:`float`
        What this session has cost so far.
    limits: :class:`list[RateLimit]`
        The usage windows that were reported.

    """

    subscription: str = field(default="unknown")
    session_cost: float = field(default=0.0)
    limits: list[RateLimit] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict) -> UsageSnapshot:
        """Builds a snapshot from the control response's body."""
        session: Any = payload.get("session")
        raw_limits: Any = payload.get("rate_limits")
        limits: list[RateLimit] = []

        if isinstance(raw_limits, dict):
            for key, label in USAGE_WINDOWS:
                window: Any = raw_limits.get(key)
                if not isinstance(window, dict) or not isinstance(window.get("utilization"), (int, float)):
                    continue
                limits.append(
                    RateLimit(label=label, percent=int(window["utilization"]), resets_at=_parse_reset(window.get("resets_at"))),
                )

        return cls(
            subscription=str(payload.get("subscription_type") or "unknown"),
            session_cost=float(session.get("total_cost_usd") or 0.0) if isinstance(session, dict) else 0.0,
            limits=limits,
        )

    @property
    def worst(self) -> Optional[RateLimit]:
        """Returns the most spent window, whatever state it is in."""
        return next(iter(sorted(self.limits, key=lambda entry: entry.percent, reverse=True)), None)

    @property
    def strained(self) -> Optional[RateLimit]:
        """Returns the most spent window that is worth warning about, if any."""
        worst: Optional[RateLimit] = self.worst
        return worst if worst is not None and worst.strained else None

    @property
    def exhausted(self) -> Optional[RateLimit]:
        """Returns the window that has run out, if one has."""
        worst: Optional[RateLimit] = self.worst
        return worst if worst is not None and worst.exhausted else None

    def summary(self) -> str:
        """Returns the usage block shown by `.usage`."""
        lines: list[str] = [f"**Usage** — shared `{self.subscription}` account"]
        lines.extend(limit.line() for limit in self.limits)
        if not self.limits:
            lines.append("-# No rate limit information was reported.")
        lines.append(f"-# This session has cost ${self.session_cost:.4f}.")
        return "\n".join(lines)


def _parse_reset(raw: Any) -> Optional[datetime.datetime]:
    """Returns a reset timestamp parsed from the CLI's ISO 8601 string, or `None`."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Per-user workspaces
#
# Every user gets their own directory *outside* this repository, and the CLI is started with it as
# the cwd. That placement is the whole isolation mechanism and it is load-bearing in two ways:
#
# - `CLAUDE.md` is discovered by walking **up** from the cwd. A workspace inside `Kuma_Kuma/` would
#   inherit `Kuma_Kuma/CLAUDE.md` and `gitHub/CLAUDE.md`, which is exactly the shared instruction set
#   a personal session should not be reading.
# - The CLI's memory directory is named after the cwd (`~/.claude/projects/<slug>/memory/`), so a
#   per-user cwd gets a per-user memory for free, with nothing for us to manage.
#
# The cwd is the *user's* root and not the session's, on purpose. Per-thread would key the memory to
# a single forum post, and the ask was for a personal experience that follows the person.
#
# `~/.claude/CLAUDE.md` still applies to every session; the only ways to suppress it are `--safe-mode`
# and `--bare`, which respectively cost skills and break subscription auth. Left alone deliberately.
# ---------------------------------------------------------------------------

USERS_ROOT: Path = Path.home().joinpath(".kuma_claude")
# The one announcement shown to anyone opening a session. On disk rather than in memory so a restart
# does not quietly drop it, and beside the user roots because it is the cog's data, not the repo's.
# JSON rather than bare text so who set it and when come along with it — an announcement nobody can
# date is one nobody can tell is stale.
ANNOUNCEMENT_FILE: Path = USERS_ROOT.joinpath("announcement.json")
# Ephemeral replies share Discord's 2000 character budget with everything else `ask` says, and an
# announcement long enough to need more than this wants to be a message in a channel.
ANNOUNCEMENT_MAX_LENGTH: int = 1000
# Generated files land here rather than in the cwd itself, so a user's root stays their own and does
# not fill up with a hundred sessions' scratch output.
SESSIONS_SUBDIR: str = "sessions"
ATTACHMENTS_SUBDIR: str = "attachments"
USER_MEMORY_NAME: str = "CLAUDE.md"

# Written once when a user's directory is created and never touched again — it is theirs to edit from
# inside a session after that. Deliberately thin: it orients the session and otherwise gets out of the
# way, because anything opinionated here would be the shared-instructions problem all over again.
USER_MEMORY_TEMPLATE: str = """# {name}

This is your personal Claude Code workspace, reached through Kuma Kuma Bear on Discord.

- Anything you write here is yours alone; no other Discord user's session can see it.
- Files you generate belong in `{sessions}/<session>/`, which is set per session.
- Edit this file whenever you like -- it is loaded at the start of every session you open.

## Preferences

_Nothing set yet. Ask Claude to write your preferences here and they will persist._
"""

# Directories granted on top of the cwd. The project root is here because a trusted user asking about
# the bot's own code is the common case, and a cwd outside the repo cannot reach it otherwise.
PROJECT_ROOT: Path = Path(__file__).parent.parent

# Appended to the first prompt of a session so generated files land somewhere predictable.
OUTPUT_DIR_NOTICE: str = (
    "\n\n[Any file you create, download or write out for this session must live inside `{directory}`. "
    "Create it if it does not exist. Do not write generated files anywhere else.]"
)

# ---------------------------------------------------------------------------
# Transcripts
#
# The CLI keeps its own conversation log per session and `--resume` reads it back, which is what makes
# reaping an idle process safe. It prunes that log on `cleanupPeriodDays` (30 by default), so a
# session left to age out loses its history for good; snapshotting the file is what lets a dormant
# post be revived with its tool calls intact.
# ---------------------------------------------------------------------------

CLAUDE_PROJECTS: Path = Path.home().joinpath(".claude", "projects")
# The CLI names a transcript directory after the working directory it ran in, with `/`, `_` and `.`
# all flattened to `-`. The `.` entry matters as much as the others: without it every path containing
# a dotted directory resolves to a name the CLI never wrote.
TRANSCRIPT_SLUG_TABLE: dict[str, str] = {"/": "-", "_": "-", ".": "-"}
TRANSCRIPT_NAME: str = "transcript-{session_id}.jsonl.gz"

# ---------------------------------------------------------------------------
# Forum layout; one category per guild, one forum per user, one thread per session.
# ---------------------------------------------------------------------------

CATEGORY_NAME: str = "Claude Sessions"
FORUM_NAME_FORMAT: str = "claude-{name}"
FORUM_TOPIC: str = (
    "Each post in this forum is one live Claude Code session. Reply in a post to continue it; start a "
    "new one with `/claude ask`. Dot commands (`.help`) change how the session runs."
)
MAX_SESSIONS_PER_USER: int = 5
SESSION_MAX_AGE_DAYS: int = 30
EXPIRED_PREFIX: str = "[EXPIRED] "
CLOSED_PREFIX: str = "[CLOSED] "
CLEANUP_INTERVAL_HOURS: int = 6
# Static, and plain unicode. A thread name is not message content, so `<t:...:R>` arrives as literal
# text there and custom app emoji never render — both of which rules out the timestamped placeholder
# the one-shot cog used, along with the locale guessing it needed to build one.
PLACEHOLDER_TITLE: str = "🆕 - New Session -"
THREAD_TITLE_SIZE: int = 100 - max(len(EXPIRED_PREFIX), len(CLOSED_PREFIX))
# How many archived posts to walk per forum when sweeping. Archived threads are not cached, so each
# page is a REST call; 30 days of one user's sessions is far short of this.
ARCHIVED_SWEEP_LIMIT: int = 200
# A workspace whose thread we cannot find is only orphaned if it has also been sitting there a while.
# Without this, a session created between its thread being made and the cache catching up is deleted
# out from under itself.
WORKSPACE_GRACE_HOURS: int = 24

GUILD_PERMISSIONS: tuple[str, ...] = ("manage_channels", "view_channel")
FORUM_PERMISSIONS: tuple[str, ...] = (
    "view_channel",
    "send_messages",
    "send_messages_in_threads",
    "create_public_threads",
    "manage_threads",
    "manage_messages",
    "read_message_history",
    "attach_files",
)

# Discord's own limit is 2000. The slack covers the separator and the fence `balance_markup` may add
# to either end of a chunk.
MESSAGE_CHUNK_SIZE: int = 1800
CODE_FENCE: str = "```"
ESCAPED_CODE_SPAN: re.Pattern[str] = re.compile(r"(?<!`)`((?:[^`\n\\]|\\.)*?\\`(?:[^`\n\\]|\\.)*?)`(?!`)")
MAX_ATTACHMENT_SIZE: int = 25 * 1024 * 1024
MAX_REPLY_FILES: int = 8
MAX_REPLY_FILE_SIZE: int = 8 * 1024 * 1024
MENTION: re.Pattern[str] = re.compile(r"<@[!&]?\d+>")

# A Discord jump URL pasted into a prompt. Full URLs only, on purpose: a bare snowflake is
# indistinguishable from any other long number someone might mention, and Discord has no
# lookup-by-ID endpoint, so following one would cost a channel-by-channel search on a guess.
MESSAGE_LINK: re.Pattern[str] = re.compile(
    r"(?:https?://)?(?:\w+\.)?discord(?:app)?\.com/channels/(?P<guild>\d{15,25}|@me)/(?P<channel>\d{15,25})/(?P<message>\d{15,25})"
)
# How many linked messages one prompt pulls in. Each costs a REST fetch and lands in the prompt as a
# path, so the cap keeps a wall of pasted links from quietly becoming a wall of context.
MAX_PROMPT_LINKS: int = 3

REPLY_SEPARATOR: str = f"-# {'─' * 30}"

# What `to_discord_markdown` needs to spot in a model's markdown. Discord renders none of these, so
# they arrive as the literal characters unless they are rewritten.
# One cell of a `|---|:--:|` rule; the colons are alignment markers and mean nothing to us.
TABLE_DELIMITER: re.Pattern[str] = re.compile(r":?-{1,}:?")
# A `---`, `***` or `___` horizontal rule on a line of its own.
TABLE_RULE: re.Pattern[str] = re.compile(r"(?:-{3,}|\*{3,}|_{3,})")
# Emphasis inside a table cell, which is noise once the cell is inside a fence.
TABLE_EMPHASIS: re.Pattern[str] = re.compile(r"\*\*|__|\*|`")
# A `- [ ]` / `- [x]` task list marker, keeping whatever indent it was nested at.
TASK_ITEM: re.Pattern[str] = re.compile(r"^(?P<indent>\s*)[-*+] \[(?P<mark>[ xX])\] ")
TASK_TODO: str = "\U00002610"  # BALLOT BOX - ☐
TASK_DONE: str = "\U00002611"  # BALLOT BOX WITH CHECK - ☑

# ---------------------------------------------------------------------------
# Tool activity
# What the thread shows while a turn is in flight. Tool names and targets, plus — on the inline
# transcript only — a single truncated line of the result. A tool's full output is routinely a whole
# file, so it is never echoed; but a call with nothing under it cannot be told apart from one still
# running, and that one line is what buys the distinction.
# ---------------------------------------------------------------------------

TOOL_VERBS: dict[str, str] = {
    "Read": "Read",
    "Write": "Write",
    "Edit": "Edit",
    "MultiEdit": "Edit",
    "NotebookEdit": "Edit",
    "Bash": "Run",
    "BashOutput": "Check",
    "KillShell": "Stop",
    "Glob": "Search",
    "Grep": "Search",
    "WebFetch": "Fetch",
    "WebSearch": "Search",
    "Task": "Delegate",
    "TodoWrite": "Plan",
    "ExitPlanMode": "Finish",
    "SlashCommand": "Run",
    "AskUserQuestion": "Ask",
}
TOOL_TARGET_KEYS: tuple[str, ...] = ("file_path", "notebook_path", "command", "pattern", "url", "query", "description", "prompt")
TOOL_TARGET_SIZE: int = 60
TOOL_LOG_VISIBLE: int = 12
PENDING_MARK: str = "▸"
DONE_MARK: str = "✓"
WAITING_MARK: str = "⏸"
ERROR_MARK: str = "✖"

# Prefixes a tool's result, beneath the call that produced it. Subtext, because a result is
# supporting detail for the line above it and should not compete with it.
RESULT_PREFIX: str = "-# ⎿ "
TOOL_RESULT_SIZE: int = 110

# The raised count that stands for a run of identical calls. Discord has no subtext *inside* a line —
# `-#` renders literally anywhere but the start of one, which is what the old ` -# x3` tail actually
# put on screen — so a raised glyph is the nearest thing to setting the count apart from the call it
# is counting while keeping it on the same line.
SUPERSCRIPT_DIGITS: dict[str, str] = {
    "0": "⁰",
    "1": "¹",
    "2": "²",
    "3": "³",
    "4": "⁴",
    "5": "⁵",
    "6": "⁶",
    "7": "⁷",
    "8": "⁸",
    "9": "⁹",
}
SUPERSCRIPT_TIMES: str = "ˣ"


class Verbosity(StrEnum):
    """How much of a turn reaches the thread while it is running.

    Two displays and a filter over one of them. VERBOSE drives the rolling transcript, where a tool,
    its result and the prose between them are interleaved in the order they happened; the other three
    drive the pinned status box and differ only in what they are willing to write onto it.

    A person's own choice, so it lives in `/preferences` rather than on the session panel — the same
    reasoning that keeps `Preferences` apart from `moderator`'s guild settings.
    """

    VERBOSE = "verbose"
    DEFAULT = "default"
    CHATTER = "chatter"
    SILENT = "silent"

    @property
    def title(self) -> str:
        """Returns the name the `/preferences` select shows."""
        return self.value.title()

    @property
    def summary(self) -> str:
        """Returns the line beneath the name, saying what picking it does."""
        return VERBOSITY_SUMMARIES[self]


VERBOSITY_SUMMARIES: dict[Verbosity, str] = {
    Verbosity.VERBOSE: "Tools, their results and every reply, in the order they happened.",
    Verbosity.DEFAULT: "Tools and their targets in one status box, replies beneath it.",
    Verbosity.CHATTER: "Replies only. The status line still says what it is working on.",
    Verbosity.SILENT: "Nothing until the turn finishes, then the reply.",
}
# What `Preferences` stores this under; namespaced by cog, and changing it puts everyone back on
# :attr:`Verbosity.DEFAULT`.
VERBOSITY_KEY: str = "claude.verbosity"

# How long a permission prompt stays live in Discord before it answers itself. The CLI blocks the
# whole turn waiting on us, so this can never be `None`; an unanswered prompt would wedge the session
# until the process was reaped.
PROMPT_TIMEOUT: float = 900.0
# What an expired prompt sends back. Denying is the only safe default — an approval nobody gave is
# the one outcome we must never manufacture.
PROMPT_TIMEOUT_MESSAGE: str = "The user did not answer in time, so this was not approved."
# Marks a prompt the CLI itself withdrew with `control_cancel_request`, which happens when the turn it
# belonged to ends early — an interrupt, mostly. Never sent anywhere: the request is already gone, so
# this only labels what the buttons turned into.
PROMPT_WITHDRAWN_MESSAGE: str = "Claude withdrew this request."

# Headroom kept clear inside a prompt view for everything that is *not* the body: the heading, the
# outcome line the settled form adds afterwards, and the note saying the body was cut. Discord
# budgets its 4000 characters across the whole view, so a body sized against that ceiling on its own
# overflows it the moment anything is put beside it — which is exactly what used to happen.
PROMPT_TEXT_RESERVE: int = 256
PLAN_ATTACHMENT_NAME: str = "plan.md"

# The longest free-text answer a question takes. Discord's own ceiling for a paragraph input is 4000;
# this is smaller because the answer is going into a blocked turn as a tool result, not into the
# conversation, and a wall of text there is better sent as the next message.
OTHER_ANSWER_LIMIT: int = 1000
# Discord's own ceilings for a select: 25 options, and 100 characters on an option's label and on its
# description. A `multiSelect` question offering more than 25 is not a thing the schema allows, but
# the menu is built from whatever arrives rather than from what should have.
SELECT_OPTION_LIMIT: int = 25
SELECT_LABEL_SIZE: int = 100
# What a single-choice option's own block says, in the session panel's shape: the label in bold with
# the model's gloss as subtext under it, and the button beside it as the accessory.
#
# The accessory carries the label. Two shapes were tried and neither held: a repeated "Choose" said
# nothing about which button was which, and bare numbers read as too sparse beside the text. Saying
# the label twice is the cost, and it is only affordable because an accessory sits alone on its row --
# the wrapping that made long labels unusable was five of them across a single action row.
OPTION_BUTTON_SIZE: int = 40
OPTION_DESCRIPTION_SIZE: int = 180
# Discord's own ceiling on a button label. Only the standing-approval button gets near it, since it
# carries a tool's display name and those are not ours to keep short.
BUTTON_LABEL_SIZE: int = 80
QUESTION_HINT_SINGLE: str = "-# Choose one, or answer in your own words."
QUESTION_HINT_MULTI: str = "-# Pick as many as apply, or answer in your own words."

# The line that pings the session's owner, closing every prompt. A blocked turn is silent by
# definition — the CLI is waiting and nothing else is being posted — so a prompt that went up during
# a long task could sit unseen until it timed out fifteen minutes later and denied itself.
#
# It lives *inside* the view because a Components V2 message cannot carry `content`; a mention in a
# text display notifies the same way, and this keeps it to one message instead of a herald above every
# prompt. Only the live prompt carries it: :class:`SettledPrompt` drops the line, since an answered
# question is nobody's turn to act.
#
# The deadline is a Discord relative timestamp, so it counts itself down in the reader's own clock and
# keeps doing so on scrollback — "in 12 minutes" is what someone coming back to a pinged thread needs
# to know, and it is the one thing a static view cannot say for itself. Denial by timeout is a real
# outcome here, not a formality, so the deadline is worth stating before it arrives.
PROMPT_MENTION_LINE: str = "-# {mention} — this one is waiting on you, until {deadline}."

# The two `can_use_tool` requests a standing approval must never answer. Neither is asking for
# permission: one asks whether a plan is right to start on, the other asks the user a question
# outright, and both need the person in front of them every time.
UNREMEMBERABLE_TOOLS: frozenset[str] = frozenset({"ExitPlanMode", "AskUserQuestion"})

# The other end of the same idea, and the turn's only other notification. A turn can run for minutes
# with the thread silent throughout, which is the point — but silence is only bearable if something
# breaks it when the answer is actually there, otherwise the reader is left polling the post.
TURN_MENTION_LINE: str = "-# {mention} — this one is done."

# ---------------------------------------------------------------------------
# Access
#
# Deliberately its own table. The bot-wide owner list gates who can administer Kuma Kuma; this gates
# who may open a Claude session, which is a different question with a different (larger) answer.
# ---------------------------------------------------------------------------

ACCESS_SETUP_SQL: str = """
CREATE TABLE IF NOT EXISTS claude_users (
    id INTEGER PRIMARY KEY NOT NULL,
    userid INTEGER NOT NULL UNIQUE,
    added_by INTEGER NOT NULL,
    added_at REAL NOT NULL)
"""


class ClaudeUser(TypedDict):
    """One row of `claude_users`; a Discord user allowed to open sessions."""

    id: int
    userid: int
    added_by: int
    added_at: float


# ---------------------------------------------------------------------------
# Session state
# The thread's opening post is the only source of truth. Its Components V2 panel carries the machine
# readable state in small text, so a bot restart re-reads it from Discord instead of a database.
# ---------------------------------------------------------------------------

STATE_LINE_FORMAT: str = (
    "-# session `{session_id}`\n-# model `{model}` · mode `{mode}` · effort `{effort}`\n-# owner {user_id} · started {started}"
)
STATE_LINE_PATTERN: re.Pattern[str] = re.compile(
    r"session `(?P<session_id>[^`]+)`"
    r"\n-# model `(?P<model>[^`]+)` · mode `(?P<mode>[^`]+)` · effort `(?P<effort>[^`]+)`"
    r"\n-# owner <@!?(?P<user_id>\d+)>",
)


class SessionStatus(StrEnum):
    """How a session post is being rendered; the thread title prefix is the stored form."""

    ACTIVE = "active"
    CLOSED = "closed"
    EXPIRED = "expired"

    @property
    def prefix(self) -> str:
        """Returns the thread title prefix that marks this status; empty for a live session."""
        if self is SessionStatus.CLOSED:
            return CLOSED_PREFIX
        if self is SessionStatus.EXPIRED:
            return EXPIRED_PREFIX
        return ""

    @property
    def dormant(self) -> bool:
        """Returns whether the session is locked; no replies, panel controls disabled."""
        return self is not SessionStatus.ACTIVE

    @classmethod
    def of(cls, thread: discord.Thread) -> SessionStatus:
        """Returns the status recorded in a session thread's title."""
        for status in (cls.CLOSED, cls.EXPIRED):
            if thread.name.startswith(status.prefix):
                return status
        return cls.ACTIVE

    @staticmethod
    def strip(name: str) -> str:
        """Returns a thread title with any status prefix removed."""
        return name.removeprefix(CLOSED_PREFIX).removeprefix(EXPIRED_PREFIX)


PANEL_HEADERS: dict[SessionStatus, str] = {
    SessionStatus.ACTIVE: "Claude Code Session",
    SessionStatus.CLOSED: "Session Closed",
    SessionStatus.EXPIRED: "Session Expired",
}
PANEL_NOTICES: dict[SessionStatus, str] = {
    # The `.help` pointer used to live here and now ships as a hint on `/claude ask`, so someone on
    # their twentieth session stops being told a thing they learned on their first.
    SessionStatus.ACTIVE: "Reply in this post to continue the session.",
    SessionStatus.CLOSED: (
        "This session was closed from its panel. Its files are kept until you delete the post, and "
        "**Restore Session** picks it back up from where it left off."
    ),
    SessionStatus.EXPIRED: (
        f"This session passed {SESSION_MAX_AGE_DAYS} days without activity, so Claude Code has dropped its "
        "transcript and it can no longer be resumed. Its files are kept until you delete the post."
    ),
}

# Custom IDs are static so the panel survives a restart as a persistent view. The session a component
# acts on is resolved from the interaction's own thread rather than baked into the ID.
PANEL_MODEL_ID: str = "claude:panel:model"
PANEL_MODE_ID: str = "claude:panel:mode"
PANEL_EFFORT_ID: str = "claude:panel:effort"
PANEL_FILES_ID: str = "claude:panel:files"
PANEL_CLEAR_ID: str = "claude:panel:clear"
PANEL_HELP_ID: str = "claude:panel:help"
PANEL_CLOSE_ID: str = "claude:panel:close"
PANEL_RESTORE_ID: str = "claude:panel:restore"
PANEL_INTERRUPT_ID: str = "claude:panel:interrupt"

# Numeric component IDs; these address a *layout* part rather than an interactive one, so the panel
# can be read back by pointing at a component instead of pattern matching the whole payload.
PANEL_HEADER_COMPONENT_ID: int = 1
PANEL_STATE_COMPONENT_ID: int = 2
PANEL_TRANSCRIPT_COMPONENT_ID: int = 3

PANEL_CONTENT_LIMIT: int = 4000
PANEL_COMPONENT_LIMIT: int = 40

PANEL_THUMBNAIL_EMOJI: str = "kuma_peak"
PANEL_THUMBNAIL_ALT: str = "Kuma Kuma Bear, peeking."

PANEL_MODEL_TITLE: str = "Model"
PANEL_MODE_TITLE: str = "Permission Mode"
PANEL_EFFORT_TITLE: str = "Effort"

PANEL_LIVE_NOTE: str = "-# ● Live — the CLI is running and holding this conversation in memory."
PANEL_PARKED_NOTE: str = "-# ○ Parked — the process was reaped for idling; your next message resumes it."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _is_owner(interaction: discord.Interaction) -> bool:
    """Returns whether the caller owns the bot; the check guarding the access commands.

    Only the commands that *administer* the allowlist use this. Opening a session is gated by
    :meth:`ClaudeCog.may_use` against `claude_users`, which is the whole point of the split.
    """
    return await interaction.client.is_owner(interaction.user)  # type: ignore[arg-type]


def claude_binary() -> Optional[str]:
    """Returns the path to the `claude` executable, or `None` when it cannot be found.

    Resolved per call rather than at import; the CLI is upgraded in place often enough that a bot left
    running for weeks should not be holding a path it looked up once at startup.
    """
    found: Optional[str] = shutil.which("claude")
    if found is not None:
        return found

    # `os.access` rather than `is_file`, as these are the installer's launchers and a symlink to a
    # version that has since been pruned still answers `is_file` on the dangling name.
    return next((str(path) for path in CLAUDE_FALLBACK_PATHS if os.access(path, os.X_OK)), None)


@dataclass(frozen=True)
class Announcement:
    """The single notice shown to anyone opening a session.

    Attributes
    ----------
    text: :class:`str`
        What to say.
    author_id: :class:`int`
        Who set it, so a stale one can be asked about.
    posted_at: :class:`float`
        Unix time it was set, rendered as a Discord timestamp when shown.

    """

    text: str
    author_id: int = field(default=0)
    posted_at: float = field(default=0.0)

    def summary(self, *, emoji: str) -> str:
        """Returns the announcement rendered for an ephemeral reply.

        Takes the emoji rather than reaching for the table, the same way `render_hint` does — this is
        a plain record and the table belongs to the cog.
        """
        line: str = f"{emoji} **Announcement**\n{self.text}"
        if self.posted_at:
            line += f"\n-# Set by <@{self.author_id}> <t:{int(self.posted_at)}:R>."
        return line


def read_announcement() -> Optional[Announcement]:
    """Reads the stored announcement, or `None` when there is none.

    Every failure is the same answer — no announcement. A missing file is the ordinary case, and a
    corrupt one must not be able to stop a session opening.
    """
    try:
        payload: Any = json.loads(ANNOUNCEMENT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict) or not str(payload.get("text") or "").strip():
        return None
    return Announcement(
        text=str(payload["text"]),
        author_id=int(payload.get("author_id") or 0),
        posted_at=float(payload.get("posted_at") or 0.0),
    )


def write_announcement(*, announcement: Optional[Announcement]) -> None:
    """Stores the announcement, replacing whatever was there, or clears it when given `None`.

    There is only ever one, so this is a whole-file write rather than an append — nothing about the
    previous announcement is worth keeping once it has been replaced.
    """
    if announcement is None:
        ANNOUNCEMENT_FILE.unlink(missing_ok=True)
        return

    ANNOUNCEMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    ANNOUNCEMENT_FILE.write_text(
        json.dumps(
            {"text": announcement.text, "author_id": announcement.author_id, "posted_at": announcement.posted_at},
            indent=2,
        ),
        encoding="utf-8",
    )


def user_root(*, user_id: int) -> Path:
    """Returns a Discord User's private Claude directory, which is also their sessions' working directory."""
    return USERS_ROOT.joinpath(str(user_id))


def session_dir(*, user_id: int, thread_id: int) -> Path:
    """Returns where one session's generated files live, under its owner's root."""
    return user_root(user_id=user_id).joinpath(SESSIONS_SUBDIR, str(thread_id))


def attachments_dir(*, user_id: int, thread_id: int) -> Path:
    """Returns where a session's uploaded attachments are saved."""
    return session_dir(user_id=user_id, thread_id=thread_id).joinpath(ATTACHMENTS_SUBDIR)


# ---------------------------------------------------------------------------
# The session index
#
# A sidecar mirroring each session's identity into its own workspace. The thread's opening post stays
# the source of truth — see `STATE_LINE_FORMAT` — and nothing reads this in preference to it. What
# it buys is findability when the post cannot be read: the CLI keeps its transcripts as a flat
# directory of bare UUIDs with nothing tying one to a Discord thread, so a gateway outage, a deleted
# post or a re-keyed session leaves an intact transcript nobody can locate.
# ---------------------------------------------------------------------------

SESSION_INDEX_NAME: str = "session.json"
# How many superseded session IDs to keep. `.clear` re-keys a parked thread and the CLI re-keys a
# live one on its own, and without a record of what came before, the previous transcript is orphaned
# the moment the panel is re-rendered. Enough to walk back through a few restarts; a breadcrumb
# trail, not an archive.
SESSION_LINEAGE_LIMIT: int = 10


class SessionIndex(TypedDict):
    """The on-disk mirror of a session's identity. See :func:`write_session_index`."""

    thread_id: int
    owner_id: int
    session_id: str
    lineage: list[str]
    """Superseded session IDs, most recent first."""
    model: str
    mode: str
    effort: str
    started: float
    updated: float


def session_index_path(*, workspace: Path) -> Path:
    """Returns where a session's sidecar index lives inside its workspace."""
    return workspace.joinpath(SESSION_INDEX_NAME)


def read_session_index(*, workspace: Path) -> Optional[SessionIndex]:
    """Reads a session's sidecar index, or `None` when there isn't a readable one.

    Blocking; call it off the loop.

    Parameters
    ----------
    workspace: :class:`Path`
        The workspace directory holding the sidecar.

    Returns
    -------
    :class:`Optional[SessionIndex]`
        The recorded index, or `None` when it is missing, unreadable or not the shape we write.

    """
    source: Path = session_index_path(workspace=workspace)
    try:
        payload: Any = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # A truncated write, a hand-edit, a workspace pruned out from under us. None of those are
        # worth raising over: the sidecar is a convenience mirror and Discord still holds the real
        # state. Only malformed content is logged; a missing file is the ordinary first-write case.
        if isinstance(e, ValueError):
            LOGGER.warning("<%s> | Ignoring an unreadable session index at %s | Error: %s", "read_session_index", source, e)
        return None

    if not isinstance(payload, dict) or not payload.get("session_id"):
        return None
    return cast("SessionIndex", payload)


def write_session_index(*, state: SessionState) -> bool:
    """Mirrors a session's identity to disk beside its workspace, so it survives losing Discord.

    Written at the same moment as the panel's state line and never read in preference to it. See the
    section comment above for what it is for.

    :attr:`SessionIndex.lineage` is the part that earns its keep. `.clear` on a parked session mints a
    fresh ID onto the same thread, and the CLI re-keys a live one by itself — see
    :meth:`ClaudeCog.track_session_id` — either of which otherwise orphans the previous transcript
    silently. The superseded ID is pushed here instead, most recent first.

    Blocking; call it off the loop.

    Parameters
    ----------
    state: :class:`SessionState`
        The session to record.

    Returns
    -------
    :class:`bool`
        Whether the sidecar was written.

    """
    workspace: Path = state.workspace
    target: Path = session_index_path(workspace=workspace)

    lineage: list[str] = []
    previous: Optional[SessionIndex] = read_session_index(workspace=workspace)
    if previous is not None:
        lineage = [entry for entry in previous.get("lineage", []) if isinstance(entry, str)]
        superseded: str = previous.get("session_id", "")
        # Only on an actual change, or a re-render of an unchanged session would prepend a duplicate
        # on every panel edit — and the panel is edited on every model, mode and effort switch.
        if superseded and superseded != state.session_id and superseded not in lineage:
            lineage.insert(0, superseded)
        del lineage[SESSION_LINEAGE_LIMIT:]

    payload: SessionIndex = {
        "thread_id": state.thread_id,
        "owner_id": state.user_id,
        "session_id": state.session_id,
        "lineage": lineage,
        "model": state.model,
        "mode": state.mode,
        "effort": state.effort,
        "started": state.started,
        "updated": time.time(),
    }

    try:
        workspace.mkdir(parents=True, exist_ok=True)
        # Written to a neighbour and renamed. `Path.replace` is atomic within a filesystem, so a crash
        # mid-write leaves the previous index intact rather than a half-file — which matters here
        # precisely because the thing this guards against is the machine losing power.
        staging: Path = target.with_suffix(".json.tmp")
        staging.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        staging.replace(target)
    except OSError as e:
        LOGGER.warning("<%s> | Failed to write the session index at %s | Error: %s", "write_session_index", target, e)
        return False
    return True


def find_session_index(*, thread_id: int) -> Optional[SessionIndex]:
    """Finds a thread's sidecar index straight off the filesystem, for when Discord is not there.

    :meth:`ClaudeCog.get_state` needs a :class:`discord.Thread` before it can read anything, so it is
    no help when the gateway is down or the opening post is gone. This answers "which CLI session was
    thread N" without asking Discord anything.

    Blocking; call it off the loop.

    Parameters
    ----------
    thread_id: :class:`int`
        The thread to look for.

    Returns
    -------
    :class:`Optional[SessionIndex]`
        The recorded index, or `None` when no workspace claims that thread.

    """
    if not USERS_ROOT.is_dir():
        return None

    # `<users>/<user_id>/sessions/<thread_id>/`. Globbing the thread ID rather than walking every
    # user keeps this cheap no matter how many people have sessions open.
    for workspace in USERS_ROOT.glob(f"*/{SESSIONS_SUBDIR}/{thread_id}"):
        index: Optional[SessionIndex] = read_session_index(workspace=workspace)
        if index is not None:
            return index
    return None


def prune_workspaces(*, known: set[int]) -> int:
    """Removes session workspaces whose thread ID is not in `known`, and returns how many went.

    The directory name **is** the thread ID, so there is nothing to read to work out what a workspace
    belongs to — a directory whose name is not a number was not made by us and is left where it is.

    Blocking; call it off the loop. See :meth:`ClaudeCog.prune_orphan_workspaces` for the two guards
    that decide whether it is safe to run at all.

    Parameters
    ----------
    known: :class:`set[int]`
        Thread IDs that still exist, whose workspaces must be kept.

    Returns
    -------
    :class:`int`
        How many directories were removed.

    """
    if not USERS_ROOT.is_dir():
        return 0

    removed: int = 0
    cutoff: float = time.time() - (WORKSPACE_GRACE_HOURS * 3600)
    for user_directory in USERS_ROOT.iterdir():
        sessions: Path = user_directory.joinpath(SESSIONS_SUBDIR)
        if not sessions.is_dir():
            continue

        for workspace in sessions.iterdir():
            if not workspace.is_dir() or not workspace.name.isdigit():
                continue
            if int(workspace.name) in known or workspace.stat().st_mtime > cutoff:
                continue

            shutil.rmtree(workspace, ignore_errors=True)
            removed += 1
            LOGGER.info("<%s> | Removed an orphaned workspace | Path: %s", "prune_workspaces", workspace)
    return removed


def prepare_user_root(*, user_id: int, name: str) -> Path:
    """Creates a user's directory and seeds their `CLAUDE.md` the first time they open a session.

    The memory file is written **only** when absent. It is theirs to edit from inside a session once
    it exists, and rewriting it on every session would silently undo whatever they had put there.

    Parameters
    ----------
    user_id: :class:`int`
        The Discord user the directory belongs to.
    name: :class:`str`
        Their display name, used to title the seeded memory file.

    Returns
    -------
    :class:`Path`
        The user's root, which is the cwd their sessions run in.

    """
    root: Path = user_root(user_id=user_id)
    root.mkdir(parents=True, exist_ok=True)

    memory: Path = root.joinpath(USER_MEMORY_NAME)
    if not memory.exists():
        memory.write_text(USER_MEMORY_TEMPLATE.format(name=name, sessions=SESSIONS_SUBDIR), encoding="utf-8")
        LOGGER.info("<%s> | Seeded a personal CLAUDE.md | User: %s | Path: %s", "prepare_user_root", user_id, memory)
    return root


def prepare_workspace(*, directory: Path) -> None:
    """Creates a session's file-output directory, parents and all."""
    directory.mkdir(parents=True, exist_ok=True)


def transcript_slug(cwd: Path) -> str:
    """Returns the directory name the CLI stores a transcript under for a given working directory."""
    return str(cwd).translate(str.maketrans(TRANSCRIPT_SLUG_TABLE))


def live_transcript(*, cwd: Path, session_id: str) -> Path:
    """Returns where the CLI keeps a session's own conversation log."""
    return CLAUDE_PROJECTS.joinpath(transcript_slug(cwd), f"{session_id}.jsonl")


def snapshot_path(*, workspace: Path, session_id: str) -> Path:
    """Returns where we keep our copy of a session's transcript."""
    return workspace.joinpath(TRANSCRIPT_NAME.format(session_id=session_id))


def truncate(text: str, size: int) -> str:
    """Returns `text` shortened to `size` with an ellipsis, or unchanged when it already fits."""
    flattened: str = " ".join(text.split())
    return flattened if len(flattened) <= size else f"{flattened[: size - 1]}…"


def tool_target(*, tool_input: dict, root: Path) -> str:
    """Returns the salient part of a tool's input, truncated but not yet marked up.

    Plain text on purpose. The status log italicises it and a permission prompt puts it in a code
    span, and only the caller knows which of those it is; markup applied here would have to be
    unpicked by one of them.

    Parameters
    ----------
    tool_input: :class:`dict`
        The tool's input object as the CLI reported it.
    root: :class:`Path`
        The session's working directory, for shortening absolute paths.

    Returns
    -------
    :class:`str`
        A one line description, or an empty string when nothing useful was found.

    """
    for key in TOOL_TARGET_KEYS:
        value: Any = tool_input.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if key in {"file_path", "notebook_path"}:
            with contextlib.suppress(ValueError):
                value = str(Path(value).relative_to(root))
        return truncate(value, TOOL_TARGET_SIZE)
    return ""


def repeat_marker(count: int) -> str:
    """Returns the raised, italicised count standing for a run of identical calls.

    Empty below two: a call that happened once has nothing to count, and a trailing `x1` on every
    other line is noise the reader has to learn to ignore.
    """
    if count < 2:
        return ""
    digits: str = "".join(SUPERSCRIPT_DIGITS.get(digit, digit) for digit in str(count))
    return f" *{SUPERSCRIPT_TIMES}{digits}*"


def tool_line(*, mark: str, tool: str, target: str, repeats: int = 1) -> str:
    """Returns one tool call as a status line: the mark, the tool, then its target in italics.

    The one place a call is turned into text, so the two displays cannot drift apart on what a tool
    call looks like.

    .. note::
        The target is escaped rather than trusted. It is a command or a glob as often as it is a
        path, and a `**/*.py` inside an italic span would swallow the emphasis and take the rest of
        the line with it.

    Parameters
    ----------
    mark: :class:`str`
        The leading glyph — pending, waiting, done or failed.
    tool: :class:`str`
        The tool's name as the CLI reported it.
    target: :class:`str`
        The salient input from :func:`tool_target`, unmarked.
    repeats: :class:`int`, optional
        How many identical consecutive calls this line stands for, by default 1.

    Returns
    -------
    :class:`str`
        The rendered line.

    """
    line: str = f"{mark} `{tool}`".lstrip()
    if target:
        line += f" *{discord.utils.escape_markdown(target)}*"
    return f"{line}{repeat_marker(repeats)}"


@dataclass
class ToolEntry:
    """One tool call in an inline transcript, and whatever came back from it.

    :class:`TurnContext` keeps its log as a list of strings edited in place, which works only while
    a line is addressed by its position and carries nothing but itself. Neither holds here: a result
    has to attach to the call that produced it, and calls do not come back in the order they were
    made. So an entry is addressed by its tool id and renders itself.

    Attributes
    ----------
    tool: :class:`str`
        The tool's name as the CLI reported it.
    target: :class:`str`
        The salient input, already rendered by :func:`tool_target`.
    mark: :class:`str`
        The leading glyph — pending, waiting, done or failed.
    detail: :class:`Optional[str]`
        A one line reading of the result, shown beneath the call. `None` until it comes back.
    repeats: :class:`int`
        How many identical consecutive calls this one line stands for.

    """

    tool: str
    target: str
    mark: str = PENDING_MARK
    detail: Optional[str] = None
    repeats: int = 1

    @property
    def signature(self) -> str:
        """Returns the call's identity — tool and target — for recognising a repeat of the same call.

        Built from the raw parts rather than the rendered line, so a change to :func:`tool_line` can
        never quietly change what counts as a repeat.
        """
        return f"{self.tool} {self.target}".rstrip()

    @property
    def open(self) -> bool:
        """Returns whether the call is still outstanding, so an interrupt knows what to mark failed."""
        return self.mark in {PENDING_MARK, WAITING_MARK}

    def lines(self) -> list[str]:
        """Returns the entry as transcript lines — the call, then its result beneath it."""
        head: str = tool_line(mark=self.mark, tool=self.tool, target=self.target, repeats=self.repeats)
        return [head] if self.detail is None else [head, f"{RESULT_PREFIX}{self.detail}"]


def tool_result_summary(*, content: Any, is_error: bool) -> str:
    """Returns a one line reading of a `tool_result` block, for the line beneath its call.

    The CLI hands over the tool's entire output — a file, a test run, a directory listing. None of
    that belongs in a status message, so this keeps the first line and a count of what it dropped.

    Parameters
    ----------
    content: :class:`Any`
        The block's `content`, which the CLI sends as either a string or a list of content blocks.
    is_error: :class:`bool`
        Whether the tool reported a failure, which decides what an empty result is called.

    Returns
    -------
    :class:`str`
        The summary; never empty, so a finished call always has something under it.

    """
    if isinstance(content, str):
        text: str = content
    elif isinstance(content, list):
        text = "\n".join(str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text")
    else:
        text = ""

    text = text.strip()
    if not text:
        return "failed" if is_error else "done"

    first, _, rest = text.partition("\n")
    summary: str = truncate(first, TOOL_RESULT_SIZE)
    dropped: int = len(rest.splitlines())
    return f"{summary} (+{dropped} lines)" if dropped else summary


def _widen_code_span(match: re.Match[str]) -> str:
    """Rewrites one backslash-escaped inline code span into the double-backtick form Discord renders."""
    body: str = match.group(1).replace("\\`", "`")
    return f"`` {body} ``" if body.startswith("`") or body.endswith("`") else f"``{body}``"


def repair_code_escapes(text: str) -> str:
    r"""Rewrites GitHub-style escaped backticks so Discord renders them instead of showing the slash.

    Discord has no escape inside an inline code span; `` \\` `` renders literally, backslash and all.
    The portable way to show a backtick is to widen the fence to two, which is what this does.
    """
    return ESCAPED_CODE_SPAN.sub(_widen_code_span, text)


def walk_markup(text: str) -> Iterator[tuple[str, Optional[str], bool]]:
    """Yields every line with its fence state, as `(line, fence_language, inside_fence)`.

    The one place a code fence is recognised. :func:`balance_markup` and :func:`to_discord_markdown`
    both read from here, so there is a single answer to "is this line code" rather than two scanners
    that can drift apart — and rewriting prose must never reach inside a block, where a table is
    somebody's actual output rather than something to reformat.

    Parameters
    ----------
    text: :class:`str`
        The markdown to walk.

    Yields
    ------
    :class:`tuple[str, Optional[str], bool]`
        The line; the language it opens when the line *is* a fence, which is `""` for an unlabelled
        one and `None` when it is not a fence at all; and whether the line sits inside a block. The
        fence lines themselves count as inside, since neither caller should rewrite them.

    """
    inside: bool = False
    for line in text.splitlines():
        stripped: str = line.lstrip()
        if stripped.startswith(CODE_FENCE):
            yield line, stripped.removeprefix(CODE_FENCE).strip(), True
            inside = not inside
            continue
        yield line, None, inside


def _table_cells(line: str) -> list[str]:
    """Splits one table row into its cells, dropping the optional leading and trailing pipes."""
    stripped: str = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_delimiter(line: str) -> bool:
    """Whether a line is the `|---|:--:|` rule that makes the row above it a table header.

    This rule is what tells a table apart from a sentence with a pipe in it, so it is required —
    which matches GitHub's own parser.
    """
    cells: list[str] = _table_cells(line)
    return bool(cells) and all(TABLE_DELIMITER.fullmatch(cell) for cell in cells)


def render_table(rows: list[list[str]]) -> list[str]:
    """Renders parsed table rows as an aligned block, fenced so the alignment survives.

    Discord has no table markup at all: a markdown table arrives as raw pipes with the `|---|` rule
    sitting in the middle of it, which is unreadable rather than merely plain. Monospace is the only
    place column padding holds, so the table becomes a fenced block.

    Emphasis markers are stripped from the cells because nothing renders inside a fence, so `**Yes**`
    would arrive with the asterisks showing. Links are left alone — their text is still readable and
    dropping the URL would lose what the cell was for.

    Parameters
    ----------
    rows: :class:`list[list[str]]`
        The header row followed by the body rows.

    Returns
    -------
    :class:`list[str]`
        The lines of the fenced block, ready to splice back into the text.

    """
    cleaned: list[list[str]] = [[TABLE_EMPHASIS.sub("", cell) for cell in row] for row in rows]
    columns: int = max(len(row) for row in cleaned)
    # Ragged rows are legal in markdown; pad them so the zip below does not lose a column.
    padded: list[list[str]] = [row + [""] * (columns - len(row)) for row in cleaned]
    widths: list[int] = [max(len(row[index]) for row in padded) for index in range(columns)]

    def draw(cells: list[str]) -> str:
        # The last column is not padded, or every line carries trailing spaces to the fence's edge.
        return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells)).rstrip()

    header, *body = padded
    lines: list[str] = [CODE_FENCE, draw(header), draw(["-" * width for width in widths])]
    lines.extend(draw(row) for row in body)
    lines.append(CODE_FENCE)
    return lines


def to_discord_markdown(text: str) -> str:
    """Rewrites the markdown an LLM writes into the markdown Discord actually renders.

    Assistant text arrives as standard markdown, and Discord is not CommonMark. Three constructs
    come through as literal characters, worst first: a table is raw pipes and a `|---|` rule, a `---`
    horizontal rule is three dashes on their own line, and `- [ ]` task lists keep their brackets.

    Only the mechanical ones are done here. `__text__` is left alone even though Discord reads it as
    underline rather than bold, because rewriting it would be guessing at intent; steering the model
    is the better route for anything that needs to know what was meant.

    .. warning::
        Nothing inside a fenced code block is touched. A table in a code block is someone's actual
        output — a `psql` result, a benchmark — and reformatting it would corrupt what they asked for.

    Parameters
    ----------
    text: :class:`str`
        The assistant text as written.

    Returns
    -------
    :class:`str`
        The same text with tables fenced, rules replaced and task lists given real checkboxes.

    """
    lines: list[tuple[str, bool]] = [(line, inside) for line, _, inside in walk_markup(text)]
    out: list[str] = []
    index: int = 0

    while index < len(lines):
        line, inside = lines[index]

        if inside:
            out.append(line)
            index += 1
            continue

        # A table is a row whose *next* line is the delimiter rule. Checking the rule rather than the
        # pipes is what keeps "use `a | b` for either" from being mistaken for a one row table.
        following: Optional[tuple[str, bool]] = lines[index + 1] if index + 1 < len(lines) else None
        if "|" in line and following is not None and not following[1] and _is_table_delimiter(following[0]):
            rows: list[list[str]] = [_table_cells(line)]
            index += 2
            while index < len(lines) and not lines[index][1] and "|" in lines[index][0] and lines[index][0].strip():
                rows.append(_table_cells(lines[index][0]))
                index += 1
            out.extend(render_table(rows))
            continue

        if TABLE_RULE.fullmatch(line.strip()):
            # Discord has no horizontal rule, so the house separator stands in for one.
            out.append(REPLY_SEPARATOR)
            index += 1
            continue

        out.append(TASK_ITEM.sub(_render_task_item, line))
        index += 1

    return "\n".join(out)


def _render_task_item(match: re.Match[str]) -> str:
    """Swaps a `- [ ]` marker for a checkbox Discord renders."""
    return f"{match.group('indent')}- {TASK_DONE if match.group('mark').lower() == 'x' else TASK_TODO} "


def balance_markup(text: str, *, carried_language: Optional[str] = None) -> tuple[str, Optional[str]]:
    """Closes any code fence left open at the end of a chunk, and returns the one to reopen next.

    Splitting a long answer mid-fence leaves one chunk starting a block nothing closes and the next
    starting outside one, so Discord renders both wrongly. We close the fence at the seam and reopen
    it — in the same language — at the start of the following chunk.

    Parameters
    ----------
    text: :class:`str`
        The chunk to balance.
    carried_language: :class:`Optional[str]`, optional
        The language of a fence left open by the previous chunk, by default `None`.

    Returns
    -------
    :class:`tuple[str, Optional[str]]`
        The balanced chunk and the language to carry into the next one.

    """
    body: str = f"{CODE_FENCE}{carried_language or ''}\n{text}" if carried_language is not None else text

    # Both start closed even when a fence was carried in, because the carried fence has already been
    # prepended to `body` and the scan below is about to find it there. Setting `open_fence` to `True`
    # as well would count that one fence twice, leaving the *next* chunk inverted — it opens a block
    # it never closes, and every chunk after it toggles the wrong way.
    language: Optional[str] = None
    open_fence: bool = False
    # Through `walk_markup`, so a fence is recognised in exactly one place in this module.
    for _, fence, _ in walk_markup(body):
        if fence is None:
            continue
        if open_fence:
            open_fence = False
            language = None
        else:
            open_fence = True
            language = fence

    if open_fence:
        return f"{body}\n{CODE_FENCE}", language
    return body, None


def chunk_text(text: str, size: int = MESSAGE_CHUNK_SIZE) -> list[str]:
    """Splits a response into Discord-sized messages, preferring to break on blank lines.

    Parameters
    ----------
    text: :class:`str`
        The response to split.
    size: :class:`int`, optional
        The maximum characters per chunk, by default :attr:`MESSAGE_CHUNK_SIZE`.

    Returns
    -------
    :class:`list[str]`
        The chunks, each with its code fences balanced.

    """
    if not text:
        return []

    raw: list[str] = []
    remaining: str = text
    while len(remaining) > size:
        window: str = remaining[:size]
        # Prefer a paragraph break, then a line break, then give up and cut mid-line.
        cut: int = window.rfind("\n\n")
        if cut < size // 2:
            cut = window.rfind("\n")
        if cut < size // 2:
            cut = size
        raw.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        raw.append(remaining)

    balanced: list[str] = []
    carried: Optional[str] = None
    for chunk in raw:
        rendered, carried = balance_markup(chunk, carried_language=carried)
        balanced.append(rendered)
    return balanced


def thread_title(prompt: str) -> str:
    """Returns a forum post title taken from the first line of a prompt."""
    first: str = next((line.strip() for line in prompt.splitlines() if line.strip()), PLACEHOLDER_TITLE)
    return truncate(first, THREAD_TITLE_SIZE)


def human_size(size: int) -> str:
    """Returns a byte count as a short human readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}"
        size = int(size / 1024)
    return f"{size:.0f}TB"


def _dir_snapshot(*, directory: Path) -> dict[Path, float]:
    """Returns every file under a directory with its modification time, for diffing after a turn."""
    if not directory.is_dir():
        return {}
    return {path: path.stat().st_mtime for path in directory.rglob("*") if path.is_file()}


def _is_bookkeeping(*, path: Path, directory: Path) -> bool:
    """Returns whether a path inside a workspace is ours rather than something the turn produced.

    Both of these sit in the workspace and both change under the bot's hand, not the model's. The
    sidecar index is rewritten by every :meth:`ClaudeCog.update_panel` — including the mid-turn one
    a re-key triggers, which is what `/clear` does — and an upload is written into `attachments/`
    on the way in. Neither is a generated file, and posting the index back would put the owner's ID
    and the session's lineage into the thread.
    """
    return path == session_index_path(workspace=directory) or path.is_relative_to(directory.joinpath(ATTACHMENTS_SUBDIR))


def _new_files(*, directory: Path, before: dict[Path, float]) -> list[Path]:
    """Returns the files created or rewritten since a snapshot, newest first."""
    changed: list[Path] = []
    for path, mtime in _dir_snapshot(directory=directory).items():
        if before.get(path) != mtime and not _is_bookkeeping(path=path, directory=directory):
            changed.append(path)
    return sorted(changed, key=lambda path: path.stat().st_mtime, reverse=True)


def _build_reply_files(*, paths: list[Path]) -> tuple[list[discord.File], list[str]]:
    """Returns the generated files small enough to attach, plus the names of those that were not."""
    files: list[discord.File] = []
    skipped: list[str] = []
    for path in paths[:MAX_REPLY_FILES]:
        if path.stat().st_size > MAX_REPLY_FILE_SIZE:
            skipped.append(path.name)
            continue
        files.append(discord.File(fp=path, filename=path.name))
    skipped.extend(path.name for path in paths[MAX_REPLY_FILES:])
    return files, skipped


# ---------------------------------------------------------------------------
# The live session
# ---------------------------------------------------------------------------


@dataclass
class SessionState:
    """Everything needed to drive one session, mirrored from its thread's opening post.

    Attributes
    ----------
    thread_id: :class:`int`
        The forum post this session lives in; also names its file-output directory.
    user_id: :class:`int`
        The Discord user the session belongs to.
    session_id: :class:`str`
        The CLI session ID; what `--resume` is given when a reaped process is respawned.
    model: :class:`str`
        The model the session runs on.
    mode: :class:`str`
        The `--permission-mode` in force.
    effort: :class:`str`
        The `--effort` level in force.
    started: :class:`float`
        Unix timestamp the session was opened, shown on the panel.
    ignoring: :class:`bool`
        Whether ordinary messages here are being dropped rather than run, toggled at `.ignore`.

    """

    thread_id: int
    user_id: int
    session_id: str
    model: str = field(default=DEFAULT_MODEL)
    mode: str = field(default=DEFAULT_MODE)
    effort: str = field(default=DEFAULT_EFFORT)
    started: float = field(default_factory=time.time)
    ignoring: bool = field(default=False)

    @property
    def cwd(self) -> Path:
        """Returns the directory the CLI runs in; the owner's root, not the session's."""
        return user_root(user_id=self.user_id)

    @property
    def workspace(self) -> Path:
        """Returns where this session's generated files belong."""
        return session_dir(user_id=self.user_id, thread_id=self.thread_id)

    @property
    def attachments(self) -> Path:
        """Returns where this session's uploaded attachments are saved."""
        return attachments_dir(user_id=self.user_id, thread_id=self.thread_id)

    @property
    def output_directory(self) -> str:
        """Returns the file-output directory as the CLI sees it, relative to the cwd."""
        return self.workspace.relative_to(self.cwd).as_posix()

    @property
    def state_line(self) -> str:
        """Returns the small-text line written into the panel and re-parsed on restart."""
        return STATE_LINE_FORMAT.format(
            session_id=self.session_id,
            model=self.model,
            mode=self.mode,
            effort=self.effort,
            user_id=f"<@{self.user_id}>",
            started=f"<t:{int(self.started)}:R>",
        )


@dataclass
class TurnResult:
    """What one turn produced, assembled from the events between a prompt and its `result`.

    Attributes
    ----------
    blocks: :class:`list[str]`
        The assistant's text blocks in order, each posted to Discord as it completed.
    error: :class:`Optional[str]`
        A user-displayable error, or `None` when the turn succeeded.
    cost_usd: :class:`Optional[float]`
        The cost the CLI reported for the turn.
    tool_calls: :class:`int`
        How many tools the turn invoked, for the collapsed status summary.
    duration: :class:`float`
        Wall-clock seconds the turn took.
    interrupted: :class:`bool`
        Whether the turn ended because we sent an `interrupt`.

    """

    blocks: list[str] = field(default_factory=list)
    error: Optional[str] = field(default=None)
    cost_usd: Optional[float] = field(default=None)
    tool_calls: int = field(default=0)
    duration: float = field(default=0.0)
    interrupted: bool = field(default=False)


class LiveSession:
    """One long-lived `claude` process, its reader task and its outstanding prompts.

    The process *is* the session. Turns are written to stdin as user events and the CLI keeps the
    conversation itself, so there is no `--resume` between messages and no transcript replay per turn.
    A reaped process is respawned with `--resume`, which restores the conversation in full.

    .. note::
        The reader runs for the life of the process, not for the life of a turn. It hands every event
        to :attr:`on_event`, and the cog decides which turn (if any) is listening.

    Attributes
    ----------
    state: :class:`SessionState`
        The session this process belongs to.
    process: :class:`asyncio.subprocess.Process`
        The running CLI.
    pending: :class:`dict[str, asyncio.Future[dict]]`
        Control requests shown in Discord and not yet answered, by request ID.
    last_active: :class:`float`
        Monotonic clock reading of the last turn, for the idle reaper.

    """

    def __init__(self, *, state: SessionState, process: asyncio.subprocess.Process) -> None:
        self.state: SessionState = state
        self.process: asyncio.subprocess.Process = process
        self.pending: dict[str, asyncio.Future[dict]] = {}
        self.last_active: float = time.monotonic()
        self.on_event: Optional[Any] = None
        # The last usage reading and when it was taken; see `usage`.
        self._usage: Optional[UsageSnapshot] = None
        self._usage_read: float = 0.0
        self._reader: Optional[asyncio.Task[None]] = None
        # Writes are serialised because a control response can be sent from a button press while a
        # turn is being submitted from the message listener. Two interleaved writes would corrupt the
        # newline framing and the CLI would drop both.
        self._writes: asyncio.Lock = asyncio.Lock()
        # One turn at a time per session; the CLI processes stdin in order, so overlapping turns would
        # interleave their events with no way to tell which answer belonged to which question. Messages
        # sent in quick succession queue on this and run in the order they were sent, which is what the
        # CLI does with the same input.
        self.turn: asyncio.Lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        """Returns whether the CLI process is still running."""
        return self.process.returncode is None

    @classmethod
    async def spawn(cls, *, state: SessionState, resume: bool) -> LiveSession:
        """Starts a CLI process for a session and completes the control handshake.

        Parameters
        ----------
        state: :class:`SessionState`
            The session to run; supplies the model, mode, effort and working directory.
        resume: :class:`bool`
            Whether to `--resume` the recorded session ID or claim it with `--session-id`. A session
            whose process was reaped resumes; a brand new one claims.

        Returns
        -------
        :class:`LiveSession`
            The started session, handshake done and reader running.

        Raises
        ------
        :exc:`FileNotFoundError`
            The CLI could not be found, or the working directory has gone away.
        :exc:`TimeoutError`
            The CLI did not answer the `initialize` handshake.

        """
        arguments: list[str] = [
            claude_binary() or "claude",
            *LIVE_ARGS,
            "--model",
            state.model,
            "--permission-mode",
            state.mode,
            # A trusted user asking about the bot's own code is the common case, and a cwd outside the
            # repository cannot reach it otherwise.
            "--add-dir",
            str(PROJECT_ROOT),
        ]
        # Only passed when the flag will actually take it. `--effort auto` is not rejected outright,
        # it warns and quietly uses the default, which would make a session claiming `auto` on its
        # panel run at `medium` every time it was respawned. It is re-applied by command below.
        if state.effort in EFFORT_FLAG_VALUES:
            arguments += ["--effort", state.effort]
        arguments += ["--resume", state.session_id] if resume else ["--session-id", state.session_id]

        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024,
            cwd=state.cwd,
        )

        session: LiveSession = cls(state=state, process=process)
        session._reader = asyncio.create_task(session._read())

        # The handshake is what switches the CLI into client-driven permissions. Until it answers,
        # a `can_use_tool` would never arrive and every tool would be decided without us.
        handshake: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        request_id: str = uuid.uuid4().hex
        session.pending[request_id] = handshake
        await session.write({"type": "control_request", "request_id": request_id, "request": {"subtype": "initialize", "hooks": {}}})
        try:
            await asyncio.wait_for(handshake, timeout=HANDSHAKE_TIMEOUT)
        except TimeoutError:
            await session.close()
            raise

        # The levels the flag would not take are applied here instead, so a respawn comes back at the
        # effort the panel claims rather than silently at the default. Safe to send unlistened-to: no
        # turn is registered yet, so the `result` it produces is dropped by the event pump.
        if state.effort not in EFFORT_FLAG_VALUES:
            await session.set_effort(level=state.effort)

        LOGGER.info(
            "<%s.%s> | Live session started | Thread: %s | Session: %s | Resumed: %s",
            cls.__name__,
            "spawn",
            state.thread_id,
            state.session_id,
            resume,
        )
        return session

    async def write(self, payload: dict) -> None:
        """Writes one newline-delimited JSON event to the CLI's stdin."""
        if self.process.stdin is None or self.process.stdin.is_closing():
            return
        async with self._writes:
            self.process.stdin.write((json.dumps(payload) + "\n").encode())
            await self.process.stdin.drain()

    async def send_prompt(self, text: str) -> None:
        """Submits a user turn to the live conversation."""
        self.last_active = time.monotonic()
        await self.write({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})

    async def request(self, *, subtype: str, seconds: float = CONTROL_TIMEOUT, **fields: Any) -> Optional[dict]:
        """Sends a control request and waits for the CLI's answer.

        Out of band, so this is safe to call while a turn is running and produces no `result` event
        for that turn to trip over.

        Parameters
        ----------
        subtype: :class:`str`
            The request subtype, eg. `get_context_usage`.
        seconds: :class:`float`, optional
            How long to wait, by default :attr:`CONTROL_TIMEOUT`.
        **fields: :class:`Any`
            Extra keys folded into the request body.

        Returns
        -------
        :class:`Optional[dict]`
            The response body, or `None` when the CLI did not answer or answered with an error.

        """
        request_id: str = uuid.uuid4().hex
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        await self.write({"type": "control_request", "request_id": request_id, "request": {"subtype": subtype, **fields}})

        try:
            async with asyncio.timeout(delay=seconds):
                answer: dict = await future
        except TimeoutError:
            self.pending.pop(request_id, None)
            return None

        if answer.get("subtype") == "error":
            LOGGER.warning(
                "<%s.%s> | Control request refused | Subtype: %s | Error: %s",
                __class__.__name__,
                "request",
                subtype,
                answer.get("error"),
            )
            return None
        response: Any = answer.get("response")
        return response if isinstance(response, dict) else {}

    async def context_usage(self) -> Optional[ContextUsage]:
        """Returns how full this session's context window is, or `None` when it cannot be read."""
        payload: Optional[dict] = await self.request(subtype="get_context_usage")
        return None if payload is None else ContextUsage.from_payload(payload)

    async def usage(self, *, refresh: bool = False) -> Optional[UsageSnapshot]:
        """Returns the shared account's usage and rate limits, cached briefly.

        The read costs no tokens and no turn — measured: twenty consecutive calls moved the context
        by zero tokens, the session cost by zero, and produced no `result` event — but it does cost
        about 275ms of latency. The windows it reports are hours and days wide, so a reading is held
        for :attr:`USAGE_CACHE_SECONDS` rather than taken again for every turn.

        Parameters
        ----------
        refresh: :class:`bool`, optional
            Whether to ignore the cache, by default `False`. Used when a person asked directly, and
            before actually refusing a turn.

        Returns
        -------
        :class:`Optional[UsageSnapshot]`
            The usage, or `None` when it has never been read successfully.

        """
        fresh: bool = time.monotonic() - self._usage_read < USAGE_CACHE_SECONDS
        if not refresh and self._usage is not None and fresh:
            return self._usage

        payload: Optional[dict] = await self.request(subtype="get_usage")
        if payload is None:
            # The last good reading beats nothing at all; a refused control request says nothing about
            # the account, only about the process.
            return self._usage

        self._usage = UsageSnapshot.from_payload(payload)
        self._usage_read = time.monotonic()
        return self._usage

    def invalidate_usage(self) -> None:
        """Drops the cached usage reading so the next check has to go and ask again.

        Called when the CLI reports the account's standing has changed. The cache is deliberately
        two minutes wide, which is fine while nothing is happening and exactly wrong the moment
        something is.
        """
        self._usage_read = 0.0

    async def rename(self, *, title: str) -> bool:
        """Renames the session as the CLI knows it, so its own listings match the Discord post.

        Cosmetic but cheap, and out of band. The name shows up in `claude --resume`'s picker, which is
        where anyone would go looking for one of these transcripts from a terminal.
        """
        return await self.request(subtype="rename_session", title=title) is not None

    async def answer(self, *, request_id: str, response: dict) -> None:
        """Answers an outstanding control request and retires its future.

        Parameters
        ----------
        request_id: :class:`str`
            The request being answered.
        response: :class:`dict`
            The response body; `{"behavior": "allow", "updatedInput": ...}` or
            `{"behavior": "deny", "message": ...}`.

        """
        await self.write({"type": "control_response", "response": {"subtype": "success", "request_id": request_id, "response": response}})
        future: Optional[asyncio.Future[dict]] = self.pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(response)

    async def set_mode(self, *, mode: str) -> None:
        """Switches the permission mode on the running process.

        This is what makes the panel's mode select mean something *now* rather than at the next turn:
        the CLI applies it live and answers with the mode it settled on. Verified against 2.1.220.

        .. note::
            There is **no slash-command alternative** for mode, so this control request is the only
            route. Tested: `/mode` answers *"Unknown command: /mode. Did you mean /model?"*,
            `/permission-mode` is likewise unknown, and `/permissions` reports it *"isn't available in
            this environment"*. Nothing in the CLI's 45 commands sets a permission mode.

        .. warning::
            Being a control request is not merely tidier here, it is **required**. The "Always" button
            on a :class:`ToolApproval` fires while the turn is blocked on that very prompt — mid-turn
            by definition — and only an out-of-band request can be sent at that moment. A slash
            command would queue behind the blocked turn as a user message and then emit a `result`
            the turn would mistake for its own ending.
        """
        self.state.mode = mode
        await self.write(
            {
                "type": "control_request",
                "request_id": uuid.uuid4().hex,
                "request": {"subtype": "set_permission_mode", "mode": mode},
            }
        )

    async def set_model(self, *, model: str) -> None:
        """Switches the model on the running process, keeping the conversation.

        Verified against 2.1.220: after a `set_model` the next turn's `modelUsage` is keyed by the new
        model and the session's `init` reports it, with no respawn and nothing lost.

        .. note::
            A `/model <name>` slash command exists too and works — tested; it returns `num_turns: 0`
            and the next turn's `init` reports the new model. We use the control request anyway,
            because it is out of band: no `result` event to be mistaken for a turn ending, and so no
            need for the deferral :meth:`ClaudeCog.apply_effort` has to carry. Both are free, so the
            slash command buys nothing and costs the ability to switch mid-turn.

        .. note::
            There is no *control request* for effort — `set_effort` is answered with "Unsupported
            control request subtype" — but there is a slash command. See :meth:`set_effort`.
        """
        self.state.model = model
        await self.write({"type": "control_request", "request_id": uuid.uuid4().hex, "request": {"subtype": "set_model", "model": model}})

    async def set_effort(self, *, level: str) -> None:
        """Switches the effort level by sending the CLI's own `/effort` command.

        Effort is the one setting with no control request behind it, but the slash command does the
        job and the CLI handles it locally: tested against 2.1.220, `/effort high` returns
        `num_turns: 0` and `total_cost_usd: 0`, so it never reaches the model and costs nothing.

        .. note::
            Effort is in band because it has no choice, **not** because in band is preferable. Mode
            and model both stay on control requests for the reasons on :meth:`set_mode` and
            :meth:`set_model`; do not "unify" the three on slash commands, as that would break the
            "Always" button and buy nothing.

        .. warning::
            This goes in as an ordinary **user message**, so unlike :meth:`set_mode` and
            :meth:`set_model` it is *in band* — the CLI answers it with a `result` event of its own.
            A turn in flight would mistake that for its own ending, which is why
            :meth:`ClaudeCog.apply_effort` defers it until the session is idle.
        """
        self.state.effort = level
        await self.send_prompt(EFFORT_COMMAND.format(level=level))

    async def interrupt(self) -> None:
        """Stops the turn in flight, leaving the session and its process usable.

        Unlike killing the process this keeps the conversation, so the user can simply ask again.
        """
        await self.write({"type": "control_request", "request_id": uuid.uuid4().hex, "request": {"subtype": "interrupt"}})

    async def close(self) -> None:
        """Ends the process and cancels the reader, failing any prompt still waiting on an answer."""
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        self.pending.clear()

        if self._reader is not None:
            self._reader.cancel()
            self._reader = None

        if self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.kill()
            with contextlib.suppress(Exception):
                await self.process.wait()

    async def _read(self) -> None:
        """Reads the CLI's event stream for the life of the process, handing each event to the cog."""
        assert self.process.stdout is not None  # noqa: S101
        try:
            while line := await self.process.stdout.readline():
                try:
                    event: dict = json.loads(line.decode().strip() or "{}")
                except json.JSONDecodeError:
                    continue

                # A control *response* is the CLI answering something we asked (the handshake, a mode
                # switch), so it is settled here and never reaches the cog.
                if event.get("type") == "control_response":
                    response: dict = event.get("response") or {}
                    future: Optional[asyncio.Future[dict]] = self.pending.pop(str(response.get("request_id")), None)
                    if future is not None and not future.done():
                        future.set_result(response)
                    continue

                if self.on_event is not None:
                    await self.on_event(self, event)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("<%s.%s> | Reader failed | Thread: %s", __class__.__name__, "_read", self.state.thread_id)


# ---------------------------------------------------------------------------
# Prompts
#
# Everything the CLI needs a human for arrives on the one `can_use_tool` channel, told apart by
# `tool_name`. Two views cover all of it: a bare yes/no for an ordinary tool, and a richer one for the
# two that carry their own content (a plan to read, or a question with options).
# ---------------------------------------------------------------------------


class PromptDecision(NamedTuple):
    """What a prompt button sends back to the CLI.

    Attributes
    ----------
    behavior: :class:`str`
        `allow` or `deny`; the CLI's own vocabulary.
    message: :class:`Optional[str]`
        Why it was denied, or the answer chosen. Shown to the model, so it is written for the model.
    mode: :class:`Optional[str]`
        A permission mode to switch to as well, taken from the CLI's own `permission_suggestions`.
    remember: :class:`Optional[str]`
        A tool name to approve for the rest of the session, so it stops prompting. The CLI's
        `tool_name`, not its display name — this is matched against later requests.

    """

    behavior: str
    message: Optional[str] = None
    mode: Optional[str] = None
    remember: Optional[str] = None


class PromptButton(discord.ui.Button):
    """A button that hands its press to the prompt view that built it.

    The views are built imperatively — an approval has two or three buttons, a question has one per
    option — so there is no fixed layout to declare with `@discord.ui.button`, and a plain Button has
    a no-op callback.
    """

    def __init__(self, *, decision: PromptDecision, label: str, style: discord.ButtonStyle = discord.ButtonStyle.secondary) -> None:
        super().__init__(label=label, style=style)
        self.decision: PromptDecision = decision

    async def callback(self, interaction: discord.Interaction) -> None:
        """Hands the press to the owning prompt."""
        view: Optional[ClaudePrompt] = self.view  # type: ignore[assignment]
        if view is None:
            return
        await view.resolve(interaction=interaction, decision=self.decision)


class ClaudePrompt(discord.ui.LayoutView):
    """Base for the views that answer a `can_use_tool` request.

    The CLI blocks its whole turn waiting on the answer, so one of these must *always* resolve. The
    timeout is the backstop and it denies, because an approval nobody gave is the one outcome we can
    never manufacture.

    .. warning::
        A Components V2 message cannot carry `content`, `embeds`, stickers or polls.

    Attributes
    ----------
    request_id: :class:`str`
        The control request this view answers.
    user_id: :class:`int`
        The session owner; nobody else may press the buttons.
    answered: :class:`bool`
        Whether a decision has already been sent, so the timeout does not send a second one.

    """

    # Whether the prompt's message is deleted once it has been answered rather than redrawn as a
    # :class:`SettledPrompt`. Off by default: an approval is a record of what was permitted and worth
    # keeping on scrollback. A prompt whose outcome is already narrated elsewhere — a question, whose
    # answer comes back as the tool's own result line above it — turns this on and leaves nothing
    # behind, because the same sentence twice reads as two things happening.
    transient: bool = False

    def __init__(self, *, cog: ClaudeCog, request_id: str, user_id: int, thread_id: int) -> None:
        super().__init__(timeout=PROMPT_TIMEOUT)
        self.cog: ClaudeCog = cog
        self.request_id: str = request_id
        self.user_id: int = user_id
        self.thread_id: int = thread_id
        self.answered: bool = False
        # Set by the cog once the prompt has been sent, so the timeout has something to edit.
        self.message: Optional[discord.Message] = None
        # When this denies itself, taken here rather than at send: the view's own clock starts at
        # construction, so this is the same instant `discord.ui.View` is counting from.
        self.expires_at: float = time.time() + PROMPT_TIMEOUT

    def add_mention(self, *, container: discord.ui.Container) -> None:
        """Closes a prompt's container with the line that pings whoever has to answer it.

        Called last by each prompt rather than folded into the base, because a container is built by
        the subclass and this belongs at the bottom of it, under whatever that subclass laid out.

        .. note::
            :class:`discord.ui.View` restarts its timeout on *any* interaction, including a press
            :meth:`interaction_check` then rejects. Somebody else poking the buttons therefore buys
            the prompt time the line has already promised it does not have. Left as it is: the view
            outliving its stated deadline is the harmless direction for that to go wrong, and the
            alternative is a timestamp that shifts under the reader for reasons they cannot see.
        """
        deadline: str = f"<t:{int(self.expires_at)}:R>"
        container.add_item(discord.ui.TextDisplay(PROMPT_MENTION_LINE.format(mention=f"<@{self.user_id}>", deadline=deadline)))

    @property
    def expired(self) -> bool:
        """Whether the deadline shown on the prompt has passed."""
        return time.time() >= self.expires_at

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Rejects anyone but the session owner, and anyone at all once the deadline has passed.

        The deadline is checked here rather than left to :attr:`discord.ui.View.timeout`, which is not
        a deadline at all: it restarts on *every* interaction, including the ones rejected below. A
        stranger idly pressing the buttons would keep a prompt alive indefinitely, well past the time
        it told the owner it would deny itself at. :attr:`expires_at` is fixed at construction, so
        what the prompt promised is what actually happens — and the press that discovers it has
        elapsed is what runs the denial, rather than waiting on a timer that keeps being pushed back.
        """
        if self.expired:
            await interaction.response.send_message(content="This prompt has expired.", ephemeral=True)
            # Whoever pressed it, the CLI is still blocked on an answer the deadline says is never
            # coming. `on_timeout` may never fire — this press just reset it again.
            await self.on_timeout()
            return False

        if interaction.user.id != self.user_id:
            await interaction.response.send_message(content="This session isn't yours!", ephemeral=True)
            return False
        return True

    async def resolve(self, *, interaction: discord.Interaction, decision: PromptDecision) -> None:
        """Sends a decision to the CLI and freezes the prompt at what was chosen.

        The answer goes out whatever happens to the message. Redrawing the prompt is cosmetic; the
        answer is not, because the CLI is blocked on it and :meth:`on_timeout` has already been
        disarmed by the time the redraw is attempted. Letting the edit's failure carry the answer
        away with it — which it did, for any settled view Discord refused — deadlocked the turn
        until it hit the watchdog's silence limit, half an hour later, with no sign of why.
        """
        # Guarded because the timeout and a press can race: the CLI must be answered exactly once, and
        # a second control response for a retired request would be dropped anyway.
        if self.answered:
            await interaction.response.defer()
            return

        self.answered = True
        self.stop()
        try:
            if self.transient:
                # Acknowledged before the delete: removing the message does not answer the press, and
                # an unanswered press is shown to the presser as a failed interaction.
                await interaction.response.defer()
                # The prompt's own message first, because this also resolves from a modal submit --
                # where `interaction.message` is the component's message only by Discord's courtesy.
                message: Optional[discord.Message] = self.message or interaction.message
                if message is not None:
                    await message.delete()
            else:
                await interaction.response.edit_message(view=self.settled(decision=decision))
        except (discord.HTTPException, ValueError):
            LOGGER.exception(
                "<%s.%s> | Could not retire a prompt; answering anyway | Request: %s",
                __class__.__name__,
                "resolve",
                self.request_id,
            )
            # `_response_type` is only set once the edit *succeeds*, so the interaction is still
            # unacknowledged here and would otherwise show the user a failed interaction.
            # `InteractionResponded` for the transient path, which acknowledges before it deletes: it
            # is a `ClientException`, so leaving it out would carry the answer below away with it.
            with contextlib.suppress(discord.HTTPException, discord.InteractionResponded):
                await interaction.response.defer()
            # Second attempt, off the message rather than the press. Its own failures are swallowed.
            await self.retire(decision=decision)
        await self.cog.answer_prompt(thread_id=self.thread_id, request_id=self.request_id, decision=decision)

    async def on_timeout(self) -> None:
        """Denies the request rather than leaving the CLI blocked on an answer that is not coming.

        Also called directly by :meth:`interaction_check`, for the press that arrives after the
        deadline and would otherwise have pushed the view's own timer further out; hence the
        :meth:`stop`, which the timeout path gets for free and that one does not.
        """
        if self.answered:
            return

        self.answered = True
        self.stop()
        decision: PromptDecision = PromptDecision(behavior="deny", message=PROMPT_TIMEOUT_MESSAGE)
        await self.retire(decision=decision)
        await self.cog.answer_prompt(thread_id=self.thread_id, request_id=self.request_id, decision=decision)

    async def withdraw(self) -> None:
        """Retires the prompt because the CLI took the request back.

        Unlike every other way a prompt ends, this one sends **nothing**. The request is already gone
        — a control response for a cancelled ID is dropped — so the only work is making the buttons
        stop being pressable, since a press would otherwise answer into the void.
        """
        if self.answered:
            return

        self.answered = True
        self.stop()
        await self.retire(decision=PromptDecision(behavior="deny", message=PROMPT_WITHDRAWN_MESSAGE))

    async def retire(self, *, decision: PromptDecision) -> None:
        """Leaves the prompt's message in its final shape, off its own message rather than a press.

        Failures are swallowed on purpose. Every caller has an answer to send afterwards — or has
        already had it cancelled out from under it — and how the message ends up looking is never
        worth losing that to. `ValueError` too: the settled view is *built* here, and Components V2
        enforces its limits at construction.
        """
        if self.message is None:
            return

        if self.transient:
            with contextlib.suppress(discord.HTTPException):
                await self.message.delete()
                return
            # The delete did not take, so the buttons are still on screen. Settling is the honest
            # second choice: a dead row of options says nothing about which one was chosen.
        with contextlib.suppress(discord.HTTPException, ValueError):
            await self.message.edit(view=self.settled(decision=decision))

    def settled(self, *, decision: PromptDecision) -> discord.ui.LayoutView:
        """Returns the view to leave on the message once the prompt has been answered.

        Overridden per prompt. The default replaces the buttons with a line saying what was chosen,
        so scrollback shows the decision rather than a row of dead buttons.
        """
        raise NotImplementedError


class ToolApproval(ClaudePrompt):
    """The yes/no prompt for an ordinary tool call.

    Rendered from the CLI's own request rather than anything decided here, so the wording follows
    whichever tool it happens to be. The third button only appears when the CLI itself offered a
    `setMode` in `permission_suggestions`; we never invent a widening it did not suggest.

    Parameters
    ----------
    request_id: :class:`str`
        The control request being answered.
    user_id: :class:`int`
        The session owner.
    thread_id: :class:`int`
        The session thread, so the answer reaches the right process.
    tool: :class:`str`
        The tool's display name.
    key: :class:`str`
        The CLI's own `tool_name`, which is what a remembered approval is matched on. Kept apart from
        :attr:`tool` because the display name is a label and can differ.
    target: :class:`str`
        The salient part of its input, unmarked as :func:`tool_target` returns it. Put in a code span
        here rather than italicised as the status log does it — standing alone under a heading, a
        path reads as the thing being asked about rather than as an aside.
    suggestion: :class:`Optional[str]`, optional
        A permission mode the CLI suggested, by default `None`.

    """

    def __init__(
        self,
        *,
        cog: ClaudeCog,
        request_id: str,
        user_id: int,
        thread_id: int,
        tool: str,
        key: str,
        target: str,
        suggestion: Optional[str] = None,
    ) -> None:
        super().__init__(cog=cog, request_id=request_id, user_id=user_id, thread_id=thread_id)
        self.tool: str = tool
        self.key: str = key
        # Marked up once, here, so the live view and the settled form it becomes cannot disagree.
        self.target: str = f"`{target}`" if target else ""

        container: discord.ui.Container = discord.ui.Container(accent_colour=discord.Color.yellow())
        container.add_item(discord.ui.TextDisplay(f"### Run `{tool}`?"))
        if self.target:
            container.add_item(discord.ui.TextDisplay(self.target))
        container.add_item(discord.ui.Separator())

        actions: discord.ui.ActionRow = discord.ui.ActionRow()
        actions.add_item(PromptButton(label="Yes", style=discord.ButtonStyle.success, decision=PromptDecision(behavior="allow")))
        actions.add_item(
            PromptButton(
                label="No",
                style=discord.ButtonStyle.danger,
                decision=PromptDecision(behavior="deny", message="The user declined this tool call."),
            ),
        )
        if suggestion is not None:
            # Labelled with the mode it actually sets, so pressing it is never a surprise.
            actions.add_item(
                PromptButton(
                    label=f"Always ({suggestion})",
                    style=discord.ButtonStyle.primary,
                    decision=PromptDecision(behavior="allow", mode=suggestion),
                ),
            )
        # Narrower than the mode switch beside it — one tool rather than every tool — and unlike a
        # mode it dies with the session rather than persisting into the next one.
        actions.add_item(
            PromptButton(
                label=truncate(f"Always allow {tool}", BUTTON_LABEL_SIZE),
                style=discord.ButtonStyle.primary,
                decision=PromptDecision(behavior="allow", remember=key),
            ),
        )
        container.add_item(actions)
        self.add_mention(container=container)
        self.add_item(container)

    def settled(self, *, decision: PromptDecision) -> discord.ui.LayoutView:
        """Returns the answered form; the same header with the outcome in place of the buttons."""
        return SettledPrompt(
            heading=f"### Run `{self.tool}`?",
            body=self.target,
            outcome=_outcome_line(decision=decision),
            allowed=decision.behavior == "allow",
        )


class PlanApproval(ClaudePrompt):
    """The prompt for `ExitPlanMode`; the plan itself, with approve or keep-planning beneath it.

    The plan can be long, so it is chunked into text displays rather than truncated — the point of
    plan mode is reading the plan before agreeing to it. Past what one view holds it *has* to be cut,
    and then :attr:`dropped` is what tells the cog to attach the whole thing as a file beside the
    prompt, so agreeing to a plan never means agreeing to a plan you were shown only part of.

    Attributes
    ----------
    plan: :class:`str`
        The plan as the CLI sent it, whole.
    shown: :class:`str`
        The part of it this view can display. The settled form renders the same text, so pressing a
        button cannot turn a view that fitted into one that does not.
    dropped: :class:`int`
        Characters of :attr:`plan` that did not fit; `0` when all of it is on screen.

    """

    def __init__(self, *, cog: ClaudeCog, request_id: str, user_id: int, thread_id: int, plan: str) -> None:
        super().__init__(cog=cog, request_id=request_id, user_id=user_id, thread_id=thread_id)
        self.plan: str = plan
        chunks, self.dropped = _fit_chunks(plan, reserved=PROMPT_TEXT_RESERVE)
        self.shown: str = "".join(chunks)

        container: discord.ui.Container = discord.ui.Container(accent_colour=discord.Color.blurple())
        container.add_item(discord.ui.TextDisplay("### Ready to start"))
        for chunk in chunks:
            container.add_item(discord.ui.TextDisplay(chunk))
        if self.dropped:
            container.add_item(discord.ui.TextDisplay(f"-# …{self.dropped:,} more characters; the whole plan is attached above."))
        container.add_item(discord.ui.Separator())

        actions: discord.ui.ActionRow = discord.ui.ActionRow()
        actions.add_item(
            PromptButton(
                label="Yes",
                style=discord.ButtonStyle.success,
                # Leaving plan mode is only half of it; the session has to land somewhere that can
                # actually edit, or the very next tool call is refused and the approval meant nothing.
                decision=PromptDecision(behavior="allow", mode="acceptEdits"),
            ),
        )
        actions.add_item(
            PromptButton(
                label="No",
                style=discord.ButtonStyle.danger,
                decision=PromptDecision(behavior="deny", message="The user wants to keep planning. Do not start implementing yet."),
            ),
        )
        container.add_item(actions)
        self.add_mention(container=container)
        self.add_item(container)

    def settled(self, *, decision: PromptDecision) -> discord.ui.LayoutView:
        """Returns the answered form, keeping the plan on screen so it stays readable afterwards.

        Renders :attr:`shown` rather than :attr:`plan`: this view is written by `edit_message`, and a
        failed edit there used to strand the CLI blocked forever — see :meth:`ClaudePrompt.resolve`.
        """
        return SettledPrompt(
            heading="### Ready to start",
            body=self.shown,
            outcome=_outcome_line(decision=decision),
            allowed=decision.behavior == "allow",
        )


class QuestionOption(NamedTuple):
    """One answer a question offers, read out of the tool's input.

    Attributes
    ----------
    label: :class:`str`
        What the option says, and what the answer sent to the model says back.
    description: :class:`str`
        The model's own gloss on the option; `""` when it wrote none. Shown only by
        :class:`QuestionSelect`, which has somewhere to put it — a button does not.

    """

    label: str
    description: str


def _question_options(*, question: dict) -> list[QuestionOption]:
    """Returns the answerable options of a question, skipping anything malformed.

    Read once here rather than in each of the two renderers, so a button question and a menu question
    are answering exactly the same list.
    """
    raw: Any = question.get("options")
    options: list[QuestionOption] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        label: str = str(entry.get("label") or "")
        if not label:
            # Unanswerable: the label is both what the option says and what the answer sends back.
            continue
        options.append(QuestionOption(label=label, description=str(entry.get("description") or "")))
    return options


def _option_body(*, option: QuestionOption) -> str:
    """Returns one option as its own block of text, for the section that carries its button."""
    body: str = f"**{option.label}**"
    if option.description:
        body += f"\n-# {truncate(option.description, OPTION_DESCRIPTION_SIZE)}"
    return body


class OtherAnswerModal(discord.ui.Modal, title="Answer in your own words"):
    """The way out of a question whose options do not fit; the typed answer becomes the answer.

    Worth the extra control because the thread is not an escape route here. A message sent while a
    turn is blocked queues *behind* it rather than reaching the question, so a reader holding none of
    the offered opinions had only two exits: sit out the fifteen-minute timeout, or interrupt the turn
    and lose the work in front of it.

    Attributes
    ----------
    prompt: :class:`QuestionPrompt`
        The question being answered; the modal resolves through it so the CLI is answered exactly
        once however the press and the view's timeout happen to race.

    """

    answer: discord.ui.TextInput = discord.ui.TextInput(
        label="Your answer",
        style=discord.TextStyle.paragraph,
        placeholder="Say what you would have said in the terminal.",
        max_length=OTHER_ANSWER_LIMIT,
        required=True,
    )

    def __init__(self, *, prompt: QuestionPrompt) -> None:
        super().__init__(timeout=PROMPT_TIMEOUT)
        self.prompt: QuestionPrompt = prompt

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Sends the typed answer as the question's answer."""
        text: str = self.answer.value.strip()
        if not text:
            # `required` should make this unreachable, but resolving on empty would tell the model the
            # user answered and then hand it nothing.
            await interaction.response.send_message(content="That answer was empty, so nothing was sent.", ephemeral=True)
            return
        await self.prompt.resolve(interaction=interaction, decision=PromptDecision(behavior="deny", message=f"The user answered: {text}"))


class OtherAnswerButton(discord.ui.Button):
    """Opens :class:`OtherAnswerModal` instead of answering, since a modal needs the press itself."""

    def __init__(self) -> None:
        super().__init__(label="Other…", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Puts the modal in front of the presser; ownership is already checked by the view."""
        view: Optional[QuestionPrompt] = self.view  # type: ignore[assignment]
        if view is None:
            return
        # A modal cannot be opened on a question that is already answered — its submit would resolve
        # into a request the CLI has long since stopped waiting on.
        if view.answered:
            await interaction.response.defer()
            return
        await interaction.response.send_modal(OtherAnswerModal(prompt=view))


class QuestionSelect(discord.ui.Select):
    """The options of a `multiSelect` question, as one menu that answers with everything ticked.

    Buttons cannot do this. A press is a whole answer, so rendering a question the model marked
    `multiSelect` as a row of them silently answered "several" with one — and the model has no way to
    tell that apart from the user genuinely wanting only that one.

    Attributes
    ----------
    labels: :class:`list[str]`
        The option labels, indexed by the menu's values. The values are *positions* rather than the
        labels themselves: an option value is capped at 100 characters, and two long labels sharing a
        prefix would otherwise truncate into the same value and become indistinguishable.

    """

    def __init__(self, *, options: list[QuestionOption]) -> None:
        super().__init__(
            placeholder="Pick as many as apply.",
            min_values=1,
            max_values=len(options),
            options=[
                discord.SelectOption(
                    label=truncate(option.label, SELECT_LABEL_SIZE),
                    value=str(index),
                    description=truncate(option.description, SELECT_LABEL_SIZE) or None,
                )
                for index, option in enumerate(options)
            ],
        )
        self.labels: list[str] = [option.label for option in options]

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answers with every option ticked, in the order the model offered them."""
        view: Optional[QuestionPrompt] = self.view  # type: ignore[assignment]
        if view is None:
            return

        # Sorted rather than taken as they arrive: Discord reports values in click order, and the
        # model wrote these in an order of its own that the answer may as well keep.
        chosen: list[str] = [self.labels[index] for index in sorted(int(value) for value in self.values) if index < len(self.labels)]
        if not chosen:
            await interaction.response.defer()
            return
        await view.resolve(
            interaction=interaction,
            decision=PromptDecision(behavior="deny", message=f"The user answered: {', '.join(chosen)}"),
        )


class QuestionPrompt(ClaudePrompt):
    """The prompt for `AskUserQuestion`; the options the model offered, plus "Other…".

    Laid out the way the session panel lays out a setting: each option is a section of its own, its
    label in bold over the model's description, with the button beside it as the accessory. A
    `multiSelect` question is one :class:`QuestionSelect` menu instead, because a button press is a
    whole answer and cannot say "these two".

    .. note::
        The answer goes back as a **deny** whose message names the chosen option. Allowing is not
        enough — tested against 2.1.220; the CLI ran the tool, found no terminal to ask at, and
        reported that the question was never answered. Denying with the answer in the message is what
        actually puts the user's choice in front of the model.

    .. note::
        Only the first question is offered. The tool can carry several, but a turn stalls on the
        answer and stacking four views on one blocked turn reads as four separate problems; the model
        asks the next one on its own turn.
    """

    # The answer is already on screen by the time this is retired: it goes back as the tool's result,
    # and the transcript writes results under the call that made them — which is the line directly
    # above this message. A settled card here would say the same thing a second time, so the prompt
    # takes itself off the thread instead. :meth:`settled` is kept for the fallback the base takes
    # when a delete fails.
    transient: bool = True

    def __init__(self, *, cog: ClaudeCog, request_id: str, user_id: int, thread_id: int, question: dict) -> None:
        super().__init__(cog=cog, request_id=request_id, user_id=user_id, thread_id=thread_id)
        self.header: str = str(question.get("header") or "Question")
        self.question: str = str(question.get("question") or "")
        self.multi: bool = bool(question.get("multiSelect"))

        options: list[QuestionOption] = _question_options(question=question)
        # A menu carries its own text in the select payload; sections spend the view's characters. So
        # what the options will cost is measured *before* the question is fitted, rather than the
        # question being fitted against a budget the options then overrun. Overrunning is not cosmetic
        # here: a view raises at construction, and `on_control_request` answers a prompt it could not
        # build by denying it.
        sections: list[str] = [] if self.multi and options else [_option_body(option=option) for option in options]

        container: discord.ui.Container = discord.ui.Container(accent_colour=discord.Color.blurple())
        # Fitted for the same reason a plan is: the model writes this text and nothing caps it, so a
        # long question would push the view over its budget and the prompt would never post.
        hint: str = QUESTION_HINT_MULTI if self.multi else QUESTION_HINT_SINGLE
        chunks, _ = _fit_chunks(
            f"### {self.header}\n{self.question}\n{hint}",
            reserved=PROMPT_TEXT_RESERVE + sum(len(body) for body in sections),
        )
        for chunk in chunks:
            container.add_item(discord.ui.TextDisplay(chunk))
        container.add_item(discord.ui.Separator())

        rows: list[discord.ui.ActionRow] = [discord.ui.ActionRow()]
        # Falls back to the single-choice shape when the menu would be empty: a `multiSelect` question
        # whose options were all malformed still has to render, because "Other…" is a real answer to
        # it — and with no sections either, that shape is just the "Other…" row.
        if self.multi and options:
            rows[0].add_item(QuestionSelect(options=options[:SELECT_OPTION_LIMIT]))
            # A row holding a select holds nothing else, so "Other…" needs one of its own.
            rows.append(discord.ui.ActionRow())
        else:
            # One section per option, the way the panel lays out a setting: the option is the text and
            # the button is only its accessory. A row of labelled buttons could not say any of this --
            # the model's description for each option had nowhere to go and was dropped on the floor,
            # and four labels long enough to be worth reading wrapped the row anyway.
            # `strict`: the two are built from the same list, and a mismatch would silently drop an
            # option off the end of the prompt rather than say so.
            for option, body in zip(options, sections, strict=True):
                container.add_item(
                    discord.ui.Section(
                        body,
                        accessory=PromptButton(
                            label=truncate(option.label, OPTION_BUTTON_SIZE),
                            decision=PromptDecision(behavior="deny", message=f"The user answered: {option.label}"),
                        ),
                    ),
                )
            if sections:
                container.add_item(discord.ui.Separator())

        rows[-1].add_item(OtherAnswerButton())
        for row in rows:
            container.add_item(row)
        self.add_mention(container=container)
        self.add_item(container)

    def settled(self, *, decision: PromptDecision) -> discord.ui.LayoutView:
        """Returns the answered form, showing which option was taken."""
        return SettledPrompt(
            heading=f"### {self.header}",
            body=self.question,
            outcome=decision.message or "Answered.",
            allowed=True,
        )


class SettledPrompt(discord.ui.LayoutView):
    """What a prompt leaves behind once it has been answered.

    A row of dead buttons says nothing on scrollback; this says what was decided, which is the part
    worth keeping.

    Fits its own body rather than trusting the caller to have done it. Every prompt ends up here and
    each one arrives with a body of its own shape — a tool target, a question, a plan — so the one
    place that knows what the heading and outcome cost is this one. Overflowing here is the worst
    place to do it: the view is written by `edit_message` from a button press, and until
    :meth:`ClaudePrompt.resolve` was reordered, failing that edit left the CLI blocked forever.
    """

    def __init__(self, *, heading: str, body: str, outcome: str, allowed: bool) -> None:
        super().__init__(timeout=None)
        container: discord.ui.Container = discord.ui.Container(
            accent_colour=discord.Color.green() if allowed else discord.Color.dark_grey(),
        )
        container.add_item(discord.ui.TextDisplay(heading))
        if body:
            chunks, dropped = _fit_chunks(body, reserved=len(heading) + len(outcome) + PROMPT_TEXT_RESERVE)
            for chunk in chunks:
                container.add_item(discord.ui.TextDisplay(chunk))
            if dropped:
                container.add_item(discord.ui.TextDisplay(f"-# …{dropped:,} more characters."))
        container.add_item(discord.ui.TextDisplay(f"-# {outcome}"))
        self.add_item(container)


def _outcome_line(*, decision: PromptDecision) -> str:
    """Returns the line describing what a decision did, for the settled prompt."""
    if decision.behavior == "allow":
        if decision.remember is not None:
            return f"Approved, and `{decision.remember}` will not ask again this session."
        return f"Approved, and the session is now in `{decision.mode}`." if decision.mode is not None else "Approved."
    if decision.message == PROMPT_TIMEOUT_MESSAGE:
        return "No answer in time, so this was declined."
    if decision.message == PROMPT_WITHDRAWN_MESSAGE:
        return "Withdrawn by Claude before it was answered."
    return "Declined."


def _mode_suggestion(*, request: dict) -> Optional[str]:
    """Returns the permission mode the CLI offered alongside an approval, if it offered one.

    The CLI attaches these itself as `permission_suggestions`, eg. `acceptEdits` beside a file edit.
    Only a `setMode` is taken, and only when we recognise the mode — the "Always" button has to say
    what it will do, and a mode we cannot name is one we cannot label honestly.

    Parameters
    ----------
    request: :class:`dict`
        The `can_use_tool` request body.

    Returns
    -------
    :class:`Optional[str]`
        The CLI `--permission-mode` value to offer, or `None` when there is nothing to offer.

    """
    suggestions: Any = request.get("permission_suggestions")
    if not isinstance(suggestions, list):
        return None

    known: set[str] = {mode.value for mode in MODES.values()}
    for entry in suggestions:
        if not isinstance(entry, dict) or entry.get("type") != "setMode":
            continue
        mode: Any = entry.get("mode")
        if isinstance(mode, str) and mode in known:
            return mode
    return None


def _fit_chunks(text: str, *, reserved: int, size: int = 1000) -> tuple[list[str], int]:
    """Splits text to what is left of a view's character budget once `reserved` is set aside.

    Replaces an earlier helper that capped the *body* at the view's ceiling, which is only correct
    for a body that is the entire view. It never is: a prompt puts a heading above the body and an
    outcome line below it, and those characters count against the same 4000. A body sized at the
    ceiling therefore built a view just over it, which Discord answers with a 400 — so the prompt
    never posted and the plan it was asking about could not be approved at all.

    Parameters
    ----------
    text: :class:`str`
        The body to fit.
    reserved: :class:`int`
        Characters to leave for the rest of the view.
    size: :class:`int`, optional
        The per-component chunk size, by default `1000`.

    Returns
    -------
    :class:`tuple[list[str], int]`
        The chunks, and how many characters had to be dropped — `0` when it all fitted.

    """
    budget: int = max(PANEL_CONTENT_LIMIT - max(reserved, 0), 0)
    kept: str = text[:budget]
    chunks: list[str] = [kept[index : index + size] for index in range(0, len(kept), size)] or [""]
    return chunks, len(text) - len(kept)


# ---------------------------------------------------------------------------
# Session panel
# ---------------------------------------------------------------------------


class PanelSelect(discord.ui.Select["SessionPanel"]):
    """Base select for the session panel; routes its choice to a cog handler."""

    def __init__(self, *, custom_id: str, placeholder: str, options: list[discord.SelectOption], handler: str) -> None:
        super().__init__(custom_id=custom_id, placeholder=placeholder, options=options, min_values=1, max_values=1)
        self.handler: str = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: Optional[ClaudeCog] = _cog_from(interaction)
        if cog is None:
            return
        await getattr(cog, self.handler)(interaction, value=self.values[0])


class PanelButton(discord.ui.Button["SessionPanel"]):
    """Base button for the session panel; routes its press to a cog handler."""

    def __init__(
        self,
        *,
        label: str,
        custom_id: str,
        handler: str,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: Optional[str] = None,
    ) -> None:
        super().__init__(label=label, custom_id=custom_id, style=style, emoji=emoji)
        self.handler: str = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: Optional[ClaudeCog] = _cog_from(interaction)
        if cog is None:
            return
        await getattr(cog, self.handler)(interaction)


def _cog_from(interaction: discord.Interaction) -> Optional[ClaudeCog]:
    """Returns the loaded cog, or `None` when the extension has been unloaded.

    Panel components are persistent, so an interaction can arrive against a cog that is no longer
    loaded; eg. a reload between the message being sent and the button being pressed.
    """
    cog: Optional[commands.Cog] = interaction.client.get_cog("Claude")  # type: ignore[attr-defined]
    if not isinstance(cog, ClaudeCog):
        return None
    return cog


class PanelChoice(NamedTuple):
    """One option on a panel select, flattened so one builder can render all three controls."""

    value: str
    label: str
    summary: str


def _model_choices() -> list[PanelChoice]:
    """Returns the model options, summarised by the CLI model ID."""
    return [PanelChoice(value=name, label=name.capitalize(), summary=f"`{model}`") for name, model in MODELS.items()]


def _mode_choices() -> list[PanelChoice]:
    """Returns the permission mode options."""
    return [PanelChoice(value=name, label=name.capitalize(), summary=mode.description) for name, mode in MODES.items()]


def _effort_choices() -> list[PanelChoice]:
    """Returns the effort options."""
    return [PanelChoice(value=name, label=name.capitalize(), summary=EFFORT_DESCRIPTIONS[name]) for name in EFFORTS]


def _mode_value(name: str) -> str:
    """Returns the CLI `--permission-mode` value for a short mode name."""
    return MODES.get(name, MODES["default"]).value


def _mode_name(value: str) -> str:
    """Returns the short mode name for a CLI value; the inverse of :func:`_mode_value`."""
    return next((name for name, mode in MODES.items() if mode.value == value), "default")


def _model_name(value: str) -> str:
    """Returns the short model name for a CLI `--model` value; the inverse of :attr:`MODELS`."""
    return next((name for name, model in MODELS.items() if model == value), "sonnet")


class SessionPanel(discord.ui.LayoutView):
    """The opening post of a session thread: state, controls and whether the process is up.

    Persistent by design. Every child carries a static custom ID and the session is resolved from the
    interaction's thread, so the panel keeps working across restarts with nothing stored about it.

    .. warning::
        A Components V2 message cannot carry `content`, `embeds`, stickers or polls. A message can be
        edited *into* this layout by clearing those, but never back out of it.

    Parameters
    ----------
    state: :class:`Optional[SessionState]`, optional
        The session to render. `None` builds the bare shell registered at startup, whose only job is
        to own the custom IDs.
    status: :class:`SessionStatus`, optional
        How to render the session, by default active.
    live: :class:`bool`, optional
        Whether a CLI process is currently up for this session, by default `False`.
    transcript: :class:`Optional[str]`, optional
        The filename of a transcript already attached to this post.

    """

    def __init__(
        self,
        *,
        state: Optional[SessionState] = None,
        status: SessionStatus = SessionStatus.ACTIVE,
        live: bool = False,
        transcript: Optional[str] = None,
    ) -> None:
        super().__init__(timeout=None)

        if state is None:
            # The shell only needs every custom ID to exist so the persistent view matches on any of
            # them. It renders dormant so `PANEL_RESTORE_ID` is registered too.
            status = SessionStatus.EXPIRED

        container: discord.ui.Container = discord.ui.Container(
            accent_colour=discord.Color.dark_grey() if status.dormant else discord.Color.blurple(),
        )

        self.add_header(container=container, state=state, status=status, live=live)
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        self.add_control(
            container=container,
            title=PANEL_MODEL_TITLE,
            custom_id=PANEL_MODEL_ID,
            handler="panel_model",
            choices=_model_choices(),
            selected=None if state is None else _model_name(state.model),
        )
        self.add_control(
            container=container,
            title=PANEL_MODE_TITLE,
            custom_id=PANEL_MODE_ID,
            handler="panel_mode",
            choices=_mode_choices(),
            selected=None if state is None else _mode_name(state.mode),
        )
        self.add_control(
            container=container,
            title=PANEL_EFFORT_TITLE,
            custom_id=PANEL_EFFORT_ID,
            handler="panel_effort",
            choices=_effort_choices(),
            selected=None if state is None else state.effort,
        )

        self.add_footer(container=container, state=state, transcript=transcript)
        self.add_item(container)

        # The actions sit outside the container, below the settings box rather than inside it. They act
        # *on* the panel; they are not settings of it, and the border makes that read at a glance.
        actions: discord.ui.ActionRow = discord.ui.ActionRow()
        actions.add_item(PanelButton(label="Clear", custom_id=PANEL_CLEAR_ID, handler="panel_clear"))
        actions.add_item(PanelButton(label="Files", custom_id=PANEL_FILES_ID, handler="panel_files"))
        actions.add_item(PanelButton(label="Stop Turn", custom_id=PANEL_INTERRUPT_ID, handler="panel_interrupt"))
        actions.add_item(PanelButton(label="Help", custom_id=PANEL_HELP_ID, handler="panel_help"))
        actions.add_item(
            PanelButton(label="Close", custom_id=PANEL_CLOSE_ID, handler="panel_close", style=discord.ButtonStyle.danger),
        )
        self.add_item(actions)

        if status.dormant:
            self.disable_controls()

        self.warn_if_oversized()

    def add_header(
        self,
        *,
        container: discord.ui.Container,
        state: Optional[SessionState],
        status: SessionStatus,
        live: bool,
    ) -> None:
        """Adds the title block; a section whose accessory is the thumbnail, or the way back in.

        A section takes exactly one accessory and it has to be a button or a thumbnail, never a select.
        A dormant panel has one live control and it belongs beside the notice explaining why everything
        else is dead, so it takes the slot.
        """
        body: list[str] = [f"## {PANEL_HEADERS[status]}"]
        if state is not None:
            body.append(PANEL_NOTICES[status])
            if not status.dormant:
                # Whether the CLI is actually up. A parked session behaves identically from the user's
                # side — the next message resumes it — but saying so is the difference between "it
                # forgot me" and "it is asleep".
                body.append(PANEL_LIVE_NOTE if live else PANEL_PARKED_NOTE)

        accessory: discord.ui.Item[Any]
        if status.dormant:
            accessory = PanelButton(
                label="Restore Session",
                custom_id=PANEL_RESTORE_ID,
                handler="panel_restore",
                style=discord.ButtonStyle.success,
            )
        else:
            accessory = discord.ui.Thumbnail(
                KumaEmojiTable.to_cdn_url(PANEL_THUMBNAIL_EMOJI) or "",
                description=PANEL_THUMBNAIL_ALT,
            )

        container.add_item(discord.ui.Section("\n\n".join(body), accessory=accessory, id=PANEL_HEADER_COMPONENT_ID))

    def add_control(
        self,
        *,
        container: discord.ui.Container,
        title: str,
        custom_id: str,
        handler: str,
        choices: list[PanelChoice],
        selected: Optional[str],
    ) -> None:
        """Adds one labelled select; a heading, the current choice's summary, then the select.

        A select only shows its own descriptions once opened, so without that summary line a closed
        panel could not tell you what mode the session was actually in.
        """
        chosen: Optional[PanelChoice] = next((choice for choice in choices if choice.value == selected), None)
        summary: str = chosen.summary if chosen is not None else "Nothing selected."

        container.add_item(discord.ui.TextDisplay(f"### {title}\n-# {summary}"))
        container.add_item(
            discord.ui.ActionRow().add_item(
                PanelSelect(
                    custom_id=custom_id,
                    placeholder=title,
                    handler=handler,
                    options=[
                        discord.SelectOption(
                            label=choice.label,
                            value=choice.value,
                            description=choice.summary,
                            default=choice.value == selected,
                        )
                        for choice in choices
                    ],
                ),
            ),
        )

    def add_footer(
        self,
        *,
        container: discord.ui.Container,
        state: Optional[SessionState],
        transcript: Optional[str],
    ) -> None:
        """Adds the reference block below the controls; the state line and the transcript."""
        if state is None and transcript is None:
            return

        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        if state is not None:
            # Carries the ID `parse_state` reads the session back out of after a restart.
            container.add_item(discord.ui.TextDisplay(state.state_line, id=PANEL_STATE_COMPONENT_ID))

        if transcript is not None:
            # References an attachment already on the message. `Message.edit` keeps attachments it is
            # not told about, so later panel edits point at the same upload instead of re-sending a
            # multi-megabyte file every time a select changes.
            container.add_item(
                discord.ui.TextDisplay(
                    "-# Conversation transcript, for `.restore` after a rebuild:",
                    id=PANEL_TRANSCRIPT_COMPONENT_ID,
                ),
            )
            container.add_item(discord.ui.File(f"attachment://{transcript}"))

    def disable_controls(self) -> None:
        """Greys out every control except the restore button.

        We walk the finished view rather than the rows we built, as the restore button lives on a
        section accessory and the actions live on the view itself; neither is reachable from a list
        of rows.
        """
        for child in self.walk_children():
            if isinstance(child, (discord.ui.Select, discord.ui.Button)) and child.custom_id != PANEL_RESTORE_ID:
                child.disabled = True

    def warn_if_oversized(self) -> None:
        """Logs when the panel has outgrown what Discord will accept in one view.

        Discord answers a view over either ceiling with a *400*, which for this panel means the post
        does not send and the session it described is gone. Better found in the log than in a thread.
        """
        length: int = self.content_length()
        count: int = self.total_children_count
        if length > PANEL_CONTENT_LIMIT or count > PANEL_COMPONENT_LIMIT:
            LOGGER.warning(
                "<%s.%s> | Panel is over a Components V2 limit | Characters: %s/%s | Components: %s/%s",
                __class__.__name__,
                "warn_if_oversized",
                length,
                PANEL_CONTENT_LIMIT,
                count,
                PANEL_COMPONENT_LIMIT,
            )


def parse_state(*, message: discord.Message, thread_id: int) -> Optional[SessionState]:
    """Rebuilds a :class:`SessionState` from a session thread's opening post.

    Reads the small-text state line straight out of the component that carries it, which is how a
    restarted bot recovers a session it has no memory of.
    """
    text: Optional[str] = _find_text(components=message.components, component_id=PANEL_STATE_COMPONENT_ID)
    if text is None:
        return None

    match: Optional[re.Match[str]] = STATE_LINE_PATTERN.search(text)
    if match is None:
        return None

    return SessionState(
        thread_id=thread_id,
        user_id=int(match.group("user_id")),
        session_id=match.group("session_id"),
        model=match.group("model"),
        mode=match.group("mode"),
        effort=match.group("effort"),
        started=message.created_at.timestamp(),
    )


def _find_text(*, components: Iterable[Any], component_id: int) -> Optional[str]:
    """Returns the content of the text display carrying `component_id`, at any depth."""
    for component in components:
        if isinstance(component, discord.components.TextDisplay) and component.id == component_id:
            return component.content
        found: Optional[str] = _find_text(components=getattr(component, "children", []) or [], component_id=component_id)
        if found is not None:
            return found
    return None


class PanelLookup(NamedTuple):
    """The result of reading a session's opening post; the panel, or why we haven't got one.

    "No panel" has two meanings that call for opposite handling. Discord answering *404* means the
    post is genuinely gone. Discord not answering means we don't know, the panel is most likely
    intact, and the right move is to change nothing and try again later.
    """

    message: Optional[discord.Message]
    gone: bool


# ---------------------------------------------------------------------------
# Dot commands
# ---------------------------------------------------------------------------


@dataclass
class DotCommand:
    """One entry in the in-thread `.command` table."""

    name: str
    summary: str
    usage: str = field(default="")
    aliases: tuple[str, ...] = field(default=())
    handler: Optional[str] = field(default=None)
    # The heading `.help` files this under. Nineteen commands in one list is a wall nobody reads to
    # the bottom of; four short lists are four things to skim past until one of them is the one.
    group: str = field(default="This post")
    # Whether the invoking message is tidied away once the command has run. Cleared for the few that
    # hand their message to `run_turn` as a reference: that turn replies to it, possibly minutes
    # later, so it has to outlive the command.
    transient: bool = field(default=True)


# The `.help` headings, in the order they are printed. Ordered from what changes a running session,
# through what changes the conversation, to the housekeeping nobody needs until they do.
GROUP_RUNNING: str = "How it runs"
GROUP_CONVERSATION: str = "The conversation"
GROUP_FILES: str = "Files and memory"
GROUP_POST: str = "This post"
DOT_GROUPS: tuple[str, ...] = (GROUP_RUNNING, GROUP_CONVERSATION, GROUP_FILES, GROUP_POST)


# Everything here is handled locally. The CLI's own slash commands are a separate matter: a live
# session *does* accept them as prompt text, so they are forwarded rather than listed here.
DOT_COMMANDS: tuple[DotCommand, ...] = (
    DotCommand(name="help", summary="Show this command list.", aliases=("h", "?"), handler="dot_help", group=GROUP_POST),
    DotCommand(
        name="model",
        summary="Switch model.",
        usage="<name>",
        aliases=("m",),
        handler="dot_model",
        group=GROUP_RUNNING,
    ),
    DotCommand(
        name="mode",
        summary="Switch permission mode.",
        usage="<name>",
        aliases=("perm",),
        handler="dot_mode",
        group=GROUP_RUNNING,
    ),
    DotCommand(
        name="effort",
        summary="Set how hard Claude works.",
        usage="<level>",
        aliases=("e",),
        handler="dot_effort",
        group=GROUP_RUNNING,
    ),
    DotCommand(
        name="approvals",
        summary="List the tools approved for this session; `clear` to revoke.",
        usage="[clear]",
        aliases=("allowed",),
        handler="dot_approvals",
        group=GROUP_RUNNING,
    ),
    DotCommand(name="plan", summary="Shortcut for `.mode plan`.", handler="dot_plan", group=GROUP_RUNNING),
    DotCommand(name="edits", summary="Shortcut for `.mode edits`.", aliases=("accept",), handler="dot_edits", group=GROUP_RUNNING),
    DotCommand(
        # Named for the CLI's own `/clear`, which is what it now delegates to; `new` and `reset` stay
        # as aliases so the older name keeps working.
        name="clear",
        summary="Clear the conversation here, keeping the files.",
        aliases=("new", "reset"),
        handler="dot_clear",
        group=GROUP_CONVERSATION,
        transient=False,
    ),
    DotCommand(
        name="ignore",
        summary="Stop treating messages here as prompts; run again to resume.",
        usage="[on | off]",
        aliases=("mute",),
        handler="dot_ignore",
        group=GROUP_CONVERSATION,
    ),
    DotCommand(
        name="context",
        summary="Show how full the context window is.",
        aliases=("ctx",),
        handler="dot_context",
        group=GROUP_CONVERSATION,
    ),
    DotCommand(
        name="compact",
        summary="Summarise the conversation now to free up room.",
        # `/compact` takes free-text steering, verified; it is forwarded through.
        usage="[what to keep]",
        handler="dot_compact",
        group=GROUP_CONVERSATION,
        transient=False,
    ),
    DotCommand(
        name="stop",
        summary="Interrupt the turn in progress, keeping the session.",
        aliases=("x", "cancel"),
        handler="dot_stop",
        group=GROUP_CONVERSATION,
    ),
    DotCommand(
        name="files",
        summary="List what this session has generated.",
        aliases=("ls", "f"),
        handler="dot_files",
        group=GROUP_FILES,
    ),
    DotCommand(name="get", summary="Upload one of them back to you.", usage="<name>", handler="dot_get", group=GROUP_FILES),
    DotCommand(
        name="memory",
        summary="Show your personal `CLAUDE.md`.",
        aliases=("claudemd",),
        handler="dot_memory",
        group=GROUP_FILES,
    ),
    DotCommand(name="status", summary="Settings, process and workspace.", aliases=("s", "info"), handler="dot_status", group=GROUP_POST),
    DotCommand(name="usage", summary="The shared account's rate limits.", aliases=("cost",), handler="dot_usage", group=GROUP_POST),
    DotCommand(name="rename", summary="Rename this post.", usage="<title>", aliases=("title",), handler="dot_rename", group=GROUP_POST),
    DotCommand(name="close", summary="Close and lock this session.", aliases=("expire",), handler="dot_close", group=GROUP_POST),
    DotCommand(
        name="restore",
        summary="Rebuild a dormant session from its snapshot.",
        handler="dot_restore",
        group=GROUP_POST,
    ),
)


# How long a `.command` message stays in the thread before it is tidied away. Long enough to read
# back what was typed next to the answer it produced, short enough that the thread stays a
# conversation rather than a command log.
DOT_COMMAND_LIFETIME: float = 15.0


def resolve_dot_command(name: str) -> tuple[Optional[DotCommand], list[str]]:
    """Resolves a typed command name, allowing any unambiguous abbreviation.

    An exact name or alias always wins. Otherwise the name is treated as a prefix and has to match
    exactly one command, so `.m` reaches `.model` while an ambiguous stub reports its candidates.
    """
    lowered: str = name.lower()
    for command in DOT_COMMANDS:
        if lowered == command.name or lowered in command.aliases:
            return command, []

    matches: list[DotCommand] = [
        command
        for command in DOT_COMMANDS
        if command.name.startswith(lowered) or any(alias.startswith(lowered) for alias in command.aliases)
    ]
    if len(matches) == 1:
        return matches[0], []
    return None, [command.name for command in matches]


# ---------------------------------------------------------------------------
# The cog
# ---------------------------------------------------------------------------


class ClaudeCog(Cog, name="Claude"):
    """Exposes the Claude Code CLI as one *live* forum post per session.

    Each user gets a private forum in the guild they ran `/claude ask` in; every post in it is one
    session held open by its own CLI process. Replying continues the conversation on stdin, and
    anything the CLI needs a human for comes back as a Discord prompt.

    Session state lives in the post's opening message and nowhere else, so a restart recovers
    everything by reading the message back off Discord. Who may open a session lives in
    `claude_users`, which is deliberately not the bot's owner list.
    """

    # Declared for `HintsCog`, which walks the loaded cogs rather than holding a registry of its own.
    # Keyed by cog so the key stays stable; changing it un-dismisses the hint for everyone.
    __hints__ = (
        Hint(
            key="claude.dot_commands",
            label="Session commands",
            text="Type `.help` in a session post to see the commands that change how it runs.",
        ),
    )

    # Declared for `Preferences`, which walks the loaded cogs the same way. The choices are built from
    # `Verbosity` itself, so the panel cannot come to offer a level the display has no path for.
    __preferences__ = (
        Preference(
            key=VERBOSITY_KEY,
            label="Claude session verbosity",
            summary="How much of a turn reaches the thread while it runs.",
            default=Verbosity.DEFAULT.value,
            choices=tuple(PreferenceChoice(value=level.value, label=level.title, summary=level.summary) for level in Verbosity),
        ),
    )

    claude = app_commands.Group(name="claude", description="Live Claude Code sessions.")

    def __init__(self, bot: Kuma_Kuma) -> None:
        super().__init__(bot=bot)
        # Session state by thread ID; a miss is re-read from the thread's opening post.
        self._sessions: dict[int, SessionState] = {}
        # The live CLI process per thread, when one is up. A session with no entry here is parked,
        # not broken; the next message respawns it with `--resume`.
        self._live: dict[int, LiveSession] = {}
        # The turn in flight per thread, so a prompt's events reach the status display that is
        # showing them and `.stop` has something to interrupt.
        self._turns: dict[int, TurnContext] = {}
        # Who may open a session at all, mirrored from `claude_users` in `cog_load`. Empty until
        # loaded, which fails closed.
        self._allowed: frozenset[int] = frozenset()
        # Serialises spawning per thread, so two messages arriving together cannot start two
        # processes for one session and have the second orphan the first.
        self._spawn_locks: dict[int, asyncio.Lock] = {}
        # Effort changes asked for while a turn was running, by thread ID. Effort is set by slash
        # command rather than control request, so it cannot be sent mid-turn; see `apply_effort`.
        self._pending_effort: dict[int, str] = {}
        # Threads already told they are nearing auto-compaction. Cleared when a compaction actually
        # happens, so a long session gets warned again on its way to the next one.
        self._context_warned: set[int] = set()
        # Threads already told the shared account is running low. Cleared when it recovers, so the
        # next squeeze is mentioned too.
        self._usage_warned: set[int] = set()
        # Prompts on screen and not yet answered, by request ID. The view itself is unreachable
        # otherwise, and `control_cancel_request` arrives with nothing but the ID.
        self._prompts: dict[str, ClaudePrompt] = {}
        # Turn tasks waiting on a session's lock, by thread. `.stop` has to reach these as well as the
        # running one: a queued turn that cannot be stopped simply runs once the turn in front of it
        # finishes, which is the one thing the person stopping it was trying to prevent.
        self._queued: dict[int, set[asyncio.Task[Any]]] = {}
        # Tools the owner has approved for the rest of a session, by thread ID. Deliberately in
        # memory: a standing permission that outlives the process holding it is a permission nobody
        # remembers granting, and a restart is exactly when you want to be asked again.
        self._approved: dict[int, set[str]] = {}
        # `.command` messages on their way out, by message ID. `reply_to` reads this to answer
        # unattached instead of replying, so the answer is not left quoting a message that is about
        # to be deleted.
        self._expiring: set[int] = set()
        # Strong references to the deletion tasks above; a task held nowhere can be collected
        # mid-sleep and the message it was going to remove would simply survive.
        self._expiry_tasks: set[asyncio.Task[None]] = set()

    # region --- Lifecycle

    async def cog_load(self) -> None:
        """Ensures the access table exists, reads it, registers the panel and starts the loops."""
        async with self.bot.pool.acquire() as conn:
            await conn.execute(ACCESS_SETUP_SQL)
        await self.load_access()

        await asyncio.to_thread(USERS_ROOT.mkdir, parents=True, exist_ok=True)

        # Before anything of ours is running, so `live` is empty and every match really is a leftover.
        # This is the one case `cog_unload` cannot cover: a bot killed outright leaves its children
        # reparented to init, holding a session each with nothing left that knows they exist.
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self.sweep_orphan_processes, live=set())

        # The shell only owns the custom IDs; every callback resolves its session from the thread.
        self.bot.add_view(view=SessionPanel())

        if self.cleanup_loop.is_running() is False:
            self.cleanup_loop.start()
            self.bot.task_loops.append(self.cleanup_loop)
        if self.reap_loop.is_running() is False:
            self.reap_loop.start()
            self.bot.task_loops.append(self.reap_loop)

    async def cog_unload(self) -> None:
        """Stops the loops and closes every live process, so a reload leaves nothing behind."""
        for loop in (self.cleanup_loop, self.reap_loop):
            if loop.is_running():
                loop.cancel()
            if loop in self.bot.task_loops:
                self.bot.task_loops.remove(loop)

        await self.seal_turns(reason=RELOAD_NOTE)

        # A reload mid-sleep would otherwise leave the odd `.command` message behind; nothing here is
        # worth keeping alive for, so they go with the cog.
        for task in list(self._expiry_tasks):
            task.cancel()
        self._expiry_tasks.clear()
        self._expiring.clear()

        # Closed rather than left running: a CLI process outliving its cog would go on writing to a
        # session nobody is reading and keep spending the account's limit.
        await asyncio.gather(*(session.close() for session in self._live.values()), return_exceptions=True)
        self._live.clear()

    async def seal_turns(self, *, reason: str) -> None:
        """Ends every in-flight turn with a finished-looking status display.

        `task.cancel()` only *requests* cancellation; the coroutine does not see `CancelledError`
        until its next await, and `run_turn` needs a further round trip after that to write its
        closing frame. Teardown used to cancel and return immediately, so that write was a race it
        usually lost on a reload and always lost on a restart — leaving the reader looking at a
        spinner for a turn that no longer exists.

        So: cancel, then actually wait for the handlers that already know how to seal correctly, and
        only reach into the display directly for whatever failed to get there in time.

        Parameters
        ----------
        reason: :class:`str`
            The closing line, via :attr:`TurnContext.stop_reason`.

        """
        contexts: list[TurnContext] = list(self._turns.values())
        self._turns.clear()
        if not contexts:
            return

        for context in contexts:
            context.stop_reason = reason

        pending: list[asyncio.Task[Any]] = [context.task for context in contexts if not context.task.done()]
        for task in pending:
            task.cancel()
        if pending:
            # Bounded on purpose. A Discord edit that hangs must not hold the reload open, and the
            # fallback below covers anything this abandons.
            with contextlib.suppress(Exception):
                await asyncio.wait(pending, timeout=SEAL_TIMEOUT)

        # A task that finished ran its own `CancelledError` handler and has already sealed. Anything
        # still running did not, and gets sealed from out here instead.
        for context in contexts:
            if context.task.done():
                continue
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    context.status.stop(final=context.interrupted_note()),
                    timeout=SEAL_TIMEOUT,
                )
        LOGGER.info("<%s.%s> | Sealed %s in-flight turn(s).", __class__.__name__, "seal_turns", len(contexts))

    def _spawn_lock(self, thread_id: int) -> asyncio.Lock:
        """Returns the per-thread spawn lock, creating it on first use."""
        return self._spawn_locks.setdefault(thread_id, asyncio.Lock())

    # endregion

    # region --- Access

    async def load_access(self) -> None:
        """Replaces the cached allowlist with what `claude_users` currently holds."""
        async with self.bot.pool.acquire() as conn:
            rows: list[Row] = await conn.fetchall("""SELECT userid FROM claude_users""")
        self._allowed = frozenset(row["userid"] for row in rows)
        LOGGER.info("<%s.%s> | Loaded %s Claude user(s).", __class__.__name__, "load_access", len(self._allowed))

    async def grant_access(self, *, user_id: int, added_by: int) -> bool:
        """Adds a user to `claude_users`, returning whether the row was new."""
        async with self.bot.pool.acquire() as conn:
            cursor = await conn.execute(
                """INSERT INTO claude_users(userid, added_by, added_at) VALUES(?, ?, ?) ON CONFLICT(userid) DO NOTHING""",
                (user_id, added_by, time.time()),
            )
            added: bool = cursor.get_cursor().rowcount > 0
        await self.load_access()
        return added

    async def revoke_access(self, *, user_id: int) -> bool:
        """Removes a user from `claude_users`, returning whether there was a row to remove."""
        async with self.bot.pool.acquire() as conn:
            cursor = await conn.execute("""DELETE FROM claude_users WHERE userid = ?""", (user_id,))
            removed: bool = cursor.get_cursor().rowcount > 0
        await self.load_access()
        return removed

    def may_use(self, user: Union[discord.Member, discord.User, discord.abc.User]) -> bool:
        """Returns whether a user may open sessions.

        The bot's owners always may — they administer the allowlist and locking themselves out of
        the thing they administer helps nobody.
        """
        return user.id in self._allowed or user.id in self.bot.owner_ids

    # endregion

    # region --- Live process management

    async def live_session(self, *, state: SessionState) -> LiveSession:
        """Returns the running CLI for a session, starting or resuming one when there isn't one.

        This is the single door onto a process. Everything that needs to talk to the CLI comes
        through here, so there is one place that decides whether to spawn, resume or reuse.

        Parameters
        ----------
        state: :class:`SessionState`
            The session wanting its process.

        Returns
        -------
        :class:`LiveSession`
            A running, handshaken session.

        """
        async with self._spawn_lock(state.thread_id):
            existing: Optional[LiveSession] = self._live.get(state.thread_id)
            if existing is not None and existing.alive:
                # The cached state object is the authoritative one; the process's copy follows it.
                existing.state = state
                return existing

            if existing is not None:
                # Dead but not cleaned up; drop it so the resume below is not confused by it.
                await existing.close()
                self._live.pop(state.thread_id, None)

            await asyncio.to_thread(prepare_workspace, directory=state.workspace)

            # Resume when the CLI has a transcript for this session and claim the ID when it does
            # not. Getting this backwards is fatal either way round: resuming an unknown ID fails,
            # and claiming a known one is refused as already in use.
            resume: bool = await asyncio.to_thread(live_transcript(cwd=state.cwd, session_id=state.session_id).is_file)
            session: LiveSession = await LiveSession.spawn(state=state, resume=resume)
            session.on_event = self.on_cli_event
            self._live[state.thread_id] = session
            return session

    def live_pids(self) -> set[int]:
        """Returns the PIDs of the processes this cog is currently driving."""
        return {session.process.pid for session in self._live.values() if session.process.pid is not None}

    @staticmethod
    def sweep_orphan_processes(*, live: set[int]) -> int:
        """Kills CLI processes left behind by a bot that did not shut down cleanly.

        `cog_unload` closes everything on a reload, so this is for the case it cannot reach: the bot
        being killed outright. The children are reparented to init and keep running — each one a few
        hundred MB of node, still holding a session and still able to spend the shared account — with
        nothing left that knows they exist.

        Identified by **two** signals together, never one: the process is working inside
        :attr:`USERS_ROOT`, *and* its arguments carry the flags only a live session is started with. A
        stray `claude` a person is running in a terminal fails the first, and anything else of ours
        under that root fails the second.

        Parameters
        ----------
        live: :class:`set[int]`
            PIDs this cog is driving right now, which are never candidates however they look.

        Returns
        -------
        :class:`int`
            How many processes were killed.

        """
        killed: int = 0
        for process in psutil.process_iter(["pid", "cmdline"]):
            if process.pid in live:
                continue
            try:
                arguments: list[str] = process.info["cmdline"] or []
                # Both markers, so this can never match a `claude` run from a shell somewhere else.
                if "--permission-prompt-tool" not in arguments or "--session-id" not in arguments:
                    continue
                if not Path(process.cwd()).is_relative_to(USERS_ROOT):
                    continue

                process.terminate()
                try:
                    process.wait(timeout=5)
                except psutil.TimeoutExpired:
                    process.kill()
                killed += 1
                LOGGER.warning(
                    "<%s.%s> | Killed an orphaned CLI process | PID: %s", __class__.__name__, "sweep_orphan_processes", process.pid
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # Gone on its own between the listing and the look, or not ours to inspect.
                continue
        return killed

    async def park(self, *, thread_id: int) -> bool:
        """Closes a session's process, keeping the session itself. Returns whether one was running."""
        session: Optional[LiveSession] = self._live.pop(thread_id, None)
        if session is None:
            return False
        await session.close()
        LOGGER.info("<%s.%s> | Parked a live session | Thread: %s", __class__.__name__, "park", thread_id)
        return True

    @tasks.loop(minutes=REAP_INTERVAL_MINUTES, reconnect=True)
    async def reap_loop(self) -> None:
        """Parks sessions that have gone quiet, so idle posts stop holding a node process each.

        Nothing is lost by this. The next message respawns with `--resume`, which restores the whole
        conversation — verified against 2.1.220 by killing a process and finding it still knew a
        codeword planted before the kill.
        """
        cutoff: float = time.monotonic() - (IDLE_REAP_MINUTES * 60)
        for thread_id, session in list(self._live.items()):
            # Never a session mid-turn; the reader task is what delivers the answer being waited on.
            if thread_id in self._turns or session.last_active > cutoff:
                continue
            await self.park(thread_id=thread_id)

    @reap_loop.before_loop
    async def before_reap_loop(self) -> None:
        await self.bot.wait_until_ready()

    # endregion

    # region --- Event pump

    async def on_cli_event(self, session: LiveSession, event: dict) -> None:
        """Routes one CLI event to the turn that is listening for it.

        The reader runs for the life of the *process*, not the turn, so events can arrive with no
        turn in flight (a late `system` message, anything after an interrupt). Those are dropped
        rather than guessed at.
        """
        kind: str = str(event.get("type") or "")

        # Before anything else, because a stale ID is silent until the process is reaped and then
        # resumes the wrong conversation entirely.
        await self.track_session_id(session=session, event=event)

        # Any event at all is proof of life, so the turn's watchdog is fed here rather than in the
        # handlers below — several of which return before a turn is ever looked up, and compaction
        # in particular is a long quiet stretch that would otherwise read as a hang.
        context: Optional[TurnContext] = self._turns.get(session.state.thread_id)
        if context is not None:
            context.touch()

        # A permission request is the exception: it is answered from a button press, which may well
        # outlive the turn that raised it, so it is handled whether or not a turn is listening.
        if kind == "control_request":
            await self.on_control_request(session=session, event=event)
            return

        # Compaction is the other thing that must not depend on a turn being registered. It happens
        # *to* the conversation rather than within one turn, and it is the only notice anyone gets
        # that earlier messages have been summarised away.
        if kind == "system" and event.get("subtype") == COMPACT_EVENT:
            await self.on_compacted(session=session, event=event)
            return

        # Also about the account rather than the turn, and it arrives *during* one — which is the
        # whole point of it, as no preflight check runs while a turn is already in flight.
        if kind == RATE_LIMIT_EVENT:
            self.on_rate_limited(session=session, event=event)
            return

        # The CLI taking back a prompt it already asked. Handled next to the request it undoes, and
        # for the same reason: the buttons outlive the turn, so this cannot wait on one being there.
        if kind == CANCEL_EVENT:
            await self.on_control_cancelled(session=session, event=event)
            return

        if context is None:
            return

        if kind == "assistant":
            payload: dict = event.get("message", {})
            # Whether the CLI wrote this one itself; see :data:`SYNTHETIC_NOISE` for what that costs us.
            synthetic: bool = payload.get("model") == SYNTHETIC_MODEL
            for block in payload.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    # Read once into a local; `isinstance` on a second `.get()` call narrows nothing.
                    raw_input: Any = block.get("input")
                    await context.on_tool(
                        tool=str(block.get("name") or "tool"),
                        target=tool_target(
                            tool_input=raw_input if isinstance(raw_input, dict) else {},
                            root=session.state.cwd,
                        ),
                        tool_id=str(block.get("id") or ""),
                    )
                elif block.get("type") == "text":
                    # Posted as it completes rather than streamed token by token. A block is the
                    # smallest unit that reads as a finished thought, and editing a message per token
                    # would spend the whole rate limit on redrawing half sentences.
                    text: str = str(block.get("text") or "").strip()
                    if text and not (synthetic and text in SYNTHETIC_NOISE):
                        await context.on_text(text)

        # The results for the calls above. Previously dropped entirely, which is why a call could
        # only ever be ticked off by the *next* call starting — a guess that a parallel batch or a
        # failure both make wrong. `user` events also carry the prompt echo, hence the block filter.
        elif kind == "user":
            for block in event.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    await context.on_tool_result(
                        tool_id=str(block.get("tool_use_id") or ""),
                        content=block.get("content"),
                        is_error=bool(block.get("is_error")),
                    )

        elif kind == "result":
            context.finish(event=event)

    async def track_session_id(self, *, session: LiveSession, event: dict) -> None:
        """Follows the CLI when it re-keys a session, so the recorded ID never goes stale.

        The ID is not ours to hold fixed. `/clear` mints a new one (verified: a cleared session came
        back under a different ID with both transcripts left on disk), and a fork does the same. The
        panel's state line is the only record of it and `--resume` is handed it verbatim, so an ID we
        failed to follow is silent right up until the process is reaped — and then the session comes
        back as the conversation it was told to forget.

        Parameters
        ----------
        session: :class:`LiveSession`
            The session the event came from.
        event: :class:`dict`
            Any event; most carry `session_id`, and the ones that don't are ignored.

        """
        incoming: Any = event.get("session_id")
        if not isinstance(incoming, str) or not incoming or incoming == session.state.session_id:
            return

        previous: str = session.state.session_id
        session.state.session_id = incoming
        # The cog's cached state is usually the very same object, but not guaranteed to be, and the
        # panel is rendered from the cached one.
        state: Optional[SessionState] = self._sessions.get(session.state.thread_id)
        if state is not None:
            state.session_id = incoming

        LOGGER.info(
            "<%s.%s> | Session re-keyed | Thread: %s | Was: %s | Now: %s",
            __class__.__name__,
            "track_session_id",
            session.state.thread_id,
            previous,
            incoming,
        )

        thread: Optional[discord.Thread] = self.bot.get_channel(session.state.thread_id)  # type: ignore[assignment]
        if isinstance(thread, discord.Thread) and state is not None:
            await self.update_panel(thread=thread, state=state)

    async def on_compacted(self, *, session: LiveSession, event: dict) -> None:
        """Tells the thread its conversation has been compacted.

        In a terminal this is visible as it happens. In a forum post it would be silent, and a session
        that has quietly summarised away the first half of its own conversation — while every one of
        those messages is still sitting in the thread, apparently still in play — is a genuinely
        confusing thing to be on the wrong end of.

        Parameters
        ----------
        session: :class:`LiveSession`
            The session that compacted.
        event: :class:`dict`
            The `compact_boundary` system event.

        """
        thread: Optional[discord.Thread] = self.bot.get_channel(session.state.thread_id)  # type: ignore[assignment]
        if not isinstance(thread, discord.Thread):
            return

        metadata: Any = event.get("compactMetadata")
        preserved: Any = (metadata or {}).get("preservedMessages") if isinstance(metadata, dict) else None
        detail: str = f" The last {preserved} messages were kept in full." if isinstance(preserved, int) and preserved > 0 else ""

        LOGGER.info(
            "<%s.%s> | Conversation compacted | Thread: %s | Preserved: %s",
            __class__.__name__,
            "on_compacted",
            session.state.thread_id,
            preserved,
        )
        # The warning has served its purpose once this fires, and a session that compacts twice should
        # get told twice.
        self._context_warned.discard(session.state.thread_id)
        await self.send_to_thread(
            thread=thread,
            content=f"-# {self.emoji_table.kuma_tea} **Conversation compacted.** Claude summarised the earlier part of "
            f"this session to make room.{detail} Everything above is still here to read, but it is no longer "
            f"in front of Claude verbatim.",
        )

    async def on_control_cancelled(self, *, session: LiveSession, event: dict) -> None:
        """Retires a prompt the CLI has taken back.

        A prompt outlives its own turn on purpose — the buttons are answerable long after the turn
        that raised them timed out — so nothing else would ever clear one the CLI no longer wants an
        answer to. Left alone the buttons stay live, and a press sends a control response for a
        request ID that was retired, which the CLI drops on the floor. The person pressing it gets a
        prompt that appears to accept the click and then does nothing at all.

        Parameters
        ----------
        session: :class:`LiveSession`
            The session the cancellation came from.
        event: :class:`dict`
            The `control_cancel_request`, carrying the `request_id` being withdrawn.

        """
        request_id: str = str(event.get("request_id") or "")
        if not request_id:
            return

        # Clears the outstanding marker whether or not a view is still up; a prompt that failed to
        # post leaves one behind.
        session.pending.pop(request_id, None)

        prompt: Optional[ClaudePrompt] = self._prompts.pop(request_id, None)
        if prompt is None:
            return

        LOGGER.info(
            "<%s.%s> | Prompt withdrawn | Thread: %s | Request: %s",
            __class__.__name__,
            "on_control_cancelled",
            session.state.thread_id,
            request_id,
        )
        await prompt.withdraw()

        # The turn was showing "waiting on you"; it is not any more.
        context: Optional[TurnContext] = self._turns.get(session.state.thread_id)
        if context is not None:
            await context.on_resumed()

    def on_rate_limited(self, *, session: LiveSession, event: dict) -> None:
        """Dates the cached usage reading when the CLI says the account's standing has changed.

        Deliberately does not talk to the thread. `blocked_by_usage` already owns everything a person
        sees about the budget — the refusal, the warning, and the re-arming — and it re-reads before
        it refuses, so all this has to do is make sure the reading it re-reads is not the stale one.
        Announcing here as well would mean two code paths writing near-identical messages into the
        same post.

        Parameters
        ----------
        session: :class:`LiveSession`
            The session the event came from.
        event: :class:`dict`
            The `rate_limit_event`, carrying `rate_limit_info`.

        """
        info: Any = event.get("rate_limit_info")
        if not isinstance(info, dict):
            return

        status: str = str(info.get("status") or "")
        if not status or status == RATE_LIMIT_OK:
            # The healthy case, and it fires on ordinary turns. Dropping the cache here would spend
            # 275ms re-reading before every single turn for no news at all.
            return

        session.invalidate_usage()
        LOGGER.warning(
            "<%s.%s> | Account standing changed | Thread: %s | Status: %s | Window: %s | Overage: %s",
            __class__.__name__,
            "on_rate_limited",
            session.state.thread_id,
            status,
            info.get("rateLimitType"),
            info.get("isUsingOverage"),
        )

    async def check_context(self, *, thread: discord.Thread, session: LiveSession) -> None:
        """Warns once when a session is closing on the point the CLI will compact it by itself.

        Read out of band so it costs nothing, and only mentioned on the way *past* the mark — a bar
        under every answer would be noise on a session that has ninety percent of its window free.
        """
        if thread.id in self._context_warned:
            return

        usage: Optional[ContextUsage] = await session.context_usage()
        if usage is None or not usage.nearing_compact:
            return

        self._context_warned.add(thread.id)
        await self.send_to_thread(
            thread=thread,
            content=f"-# {self.emoji_table.kuma_hmm} This session is {usage.percentage:.0f}% through its context "
            f"({usage.total:,} / {usage.maximum:,} tokens). Claude will summarise the earlier part by itself soon; "
            f"`.new` starts a clean conversation in this post if you would rather it did not.",
        )

    async def on_control_request(self, *, session: LiveSession, event: dict) -> None:
        """Puts a `can_use_tool` request in front of the user as the view that suits it.

        All three kinds arrive here on the one channel and are told apart by `tool_name`:
        `ExitPlanMode` carries a plan to read, `AskUserQuestion` carries options to pick from, and
        everything else is a plain yes/no.
        """
        request: dict = event.get("request") or {}
        if request.get("subtype") != "can_use_tool":
            return

        request_id: str = str(event.get("request_id") or "")
        state: SessionState = session.state
        thread: Optional[discord.Thread] = self.bot.get_channel(state.thread_id)  # type: ignore[assignment]
        if not isinstance(thread, discord.Thread) or not request_id:
            # Nobody can answer, so deny rather than leave the turn blocked until it times out.
            await session.answer(request_id=request_id, response={"behavior": "deny", "message": "The session's thread is unreachable."})
            return

        tool: str = str(request.get("tool_name") or "tool")
        # Read once into a local; `isinstance` on a second `.get()` call narrows nothing.
        raw_input: Any = request.get("input")
        tool_input: dict = raw_input if isinstance(raw_input, dict) else {}

        # A standing approval answers before anything is built or shown. Only the plain yes/no tools
        # can hold one: `ExitPlanMode` and `AskUserQuestion` are not permission questions at all --
        # one asks whether a plan is right and the other asks what you want — and answering either on
        # the strength of an old press would be answering a question that was never put.
        if tool in self._approved.get(state.thread_id, set()) and tool not in UNREMEMBERABLE_TOOLS:
            LOGGER.info(
                "<%s.%s> | Auto-approved from a standing grant | Thread: %s | Tool: %s",
                __class__.__name__,
                "on_control_request",
                state.thread_id,
                tool,
            )
            await session.answer(request_id=request_id, response={"behavior": "allow"})
            return

        # Built inside a guard because a view is not inert: Components V2 enforces its limits at
        # construction, so an oversized or malformed request raises here — *before* the request has
        # been registered as pending, which is the one place a failure leaves nobody to answer the
        # CLI and the turn hangs to :data:`TURN_SILENCE_LIMIT` with nothing said about why.
        prompt: ClaudePrompt
        try:
            if tool == "ExitPlanMode":
                prompt = PlanApproval(
                    cog=self,
                    request_id=request_id,
                    user_id=state.user_id,
                    thread_id=state.thread_id,
                    plan=str(tool_input.get("plan") or "No plan text was provided."),
                )
            elif tool == "AskUserQuestion":
                questions: Any = tool_input.get("questions")
                question: Optional[dict] = (
                    next((entry for entry in questions if isinstance(entry, dict)), None) if isinstance(questions, list) else None
                )
                if question is None:
                    await session.answer(
                        request_id=request_id, response={"behavior": "deny", "message": "The question could not be displayed."}
                    )
                    return
                prompt = QuestionPrompt(
                    cog=self, request_id=request_id, user_id=state.user_id, thread_id=state.thread_id, question=question
                )
            else:
                prompt = ToolApproval(
                    cog=self,
                    request_id=request_id,
                    user_id=state.user_id,
                    thread_id=state.thread_id,
                    tool=str(request.get("display_name") or tool),
                    key=tool,
                    target=tool_target(tool_input=tool_input, root=state.cwd),
                    suggestion=_mode_suggestion(request=request),
                )
        except Exception:
            LOGGER.exception(
                "<%s.%s> | Could not build a prompt | Thread: %s | Tool: %s",
                __class__.__name__,
                "on_control_request",
                state.thread_id,
                tool,
            )
            await session.answer(
                request_id=request_id, response={"behavior": "deny", "message": "The prompt could not be built for the user."}
            )
            return

        # Register before sending. The future is what the turn's status line watches to show that it
        # is waiting on a person rather than stalled.
        session.pending[request_id] = asyncio.get_running_loop().create_future()
        context: Optional[TurnContext] = self._turns.get(state.thread_id)
        if context is not None:
            await context.on_waiting(tool=tool)

        self._prompts[request_id] = prompt
        try:
            # Posted first, so the buttons stay at the bottom where the reader's eye already is.
            if isinstance(prompt, PlanApproval) and prompt.dropped:
                await thread.send(
                    content=f"-# {self.emoji_table.kuma_tea} The plan is longer than one message holds; here it is in full.",
                    file=discord.File(io.BytesIO(prompt.plan.encode()), filename=PLAN_ATTACHMENT_NAME),
                    # Silent like everything else a session posts; the prompt below it is what rings,
                    # and ringing twice for one decision is worse than not ringing at all.
                    silent=True,
                )
            # The one message a session sends that notifies, and both halves of that are deliberate:
            # `silent` is off so the notification fires at all, and `allowed_mentions` is explicit so
            # the owner's line in the view actually pings rather than merely rendering blue. A default
            # tightened elsewhere would otherwise silence the only thing anyone is waiting on.
            prompt.message = await thread.send(
                view=prompt,
                allowed_mentions=discord.AllowedMentions(users=True),
                silent=False,
            )
        except Exception:
            # Deliberately every exception, not just `HTTPException`. Anything escaping here leaves
            # the CLI blocked on an answer that is now never coming, and it blocks until the turn
            # times out minutes later — a far worse outcome than a denial that says why.
            LOGGER.exception("<%s.%s> | Could not post a prompt | Thread: %s", __class__.__name__, "on_control_request", state.thread_id)
            self._prompts.pop(request_id, None)
            await session.answer(
                request_id=request_id, response={"behavior": "deny", "message": "The prompt could not be shown to the user."}
            )

    async def answer_prompt(self, *, thread_id: int, request_id: str, decision: PromptDecision) -> None:
        """Sends a prompt's decision to the CLI, applying any mode switch it carried.

        Parameters
        ----------
        thread_id: :class:`int`
            The session whose process is waiting.
        request_id: :class:`str`
            The control request being answered.
        decision: :class:`PromptDecision`
            What the user chose.

        """
        # However this prompt ended — pressed, timed out, or answered for it — it is off screen now.
        self._prompts.pop(request_id, None)

        # Recorded before the answer goes out, and whether or not the process is still there to hear
        # it: the grant belongs to the session, not to this request, and a press that landed as the
        # process went away should not have to be made again on the next one.
        if decision.remember is not None and decision.behavior == "allow":
            self._approved.setdefault(thread_id, set()).add(decision.remember)
            LOGGER.info(
                "<%s.%s> | Tool approved for the session | Thread: %s | Tool: %s",
                __class__.__name__,
                "answer_prompt",
                thread_id,
                decision.remember,
            )

        session: Optional[LiveSession] = self._live.get(thread_id)
        if session is None or not session.alive:
            # The process went away while the prompt was on screen. Nothing to answer, and the
            # session will resume without this turn.
            return

        response: dict[str, Any] = {"behavior": decision.behavior}
        if decision.behavior == "deny":
            response["message"] = decision.message or "The user declined."
        if decision.message is not None and decision.behavior == "allow":
            response["message"] = decision.message

        await session.answer(request_id=request_id, response=response)

        if decision.mode is not None:
            # Applied *after* the answer so the tool that prompted runs under the decision the user
            # actually made, not under the widened mode.
            await session.set_mode(mode=_mode_value(_mode_name(decision.mode)) if decision.mode in MODES else decision.mode)
            state: Optional[SessionState] = self._sessions.get(thread_id)
            if state is not None:
                state.mode = session.state.mode
                thread: Optional[discord.Thread] = self.bot.get_channel(thread_id)  # type: ignore[assignment]
                if isinstance(thread, discord.Thread):
                    await self.update_panel(thread=thread, state=state)

        context: Optional[TurnContext] = self._turns.get(thread_id)
        if context is not None:
            await context.on_resumed()

    # endregion

    # region --- Turns

    async def run_turn(
        self,
        *,
        thread: discord.Thread,
        state: SessionState,
        prompt: str,
        reference: Optional[discord.Message] = None,
    ) -> None:
        """Sends one prompt to a session's live process and narrates it into the thread.

        The status message is the same animated tool log the one-shot cog used; what changed is where
        the events come from. There is no subprocess to wait on here — the process outlives the turn
        — so the turn ends when the CLI's `result` event arrives, or when it is interrupted.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread.
        state: :class:`SessionState`
            The session to run under.
        prompt: :class:`str`
            The prompt to send.
        reference: :class:`Optional[discord.Message]`, optional
            The message being answered, by default `None`.

        """
        try:
            session: LiveSession = await self.live_session(state=state)
        except FileNotFoundError:
            if not await asyncio.to_thread(state.cwd.is_dir):
                await self.send_to_thread(
                    thread=thread, content=f"Your workspace `{state.cwd}` has gone away. {self.emoji_table.kuma_shock}"
                )
                return
            await self.send_to_thread(
                thread=thread,
                content=f"`claude` was not found. Is Claude Code installed and on the bot's PATH? {self.emoji_table.kuma_shock}",
            )
            return
        except TimeoutError:
            await self.send_to_thread(
                thread=thread,
                content=f"Claude Code started but never answered the handshake, so I can't drive it. {self.emoji_table.kuma_sad}",
            )
            return

        # Queued, not refused. The CLI takes messages sent during a turn and works them in order, and a
        # session in Discord should behave no differently — being told to wait and re-send is not what
        # typing two things in a row means. `asyncio.Lock` hands out in the order it was asked, and
        # `live_session` above cannot reorder two messages: the second waits on the spawn lock and so
        # reaches this line after the first.
        waiting: set[asyncio.Task[Any]] = self._queued.setdefault(thread.id, set())
        task: Optional[asyncio.Task[Any]] = asyncio.current_task()
        if session.turn.locked():
            await self.send_to_thread(
                thread=thread,
                content=f"-# {self.emoji_table.kuma_tea} Queued behind the turn in flight; `.stop` drops both.",
                reference=reference,
            )

        if task is not None:
            waiting.add(task)
        try:
            async with session.turn:
                # Off the queue and running; `.stop` reaches it through `_turns` from here on.
                if task is not None:
                    waiting.discard(task)
                # Read here rather than held on the session: a queued turn should be narrated the way
                # its owner wants *now*, not the way they wanted it when the message was sent.
                level: Verbosity = await self.verbosity(user_id=state.user_id)
                runner = self._run_turn_inline if level is Verbosity.VERBOSE else self._run_turn
                await runner(thread=thread, state=state, session=session, prompt=prompt, level=level, reference=reference)
        finally:
            if task is not None:
                waiting.discard(task)
            if not waiting:
                self._queued.pop(thread.id, None)

    async def verbosity(self, *, user_id: int) -> Verbosity:
        """Returns how much of a turn this session's owner wants to see.

        Read per turn rather than cached, so a change made on the panel takes effect at the next
        message rather than at the next restart. Reached through `get_cog` rather than an import, so
        the two cogs stay independently reloadable, and an unloaded `preferences` means the default
        display rather than a turn that will not run.

        Parameters
        ----------
        user_id: :class:`int`
            The Discord ID of the session's owner.

        Returns
        -------
        :class:`Verbosity`
            The stored level, or :attr:`Verbosity.DEFAULT` when there is nothing to read it with.

        """
        cog: Optional[commands.Cog] = self.bot.get_cog("Preferences")
        if not isinstance(cog, Preferences):
            return Verbosity.DEFAULT

        # A bare ID is all the read needs, and it is all a session has: the owner may be uncached, or
        # have left the guild the post lives in, and neither should change how their session narrates.
        stored: str = await cog.value(user=discord.Object(id=user_id), key=VERBOSITY_KEY)
        try:
            return Verbosity(stored)
        except ValueError:
            LOGGER.warning("<%s.%s> | Unknown verbosity stored | User: %s | Value: %s", __class__.__name__, "verbosity", user_id, stored)
            return Verbosity.DEFAULT

    async def _run_turn(
        self,
        *,
        thread: discord.Thread,
        state: SessionState,
        session: LiveSession,
        prompt: str,
        level: Verbosity = Verbosity.DEFAULT,
        reference: Optional[discord.Message] = None,
    ) -> None:
        """Runs one turn onto a pinned status box, once :meth:`run_turn` holds the session's turn lock.

        `level` picks the display class rather than a branch in here: what changes between DEFAULT,
        CHATTER and SILENT is only what the context is willing to write down, and every one of them
        is narrated, closed out and cleaned up identically.
        """
        if await self.blocked_by_usage(thread=thread, session=session, reference=reference):
            return

        activity: discord.Message = await self.send_to_thread(
            thread=thread,
            content=f"{self.emoji_table.kuma_tea} Working on it...",
            reference=reference,
            suppress_embeds=True,
            # The turn's one acknowledgement, and the only thing before the answer allowed to ping.
            mention=reference is not None,
        )

        context: TurnContext = VERBOSITY_CONTEXTS.get(level, TurnContext)(
            cog=self,
            thread=thread,
            state=state,
            session=session,
            status=self.animate(activity, label="Thinking", header=f"{self.emoji_table.kuma_tea} Claude Code", status_last=True),
            task=asyncio.current_task(),  # type: ignore[arg-type]
        )
        self._turns[thread.id] = context

        try:
            async with context.status:
                await session.send_prompt(prompt)
                result: TurnResult = await context.wait()

                if result.error is not None:
                    await context.status.stop(final=result.error)
                    # Whatever the display was holding, before the failure is explained: a turn that
                    # got most of the way there and then broke has usually already said the useful part.
                    await context.flush()
                    # A failure ends the wait the same way an answer does, and is the more important
                    # of the two to hear about. An interrupt below does not: whoever ran `.stop` is
                    # already looking at the thread.
                    await self.notify_owner(thread=thread, state=state)
                    return
                if result.interrupted:
                    await context.status.stop(final=context.interrupted_note())
                    await context.flush()
                    return

                # Collapse to a single line. The detail was useful while it ran, but on scrollback it
                # is just noise between the question and the answer.
                summary: list[str] = [f"{result.tool_calls} tool(s)"] if result.tool_calls else []
                summary.append(f"{result.duration:.0f}s")
                if result.cost_usd is not None:
                    summary.append(f"${result.cost_usd:.4f}")
                # The box collapses to the summary as it always did. Unlike the inline transcript
                # below, this display is not a record of anything — so leaving it saying the same
                # thing the footer does costs nothing, and emptying it would leave a blank message.
                await context.status.stop(final=f"-# {self.emoji_table.kuma_tea} {' · '.join(summary)}")

            # Before the files, so the answer arrives ahead of what it produced. A no-op on every
            # level but SILENT, which is the one that has been holding the answer back all along.
            await context.flush()
            await self.post_files(thread=thread, state=state, context=context, result=result, mention_owner=True)
            # After the answer, never before: the check is free but it is still a round trip, and it
            # has nothing to say that should delay the thing the user is waiting for.
            with contextlib.suppress(Exception):
                await self.check_context(thread=thread, session=session)

        except asyncio.CancelledError:
            await context.status.stop(final=context.interrupted_note())
            await context.flush()
            raise
        finally:
            if self._turns.get(thread.id) is context:
                del self._turns[thread.id]
            # Deregistered first: `set_effort` is an in-band user message and would otherwise be
            # narrated by the turn that was just told to stop listening.
            with contextlib.suppress(Exception):
                await self.drain_pending_effort(thread_id=thread.id)

    async def _run_turn_inline(
        self,
        *,
        thread: discord.Thread,
        state: SessionState,
        session: LiveSession,
        prompt: str,
        level: Verbosity = Verbosity.VERBOSE,  # noqa: ARG002 - see below.
        reference: Optional[discord.Message] = None,
    ) -> None:
        """Runs one turn, narrated as a rolling CLI-style transcript.

        Differs from :meth:`_run_turn` in the display and nothing else. There is no single `activity`
        message here: the tail posts its own, and posts another every time answer text has to go
        above it, so tools and prose end up interleaved in the order they actually happened rather
        than sorted into a box and a pile beneath it.

        `level` is accepted and ignored. Only :attr:`Verbosity.VERBOSE` reaches this runner at all, so
        there is nothing here for it to choose between; it is taken so :meth:`run_turn` can call
        either runner without knowing which one it picked.
        """
        if await self.blocked_by_usage(thread=thread, session=session, reference=reference):
            return

        # Only the opening message replies to the prompt. A thread where every message replies to the
        # same one is unreadable, and the reply is there to tie the turn to its question — which the
        # first message already does. Held in a closure so the tail stays ignorant of all this.
        pending_reference: Optional[discord.Message] = reference

        async def send(content: str) -> discord.Message:
            nonlocal pending_reference
            message: discord.Message = await self.send_to_thread(
                thread=thread,
                content=content,
                reference=pending_reference,
                suppress_embeds=True,
                # Rides on the reference for the same reason: the opening message is the turn's
                # acknowledgement, and every one after it is the same answer still arriving.
                mention=pending_reference is not None,
            )
            pending_reference = None
            return message

        status: KumaRollingAnimation = KumaRollingAnimation(
            send=send,
            label="Thinking",
            header=f"{self.emoji_table.kuma_tea} Claude Code",
        )
        context: InlineTurnContext = InlineTurnContext(
            cog=self,
            thread=thread,
            state=state,
            session=session,
            status=status,
            task=asyncio.current_task(),  # type: ignore[arg-type]
        )
        self._turns[thread.id] = context

        try:
            async with status:
                # Opens the first tail before the prompt goes out, so the spinner is up while the CLI
                # is still deciding what to do rather than appearing with the first tool call.
                await context.render_inline()
                await session.send_prompt(prompt)
                result: TurnResult = await context.wait()

                if result.error is not None:
                    # Kept above the error, unlike :meth:`_run_turn`, which can replace its box because
                    # the box was never the record of anything. Here it is: what ran before a failure
                    # is most of what anyone reading the failure wants to know.
                    await status.stop(final=context.final_note(footer=result.error))
                    # As in :meth:`_run_turn`: a failure is the end of the wait too.
                    await self.notify_owner(thread=thread, state=state)
                    return
                if result.interrupted:
                    await status.stop(final=context.interrupted_note())
                    return

                summary: list[str] = [f"{result.tool_calls} tool(s)"] if result.tool_calls else []
                summary.append(f"{result.duration:.0f}s")
                if result.cost_usd is not None:
                    summary.append(f"${result.cost_usd:.4f}")
                # Kept rather than collapsed, which is where this parts company with :meth:`_run_turn`.
                # That one could collapse its box because the answer stood on its own below it; here
                # the transcript is interleaved *with* the answer and is the only in-order account of
                # how it was arrived at. Only the last tail is still live to write to.
                #
                # Sealed with no footer under it: the summary goes on the message below instead, where
                # the mention beside it can actually ring. The fallback covers the tail having nothing
                # on it — everything already sealed onto earlier messages — since an empty edit is
                # not a thing Discord accepts and a live spinner is what would be left behind.
                stats: str = " · ".join(summary)
                note: str = context.final_note()
                await status.stop(final=note or f"-# {self.emoji_table.kuma_tea} {stats}")

            # Dropped from the footer in the fallback case above, where the tail is already showing it.
            await self.post_files(
                thread=thread, state=state, context=context, result=result, stats=stats if note else "", mention_owner=True
            )
            with contextlib.suppress(Exception):
                await self.check_context(thread=thread, session=session)

        except asyncio.CancelledError:
            await status.stop(final=context.interrupted_note())
            raise
        finally:
            if self._turns.get(thread.id) is context:
                del self._turns[thread.id]
            with contextlib.suppress(Exception):
                await self.drain_pending_effort(thread_id=thread.id)

    async def blocked_by_usage(
        self,
        *,
        thread: discord.Thread,
        session: LiveSession,
        reference: Optional[discord.Message] = None,
    ) -> bool:
        """Refuses a turn the shared account has no budget left for, and warns when it is close.

        Restores something the one-shot cog had and this one had lost: it recognised a spent limit and
        declined rather than launching a run that could only fail. That version had to scrape the CLI's
        error text *after* the failure; this reads it structurally, beforehand, for no tokens.

        The reading is cached, so most turns pay nothing at all for this. A cached reading claiming
        exhaustion is confirmed with a fresh one before anything is actually refused — the windows
        reset on a clock, and refusing a turn on a two minute old snapshot of a window that has since
        rolled over would be worse than not checking.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread, to answer in.
        session: :class:`LiveSession`
            The session whose account to check.
        reference: :class:`Optional[discord.Message]`, optional
            The message being answered, by default `None`.

        Returns
        -------
        :class:`bool`
            Whether the turn should be abandoned.

        """
        usage: Optional[UsageSnapshot] = await session.usage()
        if usage is None:
            # Never successfully read. Not knowing is not a reason to refuse; the turn is allowed and
            # fails on its own if the account really is spent.
            return False

        if usage.exhausted is not None:
            # Confirmed against a fresh reading, never a cached one.
            usage = await session.usage(refresh=True) or usage
            spent: Optional[RateLimit] = usage.exhausted
            if spent is not None:
                resets: str = f" It resets <t:{int(spent.resets_at.timestamp())}:R>." if spent.resets_at is not None else ""
                LOGGER.info(
                    "<%s.%s> | Turn refused, budget spent | Thread: %s | Window: %s | Percent: %s",
                    __class__.__name__,
                    "blocked_by_usage",
                    thread.id,
                    spent.label,
                    spent.percent,
                )
                await self.send_to_thread(
                    thread=thread,
                    content=f"{self.emoji_table.kuma_sad} The shared account has used its **{spent.label}** budget "
                    f"({spent.percent}%), so this would only fail.{resets} Nothing was sent.",
                    reference=reference,
                )
                return True

        strained: Optional[RateLimit] = usage.strained
        if strained is None:
            # Re-armed once it recovers, so the next squeeze is mentioned too.
            self._usage_warned.discard(thread.id)
        elif thread.id not in self._usage_warned:
            self._usage_warned.add(thread.id)
            resets = f", resets <t:{int(strained.resets_at.timestamp())}:R>" if strained.resets_at is not None else ""
            await self.send_to_thread(
                thread=thread,
                content=f"-# {self.emoji_table.kuma_hmm} The shared account is {strained.percent}% through its "
                f"**{strained.label}** budget{resets}. Still running this one.",
                reference=reference,
            )
        return False

    async def notify_owner(self, *, thread: discord.Thread, state: SessionState) -> None:
        """Tells a session's owner their turn is over, when there is no footer to say it on.

        Only the failure paths land here. A turn that ran to the end says the same thing on its
        closing footer instead — see :meth:`post_files` — because one line carrying the stats *and*
        the ping beats a line of stats with a line under it saying the turn those stats came from is
        over.
        """
        with contextlib.suppress(discord.HTTPException):
            await self.send_to_thread(
                thread=thread,
                content=TURN_MENTION_LINE.format(mention=f"<@{state.user_id}>"),
                silent=False,
                mention=True,
            )

    async def post_files(
        self,
        *,
        thread: discord.Thread,
        state: SessionState,
        context: TurnContext,
        result: TurnResult,
        stats: str = "",
        mention_owner: bool = False,
    ) -> None:
        """Closes out a finished turn: one subtext footer, with any files it generated attached.

        This is the turn's last line and the only one that rings. `stats` is the tool/duration/cost
        summary the transcript used to end on, moved down here: sealing the transcript is an *edit*,
        and a mention added by an edit notifies nobody. Said on a message instead, the same line does
        both jobs — and since it is subtext either way, moving it changes nothing about how it reads.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread.
        state: :class:`SessionState`
            The session, for the settings on the footer and the owner to ping.
        context: :class:`TurnContext`
            The finished turn, for the workspace snapshot it took before running.
        result: :class:`TurnResult`
            What the turn cost, when `stats` does not already say.
        stats: :class:`str`, optional
            The turn summary, pre-joined, by default `""`.
        mention_owner: :class:`bool`, optional
            Whether to ping the session's owner, by default `False`.

        """
        files, skipped = await asyncio.to_thread(
            _build_reply_files, paths=await asyncio.to_thread(_new_files, directory=state.workspace, before=context.before)
        )

        segments: list[str] = []
        if stats:
            segments.append(stats)
        elif result.cost_usd is not None:
            # Only when `stats` is not carrying it already; the caller that passes one has the cost in it.
            segments.append(f"${result.cost_usd:.4f}")
        segments.append(f"`{state.model}` · `{state.mode}` · `{state.effort}`")
        if files or skipped:
            segments.append(f"{len(files) + len(skipped)} file(s) generated")
        if skipped:
            segments.append(f"{len(skipped)} too large to attach, use `.get`")
        # Last, so the eye lands on it after the line it belongs to rather than before.
        if mention_owner:
            segments.append(f"<@{state.user_id}>")

        # Still nothing to say and nobody to say it to: the answer blocks were already posted as they
        # arrived, so a bare settings line under a turn that produced nothing is noise.
        if not files and not skipped and not mention_owner:
            return

        footer: str = f"-# {self.emoji_table.kuma_tea} {' · '.join(segments)}"
        content: str = f"{REPLY_SEPARATOR}\n{footer}" if files or skipped else footer
        await self.send_to_thread(thread=thread, content=content, files=files, silent=not mention_owner, mention=mention_owner)

    @staticmethod
    async def send_to_thread(
        *,
        thread: discord.Thread,
        content: Optional[str] = None,
        files: Optional[list[discord.File]] = None,
        reference: Optional[discord.Message] = None,
        view: Optional[Union[discord.ui.View, discord.ui.LayoutView]] = None,
        suppress_embeds: bool = False,
        silent: bool = True,
        mention: bool = False,
    ) -> discord.Message:
        """Sends into a session thread, omitting the optional arguments rather than passing `None`.

        :meth:`discord.abc.Messageable.send` is overloaded and none of its overloads accept `None`
        for these arguments; only the implementation signature does, which a type checker ignores.

        Silent by default, and everything a session narrates goes out through here: the answer text,
        the notices, and every frame the status animation posts. A turn is a *stream* — a single
        question can produce a dozen messages — so notifying on each one trains the reader to ignore
        the session entirely, which is exactly the reader you cannot afford when a prompt finally
        needs answering. Prompts are sent past this helper, and are the only thing that rings.

        `mention` is what suppresses the rest. `silent` only stops the push; a *reply* still pings the
        person replied to, and a turn's opening sequence can reply several times over — the queue
        notice, a rejected attachment, a budget warning, the acknowledgement — each one its own red
        badge for a question asked once. Every mention a session can raise is now off unless asked
        for, so the ping goes where it was meant to: once on the acknowledgement, and again on
        :meth:`notify_owner` when the answer is ready.

        .. warning::
            :attr:`discord.utils.MISSING` is *not* a substitute for `reference`. It is typed as `Any`
            so it satisfies the checker, but the runtime guard is `is not None`; a `MISSING` sentinel
            passes that check and then raises :class:`AttributeError`.
        """
        arguments: dict[str, Any] = {
            "silent": silent,
            "allowed_mentions": discord.AllowedMentions(users=mention, replied_user=mention, everyone=False, roles=False),
        }
        if content is not None:
            arguments["content"] = content
        if files:
            arguments["files"] = files
        if reference is not None:
            arguments["reference"] = reference
        if view is not None:
            arguments["view"] = view
        if suppress_embeds:
            arguments["suppress_embeds"] = True
        return await thread.send(**arguments)

    async def reply_to(self, *, message: discord.Message, **arguments: Any) -> discord.Message:
        """Replies to a message in the thread, quietly.

        Loosely typed on purpose, unlike :meth:`send_to_thread`: this passes straight through to
        :meth:`discord.Message.reply` and exists only to put `silent` on the ~30 command replies
        without restating each one's arguments. A caller that wants the notification passes
        `silent=False`, which wins over the default below.

        A `.command` invocation is answered unattached instead. It is deleted moments later, and a
        reply to a deleted message is left showing Discord's "Original message was deleted" line
        above every answer — noisier than the command it removed.

        Nothing here pings. These are answers to something the reader just typed and is still looking
        at; see :meth:`send_to_thread` on why a reply badge is not free.
        """
        arguments.setdefault("silent", True)
        arguments.setdefault("allowed_mentions", discord.AllowedMentions.none())
        if message.id in self._expiring:
            return await message.channel.send(**arguments)
        return await message.reply(**arguments)

    def schedule_expiry(self, *, message: discord.Message) -> None:
        """Marks a `.command` message as on its way out and starts the clock on removing it.

        The mark is set here rather than in the task, which does not begin running until the handler
        it was started from next yields — by then the answer it is meant to influence has been sent.
        """
        self._expiring.add(message.id)
        task: asyncio.Task[None] = asyncio.create_task(self.expire_invocation(message=message))
        self._expiry_tasks.add(task)
        task.add_done_callback(self._expiry_tasks.discard)

    async def expire_invocation(self, *, message: discord.Message) -> None:
        """Deletes a `.command` message after :data:`DOT_COMMAND_LIFETIME`, quietly.

        Failure is nothing to report: without `manage_messages` — or in a post locked by the very
        command being cleaned up, as `.close` does — the message simply stays, which is what
        happened before any of this.
        """
        try:
            await asyncio.sleep(DOT_COMMAND_LIFETIME)
            with contextlib.suppress(discord.HTTPException):
                await message.delete()
        finally:
            self._expiring.discard(message.id)

    # endregion

    # region --- Session lookup

    def is_session_thread(self, channel: Any) -> bool:
        """Returns whether a channel is a post in one of our session forums."""
        return (
            isinstance(channel, discord.Thread)
            and isinstance(channel.parent, discord.ForumChannel)
            and channel.parent.category is not None
            and channel.parent.category.name == CATEGORY_NAME
        )

    def is_bot_post(self, thread: discord.Thread) -> bool:
        """Returns whether we opened a post, rather than a user posting into the forum themselves."""
        return thread.owner_id == self.bot.user.id if self.bot.user is not None else False

    def session_forums(self) -> Iterator[discord.ForumChannel]:
        """Yields every session forum the bot can see."""
        for guild in self.bot.guilds:
            for channel in guild.channels:
                if isinstance(channel, discord.ForumChannel) and channel.category is not None and channel.category.name == CATEGORY_NAME:
                    yield channel

    async def fetch_panel(self, *, thread: discord.Thread) -> PanelLookup:
        """Reads a thread's opening post, telling a missing post apart from an unreadable one."""
        try:
            message: discord.Message = await thread.fetch_message(thread.id)
        except discord.NotFound:
            return PanelLookup(message=None, gone=True)
        except discord.HTTPException:
            return PanelLookup(message=None, gone=False)
        return PanelLookup(message=message, gone=False)

    async def get_state(self, *, thread: discord.Thread) -> Optional[SessionState]:
        """Returns a session's state, from cache or by re-reading its opening post."""
        cached: Optional[SessionState] = self._sessions.get(thread.id)
        if cached is not None:
            return cached

        lookup: PanelLookup = await self.fetch_panel(thread=thread)
        if lookup.message is None:
            return None

        state: Optional[SessionState] = parse_state(message=lookup.message, thread_id=thread.id)
        if state is not None:
            self._sessions[thread.id] = state
        return state

    async def update_panel(self, *, thread: discord.Thread, state: SessionState, status: Optional[SessionStatus] = None) -> bool:
        """Re-renders a session's opening post to match its current state.

        The sidecar index is written first, on purpose. Every path that changes a session ends here,
        so this is the one place the mirror stays in step with the state line — and a panel that
        cannot be fetched is precisely the case the sidecar exists for, so it must not be skipped by
        the early return below.
        """
        await asyncio.to_thread(write_session_index, state=state)

        lookup: PanelLookup = await self.fetch_panel(thread=thread)
        if lookup.message is None:
            return False

        self._sessions[thread.id] = state
        panel: SessionPanel = SessionPanel(
            state=state,
            status=status if status is not None else SessionStatus.of(thread),
            live=thread.id in self._live,
            transcript=self.attached_transcript(panel=lookup.message),
        )
        try:
            await lookup.message.edit(view=panel)
        except discord.HTTPException:
            LOGGER.exception("<%s.%s> | Could not update a panel | Thread: %s", __class__.__name__, "update_panel", thread.id)
            return False
        return True

    @staticmethod
    def attached_transcript(*, panel: discord.Message) -> Optional[str]:
        """Returns the filename of a transcript already attached to a panel, if there is one."""
        return next((attachment.filename for attachment in panel.attachments if attachment.filename.startswith("transcript-")), None)

    # endregion

    # region --- Listeners

    @commands.Cog.listener(name="on_message")
    async def session_message_listener(self, message: discord.Message) -> None:
        """Treats a message in a session thread as the next turn of that session, or a dot command."""
        if message.author.bot or not self.is_session_thread(message.channel):
            return

        # `Bot.on_message` and this listener both fire for every message, independently. Without this
        # guard a message that starts with a prefix is handled twice — `@Kuma Kuma Bear restart`
        # invokes the command *and* arrives here as a prompt. Deferring to the same predicate the bot
        # uses to gate `process_commands` is what keeps exactly one of us handling it: a mention wins
        # for the command, anything else is prose and belongs to the session. Purely local --
        # `get_context` parses the prefix and looks the name up, without a round trip.
        if self.bot.commands_enabled_for(message):
            context: commands.Context[Kuma_Kuma] = await self.bot.get_context(message)
            if context.valid:
                return

        # A ping is not a turn. Only a message left empty once the mentions come out is dropped, so
        # `@someone look at this` still asks the question it means to.
        if not MENTION.sub("", message.content).strip() and not message.attachments:
            return

        thread: discord.Thread = message.channel  # type: ignore[assignment]
        status: SessionStatus = SessionStatus.of(thread)
        if status.dormant:
            await self.reply_to(
                message=message,
                content=f"This session is {status.value}; use **Restore Session** on the opening post, or start a "
                f"fresh one with `/claude ask`. {self.emoji_table.kuma_shrug}",
            )
            return

        state: Optional[SessionState] = await self.get_state(thread=thread)
        if state is None or message.author.id != state.user_id:
            return

        content: str = message.content.strip()
        if content.startswith(".") and len(content) > 1:
            await self.handle_dot_command(message=message, thread=thread, state=state, raw=content[1:])
            return

        await self.handle_prompt(message=message, thread=thread, state=state)

    @commands.Cog.listener(name="on_thread_create")
    async def session_create_listener(self, thread: discord.Thread) -> None:
        """Removes a post made in a session forum by anyone other than us, as soon as it appears.

        This fires for our own `forum.create_thread()` in `/claude ask` too, which :meth:`is_bot_post`
        is what separates out.
        """
        if not self.is_session_thread(thread) or self.is_bot_post(thread):
            return
        await self.discard_foreign_post(thread=thread)

    @commands.Cog.listener(name="on_raw_thread_delete")
    async def session_delete_listener(self, payload: discord.RawThreadDeleteEvent) -> None:
        """Tears down a deleted session; its post was the only record of it."""
        state: Optional[SessionState] = self._sessions.pop(payload.thread_id, None)
        self._approved.pop(payload.thread_id, None)
        context: Optional[TurnContext] = self._turns.get(payload.thread_id)
        if context is not None:
            context.task.cancel()
        await self.park(thread_id=payload.thread_id)

        if state is not None:
            await asyncio.to_thread(shutil.rmtree, state.workspace, ignore_errors=True)

    async def handle_prompt(self, *, message: discord.Message, thread: discord.Thread, state: SessionState) -> None:
        """Runs an ordinary message in a session thread as the next turn of that session."""
        if state.ignoring:
            # Muted at `.ignore`. Silent on purpose — a session told to ignore the thread that then
            # answers every message with "I'm ignoring you" has ignored nothing.
            return

        saved, rejected = await self.save_attachments(message=message, state=state)
        if rejected:
            await self.reply_to(
                message=message,
                content=f"Skipped {', '.join(f'`{name}`' for name in rejected)}; over the "
                f"{human_size(MAX_ATTACHMENT_SIZE)} limit. {self.emoji_table.kuma_pout}",
            )

        # `clean_content` rather than `content`, so a mention arrives as `@Kat` instead of a raw
        # snowflake the run cannot make sense of. It leaves markdown and URLs alone.
        prompt: str = message.clean_content.strip()
        if not prompt and not saved:
            return

        # A post opened without a prompt has nothing to name itself after, so the first real message
        # names it. Guarded on the placeholder so a `.rename` is never quietly overwritten.
        if prompt and thread.name.startswith(PLACEHOLDER_TITLE):
            with contextlib.suppress(discord.HTTPException):
                await thread.edit(name=thread_title(prompt), reason="Claude session named by its first message.")

        if saved:
            prompt += "\n\n[Attached: " + ", ".join(f"`{path.relative_to(state.cwd).as_posix()}`" for path in saved) + "]"

        note, problems = await self.resolve_prompt_links(prompt=prompt, thread=thread, state=state)
        if problems:
            await self.reply_to(message=message, content="\n".join(f"-# {line}" for line in problems))
        prompt += note

        await self.run_turn(thread=thread, state=state, prompt=prompt, reference=message)

    @staticmethod
    def source_note(*, message: discord.Message) -> str:
        """Returns the provenance header written above a message pulled in from a link.

        Claude Code only ever sees text, so who said it, where and when all have to be spelled out.
        The jump link is kept verbatim so it can be handed back in an answer.
        """
        channel: str = getattr(message.channel, "mention", "an unknown channel")
        return (
            f"[Quoted Discord message by {message.author.display_name} (`{message.author}`) in {channel}, "
            f"posted {message.created_at.isoformat(sep=' ', timespec='minutes')} UTC · {message.jump_url}]"
        )

    async def resolve_prompt_links(self, *, prompt: str, thread: discord.Thread, state: SessionState) -> tuple[str, list[str]]:
        """Saves every Discord message linked in a prompt into the workspace, and describes where.

        A pasted jump URL is inert text to the CLI, which cannot fetch Discord. Resolving it here and
        handing over a path instead means a link dropped into an ordinary sentence just works.

        Parameters
        ----------
        prompt: :class:`str`
            The prompt as typed.
        thread: :class:`discord.Thread`
            The session thread, which supplies the guild and bounds the lookup to it.
        state: :class:`SessionState`
            The session whose workspace the messages are saved into.

        Returns
        -------
        :class:`tuple[str, list[str]]`
            The note to append to the prompt, and any user-facing reasons a link was skipped.

        """
        # Deduplicated by message ID, so the same link pasted twice is one fetch and one file.
        wanted: dict[int, re.Match[str]] = {}
        for match in MESSAGE_LINK.finditer(prompt):
            wanted.setdefault(int(match.group("message")), match)

        if not wanted:
            return "", []

        saved: list[Path] = []
        problems: list[str] = []
        for message_id, match in list(wanted.items())[:MAX_PROMPT_LINKS]:
            target, error = await self.resolve_link(match=match, origin=thread)
            if target is None:
                problems.append(f"{match.group(0)} · {error}")
                continue

            destination: Path = state.workspace.joinpath(f"linked_message_{message_id}.md")
            body: str = f"{self.source_note(message=target)}\n\n{target.content}"
            await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(destination.write_text, body, encoding="utf-8")
            saved.append(destination)

        if len(wanted) > MAX_PROMPT_LINKS:
            problems.append(f"Only the first {MAX_PROMPT_LINKS} links were fetched; {len(wanted)} were in the message.")

        if not saved:
            return "", problems

        listed: str = ", ".join(f"`{path.relative_to(state.cwd).as_posix()}`" for path in saved)
        return f"\n\n[The user linked {len(saved)} Discord message(s), saved at {listed}]", problems

    async def resolve_link(self, *, match: re.Match[str], origin: discord.Thread) -> tuple[Optional[discord.Message], Optional[str]]:
        """Fetches the message a jump URL points at, or explains why it cannot be reached.

        A jump URL names its channel, so this is one fetch and never a search.
        """
        # Never `None`: a session thread is a forum post, and `is_session_thread` has already proved
        # this one lives under our category.
        guild: discord.Guild = origin.guild
        raw_guild: str = match.group("guild")
        if raw_guild == "@me":
            return None, f"that is a DM link, which I cannot read. {self.emoji_table.kuma_shrug}"
        # A cross-guild link would fail the fetch anyway unless the bot happens to be in that guild, so
        # it is refused up front with a reason rather than an opaque 404 further down.
        if int(raw_guild) != guild.id:
            return None, f"that link points at a different server. {self.emoji_table.kuma_shrug}"

        channel: Any = guild.get_channel_or_thread(int(match.group("channel")))
        if channel is None:
            return None, f"I cannot see that channel. {self.emoji_table.kuma_shrug}"

        me: Optional[discord.Member] = guild.me
        if me is not None and not channel.permissions_for(me).read_message_history:
            return None, f"I cannot read history in {channel.mention}. {self.emoji_table.kuma_shrug}"

        try:
            return await channel.fetch_message(int(match.group("message"))), None
        except discord.NotFound:
            return None, f"that message is gone. {self.emoji_table.kuma_shrug}"
        except discord.HTTPException:
            return None, f"Discord would not give me that message. {self.emoji_table.kuma_sad}"

    async def save_attachments(self, *, message: discord.Message, state: SessionState) -> tuple[list[Path], list[str]]:
        """Saves a message's attachments into the session workspace so the CLI can read them."""
        if not message.attachments:
            return [], []

        directory: Path = state.attachments
        await asyncio.to_thread(prepare_workspace, directory=directory)

        saved: list[Path] = []
        rejected: list[str] = []
        for attachment in message.attachments:
            if attachment.size > MAX_ATTACHMENT_SIZE:
                rejected.append(attachment.filename)
                continue
            # `Path(...).name` strips any directory the filename tries to carry; Discord does not
            # promise a bare name and a `../` in one would land the write outside the workspace.
            target: Path = directory.joinpath(Path(attachment.filename).name)
            with contextlib.suppress(discord.HTTPException):
                await attachment.save(fp=target)
                saved.append(target)
        return saved, rejected

    # endregion

    # region --- Dot commands

    async def handle_dot_command(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, raw: str) -> None:
        """Resolves and runs a `.command`, or forwards a CLI slash command into the live session."""
        name, _, argument = raw.partition(" ")
        command, candidates = resolve_dot_command(name)

        if command is None:
            if candidates:
                self.schedule_expiry(message=message)
                await self.reply_to(
                    message=message,
                    content=f"`.{name}` could be {' or '.join(f'**`.{entry}`**' for entry in candidates)}. {self.emoji_table.kuma_hmm}",
                )
                return
            # Not one of ours. A live session takes the CLI's own slash commands as prompt text, which
            # the one-shot cog could never do — `claude -p` silently ignored them.
            await self.run_turn(thread=thread, state=state, prompt=f"/{raw}", reference=message)
            return

        if command.handler is None:
            return

        # Before the handler runs, so its answer already knows not to attach itself. A forwarded CLI
        # command is left alone above: it becomes a turn, and its message is that turn's reference.
        if command.transient:
            self.schedule_expiry(message=message)
        await getattr(self, command.handler)(message=message, thread=thread, state=state, argument=argument.strip())

    def help_text(self) -> str:
        """Returns the `.help` listing, grouped under headings.

        Aliases are left off every line. They are worth having and not worth listing: the footer says
        that any unambiguous stub resolves, which covers all of them at once and teaches the rule
        rather than nineteen exceptions to it.
        """
        lines: list[str] = [f"## {self.emoji_table.kuma_tea} In-thread commands"]
        for group in DOT_GROUPS:
            members: list[DotCommand] = [command for command in DOT_COMMANDS if command.group == group]
            if not members:
                continue
            lines.append(f"### {group}")
            lines.extend(f"> `.{command.name}{' ' + command.usage if command.usage else ''}` — {command.summary}" for command in members)
        lines.append("")
        lines.append("-# Any unambiguous stub works — `.ren` reaches `.rename`, `.ig` reaches `.ignore`.")
        lines.append("-# Any other `.name` is forwarded to Claude Code as its own `/name` command.")
        return "\n".join(lines)

    async def dot_help(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        await self.reply_to(message=message, content=self.help_text()[:MESSAGE_CHUNK_SIZE])

    @staticmethod
    def _picker(*, heading: str, options: dict[str, str], current: str) -> str:
        """Renders the list a setting command shows when it is given nothing it recognises.

        Every one of these used to answer "pick one of a, b, c", which says what is allowed and not
        what any of it means or which one is already in force — the two things somebody typing the
        command bare is actually asking.
        """
        lines: list[str] = [heading]
        lines.extend(
            f"> **`{name}`** — {description}{' · __current__' if name == current else ''}" for name, description in options.items()
        )
        return "\n".join(lines)

    async def dot_model(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:
        """Switches model on the live process, with no restart and no lost context."""
        if argument not in MODELS:
            await self.reply_to(
                message=message,
                content=self._picker(
                    heading=f"### {self.emoji_table.kuma_peak} Models",
                    options={name: f"`{value}`" for name, value in MODELS.items()},
                    current=next((name for name, value in MODELS.items() if value == state.model), ""),
                ),
            )
            return

        await self.apply_model(thread=thread, state=state, name=argument)
        await self.reply_to(message=message, content=f"Model is now **`{state.model}`**. {self.emoji_table.kuma_happy}")

    async def apply_model(self, *, thread: discord.Thread, state: SessionState, name: str) -> None:
        """Applies a model to the session, live when there is a process and at spawn when there isn't."""
        state.model = MODELS.get(name, DEFAULT_MODEL)
        session: Optional[LiveSession] = self._live.get(thread.id)
        if session is not None and session.alive:
            await session.set_model(model=state.model)
        await self.update_panel(thread=thread, state=state)

    async def dot_mode(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:
        """Switches permission mode on the live process, with no restart and no lost context."""
        if argument not in MODES:
            await self.reply_to(
                message=message,
                content=self._picker(
                    heading=f"### {self.emoji_table.kuma_peak} Permission modes",
                    options={name: mode.description for name, mode in MODES.items()},
                    current=next((name for name, mode in MODES.items() if mode.value == state.mode), ""),
                ),
            )
            return
        await self.apply_mode(thread=thread, state=state, name=argument)
        await self.reply_to(message=message, content=f"Mode is now **`{state.mode}`**. {self.emoji_table.kuma_happy}")

    async def apply_mode(self, *, thread: discord.Thread, state: SessionState, name: str) -> None:
        """Applies a mode to the session, live when there is a process and at spawn when there isn't."""
        state.mode = _mode_value(name)
        session: Optional[LiveSession] = self._live.get(thread.id)
        if session is not None and session.alive:
            await session.set_mode(mode=state.mode)
        await self.update_panel(thread=thread, state=state)

    async def dot_plan(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        await self.dot_mode(message=message, thread=thread, state=state, argument="plan")

    async def dot_edits(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        await self.dot_mode(message=message, thread=thread, state=state, argument="edits")

    async def dot_effort(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:
        """Switches effort by handing the CLI its own `/effort` command."""
        if argument not in EFFORTS:
            await self.reply_to(
                message=message,
                content=self._picker(
                    heading=f"### {self.emoji_table.kuma_peak} Effort levels",
                    options={name: EFFORT_DESCRIPTIONS[name] for name in EFFORTS},
                    current=state.effort,
                ),
            )
            return

        deferred: bool = await self.apply_effort(thread=thread, state=state, level=argument)
        await self.reply_to(
            message=message,
            content=f"Effort will be **`{argument}`** once this turn finishes. {self.emoji_table.kuma_tea}\n"
            f"-# The CLI takes this in band, so it has to wait for the turn to end."
            if deferred
            else f"Effort is now **`{argument}`**. {self.emoji_table.kuma_happy}",
        )

    async def apply_effort(self, *, thread: discord.Thread, state: SessionState, level: str) -> bool:
        """Applies an effort level, deferring it when a turn is in flight.

        Unlike mode and model this is a slash command rather than a control request, so it travels
        *in band* as a user message and the CLI answers it with a `result` event. A turn in flight
        would take that as its own ending and stop narrating half way through, so the change waits
        for the session to go idle; :meth:`run_turn` drains the deferral on its way out.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread.
        state: :class:`SessionState`
            The session to change.
        level: :class:`str`
            The effort level to set.

        Returns
        -------
        :class:`bool`
            Whether the change was deferred rather than applied straight away.

        """
        state.effort = level
        session: Optional[LiveSession] = self._live.get(thread.id)

        if session is None or not session.alive:
            # Nothing running, so it is simply what the next spawn is built with.
            await self.update_panel(thread=thread, state=state)
            return False

        if thread.id in self._turns:
            self._pending_effort[thread.id] = level
            await self.update_panel(thread=thread, state=state)
            return True

        await session.set_effort(level=level)
        await self.update_panel(thread=thread, state=state)
        return False

    async def drain_pending_effort(self, *, thread_id: int) -> None:
        """Applies an effort change that arrived while a turn was running."""
        level: Optional[str] = self._pending_effort.pop(thread_id, None)
        if level is None:
            return
        session: Optional[LiveSession] = self._live.get(thread_id)
        if session is not None and session.alive:
            await session.set_effort(level=level)

    async def dot_clear(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Clears the conversation in this post, keeping the files and the personal memory.

        Delegates to the CLI's own `/clear`, which does exactly this and does it in place: verified
        against 2.1.220, a cleared session forgets the conversation, comes back under a **new**
        session ID, and leaves the old transcript on disk. :meth:`track_session_id` is what picks the
        new ID up; without it the panel would still name the conversation that was just discarded.
        """
        await self.clear_session(thread=thread, state=state, reference=message)

    async def clear_session(
        self,
        *,
        thread: discord.Thread,
        state: SessionState,
        reference: Optional[discord.Message] = None,
    ) -> None:
        """Clears a session's conversation, live where possible and by re-keying where not.

        A running process is cleared in place, which is quicker and is what the CLI itself does. A
        parked one has no conversation loaded to clear, so a fresh ID is minted here instead and the
        next message simply starts on it.
        """
        # Standing approvals go with the conversation that earned them. They were granted against a
        # task the session no longer remembers, and inheriting them silently would mean a fresh
        # conversation starting out with permissions nobody in it ever gave.
        dropped: int = len(self._approved.pop(thread.id, set()))
        note: str = f"\n-# {dropped} standing tool approval(s) dropped with it." if dropped else ""

        session: Optional[LiveSession] = self._live.get(thread.id)
        if session is None or not session.alive:
            state.session_id = str(uuid.uuid4())
            self._sessions[thread.id] = state
            self._context_warned.discard(thread.id)
            await self.update_panel(thread=thread, state=state)
            await self.send_to_thread(
                thread=thread,
                content=f"Cleared. Your files and `CLAUDE.md` are untouched. {self.emoji_table.kuma_happy}{note}",
                reference=reference,
            )
            return

        # Through `run_turn` rather than straight down stdin: `/clear` is in band, so it needs the
        # same protection from a turn already in flight that every other prompt gets, and its `result`
        # has to be consumed by something.
        self._context_warned.discard(thread.id)
        previous: str = state.session_id
        await self.run_turn(thread=thread, state=state, prompt="/clear", reference=reference)

        # `/clear` is a local command in the CLI, so the turn it runs as answers nothing: the model is
        # handed an already-emptied context and has no reason to speak, leaving the tail closing on a
        # bare footer and no word about whether anything happened. Said here instead, to match the
        # parked branch above — but only once :meth:`track_session_id` has seen the new ID, which is
        # the CLI confirming the clear rather than us assuming it. A turn that errored or was stopped
        # never re-keys, and has already said so in its own words.
        if state.session_id != previous:
            await self.send_to_thread(
                thread=thread, content=f"Cleared. Your files and `CLAUDE.md` are untouched. {self.emoji_table.kuma_happy}{note}"
            )

    async def dot_approvals(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Lists the tools that will not prompt again this session, and revokes them on request.

        A standing approval is invisible by design — the whole point is that nothing appears — so
        without somewhere to read them back the only way to find out what you had granted would be to
        watch for what *stopped* asking.
        """
        approved: set[str] = self._approved.get(thread.id, set())
        if argument.lower() in {"clear", "revoke", "off", "none"}:
            self._approved.pop(thread.id, None)
            await self.reply_to(
                message=message,
                content=f"Revoked {len(approved)} standing approval(s); everything asks again. {self.emoji_table.kuma_happy}"
                if approved
                else f"There were none to revoke. {self.emoji_table.kuma_shrug}",
            )
            return

        if not approved:
            await self.reply_to(
                message=message,
                content=f"No standing approvals; every tool still asks. {self.emoji_table.kuma_shrug}\n"
                f"-# **Always allow** on an approval prompt adds one.",
            )
            return

        lines: list[str] = [f"### {self.emoji_table.kuma_peak} Approved this session"]
        lines.extend(f"> **`{tool}`** — runs without asking" for tool in sorted(approved))
        lines.append("")
        lines.append("-# `.approvals clear` revokes them · they also go on `.clear`, on close, and on a bot restart.")
        await self.reply_to(message=message, content="\n".join(lines)[:MESSAGE_CHUNK_SIZE])

    async def dot_ignore(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:
        """Toggles whether ordinary messages here are treated as prompts."""
        state.ignoring = argument.lower() not in {"off", "false", "no"} if argument else not state.ignoring
        self._sessions[thread.id] = state
        await self.reply_to(
            message=message,
            content=f"__Ignoring__ messages here. {self.emoji_table.kuma_shrug}\n"
            f"-# Nothing you type is run until `.ignore off`; the session itself is untouched."
            if state.ignoring
            else f"__Listening__ again. {self.emoji_table.kuma_happy}",
        )

    async def dot_status(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Shows the session's settings, its process, and what the CLI itself reports.

        The local half is what we believe; the live half is what the CLI says. Worth showing both,
        since the two *can* drift — the session ID is re-keyed by `/clear` and by forks, and a status
        line that only ever repeated our own state would be the last place that showed it.
        """
        session: Optional[LiveSession] = self._live.get(thread.id)
        live: bool = session is not None and session.alive
        running: bool = thread.id in self._turns
        lines: list[str] = [
            f"### {self.emoji_table.kuma_tea} Session status",
            f"> **Process** — {'live' if live else 'parked; your next message resumes it'}",
            f"> **Turn** — {'in progress' if running else 'idle'}",
            f"> **Opened** — <t:{int(state.started)}:R>",
            f"> **Model** — `{state.model}`",
            f"> **Mode** — `{state.mode}` · **Effort** — `{state.effort}`",
        ]
        if state.ignoring:
            lines.append(f"> **Ignoring** — messages here are not being run; `.ignore off` to resume {self.emoji_table.kuma_shrug}")
        approved: set[str] = self._approved.get(thread.id, set())
        if approved:
            lines.append(f"> **Approved** — {', '.join(f'`{tool}`' for tool in sorted(approved))} · `.approvals clear` to revoke")

        if session is not None and live:
            context: Optional[ContextUsage] = await session.context_usage()
            if context is not None:
                lines.append(f"> **Context** — {context.bar} {context.percentage:.0f}%")
            usage: Optional[UsageSnapshot] = await session.usage()
            if usage is not None:
                lines.extend(f"> **{limit.label}** — {limit.percent}% used" for limit in usage.limits)

        # The machine detail, demoted. It is the half of this nobody reads until something has gone
        # wrong, and it was crowding out the half that gets read every time.
        lines.append("")
        lines.append(f"-# Session `{state.session_id}`")

        # Superseded IDs are the only pointer back to a transcript this thread has already left
        # behind, and they exist nowhere else — the panel only ever shows the current one.
        index: Optional[SessionIndex] = await asyncio.to_thread(read_session_index, workspace=state.workspace)
        if index is not None and index["lineage"]:
            lines.append(f"-# Previously {', '.join(f'`{entry}`' for entry in index['lineage'][:3])}")
        lines.append(f"-# Working directory `{state.cwd}` · files in `{state.output_directory}`")

        await self.reply_to(message=message, content="\n".join(lines)[:MESSAGE_CHUNK_SIZE])

    async def dot_files(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Lists what the session has generated."""
        entries: list[Path] = await asyncio.to_thread(lambda: sorted(path for path in state.workspace.rglob("*") if path.is_file()))
        if not entries:
            await self.reply_to(
                message=message,
                content=f"Nothing generated yet. {self.emoji_table.kuma_shrug}\n"
                f"-# Anything Claude writes to `{state.output_directory}` shows up here.",
            )
            return

        total: int = sum(path.stat().st_size for path in entries)
        lines: list[str] = [f"### {self.emoji_table.kuma_peak} Files"]
        lines.extend(f"> `{path.relative_to(state.workspace).as_posix()}` — {human_size(path.stat().st_size)}" for path in entries[:40])
        if len(entries) > 40:
            lines.append(f"> -# …and {len(entries) - 40} more.")
        lines.append("")
        lines.append(f"-# {len(entries)} file(s), {human_size(total)} in `{state.output_directory}` · `.get <name>` to fetch one.")
        await self.reply_to(message=message, content="\n".join(lines)[:MESSAGE_CHUNK_SIZE])

    async def dot_get(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Uploads one file out of the session's workspace."""
        if not argument:
            await self.reply_to(
                message=message,
                content=f"Give me a filename. {self.emoji_table.kuma_hmm}\n-# `.files` lists what there is to ask for.",
            )
            return

        target: Path = state.workspace.joinpath(argument)
        # Resolved and re-checked because the name is user supplied and `..` would otherwise walk out
        # of the workspace and upload something that was never the session's to hand over.
        resolved: Path = await asyncio.to_thread(target.resolve)
        if not resolved.is_relative_to(await asyncio.to_thread(state.workspace.resolve)) or not resolved.is_file():
            await self.reply_to(message=message, content=f"I can't find `{argument}` in this session's files. {self.emoji_table.kuma_sad}")
            return
        if resolved.stat().st_size > MAX_REPLY_FILE_SIZE:
            await self.reply_to(
                message=message,
                content=f"`{argument}` is over the {human_size(MAX_REPLY_FILE_SIZE)} upload limit. {self.emoji_table.kuma_pout}",
            )
            return
        await self.reply_to(message=message, file=discord.File(fp=resolved, filename=resolved.name))

    async def dot_memory(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Shows the user's personal `CLAUDE.md`, which is loaded at the start of every session."""
        memory: Path = state.cwd.joinpath(USER_MEMORY_NAME)
        if not await asyncio.to_thread(memory.is_file):
            await self.reply_to(
                message=message,
                content=f"You have no `CLAUDE.md` yet. {self.emoji_table.kuma_shrug}\n"
                f"-# Ask me to write one and it loads at the start of every session you open.",
            )
            return
        text: str = await asyncio.to_thread(memory.read_text, encoding="utf-8")
        await self.reply_to(
            message=message,
            content=f"### {self.emoji_table.kuma_heart} Your `CLAUDE.md`\n"
            f"```markdown\n{text[: MESSAGE_CHUNK_SIZE - 120]}\n```\n"
            f"-# Loaded at the start of every session you open · `{memory}`",
        )

    async def dot_context(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Shows how full the context window is, and what is taking up the room."""
        session: Optional[LiveSession] = self._live.get(thread.id)
        if session is None or not session.alive:
            await self.reply_to(
                message=message,
                content=f"This session is parked, so it has no context loaded right now — send a message and it "
                f"resumes. {self.emoji_table.kuma_shrug}",
            )
            return

        usage: Optional[ContextUsage] = await session.context_usage()
        if usage is None:
            await self.reply_to(message=message, content=f"Claude Code didn't answer that one. {self.emoji_table.kuma_sad}")
            return

        lines: list[str] = [f"### {self.emoji_table.kuma_tea} Context", usage.summary()]
        if usage.categories:
            lines.append("")
            lines.append("-# " + " · ".join(f"{name} {tokens:,}" for name, tokens in usage.categories[:6]))
        await self.reply_to(message=message, content="\n".join(lines)[:MESSAGE_CHUNK_SIZE])

    async def dot_compact(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:
        """Compacts the conversation now, rather than waiting for the CLI to do it on its own.

        Forwarded as the CLI's own `/compact`, so it goes through :meth:`run_turn` like any other
        command; that keeps the status line honest, since compaction is one of the few local commands
        that is genuinely slow.
        """
        await self.run_turn(thread=thread, state=state, prompt=f"/compact {argument}".strip(), reference=message)

    async def dot_rename(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Renames the post, and the session as the CLI knows it, keeping any status prefix.

        Both halves, because the CLI keeps a name of its own — `/rename` sets it and it is what
        `claude --resume` shows in its picker. Renaming only the Discord post left the two disagreeing
        about the same conversation, which is exactly the sort of thing that wastes ten minutes when
        someone goes looking for a transcript from a terminal.
        """
        if not argument:
            await self.reply_to(message=message, content=f"Give me a title. {self.emoji_table.kuma_hmm}")
            return

        title: str = truncate(argument, THREAD_TITLE_SIZE)
        prefix: str = SessionStatus.of(thread).prefix
        await thread.edit(name=f"{prefix}{title}", reason="Claude session renamed by its owner.")

        # Out of band and best effort; the post is the name that matters and a parked session has no
        # process to tell.
        session: Optional[LiveSession] = self._live.get(thread.id)
        if session is not None and session.alive:
            with contextlib.suppress(Exception):
                await session.rename(title=title)

        await self.reply_to(message=message, content=f"Renamed to **{title}**. {self.emoji_table.kuma_happy}")

    async def dot_usage(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Shows the shared account's usage and how close it is to a rate limit.

        Read with the `get_usage` control request rather than by forwarding `/usage`: out of band, so
        it needs no turn, no status animation, and works while the session is busy.
        """
        session: Optional[LiveSession] = self._live.get(thread.id)
        if session is None or not session.alive:
            await self.reply_to(
                message=message,
                content=f"This session is parked, so there is nothing to ask — send a message and it resumes. "
                f"{self.emoji_table.kuma_shrug}",
            )
            return

        usage: Optional[UsageSnapshot] = await session.usage(refresh=True)
        if usage is None:
            await self.reply_to(message=message, content=f"Claude Code didn't answer that one. {self.emoji_table.kuma_sad}")
            return
        await self.reply_to(message=message, content=usage.summary()[:MESSAGE_CHUNK_SIZE])

    async def dot_stop(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Interrupts the turn in progress, keeping the session and its conversation."""
        if await self.interrupt_turn(thread_id=thread.id):
            return
        await self.reply_to(message=message, content=f"Nothing is running right now. {self.emoji_table.kuma_shrug}")

    async def dot_restore(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Reopens a dormant session; only reachable when the thread is not actually locked."""
        await self.reply_to(
            message=message,
            content=f"Use **Restore Session** on the opening post. {self.emoji_table.kuma_peak}\n"
            f"-# It rebuilds the conversation from this session's transcript snapshot.",
        )

    async def interrupt_turn(self, *, thread_id: int) -> bool:
        """Interrupts a session's turn and drops anything queued behind it, returning whether there was one.

        Sends the CLI's own `interrupt` rather than killing anything: the process and the whole
        conversation survive, so the user can simply ask again.

        Queued turns are cancelled where they wait. Stopping only the turn in flight would let the next
        one start the moment this one ended, which reads as the stop having been ignored — and telling
        someone there was nothing to stop while a queued turn goes on to run is worse than either.
        """
        # Cancelled first: a queued task woken by the running turn ending would otherwise take the lock
        # while we are still tearing that one down.
        queued: set[asyncio.Task[Any]] = self._queued.pop(thread_id, set())
        for task in queued:
            task.cancel()

        context: Optional[TurnContext] = self._turns.get(thread_id)
        if context is None:
            if queued:
                LOGGER.info(
                    "<%s.%s> | Dropped %s queued turn(s) | Thread: %s",
                    __class__.__name__,
                    "interrupt_turn",
                    len(queued),
                    thread_id,
                )
            return bool(queued)

        session: Optional[LiveSession] = self._live.get(thread_id)
        if session is not None and session.alive:
            await session.interrupt()
        context.interrupt()
        LOGGER.info(
            "<%s.%s> | Interrupted a turn | Thread: %s | Queued dropped: %s",
            __class__.__name__,
            "interrupt_turn",
            thread_id,
            len(queued),
        )
        return True

    async def dot_close(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Closes and locks the session."""
        await self.close_session(thread=thread, state=state)

    # endregion

    # region --- Panel callbacks

    async def panel_state(self, interaction: discord.Interaction) -> Optional[tuple[discord.Thread, SessionState]]:
        """Resolves the thread and session behind a panel interaction, answering the user on failure."""
        thread: Any = interaction.channel
        if not self.is_session_thread(thread):
            await interaction.response.send_message(content="This panel is not attached to a session.", ephemeral=True)
            return None

        state: Optional[SessionState] = await self.get_state(thread=thread)
        if state is None:
            await interaction.response.send_message(
                content=f"I cannot read this session's state from its opening post. {self.emoji_table.kuma_sad}",
                ephemeral=True,
            )
            return None
        if interaction.user.id != state.user_id:
            await interaction.response.send_message(content="This session isn't yours!", ephemeral=True)
            return None
        return thread, state

    async def panel_model(self, interaction: discord.Interaction, *, value: str) -> None:
        """Switches the model on the running process, live."""
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        thread, state = resolved
        state.model = MODELS.get(value, DEFAULT_MODEL)
        session: Optional[LiveSession] = self._live.get(thread.id)
        if session is not None and session.alive:
            await session.set_model(model=state.model)
        self._sessions[thread.id] = state
        await interaction.response.edit_message(view=SessionPanel(state=state, live=thread.id in self._live))
        # This path answers the interaction itself rather than going through `update_panel`, so the
        # sidecar has to be written here too. After the response, never before: an interaction has
        # three seconds to be acknowledged and nothing optional belongs in front of that.
        await asyncio.to_thread(write_session_index, state=state)

    async def panel_mode(self, interaction: discord.Interaction, *, value: str) -> None:
        """Switches the permission mode on the running process, live."""
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        thread, state = resolved
        state.mode = _mode_value(value)
        session: Optional[LiveSession] = self._live.get(thread.id)
        if session is not None and session.alive:
            await session.set_mode(mode=state.mode)
        self._sessions[thread.id] = state
        await interaction.response.edit_message(view=SessionPanel(state=state, live=thread.id in self._live))
        # As in `panel_model`: this path does not reach `update_panel`, and the write goes after the
        # interaction has been answered.
        await asyncio.to_thread(write_session_index, state=state)

    async def panel_effort(self, interaction: discord.Interaction, *, value: str) -> None:
        """Switches the effort level via the CLI's own `/effort` command."""
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        thread, state = resolved
        state.effort = value if value in EFFORTS else DEFAULT_EFFORT
        self._sessions[thread.id] = state
        # Answered first; `apply_effort` re-renders the panel itself and an interaction has three
        # seconds to be acknowledged.
        await interaction.response.edit_message(view=SessionPanel(state=state, live=thread.id in self._live))
        await self.apply_effort(thread=thread, state=state, level=state.effort)

    async def panel_clear(self, interaction: discord.Interaction) -> None:
        """Clears the conversation in this post, keeping the files."""
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        thread, state = resolved
        # Deferred rather than answered with an edit: clearing a live session runs a turn, which is
        # well past the three seconds an interaction has to be acknowledged.
        await interaction.response.defer()
        await self.clear_session(thread=thread, state=state)

    async def panel_files(self, interaction: discord.Interaction) -> None:
        """Lists the session's generated files, privately."""
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        _, state = resolved
        entries: list[Path] = await asyncio.to_thread(lambda: sorted(path for path in state.workspace.rglob("*") if path.is_file()))
        if not entries:
            await interaction.response.send_message(content=f"Nothing generated yet. {self.emoji_table.kuma_shrug}", ephemeral=True)
            return
        lines: list[str] = [
            f"- `{path.relative_to(state.workspace).as_posix()}` ({human_size(path.stat().st_size)})" for path in entries[:40]
        ]
        await interaction.response.send_message(content="\n".join(lines)[:MESSAGE_CHUNK_SIZE], ephemeral=True)

    async def panel_interrupt(self, interaction: discord.Interaction) -> None:
        """Stops the turn in progress from the panel."""
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        thread, _ = resolved
        if await self.interrupt_turn(thread_id=thread.id):
            await interaction.response.send_message(content=f"Stopping that turn. {self.emoji_table.kuma_tea}", ephemeral=True)
            return
        await interaction.response.send_message(content=f"Nothing is running right now. {self.emoji_table.kuma_shrug}", ephemeral=True)

    async def panel_help(self, interaction: discord.Interaction) -> None:
        """Shows the dot command list, privately."""
        await interaction.response.send_message(content=self.help_text()[:MESSAGE_CHUNK_SIZE], ephemeral=True)

    async def panel_close(self, interaction: discord.Interaction) -> None:
        """Closes and locks the session from the panel."""
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        thread, state = resolved
        await interaction.response.defer()
        await self.close_session(thread=thread, state=state)

    async def panel_restore(self, interaction: discord.Interaction) -> None:
        """Reopens a dormant session and unlocks its post."""
        thread: Any = interaction.channel
        if not self.is_session_thread(thread):
            await interaction.response.send_message(content="This panel is not attached to a session.", ephemeral=True)
            return

        lookup: PanelLookup = await self.fetch_panel(thread=thread)
        state: Optional[SessionState] = None if lookup.message is None else parse_state(message=lookup.message, thread_id=thread.id)
        if state is None:
            await interaction.response.send_message(
                content=f"There is no session left on this post. {self.emoji_table.kuma_sad}", ephemeral=True
            )
            return
        if interaction.user.id != state.user_id:
            await interaction.response.send_message(content="This session isn't yours!", ephemeral=True)
            return

        # Before anything unlocks: `live_session` decides `--resume` on whether the CLI's transcript is
        # on disk, so putting ours back is what makes the next message carry the conversation on
        # instead of starting a blank one on a reused ID.
        recovered: bool = await asyncio.to_thread(self.restore_transcript, state=state)
        resumable: bool = recovered or await asyncio.to_thread(live_transcript(cwd=state.cwd, session_id=state.session_id).is_file)

        # An expired session is past the point Claude Code keeps its own transcript, so `--resume` has
        # nothing to read unless our snapshot survived. Restoring one without it would hand back a post
        # that looks like the old conversation and answers as though it never happened.
        if SessionStatus.of(thread) is SessionStatus.EXPIRED and not resumable:
            await interaction.response.send_message(
                content=f"This session expired and its transcript is gone, so there is nothing left to carry on from. "
                f"Start a fresh one with `/claude ask`. {self.emoji_table.kuma_sad}",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        await thread.edit(
            name=SessionStatus.strip(thread.name),
            archived=False,
            locked=False,
            reason="Claude session restored by its owner.",
        )
        self._sessions[thread.id] = state
        await self.update_panel(thread=thread, state=state, status=SessionStatus.ACTIVE)
        await self.send_to_thread(thread=thread, content=f"Session restored — reply here to carry on. {self.emoji_table.kuma_happy}")

    async def close_session(self, *, thread: discord.Thread, state: SessionState) -> None:
        """Snapshots, parks and locks a session so it can be restored later."""
        # A restored session is a new grant of trust, not a continuation of the old one.
        self._approved.pop(thread.id, None)
        await self.park(thread_id=thread.id)
        await asyncio.to_thread(self.snapshot_transcript, state=state)
        await self.update_panel(thread=thread, state=state, status=SessionStatus.CLOSED)
        await self.send_to_thread(thread=thread, content=f"Session closed. **Restore Session** brings it back. {self.emoji_table.kuma_tea}")
        with contextlib.suppress(discord.HTTPException):
            await thread.edit(
                name=f"{CLOSED_PREFIX}{SessionStatus.strip(thread.name)}"[:100],
                archived=True,
                locked=True,
                reason="Claude session closed.",
            )

    @staticmethod
    def snapshot_transcript(*, state: SessionState) -> bool:
        """Copies the CLI's transcript into the session workspace, gzipped.

        The CLI prunes its own log on `cleanupPeriodDays`, so without this a session left to age out
        loses the history that `--resume` needs. Nothing else could rebuild it; the Discord messages
        hold the answers but not the tool calls between them.
        """
        source: Path = live_transcript(cwd=state.cwd, session_id=state.session_id)
        if not source.is_file():
            return False

        target: Path = snapshot_path(workspace=state.workspace, session_id=state.session_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as handle, gzip.open(target, "wb") as archive:
            shutil.copyfileobj(handle, archive)
        return True

    @staticmethod
    def restore_transcript(*, state: SessionState) -> bool:
        """Puts a snapshotted transcript back where the CLI expects it, making `--resume` work again.

        The mirror of :meth:`snapshot_transcript`. :meth:`live_session` decides between `--resume` and
        `--session-id` purely on whether the CLI's own transcript file exists, so writing ours back to
        that exact path is the whole mechanism — nothing else needs to know a restore happened.

        Blocking; call it off the loop.

        Parameters
        ----------
        state: :class:`SessionState`
            The session to restore; the snapshot comes from its workspace and is written back under
            its current cwd.

        Returns
        -------
        :class:`bool`
            Whether a snapshot was found and written back.

        """
        source: Path = snapshot_path(workspace=state.workspace, session_id=state.session_id)
        if not source.is_file():
            return False

        target: Path = live_transcript(cwd=state.cwd, session_id=state.session_id)
        # Never over the CLI's own copy. The snapshot is taken at park time, so anything already there
        # is at least as new; overwriting would roll the conversation back and lose the turns since.
        if target.is_file():
            return False

        target.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(source, "rb") as compressed, target.open("wb") as raw:
            shutil.copyfileobj(compressed, raw)
        return True

    # endregion

    # region --- Forum management

    def missing_permissions(self, *, guild: discord.Guild, channel: Optional[discord.abc.GuildChannel] = None) -> list[str]:
        """Returns the permissions the bot is missing to run sessions here.

        We check before creating anything so a shortfall is one clear message instead of a
        :class:`discord.Forbidden` part way through building a category, forum and thread.

        Parameters
        ----------
        guild: :class:`discord.Guild`
            The guild to check guild-level permissions in.
        channel: :class:`Optional[discord.abc.GuildChannel]`, optional
            An existing forum to check channel-level permissions in, by default `None`.

        Returns
        -------
        :class:`list[str]`
            The missing permission names, in the order they are declared.

        """
        missing: list[str] = []
        guild_permissions: discord.Permissions = guild.me.guild_permissions
        missing.extend(name for name in GUILD_PERMISSIONS if not getattr(guild_permissions, name, False))

        if channel is not None:
            channel_permissions: discord.Permissions = channel.permissions_for(guild.me)
            missing.extend(name for name in FORUM_PERMISSIONS if not getattr(channel_permissions, name, False))
        return missing

    async def get_category(self, *, guild: discord.Guild) -> discord.CategoryChannel:
        """Returns the session category, creating it when it isn't there yet."""
        existing: Optional[discord.CategoryChannel] = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if existing is not None:
            return existing
        return await guild.create_category(
            name=CATEGORY_NAME,
            overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)},
            reason="Claude sessions.",
        )

    async def get_forum(self, *, guild: discord.Guild, user: discord.Member) -> discord.ForumChannel:
        """Returns a user's private session forum, creating it on first use.

        Private by overwrite rather than by convention: the default role cannot see it and the owner
        can, so one user's sessions are never another's to read.
        """
        name: str = FORUM_NAME_FORMAT.format(name=user.name.lower().replace(" ", "-"))[:100]
        category: discord.CategoryChannel = await self.get_category(guild=guild)

        existing: Optional[discord.ForumChannel] = next(
            (channel for channel in category.channels if isinstance(channel, discord.ForumChannel) and channel.name == name),
            None,
        )
        if existing is not None:
            return existing

        return await guild.create_forum(
            name=name,
            topic=FORUM_TOPIC,
            category=category,
            overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_threads=True, manage_messages=True),
            },
            reason=f"Claude sessions for {user}.",
        )

    def active_threads(self, *, forum: discord.ForumChannel) -> list[discord.Thread]:
        """Returns the forum's posts that are still live sessions."""
        return [thread for thread in forum.threads if not thread.archived and not SessionStatus.of(thread).dormant]

    async def all_session_threads(self, *, forum: discord.ForumChannel) -> list[discord.Thread]:
        """Returns every post in a forum, archived ones included.

        `ForumChannel.threads` is the cache, and the cache holds only what is *not* archived. Closing a
        session archives its post, so anything the sweep has to reach after a close — expiring it,
        counting its workspace as still wanted — is invisible without the REST walk.

        Parameters
        ----------
        forum: :class:`discord.ForumChannel`
            The forum to walk.

        Returns
        -------
        :class:`list[discord.Thread]`
            The forum's posts, deduplicated by ID.

        """
        threads: dict[int, discord.Thread] = {thread.id: thread for thread in forum.threads}
        with contextlib.suppress(discord.HTTPException):
            async for thread in forum.archived_threads(limit=ARCHIVED_SWEEP_LIMIT):
                threads.setdefault(thread.id, thread)
        return list(threads.values())

    @tasks.loop(hours=CLEANUP_INTERVAL_HOURS, reconnect=True)
    async def cleanup_loop(self) -> None:
        """Ages sessions out, then clears up what nothing points at any more."""
        await self.expire_sessions()
        with contextlib.suppress(Exception):
            await self.discard_foreign_posts()
        with contextlib.suppress(Exception):
            await self.prune_orphan_workspaces()
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self.sweep_orphan_processes, live=self.live_pids())

    async def expire_sessions(self) -> None:
        """Marks sessions that have passed the 30 day line, and only those, as expired.

        `EXPIRED` means one thing: the session is old enough that Claude Code has dropped its own
        transcript, so it cannot be resumed from the CLI's side. Every other way a session ends is a
        `CLOSED`. A closed post is *not* skipped here — it still ages, and reaches the same line the
        active ones do — which is why this walks the archived posts too.
        """
        cutoff: datetime.datetime = discord.utils.utcnow() - datetime.timedelta(days=SESSION_MAX_AGE_DAYS)
        for forum in self.session_forums():
            for thread in await self.all_session_threads(forum=forum):
                # As the docstring says, anything PAST Jan 9, 2022 | Chances of this bot encountering a thread that old is near impossible.
                assert thread.created_at is not None  # noqa: S101
                if SessionStatus.of(thread) is SessionStatus.EXPIRED:
                    # Terminal. Nothing it can be promoted to and nothing left to park.
                    continue

                last = (thread.last_message_id and discord.utils.snowflake_time(thread.last_message_id)) or thread.created_at
                if last > cutoff:
                    continue

                state: Optional[SessionState] = await self.get_state(thread=thread)
                if state is not None:
                    await self.park(thread_id=thread.id)
                    # Same order as `close_session`: park first so the process has stopped writing,
                    # then snapshot. Without this an expired session has nothing to restore from, and
                    # expiry is exactly when the CLI has already pruned its own copy.
                    await asyncio.to_thread(self.snapshot_transcript, state=state)
                    await self.update_panel(thread=thread, state=state, status=SessionStatus.EXPIRED)
                LOGGER.info("<%s.%s> | Session expired | Thread: %s", __class__.__name__, "expire_sessions", thread.id)
                with contextlib.suppress(discord.HTTPException):
                    await thread.edit(
                        name=f"{EXPIRED_PREFIX}{SessionStatus.strip(thread.name)}"[:100],
                        archived=True,
                        locked=True,
                        reason="Claude session expired.",
                    )

    async def discard_foreign_post(self, *, thread: discord.Thread) -> bool:
        """Deletes a post in a session forum that we did not open, DMing its author what was removed.

        A hand-made post looks like a session to everything that goes by location: it satisfies
        :meth:`is_session_thread`, so replies in it reach the session listener, and it counts against
        its author's session slots. It can never *become* one either — the opening message is theirs,
        so there is nowhere to write a panel or a state line. Removing it is the only outcome that
        leaves the forum in a state the rest of the cog can describe.

        The text is sent back first. It is the author's own writing, and this is the one place the cog
        destroys something a person typed, so it should not vanish without a copy and a reason.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The post to remove; already known to be a non-session post in one of our forums.

        Returns
        -------
        :class:`bool`
            Whether the post was deleted.

        """
        opening: Optional[discord.Message] = (await self.fetch_panel(thread=thread)).message
        text: str = (opening.content if opening is not None else "").strip()
        owner: Optional[Union[discord.Member, discord.User]] = thread.owner or self.bot.get_user(thread.owner_id or 0)

        if owner is not None:
            copy: str = f"\n\n>>> {truncate(text, MESSAGE_CHUNK_SIZE)}" if text else ""
            with contextlib.suppress(discord.HTTPException):
                await owner.send(
                    content=f"I removed **{thread.name}** from your Claude sessions forum -- posts there have to be "
                    f"opened with `/claude ask` so they carry a session panel. Here is what you wrote, so you can "
                    f"paste it into a real session. {self.emoji_table.kuma_shrug}{copy}",
                )

        try:
            await thread.delete()
        except discord.HTTPException:
            LOGGER.warning("<%s.%s> | Could not remove a foreign post | Thread: %s", __class__.__name__, "discard_foreign_post", thread.id)
            return False
        LOGGER.info("<%s.%s> | Removed a foreign post | Thread: %s", __class__.__name__, "discard_foreign_post", thread.id)
        return True

    async def discard_foreign_posts(self) -> int:
        """Removes every non-session post in a session forum, catching those made while offline."""
        removed: int = 0
        for forum in self.session_forums():
            for thread in list(forum.threads):
                if not self.is_bot_post(thread) and await self.discard_foreign_post(thread=thread):
                    removed += 1
        return removed

    async def prune_orphan_workspaces(self) -> int:
        """Removes session workspaces whose post no longer exists.

        A deleted post takes its workspace with it through the delete listener, so this is for the
        deletions that happened while the bot was down, where nothing was listening.

        .. warning::
            This deletes a user's generated files, so it fails **closed** twice over. Enumerating no
            forums at all aborts the whole sweep — during an outage or a cold cache that reads as
            "no sessions exist", which would take every workspace with it — and a directory younger
            than :attr:`WORKSPACE_GRACE_HOURS` is left alone, so a session whose post has not reached
            the cache yet cannot be deleted out from under itself.

        Returns
        -------
        :class:`int`
            How many workspace directories were removed.

        """
        known: set[int] = set()
        forums: int = 0
        for forum in self.session_forums():
            forums += 1
            known.update(thread.id for thread in await self.all_session_threads(forum=forum))

        if forums == 0:
            LOGGER.info("<%s.%s> | No forums visible; skipping the sweep", __class__.__name__, "prune_orphan_workspaces")
            return 0

        return await asyncio.to_thread(prune_workspaces, known=known)

    @cleanup_loop.before_loop
    async def before_cleanup_loop(self) -> None:
        await self.bot.wait_until_ready()

    # endregion

    # region --- Commands

    @claude.command(name="ask", description="Open a live Claude Code session as a forum post you can reply in.")
    @app_commands.describe(prompt="What to ask first. Leave empty to open an empty session.", model="Which model to run.")
    @app_commands.choices(model=MODEL_CHOICES)
    @app_commands.guild_only()
    async def ask(
        self,
        interaction: discord.Interaction,
        prompt: Optional[str] = None,
        model: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        """Opens a session post and, when given a prompt, runs the first turn in it."""
        if not self.may_use(interaction.user):
            await interaction.response.send_message(
                content=f"You don't have access to Claude sessions. Ask an owner for `/claude access`. {self.emoji_table.kuma_shrug}",
                ephemeral=True,
            )
            return

        guild: Optional[discord.Guild] = interaction.guild
        user: Any = interaction.user
        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message(content="This only works in a server.", ephemeral=True)
            return

        missing: list[str] = self.missing_permissions(guild=guild)
        if missing:
            await interaction.response.send_message(content=f"I need these permissions first: {', '.join(missing)}.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        forum: discord.ForumChannel = await self.get_forum(guild=guild, user=user)
        open_posts: list[discord.Thread] = self.active_threads(forum=forum)
        if len(open_posts) >= MAX_SESSIONS_PER_USER:
            await interaction.followup.send(
                content=f"You already have {len(open_posts)} sessions open, which is the cap. "
                f"Close one first. {self.emoji_table.kuma_pout}",
                ephemeral=True,
            )
            return

        # Seeded before the process ever starts, so the very first turn already reads their CLAUDE.md.
        await asyncio.to_thread(prepare_user_root, user_id=user.id, name=user.display_name)

        state: SessionState = SessionState(
            thread_id=0,
            user_id=user.id,
            session_id=str(uuid.uuid4()),
            model=MODELS.get(model.value if model is not None else "", DEFAULT_MODEL),
        )
        created: ThreadWithMessage = await forum.create_thread(
            name=thread_title(prompt) if prompt else PLACEHOLDER_TITLE,
            view=SessionPanel(state=state, live=False),
            reason=f"Claude session for {user}.",
        )
        thread: discord.Thread = created.thread

        # The thread ID is only knowable once Discord has made the thread, and it names the session's
        # workspace, so the panel is re-rendered rather than built right the first time.
        state.thread_id = thread.id
        self._sessions[thread.id] = state
        await self.update_panel(thread=thread, state=state)

        await interaction.followup.send(content=f"Session open: {thread.mention} {self.emoji_table.kuma_happy}", ephemeral=True)
        # Both are extras on top of a session that is already open, so neither is allowed to be the
        # reason opening one appears to have failed.
        with contextlib.suppress(discord.HTTPException):
            await self.show_announcement(interaction=interaction)
        # Into the post, not the reply. The hint is about `.help` *in a session*, so it belongs where
        # the person will be typing it — and posting it before the first turn keeps it above the
        # conversation rather than buried under it.
        await self.show_hint(thread=thread, user=user, key="claude.dot_commands")

        if prompt:
            await self.run_turn(thread=thread, state=state, prompt=prompt)

    async def show_announcement(self, *, interaction: discord.Interaction) -> None:
        """Shows the standing announcement to someone opening a session, if there is one."""
        announcement: Optional[Announcement] = await asyncio.to_thread(read_announcement)
        if announcement is None:
            return
        await interaction.followup.send(content=announcement.summary(emoji=self.emoji_table.kuma_wow), ephemeral=True)

    async def show_hint(self, *, thread: discord.Thread, user: discord.Member, key: str) -> None:
        """Posts one of this cog's hints into a session post, if the user is still due it.

        Sent into the thread rather than answered to the interaction: a hint about how to drive a
        session reads as noise beside `Session open: #thread` and as help inside the post itself.

        Hints are a nicety and this cog does not depend on them: an unloaded `hints` extension means no
        hint, not a broken command.
        """
        hints: Optional[commands.Cog] = self.bot.get_cog("Hints")
        if not isinstance(hints, HintsCog):
            return
        with contextlib.suppress(discord.HTTPException):
            await hints.send_into(thread, key, user=user)

    @claude.command(name="announcement", description="Set the notice shown to anyone opening a session.")
    @app_commands.describe(
        message="The announcement. Leave empty to see the current one.",
        clear="Remove the current announcement instead of setting one.",
    )
    @app_commands.check(_is_owner)
    async def announcement(self, interaction: discord.Interaction, message: Optional[str] = None, clear: bool = False) -> None:
        """Sets, shows or clears the standing announcement.

        There is only ever one. Setting a new one replaces the old outright, which is the whole point:
        this is for saying what changed *now*, and a history of previous notices is not something
        anyone opening a session wants read out to them.
        """
        if clear:
            await asyncio.to_thread(write_announcement, announcement=None)
            await interaction.response.send_message(content=f"Announcement cleared. {self.emoji_table.kuma_happy}", ephemeral=True)
            return

        if message is None:
            current: Optional[Announcement] = await asyncio.to_thread(read_announcement)
            await interaction.response.send_message(
                content=current.summary(emoji=self.emoji_table.kuma_wow)
                if current is not None
                else f"No announcement is set. {self.emoji_table.kuma_shrug}",
                ephemeral=True,
            )
            return

        text: str = message.strip()[:ANNOUNCEMENT_MAX_LENGTH]
        if not text:
            await interaction.response.send_message(
                content=f"That is empty; use `clear` to remove the announcement. {self.emoji_table.kuma_shrug}", ephemeral=True
            )
            return

        stored: Announcement = Announcement(text=text, author_id=interaction.user.id, posted_at=time.time())
        await asyncio.to_thread(write_announcement, announcement=stored)
        LOGGER.info("<%s.%s> | Announcement set | By: %s", __class__.__name__, "announcement", interaction.user.id)
        await interaction.response.send_message(
            content=f"Everyone opening a session will see this now.\n\n{stored.summary(emoji=self.emoji_table.kuma_wow)}", ephemeral=True
        )

    @claude.command(name="access", description="Allow or revoke a member's access to Claude sessions.")
    @app_commands.describe(member="The member to change.", grant_access="On to allow them sessions, off to revoke.")
    @app_commands.guild_only()
    @app_commands.check(_is_owner)
    async def access(self, interaction: discord.Interaction, member: discord.Member, grant_access: bool) -> None:
        """Adds or removes a member from `claude_users`.

        Its own table on purpose: the bot's owner list decides who administers Kuma Kuma, and that is
        a different (much smaller) question than who may hold a Claude session.
        """
        if grant_access:
            added: bool = await self.grant_access(user_id=member.id, added_by=interaction.user.id)
            await interaction.response.send_message(
                content=f"{member.mention} can now open Claude sessions. {self.emoji_table.kuma_happy}"
                if added
                else f"{member.mention} already had access. {self.emoji_table.kuma_shrug}",
                ephemeral=True,
            )
            LOGGER.info("<%s.%s> | Granted Claude access | User: %s | By: %s", __class__.__name__, "access", member.id, interaction.user.id)
            return

        removed: bool = await self.revoke_access(user_id=member.id)
        if removed:
            # Their open sessions stop being usable, so the processes should not stay up holding
            # memory for someone who can no longer reach them.
            for thread_id, state in list(self._sessions.items()):
                if state.user_id == member.id:
                    await self.park(thread_id=thread_id)
        await interaction.response.send_message(
            content=f"{member.mention} can no longer open Claude sessions. {self.emoji_table.kuma_tea}"
            if removed
            else f"{member.mention} didn't have access. {self.emoji_table.kuma_shrug}",
            ephemeral=True,
        )
        LOGGER.info("<%s.%s> | Revoked Claude access | User: %s | By: %s", __class__.__name__, "access", member.id, interaction.user.id)

    @claude.command(name="users", description="List who may open Claude sessions.")
    @app_commands.check(_is_owner)
    async def users(self, interaction: discord.Interaction) -> None:
        """Shows the contents of `claude_users`."""
        if not self._allowed:
            await interaction.response.send_message(
                content=f"Nobody has been granted access yet. {self.emoji_table.kuma_shrug}", ephemeral=True
            )
            return
        lines: list[str] = ["**Claude users**", *(f"- <@{user_id}>" for user_id in sorted(self._allowed))]
        await interaction.response.send_message(content="\n".join(lines)[:MESSAGE_CHUNK_SIZE], ephemeral=True)

    @claude.command(name="sessions", description="List your open Claude sessions in this guild.")
    @app_commands.guild_only()
    async def sessions(self, interaction: discord.Interaction) -> None:
        """Shows the caller's live posts and whether each one has a process up."""
        guild: Optional[discord.Guild] = interaction.guild
        if guild is None:
            await interaction.response.send_message(content="This only works in a server.", ephemeral=True)
            return

        lines: list[str] = []
        for forum in self.session_forums():
            if forum.guild.id != guild.id:
                continue
            for thread in self.active_threads(forum=forum):
                state: Optional[SessionState] = await self.get_state(thread=thread)
                if state is None or state.user_id != interaction.user.id:
                    continue
                marker: str = "●" if thread.id in self._live else "○"
                lines.append(f"{marker} {thread.mention} — `{state.model}` · `{state.mode}`")

        if not lines:
            await interaction.response.send_message(
                content=f"You have no open sessions here. {self.emoji_table.kuma_shrug}", ephemeral=True
            )
            return
        await interaction.response.send_message(
            content="\n".join(["**Your sessions**", *lines, "-# ● live · ○ parked"])[:MESSAGE_CHUNK_SIZE],
            ephemeral=True,
        )

    # endregion


class TurnContext:
    """One turn in flight: the status display it is narrating and the result it is waiting for.

    A turn no longer owns a process, so it cannot simply await one. The reader task feeds events in
    from the side and this holds the future that :meth:`run_turn` blocks on, which is what turns a
    continuous event stream back into a request and a response.

    Attributes
    ----------
    status: :class:`KumaAnimation`
        The animated status message; the same tool log the one-shot cog used.
    before: :class:`dict[Path, float]`
        The workspace as it was when the turn started, for diffing generated files afterwards.
    task: :class:`asyncio.Task`
        The task running the turn, so an interrupt has something to unblock.

    """

    def __init__(
        self,
        *,
        cog: ClaudeCog,
        thread: discord.Thread,
        state: SessionState,
        session: LiveSession,
        status: Union[KumaAnimation, KumaRollingAnimation],
        task: asyncio.Task[Any],
    ) -> None:
        self.cog: ClaudeCog = cog
        self.thread: discord.Thread = thread
        self.state: SessionState = state
        self.session: LiveSession = session
        # Widened for :class:`InlineTurnContext`, which drives a rolling tail rather than one pinned
        # message. Everything this class itself touches is common to both.
        self.status: Union[KumaAnimation, KumaRollingAnimation] = status
        self.task: asyncio.Task[Any] = task
        self.result: TurnResult = TurnResult()
        self.started: float = time.monotonic()
        self.before: dict[Path, float] = _dir_snapshot(directory=state.workspace)

        # Every tool the turn makes gets a line. Held here rather than read back off the animation so
        # the trailing "pending" entry can be flipped to done in place.
        self.log: list[str] = []
        # Why this turn is ending, when it is ending badly. Set *before* the task is cancelled so the
        # `CancelledError` handler — which already seals the status correctly — says the right thing
        # without teardown having to reach into the display itself.
        self.stop_reason: Optional[str] = None
        # When the CLI last said anything at all, for the watchdog in `wait`. Not the same as
        # `_last_event` below, which is the last tool *line* drawn and exists to collapse repeats.
        self.last_event: float = time.monotonic()
        self._last_event: Optional[str] = None
        self._repeats: int = 1
        self._done: asyncio.Future[TurnResult] = asyncio.get_running_loop().create_future()

    @property
    def finished(self) -> bool:
        """Returns whether the turn has already ended, so late events do not redress a dead status."""
        return self._done.done()

    def touch(self) -> None:
        """Records that the CLI has just said something, so the watchdog knows it is alive."""
        self.last_event = time.monotonic()

    async def wait(self) -> TurnResult:
        """Blocks until the CLI's `result` event arrives, the turn is interrupted, or it goes wrong.

        Deliberately *not* on a wall clock. A turn ends when it ends; what this watches for instead
        are the two states a turn never comes back from — a process that is gone, and one that has
        stopped saying anything at all.

        The shield matters: :func:`asyncio.wait_for` cancels what it is waiting on when it times out,
        and cancelling :attr:`_done` would destroy the very thing the rest of the turn resolves
        through. Each pass shields it, so only the wrapper is cancelled.
        """
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(self._done), timeout=TURN_HEARTBEAT)
            except TimeoutError:
                pass

            if not self.session.alive:
                # Nothing to interrupt and nothing coming. Noticed within a heartbeat rather than at
                # the end of a fixed ceiling, which is the whole reason for looking at the process.
                LOGGER.warning(
                    "<%s.%s> | Process gone mid-turn | Thread: %s | PID: %s | Exit: %s | Tools: %s",
                    __class__.__name__,
                    "wait",
                    self.thread.id,
                    self.session.process.pid,
                    self.session.process.returncode,
                    self.result.tool_calls,
                )
                self.result.error = (
                    f"Claude Code stopped running mid-turn (exit `{self.session.process.returncode}`), so this turn "
                    f"cannot finish. {self.cog.emoji_table.kuma_shock}\n"
                    f"-# Send another message and it starts again with the conversation resumed."
                )
                return self.result

            if self.session.pending:
                # Blocked on a button, which is a person's business and has its own deadline on the
                # prompt itself. Touched rather than merely skipped so the silence clock starts from
                # the answer, not from whenever the CLI last spoke before asking.
                self.touch()
                continue

            silent: float = time.monotonic() - self.last_event
            if silent < TURN_SILENCE_LIMIT:
                continue

            LOGGER.warning(
                "<%s.%s> | Turn silent for %.0fs, calling it hung | Thread: %s | PID: %s | Tools: %s",
                __class__.__name__,
                "wait",
                silent,
                self.thread.id,
                self.session.process.pid,
                self.result.tool_calls,
            )
            # The process is still up, so only this turn is abandoned; the CLI is told to drop it
            # rather than left working on an answer nobody will read.
            with contextlib.suppress(Exception):
                await self.session.interrupt()
            self.result.error = (
                f"Nothing has come back from Claude Code in {silent / 60:.0f} minutes, so I stopped waiting. "
                f"{self.cog.emoji_table.kuma_head_clench}\n"
                f"-# The session is still up; ask again and it carries on."
            )
            return self.result

    def finish(self, *, event: dict) -> None:
        """Completes the turn from the CLI's `result` event."""
        if self._done.done():
            return

        self.result.duration = time.monotonic() - self.started
        self.result.cost_usd = event.get("total_cost_usd")

        if event.get("is_error"):
            # `result` is simply absent on an aborted turn — verified by interrupting a pending
            # `can_use_tool`, which comes back `error_during_execution` / `aborted_tools` carrying no
            # text at all. Falling back to the reasons it does carry beats an empty code block.
            raw: str = str(event.get("result") or event.get("terminal_reason") or event.get("subtype") or "No detail was given.")
            # An interrupt comes back as an error, but it is one the user asked for and reads badly
            # dressed up as a failure.
            if self.result.interrupted:
                self._done.set_result(self.result)
                return
            self.result.error = f"Claude returned an error. {self.cog.emoji_table.kuma_sad}\n```\n{raw[:1000]}\n```"

        self._done.set_result(self.result)

    def interrupt(self) -> None:
        """Marks the turn as user-stopped and unblocks it, without waiting for the CLI to confirm."""
        self.result.interrupted = True
        if not self._done.done():
            self._done.set_result(self.result)

    async def on_text(self, text: str) -> None:
        """Posts one completed assistant text block into the thread.

        Blocks are posted as they finish rather than accumulated and posted at the end, which is what
        makes the session feel live. Chunked because a single block can still exceed Discord's limit.

        Dropped once the turn is over. `.stop` resolves a turn locally without waiting for the CLI, so
        blocks already in the pipe still arrive afterwards — and posting one lands answer text in the
        thread *below* the notice saying the turn was stopped.
        """
        if self.finished:
            return

        self.result.blocks.append(text)
        await self.post_text(text)

    async def post_text(self, text: str) -> None:
        """Posts one block of answer text into the thread, chunked and rewritten for Discord.

        Split out from :meth:`on_text` so a level that holds its text back has somewhere to send it
        from once the turn is over, by which point :attr:`finished` refuses everything.
        """
        # Rewritten before chunking, so a table is still whole when it is measured and fenced; the
        # raw block is what went onto `result.blocks`, since that is the answer as the model wrote it.
        for chunk in chunk_text(repair_code_escapes(to_discord_markdown(text))):
            with contextlib.suppress(discord.HTTPException):
                await self.cog.send_to_thread(thread=self.thread, content=chunk)

    async def flush(self) -> None:
        """Posts anything the display held back while the turn ran.

        A no-op here and on every level but :class:`SilentTurnContext`, so :meth:`ClaudeCog._run_turn`
        can close a turn out the same way whichever display it is driving.
        """

    async def on_tool(self, *, tool: str, target: str, tool_id: str = "") -> None:  # noqa: ARG002
        """Ticks off the previous tool call and opens a pending line for the one just started.

        Ignored after the turn ends, for the same reason as :meth:`on_text`: the log it draws into has
        already been written out as the closing frame.

        `tool_id` is accepted and ignored. A flat log has no way to address a call once a later one
        has started, so it cannot use one; the argument exists to keep one call signature across both
        contexts, which is what lets the event pump stay ignorant of which is in flight.
        """
        if self.finished:
            return

        self.result.tool_calls += 1
        signature: str = f"{tool} {target}".rstrip()
        self.status.label = self.cog.to_progressive(TOOL_VERBS.get(tool, tool))

        if signature == self._last_event:
            # The same call again; count it on the line we already have. It stays pending either way,
            # as the newest of the repeats is the one still running.
            self._repeats += 1
            self.log.pop()
        else:
            if self.log:
                # The previous call must have finished for a new one to start.
                self.log[-1] = self.log[-1].replace(PENDING_MARK, DONE_MARK, 1).replace(WAITING_MARK, DONE_MARK, 1)
            self._last_event = signature
            self._repeats = 1
        self.log.append(tool_line(mark=PENDING_MARK, tool=tool, target=target, repeats=self._repeats))
        self.render()

    async def on_tool_result(self, *, tool_id: str, content: Any, is_error: bool) -> None:
        """Ignored; a flat log has nowhere to hang a result. :class:`InlineTurnContext` overrides it."""

    async def on_waiting(self, *, tool: str) -> None:
        """Marks the status line as blocked on a person, not stalled.

        Without this a permission prompt looks identical to a hung turn: the spinner keeps going and
        the log stops moving, with nothing saying the CLI is waiting on a button nobody has pressed.
        """
        self.status.label = "Waiting for you"
        if self.log:
            self.log[-1] = self.log[-1].replace(PENDING_MARK, WAITING_MARK, 1)
        else:
            self.log.append(tool_line(mark=WAITING_MARK, tool=tool, target=""))
        self.render()

    async def on_resumed(self) -> None:
        """Puts the status line back to work once a prompt has been answered.

        Does nothing once the turn is over. A prompt can be retired *after* its turn has ended --
        `.stop` resolves the turn locally and the CLI's `control_cancel_request` follows a beat later,
        by which time the status has been stopped and given its closing text. Putting "Thinking" back
        on a finished turn would be a lie even when nothing re-renders it.
        """
        if self.finished:
            return

        self.status.label = "Thinking"
        if self.log:
            self.log[-1] = self.log[-1].replace(WAITING_MARK, PENDING_MARK, 1)
        self.render()

    def visible_log(self) -> list[str]:
        """Returns the tail of the log, with a line standing in for whatever scrolled off."""
        visible: list[str] = self.log[-TOOL_LOG_VISIBLE:]
        if len(self.log) > TOOL_LOG_VISIBLE:
            visible = [f"-# …and {len(self.log) - TOOL_LOG_VISIBLE} earlier", *visible]
        return visible

    def render(self) -> None:
        """Pushes the tail of the log onto the animation."""
        self.status.clear_body()
        for line in self.visible_log():
            self.status.add_line(line)

    def interrupted_note(self) -> str:
        """Returns the closing content for a stopped turn, tool log and all.

        The successful path collapses the log deliberately — on scrollback it is just noise between
        the question and the answer. A stopped turn has no answer coming, so the log is the only
        account of what it managed before it was halted.
        """
        note: str = f"-# {self.cog.emoji_table.kuma_shrug} {self.stop_reason or STOPPED_NOTE}"
        lines: list[str] = self.visible_log()
        if not lines:
            return note
        # The trailing entry was still pending, and leaving it marked that way reads as a tool that is
        # somehow still running on a turn that has stopped.
        lines[-1] = lines[-1].replace(PENDING_MARK, ERROR_MARK, 1).replace(WAITING_MARK, ERROR_MARK, 1)
        return "\n".join([*lines, "", note])


class InlineTurnContext(TurnContext):
    """A turn narrated as one in-order transcript rather than a box of tools and a pile of prose.

    :class:`TurnContext` draws tool calls into a pinned status message and posts answer text as
    separate messages below it. Discord cannot insert a message above an existing one, so a turn that
    alternates between the two reads out of order — every tool above every word, whatever order they
    happened in — and no amount of editing the pinned message fixes it.

    This drives a :class:`KumaRollingAnimation` instead. Answer text is *interjected*: the live tail
    is sealed where it stands, the text is posted, and a new tail opens beneath it. The transcript
    then reads top to bottom in event order with the spinner on the bottom edge, which is what the
    CLI does and why the CLI is followable.

    .. note::
        The ordering is bought with messages: a text block now costs the block plus a reopened tail,
        where the pinned display cost only the block. Discord's per-channel create budget is shared
        with the animation's edits, so a turn emitting many short blocks in a burst will feel it --
        :meth:`KumaRollingAnimation.interject` takes `reopen=False` if that ever needs trading back.

    Attributes
    ----------
    entries: :class:`list[ToolEntry]`
        Every tool call the turn has made, oldest first, results attached.

    """

    def __init__(
        self,
        *,
        cog: ClaudeCog,
        thread: discord.Thread,
        state: SessionState,
        session: LiveSession,
        status: KumaRollingAnimation,
        task: asyncio.Task[Any],
    ) -> None:
        super().__init__(cog=cog, thread=thread, state=state, session=session, status=status, task=task)
        self.status: KumaRollingAnimation = status
        self.entries: list[ToolEntry] = []
        # Addressed by tool id rather than by position: results come back out of order, and the entry
        # one belongs to is very often no longer the newest.
        self._by_id: dict[str, ToolEntry] = {}

    # region --- Narration

    async def on_text(self, text: str) -> None:
        """Seals the tail, posts the block above it, and reopens the status line underneath.

        Dropped once the turn is over, exactly as :meth:`TurnContext.on_text` is: `.stop` resolves a
        turn locally without waiting for the CLI, so blocks already in the pipe still arrive after the
        closing frame has been written.
        """
        if self.finished:
            return

        self.result.blocks.append(text)
        await self.status.interject(chunk_text(repair_code_escapes(text)))
        # The new tail opens empty; this refills it with whatever of the log has not been sealed.
        await self.render_inline()

    async def on_tool(self, *, tool: str, target: str, tool_id: str = "") -> None:
        """Opens an entry for a call, folding it into the previous one when it is a repeat."""
        if self.finished:
            return

        self.result.tool_calls += 1
        self.status.label = self.cog.to_progressive(TOOL_VERBS.get(tool, tool))

        entry: ToolEntry = ToolEntry(tool=tool, target=target)
        last: Optional[ToolEntry] = self.entries[-1] if self.entries else None
        # Only folded while the previous call is still open. Once a result has landed the line is a
        # finished record of that call, and counting a new one onto it would overwrite the result.
        if last is not None and last.signature == entry.signature and last.detail is None:
            last.repeats += 1
            entry = last
        else:
            self.entries.append(entry)

        if tool_id:
            self._by_id[tool_id] = entry
        await self.render_inline()

    async def on_tool_result(self, *, tool_id: str, content: Any, is_error: bool) -> None:
        """Closes the entry a result belongs to and writes the result beneath it.

        A call is marked done by *its own* result rather than by the next call starting, which is all
        a flat log could manage. The difference shows: parallel calls no longer tick each other off,
        and a failed tool is marked failed instead of quietly reading as finished.

        An unknown id is dropped. The CLI replays tool results after a compaction, and redressing an
        entry the current turn never made — or that a `.stop` already closed out — would be wrong.
        """
        if self.finished:
            return

        entry: Optional[ToolEntry] = self._by_id.pop(tool_id, None)
        if entry is None:
            return

        entry.mark = ERROR_MARK if is_error else DONE_MARK
        entry.detail = tool_result_summary(content=content, is_error=is_error)
        await self.render_inline()

    async def on_waiting(self, *, tool: str) -> None:
        """Marks the newest entry as blocked on a person, not stalled."""
        self.status.label = "Waiting for you"
        if self.entries and self.entries[-1].open:
            self.entries[-1].mark = WAITING_MARK
        else:
            self.entries.append(ToolEntry(tool=tool, target="", mark=WAITING_MARK))
        await self.render_inline()

    async def on_resumed(self) -> None:
        """Puts the status line back to work once a prompt has been answered.

        Every waiting entry is released, not just the newest: a batch of calls can be held on one
        prompt, and leaving the others paused would show work as blocked that is already running.
        """
        if self.finished:
            return

        self.status.label = "Thinking"
        for entry in self.entries:
            if entry.mark == WAITING_MARK:
                entry.mark = PENDING_MARK
        await self.render_inline()

    # endregion

    # region --- Rendering

    def transcript_blocks(self) -> list[TranscriptBlock]:
        """Returns every entry as one indivisible block, oldest first.

        One block per entry, so a cut never falls between a call and the result written beneath it.
        An entry still open is marked unfinished, which keeps the tail from freezing a `▸` onto a
        message it can no longer edit.

        Nothing is trimmed here, unlike :meth:`TurnContext.visible_log`. The tail decides where the
        log is cut because only it knows what has already been sealed onto an earlier message — and
        the sealed part is still on screen, so there is nothing to stand in for.
        """
        return [TranscriptBlock(lines=tuple(entry.lines()), final=not entry.open) for entry in self.entries]

    async def render_inline(self) -> None:
        """Pushes the whole transcript at the tail, which decides where it is cut."""
        await self.status.render(self.transcript_blocks())

    def final_note(self, *, footer: str = "") -> str:
        """Returns the closing content for the live tail: the unsealed transcript, then `footer`.

        The log is handed over rather than left to the tail's last frame, because a turn ending is
        exactly when entries change without a render — see :meth:`interrupted_note`. Only the live
        tail is rewritten; the sealed messages above it stand as they were, which is the point of
        having sealed them.

        `footer` is optional because a turn that ended normally no longer has one: its stats line is
        the footer *message* posted underneath, which is a message rather than an edit so that the
        mention on it rings. An empty return means the tail had nothing on it worth keeping, and the
        caller has to put something there regardless — an empty edit is not a thing Discord accepts.
        """
        return self.status.compose_final(footer=footer, blocks=self.transcript_blocks()).rstrip()

    def interrupted_note(self) -> str:
        """Returns the closing content for a stopped turn, with anything still open marked failed."""
        for entry in self.entries:
            if entry.open:
                entry.mark = ERROR_MARK
        note: str = f"-# {self.cog.emoji_table.kuma_shrug} {self.stop_reason or STOPPED_NOTE}"
        return self.final_note(footer=note)

    # endregion


class ChatterTurnContext(TurnContext):
    """A turn that says what it is doing without saying what it is doing it to.

    The same pinned box as :class:`TurnContext`, with the tool log left empty. The label still tracks
    the tool in flight, so a long turn reads as work rather than as a hang, but no file path, command
    or search pattern reaches the thread — which is the whole point for anyone running a session in a
    channel other people can read. Answer text is posted exactly as the base class posts it.

    Nothing needs overriding beyond the narration: :meth:`TurnContext.visible_log` over an empty log
    is empty, and :meth:`TurnContext.interrupted_note` already falls back to the note alone.
    """

    async def on_tool(self, *, tool: str, target: str, tool_id: str = "") -> None:  # noqa: ARG002
        """Counts the call and moves the label, writing nothing to the log."""
        if self.finished:
            return

        self.result.tool_calls += 1
        self.status.label = self.cog.to_progressive(TOOL_VERBS.get(tool, tool))

    async def on_waiting(self, *, tool: str) -> None:  # noqa: ARG002
        """Marks the status line as blocked on a person, without naming the tool it is blocked on."""
        self.status.label = "Waiting for you"

    async def on_resumed(self) -> None:
        """Puts the status line back to work once a prompt has been answered."""
        if self.finished:
            return

        self.status.label = "Thinking"


class SilentTurnContext(ChatterTurnContext):
    """A turn that says nothing until it has finished saying it.

    The status box is the entire display while the turn runs; every answer block is held and posted in
    one go at the end, in the order it arrived. Nothing is dropped — an intermediate block is part of
    the answer, and a turn stopped halfway is exactly when the little it managed to say matters most,
    which is why every path out of a turn calls :meth:`flush`.

    .. note::
        Prompts are unaffected, here as in every other level. They are a question put to a person
        rather than narration, and a session that quietly stopped asking would simply wedge.

    """

    def __init__(
        self,
        *,
        cog: ClaudeCog,
        thread: discord.Thread,
        state: SessionState,
        session: LiveSession,
        status: Union[KumaAnimation, KumaRollingAnimation],
        task: asyncio.Task[Any],
    ) -> None:
        super().__init__(cog=cog, thread=thread, state=state, session=session, status=status, task=task)
        # Not the same list as `result.blocks`, which is the record of what the model wrote and is
        # read by everything downstream. This is the queue of what has yet to go out, and it empties.
        self._held: list[str] = []

    async def on_text(self, text: str) -> None:
        """Holds one completed assistant text block rather than posting it."""
        if self.finished:
            return

        self.result.blocks.append(text)
        self._held.append(text)

    async def flush(self) -> None:
        """Posts everything held, oldest first, and forgets it.

        Emptied before anything is sent, so the second call has nothing left to post — which is what
        lets the closing path and the interrupt path both call it without either knowing about the
        other, and what stops a failure partway through resending what already landed.
        """
        held: list[str] = self._held
        self._held = []
        for text in held:
            await self.post_text(text)


# Which display each level drives. VERBOSE is absent because it is the one level that changes the
# *runner* rather than the context: a rolling transcript is a different flow of messages, not a
# different filter on the same one.
VERBOSITY_CONTEXTS: dict[Verbosity, type[TurnContext]] = {
    Verbosity.DEFAULT: TurnContext,
    Verbosity.CHATTER: ChatterTurnContext,
    Verbosity.SILENT: SilentTurnContext,
}


async def setup(bot: Kuma_Kuma) -> None:
    """Loads the cog."""
    await bot.add_cog(ClaudeCog(bot=bot))
