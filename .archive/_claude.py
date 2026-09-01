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
import json
import logging
import os
import re
import shlex
import shutil
import time
import uuid
from configparser import ConfigParser
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Optional, Self, TypedDict, Union, cast

import discord
import pytz
from discord import app_commands
from discord.ext import commands, tasks

from utils import KumaAnimation, KumaCog as Cog, KumaEmojiTable

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterable, Iterator
    from sqlite3 import Row
    from typing import Any

    from kuma_kuma import Kuma_Kuma

LOGGER = logging.getLogger()
__VERSION__ = "1.1.0"

# ---------------------------------------------------------------------------
# Paths
# Every session runs against this repository; there is no project selector. A standard session's
# cwd is its own workspace under this root, an elevated one's is the root itself, and bypass
# ignores the cwd altogether -- so the handful of users who need another repo already reach it
# that way, and a selector would only duplicate the trust boundary in a second place.
# ---------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).parent.parent
# `<root>/.claude_sessions/<user_id>/<thread_id>/`; a session's workspace is its thread. It sits
# under the project root because the CLI cannot write outside its working directory.
WORKSPACE_DIRNAME: str = ".claude_sessions"
# Dropped into each `.claude_sessions/` so the directory ignores itself instead of us
# having to amend the `.gitignore` of every repo it shows up in.
WORKSPACE_GITIGNORE: str = "*\n"
# Attachments a user drops into a thread land in `<workspace>/attachments/`, so they get
# cleaned up with the session and don't need their own prune loop.
ATTACHMENTS_SUBDIR: str = "attachments"

# ---------------------------------------------------------------------------
# Transcripts
# Claude Code keeps its own conversation log per session and `--resume` reads it back. The CLI
# prunes it on `cleanupPeriodDays` (30 by default), which is the same window we expire posts on,
# so a session left to age out loses its transcript for good. Snapshotting the file lets us revive
# an expired session with its full history, tool calls included; we could never rebuild that from
# the Discord messages alone.
# ---------------------------------------------------------------------------

CLAUDE_PROJECTS: Path = Path.home().joinpath(".claude", "projects")
# The CLI names a transcript directory after the **working directory** it ran in, with `/`, `_` and `.`
# all flattened to `-`. eg. `/home/kat/gitHub/Kuma_Kuma` -> `-home-kat-gitHub-Kuma-Kuma`, and a session
# workspace `/home/kat/gitHub/Kuma_Kuma/.claude_sessions/<uid>/<tid>` ->
# `-home-kat-gitHub-Kuma-Kuma--claude-sessions-<uid>-<tid>` (the leading `.` of `.claude_sessions`
# becoming a second `-` is what makes the doubled dash). Verified against the directories this machine's
# `claude 2.1.220` actually created.
#
# The `.` entry matters as much as the base does: without it every path containing a dotted directory
# resolved to a name the CLI never wrote, so nothing was ever found there.
TRANSCRIPT_SLUG_TABLE: dict[str, str] = {"/": "-", "_": "-", ".": "-"}
# Snapshots are named per session, not per workspace. `.new` swaps the session ID but keeps the
# thread, so one shared filename would let a later `.restore` bring back the very conversation
# `.new` was asked to forget.
TRANSCRIPT_NAME: str = "transcript-{session_id}.jsonl.gz"
# Snapshot a session once it has gone this long without a message. Kept well short of the 30 day
# expiry; waiting that long would race the CLI's own prune and a session idle for a day is very
# likely finished, so we catch it here for at most one copy per session.
TRANSCRIPT_IDLE_HOURS: int = 24

# ---------------------------------------------------------------------------
# Session index
# A sidecar mirror of the state line, written into each workspace. The opening post stays the source
# of truth; this exists so the CLI's transcripts stay *findable* when the post cannot be read. On
# disk they are a flat directory of bare UUIDs with nothing tying one to a Discord thread, so a
# gateway outage, a deleted post or a re-keyed session leaves an intact transcript nobody can locate.
# ---------------------------------------------------------------------------

SESSION_INDEX_NAME: str = "session.json"
# How many superseded session IDs to keep. `.new` and `.restore` re-key a thread, and without a
# record of what came before, the previous transcript is orphaned the moment the panel is re-rendered.
# Enough to walk back through a few restarts; it is a breadcrumb trail, not an archive.
SESSION_LINEAGE_LIMIT: int = 10


class SessionIndex(TypedDict):
    """The on-disk mirror of a session's identity. See :func:`write_session_index`."""

    thread_id: int
    owner_id: int
    session_id: str
    lineage: list[str]
    """Superseded session IDs, most recent first."""
    root: str
    model: str
    mode: str
    effort: str
    started: float
    updated: float


# ---------------------------------------------------------------------------
# Forum layout; one category per guild, one forum per user, one thread per session.
# ---------------------------------------------------------------------------

CATEGORY_NAME: str = "Claude Sessions"
FORUM_NAME_FORMAT: str = "claude-{name}"
FORUM_TOPIC: str = (
    "Each post in this forum is one Claude Code session. Reply in a post to continue that session; "
    "start a new one with `/claude ask`. Dot commands (`.help`) change how the session runs."
)
MAX_SESSIONS_PER_USER: int = 5
# Claude Code has pruned its own transcript by the time a thread has sat this long, so the session
# can no longer be resumed. We rename, lock and archive it rather than leave it looking usable.
SESSION_MAX_AGE_DAYS: int = 30
EXPIRED_PREFIX: str = "[EXPIRED] "
CLOSED_PREFIX: str = "[CLOSED] "
CLEANUP_INTERVAL_HOURS: int = 6
# A session opened without a prompt has nothing to name itself after, so it carries this until the
# first message renames it. Timestamped so a forum full of them can still be told apart.
PLACEHOLDER_TITLE: str = "New Session"
# Thread names are plain text; Discord only renders `<t:...>` markdown inside message content, so
# the placeholder timestamp has to be pre-formatted. We always keep `%Z` so the reader can tell
# whose clock they are looking at.
PLACEHOLDER_TIME_FORMAT: str = "%d/%m %H:%M %Z"
# Discord never tells us a user's timezone. The interaction locale is the only regional hint we get
# and most of it is language-only, so these are the region-less `discord.Locale` values whose
# language maps cleanly onto one country. Anything missing here falls through to UTC.
LOCALE_COUNTRIES: dict[str, str] = {
    "bg": "BG",
    "cs": "CZ",
    "da": "DK",
    "de": "DE",
    "el": "GR",
    "fi": "FI",
    "fr": "FR",
    "hi": "IN",
    "hr": "HR",
    "hu": "HU",
    "it": "IT",
    "ja": "JP",
    "ko": "KR",
    "lt": "LT",
    "nl": "NL",
    "no": "NO",
    "pl": "PL",
    "ro": "RO",
    "th": "TH",
    "tr": "TR",
    "uk": "UA",
    "vi": "VN",
}
# Discord caps thread titles at 100; leave room for whichever dormant prefix we prepend later.
THREAD_TITLE_SIZE: int = 100 - max(len(EXPIRED_PREFIX), len(CLOSED_PREFIX))

# Permissions the bot needs before `/claude ask` can do anything useful. We check these up front so
# the failure is one clear message instead of a Forbidden part way through creating channels.
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
    "embed_links",
)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

CLAUDE_TIMEOUT: int = 900
# Discord's own limit is 2000. The slack covers the separator, the footer and the fence that
# `balance_markup` may add to either end of a chunk.
MESSAGE_CHUNK_SIZE: int = 1800
CODE_FENCE: str = "```"
# Whether to run `repair_code_escapes` over a response before chunking it. Flip to `False` to hand
# Discord the text exactly as the run wrote it; nothing else in the pipeline depends on the repair.
REPAIR_CODE_ESCAPES: bool = True
# One inline code span whose content escapes a backtick the GitHub way. The content alternation cannot
# swallow a bare tick, so the only way to reach the closing one is through the `\`` pairs -- which is
# what stops the lazy match from ending the span on the very tick the author was trying to quote.
ESCAPED_CODE_SPAN: re.Pattern[str] = re.compile(r"(?<!`)`((?:[^`\n\\]|\\.)*?\\`(?:[^`\n\\]|\\.)*?)`(?!`)")
MAX_ATTACHMENT_SIZE: int = 25 * 1024 * 1024  # 25MB
MAX_REPLY_FILES: int = 8
MAX_REPLY_FILE_SIZE: int = 8 * 1024 * 1024  # 8MB; anything larger gets listed by name instead of attached.

# Phrases that mark a CLI failure as "the account is out of budget" rather than a real error. We
# match these case-insensitively against the result text and stderr as a limit can end a run either way.
RATE_LIMIT_MARKERS: tuple[str, ...] = (
    # Covers "usage limit reached" and the CLI's shorter "5-hour limit reached" phrasing.
    "limit reached",
    "rate limit",
    "rate_limit_error",
    "limit will reset",
    "exceeded your account",
    "429",
)

# The CLI's machine-readable form: `Claude AI usage limit reached|1753651800`.
RATE_LIMIT_EPOCH: re.Pattern[str] = re.compile(r"limit reached\|(\d{9,})")

# The human form, as printed by `/usage`: `resets Jul 27, 1:30pm (America/Los_Angeles)`.
RATE_LIMIT_CLOCK: re.Pattern[str] = re.compile(
    r"resets?(?:\s+at)?\s+(?:(\w{3})\s+(\d{1,2}),?\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)?(?:\s*\(([\w/]+)\))?",
    re.IGNORECASE,
)

# Where the CLI installer drops its launcher. `~/.local/bin` is on an interactive shell's PATH via
# `.profile`, but the bot is not always started from one; launched straight off `.venv/bin/python`
# from a service, a cron entry or a detached shell it inherits the bare system PATH and `claude`
# becomes unfindable. We look these up ourselves rather than rely on whatever PATH we were handed.
CLAUDE_FALLBACK_PATHS: tuple[Path, ...] = (
    Path.home().joinpath(".local", "bin", "claude"),
    Path.home().joinpath(".claude", "local", "claude"),
    Path("/usr/local/bin/claude"),
)

DEFAULT_MODEL: str = "claude-sonnet-5"

# Keyed by the short name a user types at `.model`; the value reaches the CLI's `--model`.
MODELS: dict[str, str] = {
    "sonnet": DEFAULT_MODEL,
    "opus": "claude-opus-5",
    "haiku": "claude-haiku-4-5",
    "fable": "claude-fable-5",
}

# The same short names offered on `/claude ask`, so the slash command and `.model` share one vocabulary.
MODEL_CHOICES: list[app_commands.Choice[str]] = [app_commands.Choice(name=name.capitalize(), value=name) for name in MODELS]


@dataclass(frozen=True)
class PermissionMode:
    """One entry in the `.mode` table; how hard the CLI gates and what the session starts out holding.

    .. note::
        A mode's `tools` **grant**, they do not confine. Tested against `claude 2.1.220`; a run given
        `--allowed-tools Read,Edit,Write,Grep,Glob` ran Bash anyway, which is why we hand these over as
        `--settings` allow rules instead. An allow rule is just as additive though, so leaving a tool
        out of this tuple does not withhold it. Narrowing a session is the permission mode's job as
        `default` and `manual` refuse anything ungranted, and the only way to remove a tool outright is
        a `deny` rule, which we never write. See :meth:`ClaudeCog.build_command`.

    Attributes
    ----------
    value: :class:`str`
        The `--permission-mode` value handed to the CLI.
    description: :class:`str`
        The one line shown beside the mode in the panel's select.
    tools: :class:`tuple[str, ...]`
        Permission rules pre-granted to a session in this mode, in `.claude/settings.json` syntax, sent
        as `--settings`. Only applied when the user has not set their own list with `.tools`. Empty
        means the session starts with nothing beyond what the mode and the project settings give it.

    """

    value: str
    description: str
    tools: tuple[str, ...] = field(default=())


# Keyed by the short name a user types at `.mode`; the value reaches the CLI's `--permission-mode`.
# `bypass` is left out on purpose; `ClaudeCog.modes_for` merges it in per user.
#
# The CLI also accepts `auto` and `dontAsk`. Both are omitted as their behaviour isn't documented well
# enough for us to describe it on a select. `manual` stays because it was tested; it denies instead of
# asking, which is what the denial retry below is built around.
#
# The presets are kept thin. The CLI's sandbox auto-approves read-only shell no matter what the rules
# say, so `git status`, `git log`, `git diff` and the like all run ungranted under `manual` and
# pre-granting them only adds noise to the panel. Only commands that actually write get an entry;
# anything wider belongs behind `.tools` where the user picked it themselves.
MODES: dict[str, PermissionMode] = {
    "plan": PermissionMode(value="plan", description="Read-only. Plans an approach, touches nothing."),
    "edits": PermissionMode(
        value="acceptEdits",
        description="Auto-approves file edits inside the project.",
        # `acceptEdits` covers the file tools but not the shell, and `ruff format` rewrites files so
        # it doesn't get auto-approved the way `ruff check` does. We grant the pair together; a session
        # told to format and then stopped halfway is worse than granting neither.
        #
        # Both invocations are listed as these are prefix rules over the literal command string.
        # `Bash(ruff check:*)` does not match `.venv/bin/ruff check`; tested. Which form a session
        # reaches for depends on whether the bot's environment has the venv on PATH, so cover both.
        tools=(
            "Bash(ruff check:*)",
            "Bash(ruff format:*)",
            "Bash(.venv/bin/ruff check:*)",
            "Bash(.venv/bin/ruff format:*)",
        ),
    ),
    "manual": PermissionMode(value="manual", description="Denies anything ungranted and reports it back."),
    "default": PermissionMode(value="default", description="Prompts per tool - the CLI's own default."),
}

# Merged into the offered modes per user by `ClaudeCog.modes_for`, so it lives outside `MODES`.
BYPASS: PermissionMode = PermissionMode(value="bypassPermissions", description="No approval gate on any tool. Use with care.")
BYPASS_MODE: str = BYPASS.value

# ---------------------------------------------------------------------------
# Deny rules
# The one thing in the CLI's permission model that actually confines rather than grants. Tested
# against `claude 2.1.220`:
#
# - A `deny` rule beats `acceptEdits`, beats an explicit `allow` of the same tool, and beats
#   `bypassPermissions`. Nothing a user can reach from Discord overrides one.
# - Unlike the path-scoped *allow* rules noted in `ClaudeCog.build_command`, path-scoped *deny*
#   rules do work through inline `--settings`, in relative (`local.ini`), glob (`.git/**`) and
#   absolute (`//home/kat/...`) form alike. Relative is used below so the rules follow the cwd.
# - A denied call is reported to the model but produces **no** `permission_denials` entry, so the
#   retry offer in `ClaudeCog.offer_retry` never sees one. That is deliberate here: these are not
#   things a user should be offered a button to grant themselves.
#
# The cwd is the first line of defence and these are the second, covering what a session can still
# reach *inside* its own cwd. See `SessionState.cwd`.
# ---------------------------------------------------------------------------

# Applied when the cwd is the session's own workspace. The user owns everything in there, so the
# only things withheld are our own bookkeeping; a rewritten `session.json` or a doctored transcript
# snapshot would corrupt session recovery, and neither is the user's to edit.
WORKSPACE_DENY: tuple[str, ...] = (
    f"Edit({SESSION_INDEX_NAME})",
    f"Write({SESSION_INDEX_NAME})",
    "Edit(transcript-*.jsonl.gz)",
    "Write(transcript-*.jsonl.gz)",
)

# Applied when the cwd is the project root, which is the elevated group only. Everything here is
# either a credential, something that executes, or something whose corruption takes the bot down.
# `.git/**` is on the list because a writable `.git/hooks/` is arbitrary code execution on the next
# git command, which would walk straight around every other rule in this table.
PROJECT_DENY: tuple[str, ...] = (
    # Credentials and the account's own state.
    "Read(local.ini)",
    "Edit(local.ini)",
    "Write(local.ini)",
    "Read(.env)",
    "Read(**/.env)",
    "Read(logs/**)",
    # The bot's databases; a truncated write here is not recoverable, and `claude_access` lives in
    # there, so a writable database is a way to hand out elevation.
    "Edit(*.sqlite*)",
    "Write(*.sqlite*)",
    # Deny rules are scoped to one tool, so the two `*.sqlite*` rules above only bind `Edit` and
    # `Write` -- a shell client reaches the same file untouched by them. The CLI's sandbox confines
    # Bash to the cwd, which for an elevated session *is* the directory holding the database.
    "Bash(sqlite3:*)",
    "Bash(litecli:*)",
    # Anything that runs. `kuma_kuma.py` and `Kuma.bash` are the entry points, `.git/hooks/` runs on
    # the next git command, and `.claude/` steers any interactive session opened in this repo later.
    "Edit(.git/**)",
    "Write(.git/**)",
    "Edit(.claude/**)",
    "Write(.claude/**)",
    "Edit(kuma_kuma.py)",
    "Write(kuma_kuma.py)",
    "Edit(Kuma.bash)",
    "Write(Kuma.bash)",
    # The instructions every session in this repo is handed. A session that can rewrite these can
    # change how the *next* session behaves, which outlasts the run that did it.
    "Edit(CLAUDE.md)",
    "Write(CLAUDE.md)",
    "Edit(**/CLAUDE.md)",
    "Write(**/CLAUDE.md)",
)


def foreign_workspace_deny(*, state: SessionState) -> list[str]:
    """Returns deny rules covering every session workspace except this session's owner's.

    Elevated is a licence to work on the project, not to read someone else's conversation out of
    their workspace. A blanket `.claude_sessions/**` rule cannot express that — deny beats allow, so
    there is no way to carve an exception back out — hence one rule per user directory that is
    actually on disk, skipping the owner's own.

    Parameters
    ----------
    state: :class:`SessionState`
        The session being run; its owner is the one directory left reachable.

    Returns
    -------
    :class:`list[str]`
        Deny rules, relative to the project root. Empty when the session is the only one on disk.

    """
    sessions: Path = sessions_root(root=state.root)
    if not sessions.is_dir():
        return []

    owner: str = str(state.user_id)
    rules: list[str] = []
    for entry in sorted(sessions.iterdir()):
        # Only the per-user directories; `.gitignore` and any stray file are not worth a rule.
        if not entry.is_dir() or entry.name == owner:
            continue
        target: str = f"{WORKSPACE_DIRNAME}/{entry.name}/**"
        rules += [f"Read({target})", f"Edit({target})", f"Write({target})"]
    return rules


# `/claude ask allow_edits=False` (the default) plans instead of touching files.
DEFAULT_MODE: str = MODES["plan"].value

# The name a user types at `.effort` is the value the CLI's `--effort` takes, so this is a set
# rather than a mapping. Unlike `.model` and `.mode` there is no short name to translate.
EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

# What each level buys, shown by `.effort` with no argument and on the panel's select.
EFFORT_DESCRIPTIONS: dict[str, str] = {
    "low": "Answers directly; fewest tool calls.",
    "medium": "Balanced - a good default for chat.",
    "high": "Explores more before answering.",
    "xhigh": "Best for coding and agentic work.",
    "max": "Deepest reasoning; slowest and priciest.",
}

# Matches `effortLevel` in the host account's `~/.claude/settings.json`, which is what the CLI has
# been inheriting all along, so a session left alone runs exactly as it did before `.effort` existed.
# We pass it explicitly instead of omitting the flag to keep the panel honest; the level shown is
# always the level used, even if the settings file changes under us.
DEFAULT_EFFORT: str = "medium"

# Slash commands forwarded to the CLI verbatim as prompt text. Kept to a fixed set so a dot command
# can never reach an arbitrary entry in `.claude/commands/`.
PASSTHROUGH_COMMANDS: frozenset[str] = frozenset(
    {
        "review",
        "security-review",
        "init",
        "cost",
        "context",
        "todos",
        "pr-comments",
        # The CLI answers this one locally with no model call, no turns and no cost, so anyone in a
        # session can safely run it. NOTE: the figures are for the *host account*, not the caller.
        "usage",
    }
)

# `--allowed-tools` is user supplied, so it is length capped and pattern checked before it is handed
# to the CLI.
TOOL_SPEC_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\([^()`$;|&\n]{0,120}\))?$")
MAX_ALLOWED_TOOLS: int = 20
MAX_TOOL_SPEC_LENGTH: int = 140

# ---------------------------------------------------------------------------
# Tool activity
# What the thread shows while a run is in flight. Tool *names and targets* only; tool results are
# roughly a third of a transcript by volume and routinely contain whole files, so echoing them into
# Discord would be unreadable and would leak file contents into a channel.
# ---------------------------------------------------------------------------

# CLI tool name -> the verb shown in the status line, inflected to a participle by `to_progressive`.
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
# Tool input keys worth showing, most specific first. The first one present becomes the target.
TOOL_TARGET_KEYS: tuple[str, ...] = ("file_path", "notebook_path", "command", "pattern", "url", "query", "description", "prompt")
TOOL_TARGET_SIZE: int = 60
# We only keep the tail of the log on screen; the rest gets summarised as a count.
TOOL_LOG_VISIBLE: int = 12
PENDING_MARK: str = "▸"
DONE_MARK: str = "✓"

# Appended to every prompt so file output never escapes the session's own workspace.
OUTPUT_DIR_NOTICE: str = (
    "\n\n[Any file you create, download or write out for this request must live inside the `{directory}/` "
    "directory of this project. Create it if it does not exist. Do not write generated files anywhere else.]"
)

# ---------------------------------------------------------------------------
# Session state
# The thread's opening post is our only source of truth. It's a Components V2 panel whose small-text
# line below carries the machine readable state, so a bot restart re-reads it from Discord instead
# of a database.
# ---------------------------------------------------------------------------

# We write the owner out instead of taking `Thread.owner_id`. The *bot* creates these posts, so
# Discord records the bot as their owner and every ownership check would pass for anyone.
# One key per line; Discord renders `-#` per line, so every line needs its own marker to stay small.
STATE_LINE_FORMAT: str = (
    "-# session `{session_id}`\n-# model `{model}` · mode `{mode}` · effort `{effort}`\n-# owner {user_id} · started {started}"
)
# Mirrors :attr:`STATE_LINE_FORMAT`; the two are edited together or a session stops being readable.
# The `project` line is the one exception: it was dropped with the project selector, and posts
# written before that still carry it, so it is matched optionally and thrown away. Sessions open at
# the time of the change would otherwise stop parsing and become unrecoverable. Safe to delete once
# no live post predates it.
STATE_LINE_PATTERN: re.Pattern[str] = re.compile(
    r"session `(?P<session_id>[^`]+)`(?:\n-# project `[^`]+`)?"
    r"\n-# model `(?P<model>[^`]+)` · mode `(?P<mode>[^`]+)` · effort `(?P<effort>[^`]+)`"
    r"\n-# owner <@!?(?P<user_id>\d+)>",
)

# Discord does not render a markdown horizontal rule in a message, so we draw the break under a
# reply literally. Small text keeps it a hairline instead of a bar competing with the answer.
REPLY_SEPARATOR: str = f"-# {'─' * 30}"


class SessionStatus(StrEnum):
    """How a session post is being rendered; the thread title prefix is the stored form.

    `CLOSED` and `EXPIRED` are both dormant and both restorable. We keep them apart because the
    reason matters to the reader. A closed session was ended on purpose and its transcript is
    almost certainly still on disk, whereas an expired one aged out and may have nothing left
    to resume.
    """

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
SESSION_MARKERS: dict[SessionStatus, str] = {
    SessionStatus.ACTIVE: "💬",
    SessionStatus.CLOSED: "⏹️",
    SessionStatus.EXPIRED: "🕰️",
}
PANEL_NOTICES: dict[SessionStatus, str] = {
    SessionStatus.ACTIVE: "Reply in this post to continue the session. Type `.help` for the in-thread commands.",
    SessionStatus.CLOSED: (
        "This session was closed from its panel. Its files are kept until you delete the post, and "
        "**Restore Session** picks it back up from where it left off."
    ),
    SessionStatus.EXPIRED: (
        f"This session passed {SESSION_MAX_AGE_DAYS} days without activity, so Claude Code has dropped its "
        "transcript and it can no longer be resumed. Its files are kept until you delete the post."
    ),
}

# Custom IDs are static so the panel survives a restart as a persistent view. We resolve the session
# it acts on from the interaction's own thread instead of baking it into the ID.
PANEL_MODEL_ID: str = "claude:panel:model"
PANEL_MODE_ID: str = "claude:panel:mode"
PANEL_EFFORT_ID: str = "claude:panel:effort"
PANEL_FILES_ID: str = "claude:panel:files"
PANEL_NEW_ID: str = "claude:panel:new"
PANEL_HELP_ID: str = "claude:panel:help"
PANEL_CLOSE_ID: str = "claude:panel:close"
PANEL_RESTORE_ID: str = "claude:panel:restore"

# Numeric component IDs. Unrelated to the custom IDs above; these address a *layout* part rather than
# an interactive one, so the panel can be read back by pointing at a component instead of pattern
# matching every piece of text in the payload. See `parse_state`.
PANEL_HEADER_COMPONENT_ID: int = 1
PANEL_STATE_COMPONENT_ID: int = 2
PANEL_TRANSCRIPT_COMPONENT_ID: int = 3

# Discord's ceilings on a single Components V2 view; the character budget is shared across every text
# display in the view, and the component count includes everything nested. We are well inside both,
# but the notices, the control summaries and the project path all grow, and going over means a 400
# that would lose the post; which is the only record a session has.
PANEL_CONTENT_LIMIT: int = 4000
PANEL_COMPONENT_LIMIT: int = 40

# The panel's artwork, served from the emoji CDN rather than uploaded. `SessionPanel` re-renders on
# every select change and `Message.edit` replaces the whole attachment list, so an `attachment://`
# thumbnail would mean re-uploading the image alongside the transcript on each edit. See
# :meth:`KumaEmojiTable.to_cdn_url`.
PANEL_THUMBNAIL_EMOJI: str = "kuma_peak"
PANEL_THUMBNAIL_ALT: str = "Kuma Kuma Bear, peeking."

# The heading shown above each of the panel's selects, in render order.
PANEL_MODEL_TITLE: str = "Model"
PANEL_MODE_TITLE: str = "Permission Mode"
PANEL_EFFORT_TITLE: str = "Effort"

# How long the "allow and retry" button under an answer stays live and how many denied tools we list
# before summarising the rest. The timeout is generous as a denial often gets read long after the run
# finished, but it stays bounded since the view cannot survive a restart either way.
DENIAL_RETRY_TIMEOUT: float = 3600.0
MAX_DENIALS_SHOWN: int = 8


async def _is_owner(interaction: discord.Interaction) -> bool:
    """Returns whether the invoking user is a bot owner; the check guarding every `/claude` command."""
    return await interaction.client.is_owner(interaction.user)  # type: ignore[arg-type]


def claude_binary() -> Optional[str]:
    """Returns the path to the `claude` executable, or `None` when it cannot be found.

    We resolve this ourselves instead of handing the bare name to the subprocess. A process launched
    outside an interactive shell inherits a PATH without `~/.local/bin` on it, which is where the CLI
    installs, so `claude` alone raises `FileNotFoundError` on a machine that has it installed and
    working. PATH still gets first say for anyone running a version from elsewhere.

    Resolved per call rather than at import; the CLI is upgraded in place often enough that a bot left
    running for weeks should not be holding a path it looked up once at startup.
    """
    found: Optional[str] = shutil.which("claude")
    if found is not None:
        return found

    # `os.access` rather than `is_file`, as these are the installer's launchers and a symlink to a
    # version that has since been pruned still answers `is_file` on the dangling name.
    return next((str(path) for path in CLAUDE_FALLBACK_PATHS if os.access(path, os.X_OK)), None)


# ---------------------------------------------------------------------------
# Paths and workspace helpers
# ---------------------------------------------------------------------------


def sessions_root(*, root: Path) -> Path:
    """Returns the `.claude_sessions/` directory inside a project root."""
    return root.joinpath(WORKSPACE_DIRNAME)


def session_dir(*, root: Path, user_id: int, thread_id: int) -> Path:
    """Returns a session's workspace of `<root>/.claude_sessions/<user_id>/<thread_id>/`."""
    return sessions_root(root=root).joinpath(str(user_id), str(thread_id))


def attachments_dir(*, root: Path, user_id: int, thread_id: int) -> Path:
    """Returns the attachment directory inside a session's workspace."""
    return session_dir(root=root, user_id=user_id, thread_id=thread_id).joinpath(ATTACHMENTS_SUBDIR)


def prepare_workspace(*, root: Path, directory: Path) -> None:
    """Creates a directory inside a session workspace and makes `.claude_sessions/` self-ignoring.

    The workspace has to sit inside the session's own repo so the CLI can write to it, which would
    otherwise leave an untracked directory in every project we open a session against. A `.gitignore`
    of `*` at the top of `.claude_sessions/` hides it from that repo's git without us touching the
    repo's own ignore file.

    Parameters
    ----------
    root: :class:`Path`
        The session's project root; anchors where the `.gitignore` is written.
    directory: :class:`Path`
        The directory to create; a session workspace or its `attachments/` subdirectory.

    """
    directory.mkdir(parents=True, exist_ok=True)
    ignore_file: Path = sessions_root(root=root).joinpath(".gitignore")
    if not ignore_file.exists():
        ignore_file.write_text(data=WORKSPACE_GITIGNORE, encoding="utf-8")


def tool_target(*, name: str, tool_input: dict, root: Path) -> str:
    """Returns a short, single line description of what a tool call is acting on.

    We show paths relative to the session root so a line reads `Read utils/ui.py` instead of an
    absolute path that wraps. Everything else gets collapsed to one line and truncated.

    Parameters
    ----------
    name: :class:`str`
        The tool name, used to special-case a couple of shapes.
    tool_input: :class:`dict`
        The tool's input object as the CLI reported it.
    root: :class:`Path`
        The session's project root, for relativising paths.

    Returns
    -------
    :class:`str`
        The target description; empty when the tool takes nothing worth showing.

    """
    value: str = ""
    for key in TOOL_TARGET_KEYS:
        candidate: Any = tool_input.get(key)
        if isinstance(candidate, str) and candidate.strip():
            value = candidate.strip()
            break

    if not value:
        # `TodoWrite` and the like carry a list instead of a string, so we say how much, not what.
        for candidate in tool_input.values():
            if isinstance(candidate, list) and candidate:
                return f"{len(candidate)} item(s)"
        return ""

    if name in {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit"}:
        with contextlib.suppress(ValueError):
            value = Path(value).resolve().relative_to(root.resolve()).as_posix()

    value = " ".join(value.split())
    if len(value) > TOOL_TARGET_SIZE:
        return f"{value[: TOOL_TARGET_SIZE - 1]}…"
    return value


def transcript_slug(cwd: Path) -> str:
    """Returns the CLI's transcript directory name for a working directory.

    Keyed on the **working directory**, not the project root. Those are the same thing only for an
    elevated session; a standard one runs inside its own workspace and the CLI files its transcript
    under that instead. See :attr:`TRANSCRIPT_SLUG_TABLE`.
    """
    return str(cwd.resolve()).translate(str.maketrans(TRANSCRIPT_SLUG_TABLE))


def snapshot_path(*, workspace: Path, session_id: str) -> Path:
    """Returns where a session's transcript snapshot lives inside its workspace."""
    return workspace.joinpath(TRANSCRIPT_NAME.format(session_id=re.sub(r"[^\w.\-]", "_", session_id)))


def live_transcript(*, cwd: Path, session_id: str) -> Path:
    """Returns where Claude Code keeps a session's own transcript, which is what `--resume` reads."""
    return CLAUDE_PROJECTS.joinpath(transcript_slug(cwd), f"{session_id}.jsonl")


def session_is_known(*, state: SessionState) -> bool:
    """Returns whether Claude Code has a transcript for this session, i.e. whether `--resume` will work.

    We ask the filesystem instead of tracking a flag as the answer has to survive a bot restart and,
    more importantly, has to be *right* after things go wrong. A session whose opening run never
    finished (the CLI missing, a timeout, an early error, a `.cancel`) was never registered CLI-side,
    so resuming it would fail on every message from then on.

    Looked up under :attr:`SessionState.cwd`, which is where the CLI actually files it. Keying this on
    the project root instead is what made every standard session look unknown on every turn, so each one
    re-claimed its own session ID with `--session-id` and the CLI answered that the ID was already in
    use.

    Parameters
    ----------
    state: :class:`SessionState`
        The session to check.

    Returns
    -------
    :class:`bool`
        `True` when a transcript exists and the session can be resumed.

    """
    return live_transcript(cwd=state.cwd, session_id=state.session_id).is_file()


def adopt_transcript(*, state: SessionState) -> bool:
    """Moves a session's transcript to its current working directory when its tier changed under it.

    A transcript is filed under the cwd of the run that wrote it, so granting or revoking elevation
    moves where the CLI will look for it next turn. Without this the conversation would still exist,
    :func:`session_is_known` would say `False`, and the session would try to re-claim its own ID and be
    told the ID is in use -- the same failure as the root-versus-cwd bug, from the other direction.

    Parameters
    ----------
    state: :class:`SessionState`
        The session to repair; its current :attr:`SessionState.cwd` is the destination.

    Returns
    -------
    :class:`bool`
        Whether a transcript was carried over.

    """
    target: Path = live_transcript(cwd=state.cwd, session_id=state.session_id)
    if target.is_file():
        return False

    # The only two directories a session can ever have run in.
    other: Path = state.workspace if state.cwd == state.root else state.root
    source: Path = live_transcript(cwd=other, session_id=state.session_id)
    if not source.is_file():
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    LOGGER.info(
        "<%s> | Carried a session transcript across a tier change. | Session: %s | From: %s | To: %s",
        "adopt_transcript",
        state.session_id,
        source.parent.name,
        target.parent.name,
    )
    return True


def prepare_resume(*, state: SessionState) -> bool:
    """Returns whether the next run may `--resume`, carrying the transcript over first if it has to."""
    adopt_transcript(state=state)
    return session_is_known(state=state)


def snapshot_transcript(*, state: SessionState) -> Optional[int]:
    """Copies a session's live transcript into its workspace, gzipped.

    Skipped when the transcript is missing (already pruned, or the session never ran) or when the
    existing snapshot is already at least as new. A snapshot is worth taking once per burst of
    activity, not once per sweep.

    Parameters
    ----------
    state: :class:`SessionState`
        The session whose transcript to copy; supplies the cwd it was filed under and the workspace to
        copy it into.

    Returns
    -------
    :class:`Optional[int]`
        The compressed size in bytes, or `None` when nothing was copied.

    """
    source: Path = live_transcript(cwd=state.cwd, session_id=state.session_id)
    if not source.is_file():
        return None

    workspace: Path = state.workspace
    target: Path = snapshot_path(workspace=workspace, session_id=state.session_id)
    if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return None

    # The project root, not the cwd: this is what anchors the self-ignoring `.gitignore` above the
    # per-user directories, and that sits at `<root>/.claude_sessions/` whatever the tier.
    prepare_workspace(root=state.root, directory=workspace)
    # Transcripts run from a few hundred KB to several MB and are almost all repeated JSON keys, so
    # they compress by roughly an order of magnitude. Worth the CPU to keep the workspace small.
    with source.open("rb") as raw, gzip.open(target, "wb") as compressed:
        shutil.copyfileobj(raw, compressed)
    return target.stat().st_size


def restore_transcript(*, state: SessionState) -> bool:
    """Puts a snapshotted transcript back where the CLI expects it, making `--resume` work again.

    Parameters
    ----------
    state: :class:`SessionState`
        The session to restore; the snapshot comes from its workspace and is written back under its
        current cwd.

    Returns
    -------
    :class:`bool`
        Whether a snapshot was found and written back.

    """
    source: Path = snapshot_path(workspace=state.workspace, session_id=state.session_id)
    if not source.is_file():
        return False

    target: Path = live_transcript(cwd=state.cwd, session_id=state.session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(source, "rb") as compressed, target.open("wb") as raw:
        shutil.copyfileobj(compressed, raw)
    return True


def session_index_path(*, workspace: Path) -> Path:
    """Returns where a session's sidecar index lives inside its workspace."""
    return workspace.joinpath(SESSION_INDEX_NAME)


def read_session_index(*, workspace: Path) -> Optional[SessionIndex]:
    """Reads a session's sidecar index, or `None` when there isn't a readable one.

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
        # A truncated write, a hand-edit, a deleted workspace. None of those are worth raising over:
        # the sidecar is a convenience mirror and Discord still holds the real state.
        if isinstance(e, ValueError):
            LOGGER.warning("<%s> | Ignoring an unreadable session index at %s | Error: %s", "read_session_index", source, e)
        return None

    if not isinstance(payload, dict) or not payload.get("session_id"):
        return None
    return cast("SessionIndex", payload)


def write_session_index(*, state: SessionState) -> bool:
    """Mirrors a session's identity to disk beside its workspace, so it survives losing Discord.

    The thread's opening post stays the source of truth -- see :data:`STATE_LINE_FORMAT`. This is a
    *mirror* of it, written at the same moment, and nothing reads it in preference to the post. What
    it buys is findability: without it the CLI's transcripts are a directory of bare UUIDs with
    nothing on disk tying any of them to a Discord thread, so a post that is unreachable, deleted or
    re-keyed leaves a fully intact transcript that cannot be located.

    :attr:`SessionIndex.lineage` is the part that earns its keep. `.new` and `.restore` mint a fresh
    session ID onto the same thread, which otherwise orphans the previous transcript silently; the
    superseded ID is pushed here instead, most recent first.

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
        # on every panel edit.
        if superseded and superseded != state.session_id and superseded not in lineage:
            lineage.insert(0, superseded)
        del lineage[SESSION_LINEAGE_LIMIT:]

    payload: SessionIndex = {
        "thread_id": state.thread_id,
        "owner_id": state.user_id,
        "session_id": state.session_id,
        "lineage": lineage,
        "root": str(state.root),
        "model": state.model,
        "mode": state.mode,
        "effort": state.effort,
        "started": state.started,
        "updated": time.time(),
    }

    try:
        workspace.mkdir(parents=True, exist_ok=True)
        # Written to a neighbour and renamed. `Path.replace` is atomic within a filesystem, so a crash
        # mid-write leaves the previous index intact rather than a half-file -- which matters here
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

    Parameters
    ----------
    thread_id: :class:`int`
        The thread to look for.

    Returns
    -------
    :class:`Optional[SessionIndex]`
        The recorded index, or `None` when no workspace claims that thread.

    """
    sessions: Path = sessions_root(root=PROJECT_ROOT)
    if not sessions.is_dir():
        return None

    # `<sessions>/<user_id>/<thread_id>/`. Globbing the thread ID rather than walking every user
    # keeps this cheap no matter how many people have sessions open.
    for workspace in sessions.glob(f"*/{thread_id}"):
        index: Optional[SessionIndex] = read_session_index(workspace=workspace)
        if index is not None:
            return index
    return None


def is_inside(path: Path, root: Path) -> bool:
    """Returns whether `path` resolves to `root` or something beneath it.

    We resolve first so `..` segments and symlinks get collapsed before the comparison; checking the
    unresolved path would let `workspace/../../etc/passwd` through.

    Parameters
    ----------
    path: :class:`Path`
        The path to test.
    root: :class:`Path`
        The directory the path must stay within.

    Returns
    -------
    :class:`bool`
        `True` when the resolved path is `root` or lives beneath it.

    """
    try:
        resolved: Path = path.resolve()
        base: Path = root.resolve()
    except OSError:
        return False
    return resolved == base or base in resolved.parents


def _dir_snapshot(*, directory: Path) -> dict[Path, float]:
    """Returns the modified time of every file currently inside `directory`, keyed by path."""
    if not directory.is_dir():
        return {}
    return {entry: entry.stat().st_mtime for entry in directory.rglob("*") if entry.is_file()}


def _new_files(*, directory: Path, before: dict[Path, float]) -> list[Path]:
    """Returns any file inside `directory` created or touched since `before` was taken, newest first.

    We skip anything the user uploaded themselves. Those live under `attachments/` and were already
    shown in Discord, so re-attaching them to the reply would just echo them back.

    Parameters
    ----------
    directory: :class:`Path`
        The session workspace to scan.
    before: :class:`dict[Path, float]`
        A snapshot from :func:`_dir_snapshot` taken prior to the CLI run.

    Returns
    -------
    :class:`list[Path]`
        The changed files, newest first.

    """
    after: dict[Path, float] = _dir_snapshot(directory=directory)
    changed: list[Path] = [
        path
        for path, mtime in after.items()
        if before.get(path, 0.0) < mtime and ATTACHMENTS_SUBDIR not in path.relative_to(directory).parts
    ]
    changed.sort(key=lambda path: after[path], reverse=True)
    return changed


def _dir_stats(*, directory: Path) -> tuple[int, int]:
    """Returns the file count and total byte size of `directory`; `(0, 0)` when it does not exist."""
    if not directory.is_dir():
        return 0, 0
    files: list[Path] = [entry for entry in directory.rglob("*") if entry.is_file()]
    return len(files), sum(entry.stat().st_size for entry in files)


def _list_workspace(*, directory: Path) -> list[tuple[str, int]]:
    """Returns `(relative posix path, size in bytes)` for every file in a session workspace, newest first."""
    if not directory.is_dir():
        return []
    files: list[Path] = [entry for entry in directory.rglob("*") if entry.is_file()]
    files.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    return [(entry.relative_to(directory).as_posix(), entry.stat().st_size) for entry in files]


def _build_reply_files(*, paths: list[Path]) -> tuple[list[discord.File], list[str]]:
    """Turns generated file paths into attachments, skipping anything too large for Discord.

    Parameters
    ----------
    paths: :class:`list[Path]`
        The files to attach, in priority order.

    Returns
    -------
    :class:`tuple[list[discord.File], list[str]]`
        The attachable files and the display names of everything that was skipped.

    """
    files: list[discord.File] = []
    skipped: list[str] = []
    for path in paths:
        if len(files) >= MAX_REPLY_FILES or path.stat().st_size > MAX_REPLY_FILE_SIZE:
            skipped.append(path.name)
            continue
        files.append(discord.File(fp=path, filename=path.name))
    return files, skipped


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _widen_code_span(match: re.Match[str]) -> str:
    """Returns one escaped inline span rewritten as a double-tick span holding real backticks."""
    content: str = match[1].replace("\\`", "`")
    # Padding is not cosmetic. A tick against either wall would give Discord three in a row, which it
    # reads as a fence opening rather than a span, and the rest of the message goes into a code box.
    if content.startswith("`") or content.endswith("`"):
        content = f" {content} "
    return f"``{content}``"


def repair_code_escapes(text: str) -> str:
    """Returns `text` with GitHub-style escaped backticks inside inline code rewritten for Discord.

    Discord takes every character inside a code span literally, backslashes included, so escaping a
    backtick the GitHub way does not quote it -- the span closes on that tick regardless, and what was
    meant as one code phrase renders as two boxes with the slashes showing and the middle of it as
    prose. The construct Discord *does* understand is a double-tick span, which may hold a bare
    backtick, so that is what we rewrite to.

    Fenced blocks are skipped. Their content is already literal, so nothing needs repairing, and a
    backslash in there far more likely belongs to the code being shown than to a markdown escape.

    Parameters
    ----------
    text: :class:`str`
        The response text, before it is split into chunks.

    Returns
    -------
    :class:`str`
        The same text with any escaped inline spans widened; unchanged if there were none.

    """
    lines: list[str] = text.split("\n")
    repaired: list[str] = []
    in_block: bool = False

    for line in lines:
        # Matched the way `balance_markup` does it, on the stripped form, so an indented fence counts.
        if line.strip().startswith(CODE_FENCE):
            in_block = not in_block
            repaired.append(line)
            continue
        repaired.append(line if in_block else ESCAPED_CODE_SPAN.sub(_widen_code_span, line))
    return "\n".join(repaired)


def balance_markup(text: str, *, carried_language: Optional[str] = None) -> tuple[str, Optional[str]]:
    """Closes any code fence or inline code span `text` leaves hanging, and reopens a carried one.

    Discord renders each message on its own, so a fence opened in one chunk and closed in the next
    shows up as literal backticks in the first and a wall of unformatted text in the second. The
    chunker prefers to split on a fence boundary, so we hit this every time the boundary it picks is
    a *closing* fence. Repairing per chunk keeps every message well formed on its own, at the cost of
    a visible seam where a long code block spans two messages.

    Parameters
    ----------
    text: :class:`str`
        The chunk to repair.
    carried_language: :class:`Optional[str]`, optional
        The info string of a fence left open by the previous chunk, reopened at the top of this one.

    Returns
    -------
    :class:`tuple[str, Optional[str]]`
        The repaired chunk, and the language of the fence it leaves open for the next chunk.

    """
    lines: list[str] = text.split("\n")

    # The chunker splits *before* a fence, so when the fence it picked was a closing one this chunk
    # opens with the tail of a block the previous chunk already had to close. Reopening it would
    # render an empty code box at the top of the message, so we drop the orphan instead.
    if carried_language is not None and lines and lines[0].strip() == CODE_FENCE:
        lines = lines[1:]
        carried_language = None

    language: Optional[str] = carried_language
    repaired: list[str] = []
    # The reopening fence goes straight into the output instead of through the loop, which would
    # otherwise read it as a *closing* fence and treat the rest of the chunk as prose.
    if carried_language is not None:
        repaired.append(f"{CODE_FENCE}{carried_language}")

    for line in lines:
        stripped: str = line.strip()
        if stripped.startswith(CODE_FENCE):
            # A fence line either opens a block (and names its language) or closes the open one.
            # We match on the stripped form to mirror Discord, which honours an indented fence too.
            language = None if language is not None else stripped[len(CODE_FENCE) :].strip()
            repaired.append(line)
            continue

        # Inside a block every character is literal, so only prose can have a dangling inline span.
        # An odd backtick count means a hard cut landed inside one and closing it beats leaking a
        # tick. We skip prose that *quotes* a fence mid-line; those ticks are not a span and
        # "repairing" the parity would append a stray tick to an otherwise fine sentence.
        if language is None and CODE_FENCE not in line and line.count("`") % 2:
            line += "`"  # noqa: PLW2901 - Repairing the line in place is the point.
        repaired.append(line)

    if language is not None:
        repaired.append(CODE_FENCE)
    return "\n".join(repaired), language


def chunk_text(text: str, size: int = MESSAGE_CHUNK_SIZE) -> list[str]:
    """Returns the response split into message-sized chunks, preferring natural break points.

    We split at code-fence boundaries first, then double newlines, then single newlines, and only
    fall back to a hard cut when none of those land in the second half of the window. That keeps a
    fence from being torn in half across two messages.

    The text first runs through :func:`repair_code_escapes` unless :attr:`REPAIR_CODE_ESCAPES` says
    otherwise, then every chunk runs through :func:`balance_markup`, so a block the split *did* land
    inside gets closed off and reopened instead of rendering as raw backticks on one side and
    unformatted text on the other.

    Parameters
    ----------
    text: :class:`str`
        The full response text to split.
    size: :class:`int`, optional
        The maximum characters per chunk, by default :attr:`MESSAGE_CHUNK_SIZE`.

    Returns
    -------
    :class:`list[str]`
        The chunks in order; empty chunks are dropped.

    """
    # Before the split, so a span the repair widens is measured at the width it will actually be sent
    # at. Drop the flag and the chunker neither knows nor cares.
    if REPAIR_CODE_ESCAPES:
        text = repair_code_escapes(text)

    chunks: list[str] = []
    while text:
        if len(text) <= size:
            chunks.append(text)
            break

        window: str = text[:size]
        split_at: int = size
        for marker in ("```", "\n\n", "\n"):
            marker_index: int = window.rfind(marker)
            if marker_index > size // 2:
                split_at = marker_index
                break

        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()

    # We repair after the split, not during, to keep the split logic honest about where the seams are.
    balanced: list[str] = []
    carried: Optional[str] = None
    for chunk in chunks:
        if not chunk:
            continue
        repaired, carried = balance_markup(chunk, carried_language=carried)
        balanced.append(repaired)
    return balanced


def is_rate_limited(text: str) -> bool:
    """Returns whether `text` reads as a usage/rate limit rejection rather than a genuine failure."""
    lowered: str = text.lower()
    return any(marker in lowered for marker in RATE_LIMIT_MARKERS)


def parse_reset_time(text: str) -> Optional[datetime.datetime]:
    """Returns when the exhausted limit resets, as read out of the CLI's error text.

    We understand two forms; the machine one the CLI appends to its limit error (`...reached|<epoch>`)
    and the human one `/usage` prints (`resets Jul 27, 1:30pm (America/Los_Angeles)`). The human form
    is the fragile one as it never prints a year and may leave the date out entirely, so we take both
    from the parsed zone's today. A bare time already behind us gets rolled forward a day since a
    reset is by definition still ahead.

    Parameters
    ----------
    text: :class:`str`
        The raw CLI error or usage text to read.

    Returns
    -------
    :class:`Optional[datetime.datetime]`
        The timezone-aware reset time, or `None` when no time could be read.

    """
    epoch_match: Optional[re.Match[str]] = RATE_LIMIT_EPOCH.search(text)
    if epoch_match is not None:
        with contextlib.suppress(ValueError, OSError, OverflowError):
            return datetime.datetime.fromtimestamp(int(epoch_match.group(1)), tz=datetime.UTC)

    clock_match: Optional[re.Match[str]] = RATE_LIMIT_CLOCK.search(text)
    if clock_match is None:
        return None

    month_name, day, hour, minute, meridiem, zone_name = clock_match.groups()
    zone: datetime.tzinfo = datetime.UTC
    if zone_name is not None:
        with contextlib.suppress(pytz.UnknownTimeZoneError):
            zone = pytz.timezone(zone_name)

    hour_value: int = int(hour)
    if meridiem is not None:
        # 12am is hour 0 and 12pm is hour 12, so the modulo has to come before the pm offset.
        hour_value = hour_value % 12 + (12 if meridiem.lower() == "pm" else 0)
    if hour_value > 23:  # A bare clock reading we can't make sense of.
        return None

    now: datetime.datetime = datetime.datetime.now(tz=zone)
    month_value: int = now.month
    if month_name is not None:
        try:
            month_value = datetime.datetime.strptime(month_name, "%b").month  # noqa: DTZ007 - Month name only.
        except ValueError:
            month_name = None

    try:
        reset: datetime.datetime = now.replace(
            month=month_value,
            day=int(day) if day is not None else now.day,
            hour=hour_value,
            minute=int(minute) if minute is not None else 0,
            second=0,
            microsecond=0,
        )
    except ValueError:
        return None

    # No date given means the CLI meant the next time that clock comes around, not one already past.
    # eg. `resets 1:30pm` read at 3pm is tomorrow's 1:30pm.
    if month_name is None and reset <= now:
        reset += datetime.timedelta(days=1)
    return reset


@dataclass
class MessageLocation:
    """Whatever part of a message's address could be read out of what the user typed.

    Attributes
    ----------
    message_id: :class:`int`
        The message itself; the only part always present.
    channel_id: :class:`Optional[int]`
        The channel it lives in, when the input named one.
    guild_id: :class:`Optional[int]`
        The guild it lives in, when the input named one. `None` for a DM link.

    """

    message_id: int
    channel_id: Optional[int] = field(default=None)
    guild_id: Optional[int] = field(default=None)


# A full jump URL, in any of the forms Discord has shipped: `discord.com`, `discordapp.com`,
# `canary.`/`ptb.` subdomains, and `@me` in the guild slot for a DM.
MESSAGE_LINK: re.Pattern[str] = re.compile(
    r"(?:https?://)?(?:\w+\.)?discord(?:app)?\.com/channels/(?P<guild>\d{15,25}|@me)/(?P<channel>\d{15,25})/(?P<message>\d{15,25})"
)

# `channel-message`, which is what the desktop client's "Copy ID" gives on some builds, plus the
# `channel/message` and whitespace separated forms people type by hand.
ID_PAIR: re.Pattern[str] = re.compile(r"^(?P<channel>\d{15,25})\s*[-/\s]\s*(?P<message>\d{15,25})$")

# A bare snowflake. Discord has no lookup-by-ID endpoint, so this one costs us a search.
BARE_ID: re.Pattern[str] = re.compile(r"^(?P<message>\d{15,25})$")

# A user, role or channel mention, in every form the client emits. Used to decide whether a message
# said anything besides pinging somebody; see `session_message_listener`.
MENTION: re.Pattern[str] = re.compile(r"<(?:@[!&]?|#)\d{15,25}>")

# How many channels a bare-ID search probes before giving up. Each probe is its own REST call, so
# this caps how long one `.raw` can tie up the session.
MESSAGE_SEARCH_LIMIT: int = 40

# How many linked messages one prompt pulls in. Each costs a REST fetch and lands in the prompt as a
# path, so the cap keeps a wall of pasted links from quietly becoming a wall of context.
MAX_PROMPT_LINKS: int = 3


def parse_message_location(raw: str) -> Optional[MessageLocation]:
    """Reads a message's address out of a jump URL, an ID pair, or a bare message ID.

    We accept progressively less detail. A full link pins all three IDs, a `channel-message` pair
    pins two, and a bare ID pins only the message, which the caller then has to go looking for.

    Parameters
    ----------
    raw: :class:`str`
        The text the user typed; surrounding angle brackets and whitespace are tolerated.

    Returns
    -------
    :class:`Optional[MessageLocation]`
        What could be read, or `None` when the input holds no message address at all.

    """
    cleaned: str = raw.strip().strip("<>").strip()

    link: Optional[re.Match[str]] = MESSAGE_LINK.search(cleaned)
    if link is not None:
        guild_raw: str = link.group("guild")
        return MessageLocation(
            message_id=int(link.group("message")),
            channel_id=int(link.group("channel")),
            # `@me` is a DM, which has no guild and which the bot cannot read anyway.
            guild_id=None if guild_raw == "@me" else int(guild_raw),
        )

    pair: Optional[re.Match[str]] = ID_PAIR.match(cleaned)
    if pair is not None:
        return MessageLocation(message_id=int(pair.group("message")), channel_id=int(pair.group("channel")))

    bare: Optional[re.Match[str]] = BARE_ID.match(cleaned)
    if bare is not None:
        return MessageLocation(message_id=int(bare.group("message")))
    return None


def thread_title(prompt: str) -> str:
    """Returns a single line, length-capped thread title built from the opening prompt."""
    cleaned: str = " ".join(prompt.split())
    if len(cleaned) > THREAD_TITLE_SIZE:
        cleaned = f"{cleaned[: THREAD_TITLE_SIZE - 1].rstrip()}…"
    return cleaned or "Claude Session"


def locale_timezone(locale: Optional[discord.Locale]) -> datetime.tzinfo:
    """Returns the timezone a Discord locale implies, or UTC when it implies more than one.

    .. note::
        Discord has no timezone field on a user. The locale attached to an interaction is the
        closest thing we get, and it only narrows the user down to a country.

    We only trust a country's zones when they currently agree on a single UTC offset, so `de`
    resolves to Europe/Berlin (Busingen keeps the same clock) while `en-US` stays UTC instead of
    guessing east coast at a west coast user.

    Parameters
    ----------
    locale: :class:`discord.Locale | None`
        The locale Discord attached to the interaction, if any.

    Returns
    -------
    :class:`datetime.tzinfo`
        The resolved timezone, otherwise `datetime.UTC`.

    """
    if locale is None:
        return datetime.UTC

    country: Optional[str] = LOCALE_COUNTRIES.get(locale.value)
    if country is None and "-" in locale.value:
        # Region-carrying locales (`en-GB`, `pt-BR`, `zh-CN`) already name their country. `es-419`
        # names a UN region instead, so it just misses in the timezone data below.
        country = locale.value.rsplit("-", maxsplit=1)[1].upper()
    if country is None:
        return datetime.UTC

    zone_names: list[str] = list(pytz.country_timezones.get(country, []))
    offsets: set[Optional[datetime.timedelta]] = {datetime.datetime.now(tz=pytz.timezone(name)).utcoffset() for name in zone_names}
    if len(offsets) != 1:
        return datetime.UTC
    return pytz.timezone(zone_names[0])


def placeholder_title(locale: Optional[discord.Locale] = None) -> str:
    """Returns the timestamped title a promptless session carries until its first message renames it.

    Parameters
    ----------
    locale: :class:`discord.Locale | None`, optional
        The locale of the user opening the session, by default None (UTC).

    Returns
    -------
    :class:`str`
        eg. `New Session · 27/07 14:32 UTC`.

    """
    now: datetime.datetime = datetime.datetime.now(tz=locale_timezone(locale))
    return f"{PLACEHOLDER_TITLE} · {now.strftime(PLACEHOLDER_TIME_FORMAT)}"


def human_size(size: int) -> str:
    """Returns a byte count rendered as B/KB/MB."""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / 1024 / 1024:.1f}MB"


class AccessTier(StrEnum):
    """Which permission group a user falls into; decides their cwd and the modes they may select.

    The tiers are named on the panel and in `.access`, so the values are what a reader sees.

    .. note::
        `BYPASS` does **not** imply `ELEVATED`. A bypass user sits in their own workspace like anyone
        else until they actually switch to `bypass` mode, which is the point — the power has to be
        reached for deliberately rather than carried around by default. Anyone wanting both puts
        themselves in the elevated group as well; see :meth:`ClaudeCog.access_for`.
    """

    STANDARD = "standard"
    ELEVATED = "elevated"
    BYPASS = "bypass"

    @property
    def label(self) -> str:
        """Returns the tier's name as shown on the panel."""
        return self.value.capitalize()

    @property
    def description(self) -> str:
        """Returns the one line shown beside the tier at `.access`."""
        return ACCESS_DESCRIPTIONS[self]


ACCESS_DESCRIPTIONS: dict[AccessTier, str] = {
    AccessTier.STANDARD: "Confined to this session's own workspace directory.",
    AccessTier.ELEVATED: "Runs at the project root; may reach any file in the project.",
    AccessTier.BYPASS: "Confined like Standard until `bypass` mode is selected, which lifts every limit.",
}

# Shown on the panel for the tiers that are *not* the default. Standard says nothing: it is what a
# session is unless something was granted on purpose, and a line on every panel would be noise.
# Deliberately not part of the state line -- the tier is re-resolved from live roles every turn and
# never read back out of the panel, so it cannot be forged by editing one.
PANEL_ACCESS_NOTICES: dict[AccessTier, str] = {
    AccessTier.ELEVATED: "🗝️ **Elevated** · this session works in the whole project, not just its own workspace.",
    AccessTier.BYPASS: "🔑 **Bypass available** · `.mode bypass` lifts every limit, the working directory included.",
}
# Replaces the above once `bypass` is actually the selected mode; at that point it is not a capability
# sitting unused, it is the state the next run will go out in.
PANEL_BYPASS_ACTIVE: str = "🔓 **Bypass is active** · this session can reach anything on this machine. `.plan` steps back down."


@dataclass(frozen=True)
class Access:
    """What one user is allowed to do, resolved fresh each turn from the ini and the access table.

    Never persisted onto a session. A session's tier is re-resolved on every turn from the user's
    current roles, so losing a role takes effect on a running session's next message rather than
    whenever the post happens to be reopened. Anything we cannot resolve — a DM, a member who has
    left, a user we cannot fetch — falls back to the all-`False` default, which is
    :attr:`AccessTier.STANDARD`.

    Attributes
    ----------
    elevated: :class:`bool`
        Whether the session runs with the project root as its cwd instead of its own workspace.
    bypass: :class:`bool`
        Whether `bypass` is offered at `.mode`. Read from `local.ini` only; no command can grant it.

    """

    elevated: bool = field(default=False)
    bypass: bool = field(default=False)

    @property
    def tier(self) -> AccessTier:
        """Returns the tier this access resolves to, most powerful first."""
        if self.bypass:
            return AccessTier.BYPASS
        return AccessTier.ELEVATED if self.elevated else AccessTier.STANDARD


class AccessEntity(StrEnum):
    """Whether an elevated-group row names a user or a role.

    Stored as text rather than a flag so a row reads for itself in the database, and so a third kind
    of grant can be added later without a migration.
    """

    USER = "user"
    ROLE = "role"


# The elevated group. Kept in the bot's database rather than a JSON file beside `local.ini`: that path
# resolves to the repo root, which is exactly an elevated session's own cwd, and no deny rule covered
# it -- so an elevated session could have handed elevation to anybody. `*.sqlite*` is already denied
# for both `Edit` and `Write`, and this matches how every other cog here keeps its state.
#
# Composite primary key rather than a surrogate id, so a repeated grant is an upsert instead of a
# duplicate row. `added_by`/`added_at` are never read by the cog; they are here because a trust
# boundary should record who widened it.
ACCESS_SETUP_SQL: str = """
CREATE TABLE IF NOT EXISTS claude_access (
    entityid INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    added_by INTEGER NOT NULL,
    added_at REAL NOT NULL,
    PRIMARY KEY (entityid, entity_type))
"""


@dataclass(frozen=True)
class AccessConfig:
    """The elevated group's membership; the mutable half of the trust boundary.

    An immutable snapshot of the `claude_access` table, cached on the cog and replaced wholesale on
    every change. Held in memory because :meth:`ClaudeCog.access_for` runs on every turn and a tier
    check should not wait on the database.

    Attributes
    ----------
    user_ids: :class:`frozenset[int]`
        Users in the elevated group.
    role_ids: :class:`frozenset[int]`
        Roles whose holders are in the elevated group.

    """

    user_ids: frozenset[int] = field(default_factory=frozenset)
    role_ids: frozenset[int] = field(default_factory=frozenset)

    @property
    def empty(self) -> bool:
        """Returns whether the group has no members at all."""
        return not self.user_ids and not self.role_ids


@dataclass(frozen=True)
class ClaudeSettings:
    """The `[CLAUDE]` section of `local.ini`; the whole trust boundary for this cog.

    Attributes
    ----------
    bypass_user_ids: :class:`frozenset[int]`
        Users allowed to reach `bypassPermissions`, which is also how they reach anything outside
        this repository now that there is no project selector.
    attach_transcripts: :class:`bool`
        Whether a snapshotted transcript is uploaded to its session's opening post.

    """

    bypass_user_ids: frozenset[int] = field(default_factory=frozenset)
    attach_transcripts: bool = field(default=False)


def read_settings(*, ini_path: Path) -> ClaudeSettings:
    """Reads the `[CLAUDE]` section, falling back to the safe defaults for anything absent.

    Both settings are opt in and we read them from the ini on purpose, instead of inferring them
    from bot ownership or a Discord permission:

    - `bypass_user_ids` gates `bypassPermissions`, which lets the CLI run any tool (arbitrary shell
      included) with no approval gate and ignores the cwd, so it reaches any directory on the machine.
    - `attach_transcripts` uploads a session's transcript to Discord. A transcript contains
      everything Claude read, so turning it on moves possible file contents and credentials off
      this machine and onto Discord's CDN. Off unless asked for.

    Parameters
    ----------
    ini_path: :class:`Path`
        The path to `local.ini`.

    Returns
    -------
    :class:`ClaudeSettings`
        The parsed settings; all defaults when the file or section is absent.

    """
    if not ini_path.is_file():
        return ClaudeSettings()

    parser: ConfigParser = ConfigParser()
    try:
        parser.read(filenames=ini_path.as_posix())
    except Exception as e:  # noqa: BLE001 - A malformed ini must not take the cog down.
        LOGGER.warning("<%s> | Failed to parse `local.ini`. | Error: %s", "read_settings", e)
        return ClaudeSettings()

    raw_ids: str = parser.get(section="CLAUDE", option="bypass_user_ids", fallback="")
    # Read separately from the IDs above. `getboolean` raises on an unrecognised value and a typo
    # in this option must not take the bypass allowlist down with it.
    try:
        attach: bool = parser.getboolean(section="CLAUDE", option="attach_transcripts", fallback=False)
    except ValueError as e:
        LOGGER.warning("<%s> | `attach_transcripts` is not a boolean; leaving it off. | Error: %s", "read_settings", e)
        attach = False

    ids: set[int] = set()
    for entry in raw_ids.split(","):
        value: str = entry.strip()
        if value.isdigit():
            ids.add(int(value))
    return ClaudeSettings(bypass_user_ids=frozenset(ids), attach_transcripts=attach)


def _access_target(
    *,
    user: Optional[discord.Member],
    role: Optional[discord.Role],
) -> tuple[Optional[tuple[AccessEntity, int, str]], Optional[str]]:
    """Resolves the one target a `/claude access` grant or revoke names.

    Both options are optional in the signature because Discord has no way to say "exactly one of
    these", so the pairing is checked here instead and both commands refuse in the same wording.

    Parameters
    ----------
    user: :class:`Optional[discord.Member]`
        The `user` option as submitted.
    role: :class:`Optional[discord.Role]`
        The `role` option as submitted.

    Returns
    -------
    :class:`tuple[Optional[tuple[AccessEntity, int, str]], Optional[str]]`
        The entity kind, its ID and a mention to echo back; or the message explaining the refusal.

    """
    if user is not None and role is not None:
        return None, "Give me a user *or* a role, not both."
    if user is not None:
        return (AccessEntity.USER, user.id, user.mention), None
    if role is not None:
        # `@everyone` carries the guild ID and every member holds it, so granting it would elevate the
        # whole guild in one click while reading like an ordinary role.
        if role.is_default():
            return None, "That is `@everyone`; elevating it would elevate the entire guild."
        return (AccessEntity.ROLE, role.id, role.mention), None
    return None, "Name a user or a role to change."


def validate_allowed_tools(raw: str) -> tuple[list[str], Optional[str]]:
    """Validates a user supplied `--allowed-tools` list.

    Each entry must look like `Read` or `Bash(git log:*)`; a bare identifier with an optional
    parenthesised argument that carries no shell metacharacters. `..` is rejected outright so a tool
    spec cannot name a path outside the project.

    Parameters
    ----------
    raw: :class:`str`
        The comma separated list as typed.

    Returns
    -------
    :class:`tuple[list[str], Optional[str]]`
        The accepted entries and an error message; the error is `None` when every entry passed.

    """
    entries: list[str] = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if not entries:
        return [], "Give me at least one tool, e.g. `.tools Read, Bash(git log:*)`."
    if len(entries) > MAX_ALLOWED_TOOLS:
        return [], f"That is more than {MAX_ALLOWED_TOOLS} tools; trim the list."

    for entry in entries:
        if len(entry) > MAX_TOOL_SPEC_LENGTH:
            return [], f"`{entry[:40]}…` is longer than {MAX_TOOL_SPEC_LENGTH} characters."
        if ".." in entry:
            return [], f"`{entry}` contains `..`; tool specs may not walk out of the project."
        if TOOL_SPEC_PATTERN.match(entry) is None:
            return [], f"`{entry}` is not a valid tool spec. Use e.g. `Read`, `Edit` or `Bash(git log:*)`."
    return entries, None


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class SessionState:
    """Everything needed to continue one session, mirrored from its thread's opening post.

    Attributes
    ----------
    thread_id: :class:`int`
        The forum post this session lives in; also names its workspace directory.
    user_id: :class:`int`
        The Discord user the session belongs to.
    session_id: :class:`str`
        The Claude Code CLI session ID passed to `--resume`.
    model: :class:`str`
        The model the next run will use.
    mode: :class:`str`
        The `--permission-mode` the next run will use.
    effort: :class:`str`
        The `--effort` level the next run will use.
    started: :class:`float`
        Unix timestamp the session was opened, shown on the panel.
    allowed_tools: :class:`list[str]`
        A tool allowlist set via `.tools` or carried by the permission mode, forwarded as
        `--allowed-tools`.
    tools_explicit: :class:`bool`
        Whether :attr:`allowed_tools` was set by hand at `.tools`. A hand-picked list outranks the
        mode's preset, so switching mode never silently discards it.
    ignoring: :class:`bool`
        Whether ordinary messages in the thread are being dropped rather than run, toggled at
        `.ignore`. Commands are never affected; that is what turns it back off.

    .. note::
        Only the fields in :attr:`STATE_LINE_FORMAT` survive a restart. :attr:`allowed_tools` comes
        back as the recorded mode's own preset with :attr:`tools_explicit` reset, so a list
        hand-picked at `.tools` does not survive either. :attr:`ignoring` clears as well, which is
        the safer way round — a restart should not leave a post silently mute with no sign of why.

    """

    thread_id: int
    user_id: int
    session_id: str
    model: str = field(default=DEFAULT_MODEL)
    mode: str = field(default=DEFAULT_MODE)
    effort: str = field(default=DEFAULT_EFFORT)
    started: float = field(default_factory=time.time)
    allowed_tools: list[str] = field(default_factory=list)
    tools_explicit: bool = field(default=False)
    ignoring: bool = field(default=False)
    access: Access = field(default_factory=Access)

    @property
    def root(self) -> Path:
        """Returns this session's project root, which is always this repository.

        Kept as a property rather than reaching for :attr:`PROJECT_ROOT` directly at each use, so the
        workspace, transcript and deny-rule helpers all read off the session. It is only the
        subprocess cwd for the elevated group; see :attr:`cwd`, which is the one that decides what a
        session can reach.
        """
        return PROJECT_ROOT

    @property
    def cwd(self) -> Path:
        """Returns the directory the CLI actually runs in, which is what confines the session.

        The cwd is the real boundary. Tested against `claude 2.1.220`: under `plan`, `acceptEdits`,
        `manual` and `default` the CLI refuses to `Read` *or* `Write` anything outside its working
        directory, and reports the refusal as a `permission_denial`. So picking the cwd is how a
        tier is enforced, and no rule we pass alongside it has to hold that line.

        - Standard sits in its own workspace and can reach nothing else.
        - Elevated sits at the project root and can reach the whole project.
        - Bypass is **not** special here. `bypassPermissions` ignores the cwd outright — tested; it
          wrote outside the working directory and read outside the repo with no denials — so a
          bypass user is confined exactly like a standard one until they switch modes, and the
          moment they do, the cwd stops mattering at all.
        """
        return self.root if self.access.elevated else self.workspace

    @property
    def workspace(self) -> Path:
        """Returns this session's workspace directory."""
        return session_dir(root=self.root, user_id=self.user_id, thread_id=self.thread_id)

    @property
    def attachments(self) -> Path:
        """Returns this session's attachment directory."""
        return attachments_dir(root=self.root, user_id=self.user_id, thread_id=self.thread_id)

    @property
    def state_line(self) -> str:
        """Returns the small-text line written into the panel and re-parsed on restart."""
        return STATE_LINE_FORMAT.format(
            session_id=self.session_id,
            model=self.model,
            mode=self.mode,
            effort=self.effort,
            # Rendered as the mention rather than the raw ID so the panel names a person. We only hold
            # the ID, and `Member.mention` is this exact string, so there is nothing to look up.
            user_id=f"<@{self.user_id}>",
            started=f"<t:{int(self.started)}:R>",
        )


@dataclass(frozen=True)
class ToolDenial:
    """One tool the CLI refused to run, lifted from the result event's `permission_denials`.

    `claude -p` is non-interactive, so it never asks about a tool it has no permission for; it denies
    the tool outright and reports it here once the turn ends. That makes denials the only permission
    signal we can act on and the basis for the retry offer under an answer.

    Attributes
    ----------
    tool: :class:`str`
        The tool name as the CLI reports it, e.g. `Read` or `Bash`.
    target: :class:`str`
        The salient part of the tool's input (a path, a command) rendered for display.
    outside_cwd: :class:`bool`
        Whether the call was refused for naming something outside the session's working directory
        rather than for lacking the tool. Granting the tool cannot fix one of these -- tested against
        `claude 2.1.220`; an explicit `Bash(cat:*)` allow rule still could not read a parent directory
        -- so :meth:`ClaudeCog.offer_retry` must not offer a retry for it.

    """

    tool: str
    target: str
    outside_cwd: bool = field(default=False)


def names_path_outside(*, tool_input: dict, cwd: Path) -> bool:
    """Returns whether a denied call named something outside the session's working directory.

    The distinction matters because the two kinds of denial have opposite remedies: a tool the mode
    withheld is fixed by granting it, and a path outside the cwd cannot be fixed by any grant at all.

    Exact for the file tools, which carry their path as its own input field. For `Bash` the input is a
    command string, so only the two unambiguous forms are judged -- an absolute path that lands outside
    the cwd, and an explicit parent-directory step. A relative token is left alone: it resolves inside
    the cwd by definition, and guessing more from a shell string would eventually suppress a retry
    offer that would have worked.

    Parameters
    ----------
    tool_input: :class:`dict`
        The tool's input object as the CLI reported it.
    cwd: :class:`Path`
        The directory the run was confined to.

    Returns
    -------
    :class:`bool`
        Whether the call reached outside `cwd`.

    """
    for key in ("file_path", "notebook_path", "path"):
        value: Any = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            named: Path = Path(value.strip())
            return not is_inside(named if named.is_absolute() else cwd.joinpath(named), cwd)

    command: Any = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return False

    try:
        tokens: list[str] = shlex.split(command)
    except ValueError:
        # Unbalanced quotes; we cannot read the command reliably, so we do not claim anything about it.
        return False

    for token in tokens:
        # `..` as its own path segment. Matched this way rather than as a substring so a revision range
        # like `HEAD~1..HEAD` is not mistaken for a directory step.
        parts: list[str] = token.replace("\\", "/").split("/")
        if token.startswith("/"):
            if not is_inside(Path(token), cwd):
                return True
        elif ".." in parts and not is_inside(cwd.joinpath(token), cwd):
            return True
    return False


def parse_denials(*, result_event: dict, root: Path, cwd: Path) -> list[ToolDenial]:
    """Returns the tools a run was refused, de-duplicated by tool and target.

    A model that keeps retrying the same blocked call gives us one denial entry per attempt, which
    would otherwise read as several separate problems.

    Parameters
    ----------
    result_event: :class:`dict`
        The CLI's `result` event.
    root: :class:`Path`
        The session's project root, so paths render relative to it.
    cwd: :class:`Path`
        The directory the run was confined to, for classifying each denial; see
        :func:`names_path_outside`.

    Returns
    -------
    :class:`list[ToolDenial]`
        The distinct denials, in the order they were first reported.

    """
    denials: list[ToolDenial] = []
    seen: set[tuple[str, str]] = set()
    raw: object = result_event.get("permission_denials")
    if not isinstance(raw, list):
        return denials

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        tool: str = str(entry.get("tool_name") or "tool")
        raw_input: Any = entry.get("tool_input")
        tool_input: dict = raw_input if isinstance(raw_input, dict) else {}
        denial: ToolDenial = ToolDenial(
            tool=tool,
            target=tool_target(name=tool, tool_input=tool_input, root=root),
            outside_cwd=names_path_outside(tool_input=tool_input, cwd=cwd),
        )
        key: tuple[str, str] = (denial.tool, denial.target)
        if key not in seen:
            seen.add(key)
            denials.append(denial)
    return denials


@dataclass
class ClaudeResult:
    """The outcome of a single Claude Code CLI invocation.

    Attributes
    ----------
    text: :class:`str`
        The response text; empty when :attr:`error` is set.
    session_id: :class:`str`
        The session ID the CLI actually ran under (may differ from the one we asked for on a fork).
    cost_usd: :class:`Optional[float]`
        The API cost reported by Claude Code, if available.
    error: :class:`Optional[str]`
        A user-displayable error message; `None` on success.
    files: :class:`list[Path]`
        Any file created or touched inside the session workspace during the run, newest first.
    tool_calls: :class:`int`
        How many tools the CLI invoked, for the collapsed activity summary.
    duration: :class:`float`
        Wall-clock seconds the run took.
    denials: :class:`list[ToolDenial]`
        Tools the CLI refused during the run, for the retry offer posted under the answer.

    """

    text: str = field(default="")
    session_id: str = field(default="")
    cost_usd: Optional[float] = field(default=None)
    error: Optional[str] = field(default=None)
    files: list[Path] = field(default_factory=list)
    tool_calls: int = field(default=0)
    duration: float = field(default=0.0)
    denials: list[ToolDenial] = field(default_factory=list)


@dataclass
class DotCommand:
    """One entry in the in-thread `.command` table.

    Attributes
    ----------
    name: :class:`str`
        The canonical command name, typed as `.name`.
    summary: :class:`str`
        One line description shown by `.help`.
    usage: :class:`str`
        The argument form shown by `.help`; empty when the command takes none.
    aliases: :class:`tuple[str, ...]`
        Extra names that resolve to this command.
    handler: :class:`Optional[str]`
        The :class:`ClaudeCog` method name that runs it; `None` marks a CLI pass-through.

    """

    name: str
    summary: str
    usage: str = field(default="")
    aliases: tuple[str, ...] = field(default=())
    handler: Optional[str] = field(default=None)


# Bot-mapped commands. Everything here is handled locally and never reaches the CLI as prompt text,
# because `claude -p` is non-interactive and silently ignores real slash commands like `/model`.
DOT_COMMANDS: tuple[DotCommand, ...] = (
    DotCommand(name="help", summary="Show this command list.", aliases=("h", "?"), handler="dot_help"),
    DotCommand(
        name="model",
        summary=f"Switch model for this session ({', '.join(MODELS)}).",
        usage="<name>",
        aliases=("m",),
        handler="dot_model",
    ),
    DotCommand(
        name="mode",
        summary=f"Switch permission mode ({', '.join(MODES)}).",
        usage="<name>",
        aliases=("perm",),
        handler="dot_mode",
    ),
    DotCommand(
        name="effort",
        summary=f"Set how hard Claude works ({', '.join(EFFORTS)}).",
        usage="<level>",
        aliases=("e",),
        handler="dot_effort",
    ),
    DotCommand(name="plan", summary="Shortcut for `.mode plan` - read-only planning.", handler="dot_plan"),
    DotCommand(name="edits", summary="Shortcut for `.mode edits` - auto-approve file edits.", aliases=("accept",), handler="dot_edits"),
    DotCommand(
        name="new",
        summary="Start a fresh session in this post, keeping the files.",
        aliases=("clear", "reset"),
        handler="dot_new",
    ),
    DotCommand(
        name="ignore",
        summary="Stop treating messages here as prompts; run it again to resume.",
        usage="[on | off]",
        aliases=("mute",),
        handler="dot_ignore",
    ),
    DotCommand(name="status", summary="Show this session's settings and workspace.", aliases=("s", "info"), handler="dot_status"),
    DotCommand(name="access", summary="Explain this session's access tier and where it can reach.", handler="dot_access"),
    DotCommand(name="files", summary="List the files in this session's workspace.", aliases=("ls", "f"), handler="dot_files"),
    DotCommand(name="get", summary="Upload one file from the workspace.", usage="<name>", handler="dot_get"),
    DotCommand(
        name="raw",
        summary="Save another message's raw text into the workspace.",
        usage="<link | channel-message | id>",
        handler="dot_raw",
    ),
    DotCommand(name="tools", summary="Restrict the session to a tool allowlist.", usage="<a, b, ...>", handler="dot_tools"),
    DotCommand(name="rename", summary="Rename this post.", usage="<title>", aliases=("title",), handler="dot_rename"),
    DotCommand(name="cancel", summary="Cancel the run currently in progress.", aliases=("x",), handler="dot_cancel"),
    DotCommand(name="close", summary="Close and lock this session.", aliases=("expire", "stop"), handler="dot_close"),
    DotCommand(name="restore", summary="Rebuild a dormant session from its transcript snapshot.", handler="dot_restore"),
)

# Pass-throughs are appended so an explicit bot-mapped name always wins a prefix match.
DOT_COMMANDS += tuple(
    DotCommand(name=name, summary=f"Run the CLI's `/{name}` command.", handler=None) for name in sorted(PASSTHROUGH_COMMANDS)
)


def resolve_dot_command(name: str) -> tuple[Optional[DotCommand], list[str]]:
    """Resolves a typed command name, allowing any unambiguous abbreviation.

    An exact name or alias always wins. Otherwise we treat the name as a prefix and it has to match
    exactly one command, so `.m` reaches `.model` while an ambiguous stub reports its candidates.

    Parameters
    ----------
    name: :class:`str`
        The name as typed, without the leading dot.

    Returns
    -------
    :class:`tuple[Optional[DotCommand], list[str]]`
        The resolved command and, when it could not be resolved, the candidate names to suggest.

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
# Components V2 session panel; the thread's opening post.
# ---------------------------------------------------------------------------


class PanelSelect(discord.ui.Select["SessionPanel"]):
    """Base select for the session panel; routes its choice to a :class:`ClaudeCog` handler.

    Parameters
    ----------
    custom_id: :class:`str`
        The static custom ID, so the panel keeps working after a restart.
    placeholder: :class:`str`
        The select's placeholder text.
    options: :class:`list[discord.SelectOption]`
        The choices to offer.
    handler: :class:`str`
        The :class:`ClaudeCog` method name invoked with the chosen value.

    """

    def __init__(self, *, custom_id: str, placeholder: str, options: list[discord.SelectOption], handler: str) -> None:
        super().__init__(custom_id=custom_id, placeholder=placeholder, options=options, min_values=1, max_values=1)
        self.handler: str = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: Optional[ClaudeCog] = _cog_from(interaction)
        if cog is None:
            return
        await getattr(cog, self.handler)(interaction, value=self.values[0])


class PanelButton(discord.ui.Button["SessionPanel"]):
    """Base button for the session panel; routes its press to a :class:`ClaudeCog` handler.

    Parameters
    ----------
    label: :class:`str`
        The button label.
    custom_id: :class:`str`
        The static custom ID, so the panel keeps working after a restart.
    handler: :class:`str`
        The :class:`ClaudeCog` method name invoked on press.
    style: :class:`discord.ButtonStyle`, optional
        The button style, by default :attr:`discord.ButtonStyle.secondary`.
    emoji: :class:`Optional[str]`, optional
        The button emoji, by default `None`.

    """

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


class DenialRetry(discord.ui.View):
    """The "allow and retry" offer posted under an answer whose run had tools denied.

    Unlike :class:`SessionPanel` this one is *not* persistent. It carries the prompt to re-run and the
    tools to grant, neither of which survives a restart, so a stale button after a rebuild would offer
    a retry it can't perform. We let it time out instead.

    Parameters
    ----------
    prompt: :class:`str`
        The prompt to send again once the tools are granted.
    tools: :class:`list[str]`
        The tool names to append to the session's allowlist.
    user_id: :class:`int`
        The session owner; nobody else may press the button.

    """

    def __init__(self, *, prompt: str, tools: list[str], user_id: int) -> None:
        super().__init__(timeout=DENIAL_RETRY_TIMEOUT)
        self.prompt: str = prompt
        self.tools: list[str] = tools
        self.user_id: int = user_id

    @discord.ui.button(label="Allow & Retry", style=discord.ButtonStyle.primary, emoji="🔓")
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa: ARG002
        """Grants the denied tools for this session and re-runs the prompt that hit them."""
        cog: Optional[ClaudeCog] = _cog_from(interaction)
        if cog is None:
            return
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(content="This session isn't yours!", ephemeral=True)
            return

        # One press only. The retry posts a fresh answer with its own offer, so leaving this live
        # would let the same prompt get queued twice against a session that runs one turn at a time.
        self.stop()
        await interaction.response.edit_message(view=None)
        await cog.retry_with_tools(interaction=interaction, prompt=self.prompt, tools=self.tools)


class RunControls(discord.ui.View):
    """The Cancel button that rides on a turn's status message.

    Not persistent, like :class:`DenialRetry`, and for the same reason: it is only meaningful for the
    one turn it was posted with. :meth:`ClaudeCog.run_and_post` takes it off the message as that turn
    ends, and the timeout is only a backstop for a turn whose cog was torn out from under it.

    .. note::
        :class:`KumaAnimation` writes `content` alone and a partial edit leaves components where they
        are, so this survives every frame of the animation underneath it.

    Parameters
    ----------
    thread_id: :class:`int`
        The session thread whose turn this cancels.
    user_id: :class:`int`
        The session owner; nobody else may press the button.

    """

    def __init__(self, *, thread_id: int, user_id: int) -> None:
        # A turn cannot outlive `CLAUDE_TIMEOUT`, so neither should the button that stops it.
        super().__init__(timeout=float(CLAUDE_TIMEOUT))
        self.thread_id: int = thread_id
        self.user_id: int = user_id

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa: ARG002
        """Cancels the turn this button was posted with, queued or in flight."""
        cog: Optional[ClaudeCog] = _cog_from(interaction)
        if cog is None:
            return
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(content="This session isn't yours!", ephemeral=True)
            return

        # One press either way. On a hit the turn is already unwinding and will clear this view itself,
        # so the press only needs acknowledging; the ephemeral is for the race where it just finished.
        self.stop()
        if cog.cancel_run(thread_id=self.thread_id):
            await interaction.response.defer()
            return
        await interaction.response.send_message(
            content=f"That run has already finished. {cog.emoji_table.kuma_shrug}",
            ephemeral=True,
        )


def _cog_from(interaction: discord.Interaction) -> Optional[ClaudeCog]:
    """Returns the loaded :class:`ClaudeCog`, or `None` when the extension has been unloaded.

    Panel components are persistent, so an interaction can arrive against a cog that is no longer
    loaded; eg. a reload between the message being sent and the button being pressed.
    """
    cog: Optional[commands.Cog] = interaction.client.get_cog("Claude")  # type: ignore[attr-defined]
    if not isinstance(cog, ClaudeCog):
        return None
    return cog


class PanelChoice(NamedTuple):
    """One option on a panel select, in the shape :meth:`SessionPanel.add_control` wants.

    The three controls pick from unrelated tables (:attr:`MODELS`, :attr:`MODES`, :attr:`EFFORTS`) that
    agree on nothing but this: something to send back, something to show, and a line explaining it.
    Flattening them to this lets one builder render all three.

    Attributes
    ----------
    value: :class:`str`
        What the select hands to its handler.
    label: :class:`str`
        The option's name, as shown in the open dropdown.
    summary: :class:`str`
        The one line describing what picking it does. Shown beside the option in the open dropdown, and
        above the closed select when it is the one selected.

    """

    value: str
    label: str
    summary: str


def _model_choices() -> list[PanelChoice]:
    """Returns the model options; summarised by the CLI model ID, which is the useful detail."""
    return [PanelChoice(value=name, label=name.capitalize(), summary=f"`{model}`") for name, model in MODELS.items()]


def _mode_choices(mode_names: Iterable[str]) -> list[PanelChoice]:
    """Returns the permission mode options for the given short names."""
    return [PanelChoice(value=name, label=name.capitalize(), summary=_mode_description(name)) for name in mode_names]


def _effort_choices() -> list[PanelChoice]:
    """Returns the effort options."""
    return [PanelChoice(value=name, label=name.capitalize(), summary=EFFORT_DESCRIPTIONS[name]) for name in EFFORTS]


class SessionPanel(discord.ui.LayoutView):
    """The opening post of a session thread: state, controls and the dot-command hint.

    Persistent by design. Every child carries a static custom ID and we resolve the session it acts
    on from the interaction's thread, so the panel keeps working across bot restarts without us
    storing anything about it.

    Laid out as a settings sheet: each control gets a heading and, in small text beneath it, what the
    *currently selected* option does. A select only shows its own description once you open it, so
    without that line a closed panel could not tell you what mode the session was actually in.

    .. note::
        Built imperatively in `__init__` rather than as class attributes. The layout is not fixed;
        a dormant panel swaps the header's thumbnail for the restore button, the transcript block comes
        and goes, and the mode list is per user, so there is no one arrangement to declare.

    .. warning::
        A Components V2 message cannot carry `content`, `embeds`, stickers or polls. A message can be
        edited *into* this layout by clearing those, but never back out of it.

    Parameters
    ----------
    state: :class:`Optional[SessionState]`, optional
        The session to render. `None` builds the bare shell registered with
        :meth:`discord.Client.add_view` at startup, whose only job is to own the custom IDs.
    modes: :class:`Optional[Iterable[str]]`, optional
        The permission modes to offer, by default the entries in :attr:`MODES`. Callers pass a
        widened set for users allowed to reach `bypassPermissions`.
    status: :class:`SessionStatus`, optional
        How to render the session, by default :attr:`SessionStatus.ACTIVE`. Either dormant status
        disables the controls and shows the matching notice plus the restore button.
    transcript: :class:`Optional[str]`, optional
        The filename of a transcript already attached to this post. Rendered as a file component so
        the attachment stays visible; the file itself is uploaded by the caller, not by the panel.

    """

    def __init__(
        self,
        *,
        state: Optional[SessionState] = None,
        modes: Optional[Iterable[str]] = None,
        status: SessionStatus = SessionStatus.ACTIVE,
        transcript: Optional[str] = None,
    ) -> None:
        super().__init__(timeout=None)

        if state is None:
            # The shell only needs every custom ID to exist so the persistent view matches on any of
            # them. It renders dormant so `PANEL_RESTORE_ID` is registered too.
            status = SessionStatus.EXPIRED

        mode_names: list[str] = list(modes if modes is not None else MODES)
        container: discord.ui.Container = discord.ui.Container(
            accent_colour=discord.Color.dark_grey() if status.dormant else discord.Color.blurple(),
        )

        self.add_header(container=container, state=state, status=status)
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
            choices=_mode_choices(mode_names),
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
        actions.add_item(PanelButton(label="New Session", custom_id=PANEL_NEW_ID, handler="panel_new", emoji="🔄"))
        actions.add_item(PanelButton(label="Files", custom_id=PANEL_FILES_ID, handler="panel_files", emoji="📁"))
        actions.add_item(PanelButton(label="Help", custom_id=PANEL_HELP_ID, handler="panel_help", emoji="❔"))
        actions.add_item(
            PanelButton(label="Close", custom_id=PANEL_CLOSE_ID, handler="panel_close", style=discord.ButtonStyle.danger, emoji="⏹️"),
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
    ) -> None:
        """Adds the title block; a section whose accessory is the thumbnail, or the way back in.

        A section takes exactly one accessory and it has to be a button or a thumbnail, never a select.
        A dormant panel has one live control and it belongs beside the notice explaining why everything
        else is dead, so it takes the slot; a live panel has nothing urgent to put there and gets the
        artwork instead.

        Parameters
        ----------
        container: :class:`discord.ui.Container`
            The panel's box, to add the header to.
        state: :class:`Optional[SessionState]`
            The session being rendered, or `None` for the persistent shell.
        status: :class:`SessionStatus`
            How the panel is being rendered.

        """
        body: list[str] = [f"## {PANEL_HEADERS[status]}"]
        if state is not None:
            notice: Optional[str] = PANEL_BYPASS_ACTIVE if state.mode == BYPASS_MODE else PANEL_ACCESS_NOTICES.get(state.access.tier)
            if notice is not None:
                body.append(notice)
            body.append(PANEL_NOTICES[status])

        accessory: discord.ui.Item[Any]
        if status.dormant:
            # The only way back in; the thread itself is locked, so a `.restore` message could not be
            # posted into it.
            accessory = PanelButton(
                label="Restore Session",
                custom_id=PANEL_RESTORE_ID,
                handler="panel_restore",
                style=discord.ButtonStyle.success,
                emoji="♻️",
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
        """Adds one labelled select to the panel; a heading, the current choice's summary, then the select.

        Parameters
        ----------
        container: :class:`discord.ui.Container`
            The panel's box, to add the control to.
        title: :class:`str`
            The heading shown above the select.
        custom_id: :class:`str`
            The select's static custom ID.
        handler: :class:`str`
            The :class:`ClaudeCog` method name the select routes its choice to.
        choices: :class:`list[PanelChoice]`
            The options to offer, in render order.
        selected: :class:`Optional[str]`
            The :attr:`PanelChoice.value` currently in force, or `None` for the persistent shell, which
            renders nothing selected.

        """
        chosen: Optional[PanelChoice] = next((choice for choice in choices if choice.value == selected), None)
        # The shell renders no selection at all, so it has no summary to show and says so rather than
        # leaving a heading hanging over an empty line.
        summary: str = chosen.summary if chosen is not None else "Nothing selected."

        container.add_item(discord.ui.TextDisplay(f"### {title}\n-# {summary}"))
        container.add_item(
            discord.ui.ActionRow().add_item(
                PanelSelect(
                    custom_id=custom_id,
                    # Only ever seen when nothing is selected, which is the shell and any session whose
                    # stored value has since left the table. The heading already sits directly above,
                    # so it repeats that rather than inventing a second phrasing for the same thing.
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
        """Adds the reference block below the controls; the machine readable state line and the transcript.

        Neither is a setting, so both sit under their own divider at the bottom rather than competing
        with the controls above.

        Parameters
        ----------
        container: :class:`discord.ui.Container`
            The panel's box, to add the footer to.
        state: :class:`Optional[SessionState]`
            The session being rendered. The shell passes `None` and gets no state line; it is never
            posted, so there would be nothing true to write in one.
        transcript: :class:`Optional[str]`
            The filename of a transcript already attached to this post, if there is one.

        """
        if state is None and transcript is None:
            return

        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        if state is not None:
            # Carries the ID `parse_state` reads the session back out of after a restart.
            container.add_item(discord.ui.TextDisplay(state.state_line, id=PANEL_STATE_COMPONENT_ID))

        if transcript is not None:
            # References an attachment already on the message. `Message.edit` keeps attachments it is
            # not told about, so later panel edits point at the same upload instead of us resending a
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

        A dormant session takes no further turns, so nothing on the panel should imply otherwise. We
        walk the finished view rather than the rows we built, as the restore button lives on a section
        accessory and the actions live on the view itself; neither is reachable from a list of rows.
        """
        for child in self.walk_children():
            if isinstance(child, (discord.ui.Select, discord.ui.Button)) and child.custom_id != PANEL_RESTORE_ID:
                child.disabled = True

    def warn_if_oversized(self) -> None:
        """Logs when the panel has outgrown what Discord will accept in one view.

        Discord answers a view over either ceiling with a *400*, which for this panel means the post
        does not send and the session it described is gone. We would rather find that in the log than
        in a user's thread, so the check is here where every panel passes through.
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


def _mode(name: str) -> PermissionMode:
    """Returns the :class:`PermissionMode` a short mode name selects, falling back to `plan`."""
    if name == "bypass":
        return BYPASS
    return MODES.get(name, MODES["plan"])


def _mode_value(name: str) -> str:
    """Returns the CLI `--permission-mode` value for a short mode name."""
    return _mode(name).value


def _mode_name(value: str) -> str:
    """Returns the short mode name for a CLI `--permission-mode` value; the inverse of :func:`_mode_value`.

    We need this because :attr:`SessionState.mode` stores the CLI value, while the mode's tool preset
    is keyed by its short name.
    """
    if value == BYPASS.value:
        return "bypass"
    for name, mode in MODES.items():
        if mode.value == value:
            return name
    return "plan"


def _mode_description(name: str) -> str:
    """Returns the one line description shown beside a mode in the panel's select."""
    return _mode(name).description


def _model_name(value: str) -> str:
    """Returns the short model name for a CLI `--model` value; the inverse of :attr:`MODELS`.

    We need this for the same reason :func:`_mode_name` exists: :attr:`SessionState.model` stores the
    CLI value, while the panel's select is keyed by the short name a user types at `.model`.
    """
    for name, model in MODELS.items():
        if model == value:
            return name
    return "sonnet"


def parse_state(*, message: discord.Message, thread_id: int) -> Optional[SessionState]:
    """Rebuilds a :class:`SessionState` from a session thread's opening post.

    Reads the small-text state line straight out of the component that carries it, which is how a
    restarted bot recovers a session it has no memory of. We don't persist settings that only live for
    a run (`.tools`); those reset when the bot restarts.

    Parameters
    ----------
    message: :class:`discord.Message`
        The thread's opening post.
    thread_id: :class:`int`
        The thread the post belongs to.

    Returns
    -------
    :class:`Optional[SessionState]`
        The recovered state, or `None` when the post carries no readable state line.

    """
    text: Optional[str] = _find_text(components=message.components, component_id=PANEL_STATE_COMPONENT_ID)
    if text is None:
        return None

    match: Optional[re.Match[str]] = STATE_LINE_PATTERN.search(text)
    if match is None:
        return None

    mode: str = match.group("mode")
    return SessionState(
        thread_id=thread_id,
        user_id=int(match.group("user_id")),
        session_id=match.group("session_id"),
        model=match.group("model"),
        mode=mode,
        effort=match.group("effort"),
        started=message.created_at.timestamp(),
        # The state line carries the mode but not the tool list, so a restored session comes back with
        # the mode's own preset. Anything set by hand at `.tools` is gone with the restart, which is
        # why `tools_explicit` stays at its default.
        allowed_tools=list(_mode(_mode_name(mode)).tools),
    )


def _find_text(*, components: Iterable[Any], component_id: int) -> Optional[str]:
    """Returns the content of the text display carrying `component_id`, at any depth.

    :attr:`discord.Message.components` is the read-only mirror of what we sent, and the numeric IDs
    :class:`SessionPanel` sets survive the round trip, so a part of the panel can be addressed rather
    than searched for. :meth:`discord.ui.LayoutView.from_message` plus `find_item` would do the same,
    but it rebuilds every select and button on the panel to hand back one string.

    Parameters
    ----------
    components: :class:`Iterable[Any]`
        The components to search, typically :attr:`discord.Message.components`.
    component_id: :class:`int`
        The numeric component ID to look for.

    Returns
    -------
    :class:`Optional[str]`
        The text content, or `None` when nothing on the message carries that ID.

    """
    for component in components:
        if isinstance(component, discord.components.TextDisplay) and component.id == component_id:
            return component.content
        found: Optional[str] = _find_text(components=getattr(component, "children", []) or [], component_id=component_id)
        if found is not None:
            return found
    return None


class PanelLookup(NamedTuple):
    """The result of reading a session's opening post; the panel, or why we haven't got one.

    "No panel" has two meanings and they call for opposite handling. Discord answering *404* means the
    post is genuinely gone and the session with it. Discord not answering at all means we don't know,
    the panel is most likely intact, and the right move is to change nothing and try again later.

    Attributes
    ----------
    message: :class:`Optional[discord.Message]`
        The opening post, or `None` when we could not read it.
    gone: :class:`bool`
        Whether Discord confirmed the post no longer exists. Never `True` alongside a message.

    """

    message: Optional[discord.Message]
    gone: bool


class ClaudeCog(Cog, name="Claude"):
    """Cog exposing the Claude Code CLI as one forum post per session.

    Each user gets a private forum in the guild they ran `/claude ask` in; every post in it is one
    session, and replying in a post continues that session. The post's opening message is the only
    place session state is kept (there is no database) so a restart recovers everything by reading
    the message back off Discord.

    Files generated by a session live in `.claude_sessions/<user_id>/<thread_id>/`, which we remove
    when the post is deleted. We rename, lock and archive posts untouched for
    :attr:`SESSION_MAX_AGE_DAYS`, which matches the point Claude Code drops its own transcript.
    """

    claude = app_commands.Group(name="claude", description="Claude Code CLI integration.")
    # A subgroup rather than three `/claude access_*` commands, so the trust boundary reads as one thing
    # in the command list. Each subcommand carries its own `_is_owner` check; a check on the parent group
    # does not cascade to children, and this is the last thing to leave gating to assumption.
    access = app_commands.Group(name="access", description="Manage who runs sessions at the project root.", parent=claude)

    def __init__(self, bot: Kuma_Kuma) -> None:
        super().__init__(bot=bot)
        # One lock per user serializes their CLI runs so two overlapping prompts never share a
        # session transcript or race the workspace file-diff.
        self._locks: dict[int, asyncio.Lock] = {}
        # Session state by thread ID; a miss is re-read from the thread's opening post.
        self._sessions: dict[int, SessionState] = {}
        # Running subprocesses by thread ID, so a cancel has something to kill and `.status` can say
        # whether a turn has actually launched one yet.
        self._running: dict[int, asyncio.subprocess.Process] = {}
        # The task driving each session's turn, by thread ID. Registered for the whole of
        # `run_and_post`, which begins *before* the per-user lock is taken, so a turn still queued
        # behind another of the same user's sessions is cancellable too. `_running` alone cannot do
        # that: a queued turn has no subprocess to be found under, yet it will still run.
        self._tasks: dict[int, asyncio.Task[Any]] = {}
        # When the account's usage limit is hit, the reset time read out of the CLI's error. Every
        # session shares one account, so one thread hitting the wall means they are all blocked.
        # Remembering it here spares the rest a doomed subprocess launch each.
        self._limit_reset: Optional[datetime.datetime] = None
        self._settings: ClaudeSettings = ClaudeSettings()
        # The elevated group, mirrored from `claude_access` in `cog_load`. `access_for` runs on every
        # turn, so it reads this rather than the database. Empty until loaded, which fails closed.
        self._access: AccessConfig = AccessConfig()
        # Context menus can't live in a `Group`, so we build and register this one by hand and tear it
        # down again in `cog_unload`; otherwise a reload leaves a duplicate in the tree.
        self.ask_menu: app_commands.ContextMenu = app_commands.ContextMenu(name="Ask Claude", callback=self.ask_message)
        self.ask_menu.add_check(_is_owner)
        self.bot.tree.add_command(self.ask_menu)

    # region --- Lifecycle

    async def cog_load(self) -> None:
        """Ensures the access table exists, reads both halves of the trust boundary, starts cleanup."""
        self._settings = await asyncio.to_thread(read_settings, ini_path=self.bot.local_ini)
        if self._settings.bypass_user_ids:
            LOGGER.info(
                "<%s.%s> | Claude bypassPermissions enabled for %s user(s).",
                __class__.__name__,
                "cog_load",
                len(self._settings.bypass_user_ids),
            )

        async with self.bot.pool.acquire() as conn:
            await conn.execute(ACCESS_SETUP_SQL)
        await self.load_access()

        # The shell only owns the custom IDs; every callback resolves its session from the thread.
        self.bot.add_view(view=SessionPanel())

        if self.cleanup_loop.is_running() is False:
            self.cleanup_loop.start()
            self.bot.task_loops.append(self.cleanup_loop)

    async def cog_unload(self) -> None:
        """Stops the cleanup loop and kills anything still running, so a reload leaves nothing behind."""
        self.bot.tree.remove_command(self.ask_menu.name, type=self.ask_menu.type)

        if self.cleanup_loop.is_running():
            self.cleanup_loop.cancel()
        if self.cleanup_loop in self.bot.task_loops:
            self.bot.task_loops.remove(self.cleanup_loop)

        # Tasks first: each one kills its own subprocess on the way out, and a task left running would
        # otherwise go on editing a status message for a cog that no longer exists.
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

        for process in self._running.values():
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        self._running.clear()

    def _lock_for(self, user_id: int) -> asyncio.Lock:
        """Returns the per-user run lock, creating it on first use."""
        return self._locks.setdefault(user_id, asyncio.Lock())

    def cancel_run(self, *, thread_id: int) -> bool:
        """Cancels a session's turn, whether it is running or still queued behind another.

        Cancelling the task rather than killing the subprocess is what makes those two cases one case.
        A queued turn is sitting in `run_and_post` waiting on the per-user lock and has no process to
        find; a running one is inside `run_claude`, whose `finally` kills the CLI as the cancellation
        unwinds through it.

        Parameters
        ----------
        thread_id: :class:`int`
            The session thread's ID.

        Returns
        -------
        :class:`bool`
            Whether there was a turn to cancel.

        """
        task: Optional[asyncio.Task[Any]] = self._tasks.get(thread_id)
        if task is None or task.done():
            return False
        task.cancel()
        # A cancel otherwise leaves no trace at all: the process is gone, the status message says so
        # and then ages out, and nothing is written down. Reconstructing one after the fact means
        # reading the CLI's own transcript for a gap. Whether it had launched yet is the part worth
        # recording — a queued cancel and a killed run look identical once both are over.
        LOGGER.info(
            "<%s.%s> | Cancelled a run | Thread: %s | State: %s",
            __class__.__name__,
            "cancel_run",
            thread_id,
            "running" if thread_id in self._running else "queued",
        )
        return True

    def modes_for(self, user_id: int) -> list[str]:
        """Returns the permission modes a user may select, adding `bypass` only for the ini allowlist."""
        names: list[str] = list(MODES)
        if user_id in self._settings.bypass_user_ids:
            names.append("bypass")
        return names

    # endregion

    # region --- Access

    async def load_access(self) -> None:
        """Replaces the cached elevated group with what the table currently holds."""
        async with self.bot.pool.acquire() as conn:
            rows: list[Row] = await conn.fetchall("""SELECT entityid, entity_type FROM claude_access""")

        self._access = AccessConfig(
            user_ids=frozenset(row["entityid"] for row in rows if row["entity_type"] == AccessEntity.USER),
            role_ids=frozenset(row["entityid"] for row in rows if row["entity_type"] == AccessEntity.ROLE),
        )
        LOGGER.info(
            "<%s.%s> | Claude elevated group loaded. | Users: %s | Roles: %s",
            __class__.__name__,
            "load_access",
            len(self._access.user_ids),
            len(self._access.role_ids),
        )

    async def grant_access(self, *, entity_id: int, entity: AccessEntity, added_by: int) -> None:
        """Adds a user or role to the elevated group and refreshes the cache.

        Parameters
        ----------
        entity_id: :class:`int`
            The user or role ID being granted.
        entity: :class:`AccessEntity`
            Which of the two `entity_id` names.
        added_by: :class:`int`
            The user making the grant, recorded on the row.

        """
        async with self.bot.pool.acquire() as conn:
            # The composite primary key makes a repeated grant a no-op rather than a duplicate row; the
            # original `added_by` is kept, as it names whoever actually widened the boundary.
            await conn.execute(
                """INSERT OR IGNORE INTO claude_access (entityid, entity_type, added_by, added_at) VALUES (?, ?, ?, ?)""",
                entity_id,
                entity.value,
                added_by,
                time.time(),
            )
        await self.load_access()
        # Worth a line of its own for the same reason `.mode bypass` is: this outlives the reply that
        # confirmed it and changes what somebody else's sessions can reach.
        LOGGER.warning(
            "<%s.%s> | Claude elevated access granted | %s: %s | By: %s",
            __class__.__name__,
            "grant_access",
            entity.value,
            entity_id,
            added_by,
        )

    async def revoke_access(self, *, entity_id: int, entity: AccessEntity) -> bool:
        """Removes a user or role from the elevated group, returning whether a row was actually there.

        Parameters
        ----------
        entity_id: :class:`int`
            The user or role ID being revoked.
        entity: :class:`AccessEntity`
            Which of the two `entity_id` names.

        Returns
        -------
        :class:`bool`
            Whether the entity had been in the group.

        """
        present: bool = entity_id in (self._access.role_ids if entity is AccessEntity.ROLE else self._access.user_ids)
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """DELETE FROM claude_access WHERE entityid = ? AND entity_type = ?""",
                entity_id,
                entity.value,
            )
        await self.load_access()
        if present:
            LOGGER.warning(
                "<%s.%s> | Claude elevated access revoked | %s: %s",
                __class__.__name__,
                "revoke_access",
                entity.value,
                entity_id,
            )
        return present

    def access_for(self, user: Union[discord.Member, discord.User, discord.abc.User]) -> Access:
        """Resolves what one user is allowed to do, from the ini allowlist and the elevated group.

        The validation the tiers rest on. Called on every turn rather than stored on the session, so a
        role added or taken away lands on that session's next message. Two deliberate properties:

        - **Fails closed.** Anything we cannot resolve gives the default :class:`Access`, which is
          :attr:`AccessTier.STANDARD`. A :class:`discord.User` rather than a
          :class:`discord.Member` — a DM, or a member who has left the guild — has no roles to check,
          so only a direct user grant can elevate them.
        - **Bypass does not imply elevated.** They are read from separate sources and set
          independently; see :class:`AccessTier`. A bypass user sits in their own workspace until they
          select `bypass` at `.mode`, so somebody wanting the project root by default has to be in the
          elevated group as well.

        Parameters
        ----------
        user: :class:`Union[discord.Member, discord.User, discord.abc.User]`
            The user to resolve; roles are only consulted when this is a :class:`discord.Member`.

        Returns
        -------
        :class:`Access`
            What that user is allowed to do right now.

        """
        elevated: bool = user.id in self._access.user_ids
        if not elevated and isinstance(user, discord.Member) and self._access.role_ids:
            elevated = any(role.id in self._access.role_ids for role in user.roles)

        return Access(elevated=elevated, bypass=user.id in self._settings.bypass_user_ids)

    async def resolve_access(self, *, state: SessionState, thread: discord.Thread) -> Access:
        """Re-resolves a session's tier from its owner's *current* standing and stores it on the state.

        The state line carries no tier -- deliberately, as a panel is editable history and a tier read
        back out of one would be a tier a user could try to forge. So it is recomputed here instead,
        from the owner's live roles, before every run and before the panel is rendered.

        Falls back to :attr:`AccessTier.STANDARD` when the owner cannot be resolved to a member of the
        thread's guild, which covers having left, being uncacheable, or the fetch failing outright.

        Parameters
        ----------
        state: :class:`SessionState`
            The session to update; :attr:`SessionState.access` is replaced in place.
        thread: :class:`discord.Thread`
            The session's thread, whose guild the owner is looked up in.

        Returns
        -------
        :class:`Access`
            The freshly resolved access, also now on `state`.

        """
        member: Optional[discord.Member] = thread.guild.get_member(state.user_id)
        if member is None:
            try:
                member = await thread.guild.fetch_member(state.user_id)
            except discord.HTTPException:
                member = None

        state.access = self.access_for(member) if member is not None else Access()
        return state.access

    @staticmethod
    def relative_cwd(*, state: SessionState) -> str:
        """Returns a session's cwd as a short label, for `.status` and the panel.

        The project root renders as its own directory name rather than a bare `.`, which would read as
        though nothing had been decided.
        """
        if state.cwd == state.root:
            return state.root.name
        return state.cwd.relative_to(state.root).as_posix()

    # endregion

    # region --- Forum plumbing

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
        """Returns the guild's `Claude Sessions` category, creating it when absent."""
        for category in guild.categories:
            if category.name == CATEGORY_NAME:
                return category
        return await guild.create_category(
            name=CATEGORY_NAME,
            reason="Claude Code session forums.",
            overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)},
        )

    async def get_forum(self, *, guild: discord.Guild, user: discord.Member) -> discord.ForumChannel:
        """Returns the user's private session forum in `guild`, creating it when absent.

        We hide the forum from `@everyone` and open it to the owning user only, so one user's sessions
        are never visible to another even though the category is shared.

        Parameters
        ----------
        guild: :class:`discord.Guild`
            The guild to look in.
        user: :class:`discord.Member`
            The user the forum belongs to.

        Returns
        -------
        :class:`discord.ForumChannel`
            The user's forum.

        """
        name: str = FORUM_NAME_FORMAT.format(name=user.name.lower())[:100]
        for channel in guild.forums:
            if channel.name == name:
                return channel

        category: discord.CategoryChannel = await self.get_category(guild=guild)
        return await guild.create_forum(
            name=name,
            topic=FORUM_TOPIC,
            category=category,
            reason=f"Claude Code sessions for {user}.",
            overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_threads=True),
            },
        )

    def is_session_thread(self, channel: Any) -> bool:
        """Returns whether `channel` is a thread inside one of our session forums."""
        return (
            isinstance(channel, discord.Thread)
            and isinstance(channel.parent, discord.ForumChannel)
            and channel.parent.category is not None
            and channel.parent.category.name == CATEGORY_NAME
        )

    def limit_error(self, raw: str) -> Optional[str]:
        """Returns a user-facing limit message when `raw` is a usage rejection, otherwise `None`.

        Also records the reset time on the cog so :meth:`limit_block` can turn away the sessions that
        haven't tried yet, instead of each of them spending a subprocess to learn the same thing.

        Parameters
        ----------
        raw: :class:`str`
            The CLI's error text or stderr output.

        Returns
        -------
        :class:`Optional[str]`
            The formatted message to show, or `None` when this was not a limit rejection.

        """
        if not is_rate_limited(raw):
            return None

        self._limit_reset = parse_reset_time(raw)
        # Logged here rather than at the call sites because this is where the cog-wide block is set:
        # one session hitting the wall turns every other one away until the reset, and that is worth
        # being able to date afterwards.
        LOGGER.warning(
            "<%s.%s> | Usage limit reached; all sessions blocked | Reset: %s",
            __class__.__name__,
            "limit_error",
            self._limit_reset.isoformat() if self._limit_reset is not None else "unknown",
        )
        return (
            f"The account's Claude usage limit is spent. {self.emoji_table.kuma_pout}\n"
            f"{self.reset_note()}\nYour session is untouched; the same prompt will resume it once the limit clears."
        )

    def reset_note(self) -> str:
        """Returns the 'try again' line, as a Discord timestamp when the reset time is known."""
        if self._limit_reset is None:
            return "-# The CLI did not say when it resets, so try again a little later."
        stamp: int = int(self._limit_reset.timestamp())
        return f"-# Resets <t:{stamp}:t> (<t:{stamp}:R>)."

    def limit_block(self) -> Optional[str]:
        """Returns the refusal to show when the account is still inside a known limit window.

        Clears the stored reset once it passes, so the next run goes through normally instead of
        needing a restart to forget it.
        """
        if self._limit_reset is None:
            return None
        if datetime.datetime.now(tz=datetime.UTC) >= self._limit_reset:
            self._limit_reset = None
            return None

        return (
            f"The account's Claude usage limit is still spent, so I have not started this run. "
            f"{self.emoji_table.kuma_tea}\n{self.reset_note()}"
        )

    async def is_command_invocation(self, message: discord.Message) -> bool:
        """Returns whether `message` is aimed at the bot's command handler rather than at Claude.

        Session threads are ordinary channels, so without this a prefix command or a mention invocation
        typed inside one gets forwarded to Claude as a prompt, burning a turn and billing us for it while
        the command runs anyway. `get_prefix()` covers both cases; `when_mentioned_or()` folds the bot's
        mention strings into the same list as the guild's stored prefixes.
        """
        prefixes: list[str] | str = await self.bot.get_prefix(message)
        if isinstance(prefixes, str):
            prefixes = [prefixes]

        return message.content.startswith(tuple(prefixes))

    def session_forums(self) -> Iterator[discord.ForumChannel]:
        """Yields every session forum the bot can see, across all of its guilds."""
        for guild in self.bot.guilds:
            for forum in guild.forums:
                if forum.category is not None and forum.category.name == CATEGORY_NAME:
                    yield forum

    def is_bot_post(self, thread: discord.Thread) -> bool:
        """Returns whether we opened this post, which is what makes it a session rather than a stray.

        A forum post's opening message belongs to whoever created the post, and that message is the only
        place session state lives. So a post we did not open can never hold a panel we are able to write
        to, and is not a session no matter where it sits.
        """
        return self.bot.user is not None and thread.owner_id == self.bot.user.id

    def active_threads(self, *, forum: discord.ForumChannel) -> list[discord.Thread]:
        """Returns the forum's live sessions; unarchived posts of ours that are neither closed nor expired.

        Posts we did not open are excluded rather than counted. They cannot be sessions, and counting
        them would spend the caller's :data:`MAX_SESSIONS_PER_USER` budget on posts that never held one.
        """
        return [
            thread for thread in forum.threads if self.is_bot_post(thread) and not thread.archived and not SessionStatus.of(thread).dormant
        ]

    # endregion

    # region --- Session state

    async def fetch_panel(self, *, thread: discord.Thread) -> PanelLookup:
        """Returns a session thread's opening post, fetching it when it is not cached.

        A forum post's opening message shares the thread's ID, which is what makes the panel
        recoverable after a restart has thrown away every cached :class:`discord.Message`.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread.

        Returns
        -------
        :class:`PanelLookup`
            The opening post, and whether Discord confirmed it is gone when there isn't one.

        """
        if thread.starter_message is not None:
            return PanelLookup(message=thread.starter_message, gone=False)
        try:
            return PanelLookup(message=await thread.fetch_message(thread.id), gone=False)
        except discord.NotFound:
            # A forum post's opening message cannot outlive the post, so this is all but unreachable;
            # `session_delete_listener` handles the deletion itself. Answered honestly anyway.
            return PanelLookup(message=None, gone=True)
        except discord.HTTPException as e:
            # A blip, a 5xx, an exhausted rate limit. The panel is very probably fine, and saying it is
            # missing would be a lie that reads as data loss, so callers are told we could not ask.
            LOGGER.warning("<%s.%s> | Could not read the panel of thread %s | Error: %s", __class__.__name__, "fetch_panel", thread.id, e)
            return PanelLookup(message=None, gone=False)

    async def fetch_message(self, *, thread: discord.Thread, message_id: int) -> Optional[discord.Message]:
        """Returns any message in a session thread by ID, for when the cache no longer holds it.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The thread to look in.
        message_id: :class:`int`
            The message to fetch.

        Returns
        -------
        :class:`Optional[discord.Message]`
            The message, or `None` when it is gone or unreachable.

        """
        try:
            return await thread.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def resolve_message(self, *, raw: str, origin: discord.Thread) -> tuple[Optional[discord.Message], Optional[str]]:
        """Finds the message `raw` points at, from a jump URL, an ID pair, or a bare message ID.

        A link or pair names its channel, so the lookup is a single fetch. A bare ID names nothing and
        Discord has no lookup-by-ID endpoint, so we have to guess the channel by probing; the thread the
        command was typed in first, then the rest of the guild up to :attr:`MESSAGE_SEARCH_LIMIT`
        channels.

        Parameters
        ----------
        raw: :class:`str`
            The link or ID as typed.
        origin: :class:`discord.Thread`
            The thread the command was run in; supplies the guild and is probed first.

        Returns
        -------
        :class:`tuple[Optional[discord.Message], Optional[str]]`
            The message, or `None` and a user-facing reason it could not be reached.

        """
        location: Optional[MessageLocation] = parse_message_location(raw)
        if location is None:
            return None, f"I could not read a message link or ID out of that. {self.emoji_table.kuma_hmm}"

        guild: Optional[discord.Guild] = origin.guild
        if guild is None:
            return None, f"I can only look up messages from inside a guild. {self.emoji_table.kuma_shrug}"

        # A cross-guild link would fail the fetch anyway unless the bot happens to be in that guild,
        # so we refuse it up front with a reason instead of an opaque 404 further down.
        if location.guild_id is not None and location.guild_id != guild.id:
            return None, (
                f"That link points at a different server, and I can only read messages from this one. {self.emoji_table.kuma_shrug}"
            )
        if location.guild_id is None and location.channel_id is not None and MESSAGE_LINK.search(raw) is not None:
            return None, f"That is a DM link, which I cannot read. {self.emoji_table.kuma_shrug}"

        if location.channel_id is not None:
            channel: Optional[Any] = guild.get_channel_or_thread(location.channel_id)
            if channel is None:
                # Not cached is not the same as not existing; we have to fetch an archived thread.
                try:
                    channel = await guild.fetch_channel(location.channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    return None, f"I cannot see that channel. {self.emoji_table.kuma_sad}"

            message: Optional[discord.Message] = await self._try_fetch(channel=channel, message_id=location.message_id)
            if message is None:
                return None, (
                    f"I could not fetch that message; it may be deleted, or I cannot read that channel. {self.emoji_table.kuma_sad}"
                )
            return message, None

        return await self._search_message(guild=guild, origin=origin, message_id=location.message_id)

    @staticmethod
    async def _try_fetch(*, channel: Any, message_id: int) -> Optional[discord.Message]:
        """Returns a message from a channel, or `None` for any reason it could not be read."""
        if not isinstance(channel, discord.abc.Messageable):
            return None
        try:
            return await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _search_message(
        self, *, guild: discord.Guild, origin: discord.Thread, message_id: int
    ) -> tuple[Optional[discord.Message], Optional[str]]:
        """Probes channels for a message ID, nearest first, when the input did not name a channel.

        We order by how likely each candidate is instead of by channel position; the thread the command
        was typed in, then its parent forum and that forum's other posts, then the guild's text channels
        and finally its remaining threads. We skip anything the bot cannot read history in rather than
        probing it, so the cap only gets spent on candidates that could hit. Each probe is a REST call,
        which is why there is a cap at all.
        """
        me: Optional[discord.Member] = guild.me
        candidates: list[Any] = [origin]
        if origin.parent is not None:
            candidates.append(origin.parent)
            candidates.extend(thread for thread in origin.parent.threads if thread.id != origin.id)
        candidates.extend(guild.text_channels)
        candidates.extend(guild.threads)

        seen: set[int] = set()
        probed: int = 0
        for channel in candidates:
            if channel.id in seen:
                continue
            seen.add(channel.id)

            # Skipping unreadable channels keeps the cap spent on candidates that could actually hit.
            if me is not None and not channel.permissions_for(me).read_message_history:
                continue
            if probed >= MESSAGE_SEARCH_LIMIT:
                break
            probed += 1

            message: Optional[discord.Message] = await self._try_fetch(channel=channel, message_id=message_id)
            if message is not None:
                return message, None

        return None, (
            f"I searched {probed} channel(s) and could not find that message. {self.emoji_table.kuma_sad}\n"
            "-# A bare ID has to be hunted for; paste the full message link instead and I can go straight to it."
        )

    async def get_state(self, *, thread: discord.Thread) -> Optional[SessionState]:
        """Returns the session state for a thread, re-reading the opening post on a cache miss.

        Every path that touches a session goes through here -- runs, dot commands and panel callbacks
        alike -- which is why the access tier is re-resolved on the way out rather than at each of
        those call sites. The rest of the state is cached; the tier never is.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread.

        Returns
        -------
        :class:`Optional[SessionState]`
            The state, or `None` when the opening post is gone or carries no state line.

        """
        cached: Optional[SessionState] = self._sessions.get(thread.id)
        if cached is not None:
            await self.resolve_access(state=cached, thread=thread)
            return cached

        panel: Optional[discord.Message] = (await self.fetch_panel(thread=thread)).message
        if panel is None:
            return None

        state: Optional[SessionState] = parse_state(message=panel, thread_id=thread.id)
        if state is not None:
            self._sessions[thread.id] = state
            await self.resolve_access(state=state, thread=thread)
            # A restart repopulates the cache from Discord and never re-renders the panel, so without
            # this a session that has not been touched since the last restart has no sidecar at all.
            await asyncio.to_thread(write_session_index, state=state)
        return state

    @staticmethod
    def attached_transcript(*, panel: discord.Message, state: SessionState) -> Optional[discord.Attachment]:
        """Returns this session's transcript attachment on its opening post, if one is there.

        We match on the exact filename, which encodes the session ID, so once `.new` has rotated the ID
        we correctly ignore the older generation's upload instead of offering it as this session's history.

        Parameters
        ----------
        panel: :class:`discord.Message`
            The session's opening post.
        state: :class:`SessionState`
            The session to match against.

        Returns
        -------
        :class:`Optional[discord.Attachment]`
            The attachment, or `None` when this session has none.

        """
        wanted: str = snapshot_path(workspace=state.workspace, session_id=state.session_id).name
        return discord.utils.get(panel.attachments, filename=wanted)

    async def update_panel(
        self,
        *,
        thread: discord.Thread,
        state: SessionState,
        status: SessionStatus = SessionStatus.ACTIVE,
    ) -> bool:
        """Re-renders a session's opening post so the panel matches the state it is meant to show.

        We re-reference any transcript already attached instead of re-uploading it. `Message.edit` keeps
        attachments it is not told about, but the view has to keep pointing at them or the file component
        silently disappears from the post.

        Returns
        -------
        :class:`bool`
            Whether the post now shows `state`. `False` means the panel is stale and still showing the
            settings it had before; the caller decides whether that is worth telling the user about.

        """
        panel: Optional[discord.Message] = (await self.fetch_panel(thread=thread)).message
        if panel is None:
            return False
        self._sessions[thread.id] = state
        # The sidecar mirrors the state line, so it is written wherever the state line is. Deliberately
        # before the edit rather than after: an index naming a session that Discord failed to re-render
        # is harmless, while a successful edit we failed to mirror is the exact gap this closes.
        await asyncio.to_thread(write_session_index, state=state)

        existing: Optional[discord.Attachment] = self.attached_transcript(panel=panel, state=state)
        try:
            await panel.edit(
                view=SessionPanel(
                    state=state,
                    modes=self.modes_for(state.user_id),
                    status=status,
                    transcript=existing.filename if existing is not None else None,
                ),
            )
        except discord.HTTPException as e:
            LOGGER.warning(
                "<%s.%s> | Failed to re-render the panel of thread %s | Error: %s", __class__.__name__, "update_panel", thread.id, e
            )
            return False
        return True

    def stale_panel_note(self, *, rendered: bool) -> str:
        """Returns the caveat to append to a settings reply when the panel didn't take the change.

        The setting itself did apply; `state` is the cached object the next run reads, so the session is
        already using it. What was lost is the *written* copy on the panel, which is the only thing a
        restart can read back — so the change holds until then and no further. Silence would let that
        surface later as a session that quietly reverted.
        """
        if rendered:
            return ""
        return f"\n-# I couldn't update the panel, so this applies now but won't survive a restart. {self.emoji_table.kuma_sad}"

    async def attach_transcript(self, *, thread: discord.Thread, state: SessionState) -> bool:
        """Uploads a session's transcript snapshot onto its opening post.

        Off unless `[CLAUDE] attach_transcripts` is set. A transcript holds everything Claude read during
        the session, so this moves file contents (and any credential it happened to open) off this machine
        and onto Discord's CDN.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread.
        state: :class:`SessionState`
            The session whose snapshot to upload.

        Returns
        -------
        :class:`bool`
            Whether a fresh upload was made.

        """
        if not self._settings.attach_transcripts:
            return False

        snapshot: Path = snapshot_path(workspace=state.workspace, session_id=state.session_id)
        if not snapshot.is_file():
            return False

        panel: Optional[discord.Message] = (await self.fetch_panel(thread=thread)).message
        if panel is None:
            return False

        limit: int = thread.guild.filesize_limit
        size: int = snapshot.stat().st_size
        if size > limit:
            LOGGER.warning(
                "<%s.%s> | Transcript is too large to attach | Thread: %s | Size: %.1fMB | Limit: %.1fMB",
                __class__.__name__,
                "attach_transcript",
                thread.id,
                size / 1024 / 1024,
                limit / 1024 / 1024,
            )
            return False

        # We replace outright instead of appending, so a re-snapshot supersedes the older upload rather
        # than stacking generations of the same session onto the post.
        try:
            await panel.edit(
                view=SessionPanel(
                    state=state,
                    modes=self.modes_for(state.user_id),
                    status=SessionStatus.of(thread),
                    transcript=snapshot.name,
                ),
                attachments=[discord.File(fp=snapshot, filename=snapshot.name)],
            )
        except discord.HTTPException as e:
            LOGGER.warning("<%s.%s> | Failed to attach a transcript | Error: %s", __class__.__name__, "attach_transcript", e)
            return False
        return True

    # endregion

    # region --- CLI

    def build_command(self, *, prompt: str, state: SessionState, resume: bool) -> list[str]:
        """Builds the `claude` argument list for one run.

        .. warning::
            **The project's `.claude/settings.json` does not apply to these runs and must never be
            relied on here.** That's Katelynn's file for interactive editing in the repo and `claude -p`
            does not load it. Tested against `claude 2.1.220`; a `Write` covered by that file's own
            `Write(extensions/.claude_asks/**)` rule was refused under `manual` with no `--settings`
            flag, and only allowed once the same file was passed explicitly as `--settings`.

            So a session gets exactly what this method hands it and nothing else. Anything a session
            needs has to come from the mode's :attr:`PermissionMode.tools` or the user's `.tools`, never
            from an assumption about that file. Editing it cannot break sessions and cannot fix them
            either; keep it that way, the two are meant to stay independent.

        Parameters
        ----------
        prompt: :class:`str`
            The prompt to send, already suffixed with :attr:`OUTPUT_DIR_NOTICE`.
        state: :class:`SessionState`
            The session being run; supplies the model, permission mode, effort level and session ID.
        resume: :class:`bool`
            Whether to `--resume` the session ID or claim it with `--session-id`.

        Returns
        -------
        :class:`list[str]`
            The argument list for :func:`asyncio.create_subprocess_exec`.

        """
        args: list[str] = [
            # Absolute where we can resolve one. The bare name is kept as the fallback so a machine
            # that installs the CLI somewhere we don't know about still works off its own PATH.
            claude_binary() or "claude",
            "-p",
            prompt,
            "--model",
            state.model,
            "--permission-mode",
            state.mode,
            "--effort",
            state.effort,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        # We use `--settings` instead of `--allowed-tools`, which tested as purely additive; a session
        # given `--allowed-tools Read,Edit` still ran Bash quite happily, so it can grant but never
        # confine. `--settings` takes the JSON inline and uses the same rule syntax as
        # `.claude/settings.json`, so `Bash(ruff check:*)` means here exactly what it means there.
        #
        # Inline JSON reliably carries bare tool names (`Read`) and `Bash(cmd:*)` prefix rules. Path
        # scoped *allow* rules like `Write(utils/**)` tested as *not* working inline, in relative and
        # absolute form alike, so don't add one and assume it took. Path scoped *deny* rules are a
        # different matter and do work; see the deny tables above.
        #
        # `--setting-sources ''` is passed so this is genuinely the whole rule set. Without it the CLI
        # is free to merge in `~/.claude/settings.json`, which is Katelynn's own file and grants things
        # no Discord session should inherit.
        permissions: dict[str, list[str]] = {}
        if state.allowed_tools:
            permissions["allow"] = state.allowed_tools

        denied: list[str] = self.deny_rules(state=state)
        if denied:
            permissions["deny"] = denied

        if permissions:
            args += ["--setting-sources", "", "--settings", json.dumps({"permissions": permissions})]
        args += ["--resume", state.session_id] if resume else ["--session-id", state.session_id]
        return args

    def deny_rules(self, *, state: SessionState) -> list[str]:
        """Returns the deny rules for a session, chosen by its cwd rather than by its tier.

        Keyed off :attr:`SessionState.cwd` because the rules are relative paths and only mean
        anything against the directory the CLI is started in. That keeps the two from drifting: a
        tier that moves to a different cwd gets the matching table automatically.

        Returns nothing at all under `bypass` mode. That mode exists to lift every limit for the
        handful of users in the ini, and a deny rule is the one thing that would still bite — tested;
        a denied path stayed denied under `bypassPermissions`. Leaving them on would make `bypass`
        quietly not mean what it says.

        Parameters
        ----------
        state: :class:`SessionState`
            The session about to run.

        Returns
        -------
        :class:`list[str]`
            Deny rules in `.claude/settings.json` syntax, relative to the session's cwd.

        """
        if state.mode == BYPASS_MODE:
            return []
        if state.cwd == state.root:
            return list(PROJECT_DENY) + foreign_workspace_deny(state=state)
        return list(WORKSPACE_DENY)

    async def run_claude(
        self,
        *,
        prompt: str,
        state: SessionState,
        resume: bool,
        progress: Optional[Callable[[str, str], Coroutine[Any, Any, None]]] = None,
    ) -> ClaudeResult:
        """Runs the Claude Code CLI for a session and returns its result.

        We serialize the run per user with :meth:`_lock_for` and register the subprocess in
        :attr:`_running`, which is what `.status` reads and what :meth:`cog_unload` reaps. The prompt
        always gets :attr:`OUTPUT_DIR_NOTICE` suffixed onto it so generated files stay inside the
        session's own workspace.

        .. note::
            A cancel arrives here as a :exc:`asyncio.CancelledError` raised at whatever line the run
            happens to be on — :meth:`ClaudeCog.cancel_run` cancels the task rather than killing the
            process — so the `finally` below is what kills the CLI, not any `return` of ours.

        Parameters
        ----------
        prompt: :class:`str`
            The prompt to send to Claude Code.
        state: :class:`SessionState`
            The session to run under.
        resume: :class:`bool`
            Whether this continues an existing session or opens a new one.
        progress: :class:`Optional[Callable[[str, str], Coroutine[Any, Any, None]]]`, optional
            An async callback invoked with `(tool name, target)` each time the CLI calls a tool, by
            default `None`.

        Returns
        -------
        :class:`ClaudeResult`
            The response text, session ID, cost and generated files, or a user-displayable error.

        """
        async with self._lock_for(state.user_id):
            # We check inside the lock. A run queued behind one that just hit the wall would otherwise
            # have passed this on the way in and launched anyway.
            blocked: Optional[str] = self.limit_block()
            if blocked is not None:
                return ClaudeResult(session_id=state.session_id, error=blocked)

            started: float = time.monotonic()
            root: Path = state.root
            workspace: Path = state.workspace
            cwd: Path = state.cwd
            await asyncio.to_thread(prepare_workspace, root=root, directory=workspace)
            before: dict[Path, float] = await asyncio.to_thread(_dir_snapshot, directory=workspace)

            # Only worth saying when the two differ. A standard session is already *inside* its
            # workspace, so there is nowhere else for its output to go and the notice would just be
            # telling it to create a subdirectory of itself.
            full_prompt: str = prompt
            if cwd != workspace:
                full_prompt += OUTPUT_DIR_NOTICE.format(directory=workspace.relative_to(cwd).as_posix())
            command_args: list[str] = self.build_command(prompt=full_prompt, state=state, resume=resume)

            try:
                process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
                    *command_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=1024 * 1024,
                    cwd=cwd,
                )
            except FileNotFoundError:
                # `create_subprocess_exec` raises this for a missing `cwd` just as readily as for a
                # missing executable, so say which one it actually was rather than blaming the CLI for
                # a project directory that has gone away underneath us.
                if not cwd.is_dir():
                    return ClaudeResult(error=f"The project directory `{cwd}` no longer exists. {self.emoji_table.kuma_shock}")
                return ClaudeResult(
                    error=f"`claude` was not found. Is Claude Code installed and on the bot's PATH? {self.emoji_table.kuma_shock}"
                )

            self._running[state.thread_id] = process
            assert process.stdout is not None and process.stderr is not None  # noqa: PT018, S101
            stderr_task: asyncio.Task[bytes] = asyncio.create_task(process.stderr.read())
            # The CLI streams one JSON event per line; the single `result` event carries the answer.
            result_event: Optional[dict] = None
            tool_calls: int = 0

            try:
                async with asyncio.timeout(delay=CLAUDE_TIMEOUT):
                    while line := await process.stdout.readline():
                        try:
                            event: dict = json.loads(line.decode().strip() or "{}")
                        except json.JSONDecodeError:
                            continue

                        if event.get("type") == "assistant" and progress is not None:
                            for block in event.get("message", {}).get("content", []):
                                if block.get("type") == "tool_use":
                                    name: str = str(block.get("name") or "tool")
                                    raw_input: Any = block.get("input")
                                    tool_input: dict = raw_input if isinstance(raw_input, dict) else {}
                                    await progress(name, tool_target(name=name, tool_input=tool_input, root=root))
                                    tool_calls += 1
                        elif event.get("type") == "result":
                            result_event = event
                    await process.wait()

            except TimeoutError:
                # A quarter of an hour of the shared account spent on a turn nobody gets an answer to,
                # and the only other record is a status message that ages out. The tool count says
                # whether it was working the whole time or hung early.
                LOGGER.warning(
                    "<%s.%s> | Run timed out after %ss | Thread: %s | Tools: %s",
                    __class__.__name__,
                    "run_claude",
                    CLAUDE_TIMEOUT,
                    state.thread_id,
                    tool_calls,
                )
                return ClaudeResult(error=f"Claude Code timed out after {CLAUDE_TIMEOUT} seconds. {self.emoji_table.kuma_head_clench}")
            finally:
                self._running.pop(state.thread_id, None)
                # The kill lives here rather than at each exit because a `.cancel` arrives as a
                # `CancelledError` raised at whatever line we happen to be on — there is no `return`
                # of ours to hang it off, and a CLI left running would hold the project directory and
                # keep spending the account's limit with nobody reading the answer.
                # `returncode` is still None on the timeout path (the signal is sent, the reaping
                # happens later), so that case comes through here too rather than killing twice.
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    stderr_task.cancel()

            stderr_output: str = (await stderr_task).decode().strip()
            if result_event is None:
                # A limit can end a run before any result event arrives, so we check stderr first.
                limit: Optional[str] = self.limit_error(stderr_output)
                if limit is not None:
                    return ClaudeResult(error=limit)

                # The reply truncates stderr to 1000 characters and the message eventually ages out, so
                # this is the only place the whole thing is kept. It is also the one failure here with
                # no explanation of its own — the CLI died and we are reading the wreckage.
                LOGGER.warning(
                    "<%s.%s> | Run exited without a result | Thread: %s | Exit: %s | Stderr: %s",
                    __class__.__name__,
                    "run_claude",
                    state.thread_id,
                    process.returncode,
                    stderr_output or "<empty>",
                )
                error_block: str = f"\n```\n{stderr_output[:1000]}\n```" if stderr_output else ""
                return ClaudeResult(error=f"Claude Code exited without a result. {self.emoji_table.kuma_crying}{error_block}")

            if result_event.get("is_error"):
                raw_error: str = str(result_event.get("result", "Unknown error"))
                # A spent limit isn't a failure worth a stack-trace shaped error; it just needs a time.
                limit = self.limit_error(raw_error)
                if limit is not None:
                    return ClaudeResult(session_id=str(result_event.get("session_id") or state.session_id), error=limit)

                return ClaudeResult(error=f"Claude returned an error. {self.emoji_table.kuma_sad}\n```\n{raw_error}\n```")

            # The CLI can hand back a different ID than we asked for (eg. a fork), so follow it.
            session_id: str = str(result_event.get("session_id") or state.session_id)
            text: str = str(result_event.get("result", "")).strip()
            denials: list[ToolDenial] = parse_denials(result_event=result_event, root=root, cwd=cwd)
            if not text:
                # We carry the denials even here. A turn blocked out of every tool it tried can come
                # back empty, and the denial list is the only thing that explains why.
                return ClaudeResult(
                    session_id=session_id,
                    error=f"Claude returned an empty response. {self.emoji_table.kuma_shrug}",
                    denials=denials,
                )

            files: list[Path] = await asyncio.to_thread(_new_files, directory=workspace, before=before)
            return ClaudeResult(
                text=text,
                session_id=session_id,
                cost_usd=result_event.get("total_cost_usd"),
                files=files,
                tool_calls=tool_calls,
                duration=time.monotonic() - started,
                denials=denials,
            )

    # endregion

    # region --- Rendering

    async def save_attachments(self, *, message: discord.Message, state: SessionState) -> tuple[list[Path], list[str]]:
        """Saves a message's attachments into the session workspace so the CLI can read them.

        Parameters
        ----------
        message: :class:`discord.Message`
            The message whose attachments to save.
        state: :class:`SessionState`
            The session to save them under.

        Returns
        -------
        :class:`tuple[list[Path], list[str]]`
            The saved paths and the display names of anything rejected for being too large.

        """
        saved: list[Path] = []
        rejected: list[str] = []
        if not message.attachments:
            return saved, rejected

        directory: Path = state.attachments
        await asyncio.to_thread(prepare_workspace, root=state.root, directory=directory)
        for attachment in message.attachments:
            if attachment.size > MAX_ATTACHMENT_SIZE:
                rejected.append(f"{attachment.filename} ({human_size(attachment.size)})")
                continue
            name: str = re.sub(r"[^\w.\-]", "_", attachment.filename)
            path: Path = directory.joinpath(f"{message.id}-{name}")
            await attachment.save(fp=path)
            saved.append(path)
        return saved, rejected

    def describe_attachments(self, *, paths: list[Path], root: Path) -> str:
        """Returns the bracketed note appended to a prompt telling the CLI where the uploads landed."""
        if not paths:
            return ""
        listed: str = ", ".join(f"`{path.relative_to(root).as_posix()}`" for path in paths)
        return f"\n\n[The user attached {len(paths)} file(s), saved at {listed}]"

    async def resolve_prompt_links(self, *, prompt: str, thread: discord.Thread, state: SessionState) -> tuple[str, list[str]]:
        """Saves every Discord message linked in a prompt into the workspace, and describes where.

        A pasted jump URL is inert text to the CLI as it cannot fetch Discord. We resolve the link here
        and hand over a path instead, so a link dropped into an ordinary sentence just works with no
        `.raw` round trip.

        We only follow full jump URLs. A bare snowflake gets ignored on purpose; it's indistinguishable
        from any other long number someone might mention, and resolving it would cost a
        channel-by-channel search on a guess.

        Parameters
        ----------
        prompt: :class:`str`
            The prompt as typed.
        thread: :class:`discord.Thread`
            The session thread, used to resolve and to bound the lookup to this guild.
        state: :class:`SessionState`
            The session whose workspace the messages are saved into.

        Returns
        -------
        :class:`tuple[str, list[str]]`
            The note to append to the prompt, and any user-facing reasons a link was skipped.

        """
        # Dedupe by message ID, so the same link pasted twice is one fetch and one file.
        wanted: dict[int, str] = {}
        for match in MESSAGE_LINK.finditer(prompt):
            wanted.setdefault(int(match.group("message")), match.group(0))

        if not wanted:
            return "", []

        saved: list[Path] = []
        problems: list[str] = []
        for message_id, link in list(wanted.items())[:MAX_PROMPT_LINKS]:
            target, error = await self.resolve_message(raw=link, origin=thread)
            if target is None:
                problems.append(f"{link} · {error}")
                continue

            destination: Path = state.workspace.joinpath(f"linked_message_{message_id}.md")
            # Provenance first, then the body verbatim. `.raw` stays the tool for byte-exact debugging;
            # this file is for context, so knowing who said it and where earns the header.
            body: str = f"{self.source_note(message=target)}\n\n{target.content}"
            await asyncio.to_thread(destination.write_text, body, encoding="utf-8")
            saved.append(destination)

        if len(wanted) > MAX_PROMPT_LINKS:
            problems.append(f"Only the first {MAX_PROMPT_LINKS} links were fetched; {len(wanted)} were in the message.")

        if not saved:
            return "", problems

        listed: str = ", ".join(f"`{path.relative_to(state.root).as_posix()}`" for path in saved)
        return f"\n\n[The user linked {len(saved)} Discord message(s), saved at {listed}]", problems

    @staticmethod
    async def send_to_thread(
        *,
        thread: discord.Thread,
        content: str,
        files: Optional[list[discord.File]] = None,
        reference: Optional[discord.Message] = None,
        view: Optional[Union[discord.ui.View, discord.ui.LayoutView]] = None,
        suppress_embeds: bool = False,
    ) -> discord.Message:
        """Sends into a session thread, omitting the optional arguments rather than passing `None`.

        :meth:`discord.abc.Messageable.send` is overloaded and none of its overloads accept `None` for
        these arguments; only the implementation signature does, which a type checker ignores.
        Passing `reference=None` happens to work today purely because the implementation guards on
        `is not None`; that is an accident of the runtime, not the documented surface. Building the
        call keyword by keyword keeps the call sites honest and the type checker quiet.

        .. warning::
            :attr:`discord.utils.MISSING` is *not* a substitute for `reference`. It is typed as `Any`
            so it satisfies the checker, but the runtime guard is `is not None`; a `MISSING` sentinel
            passes that check and then raises :class:`AttributeError` on `to_message_reference_dict`.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread to send into.
        content: :class:`str`
            The message content.
        files: :class:`Optional[list[discord.File]]`, optional
            Attachments to send, by default `None`. An empty list is treated as no files.
        reference: :class:`Optional[discord.Message]`, optional
            The message to reply to, by default `None`.
        view: :class:`Optional[Union[discord.ui.View, discord.ui.LayoutView]]`, optional
            Components to attach, by default `None`. :class:`discord.ui.LayoutView` is not a
            :class:`discord.ui.View` subclass, so both have to be named. In practice only the former
            works here: `content` is required, and a Components V2 message cannot carry any. See the
            comment on the spoofed panel in :meth:`ClaudeCog.claude_spoof`.
        suppress_embeds: :class:`bool`, optional
            Whether to stop Discord unfurling links in the content, by default `False`. Wanted for
            the status message, whose tool log carries the raw URL of anything the run fetched or
            searched; the flag sticks to the message, so the later animation edits inherit it.

        Returns
        -------
        :class:`discord.Message`
            The sent message.

        """
        arguments: dict[str, Any] = {"content": content}
        if files:
            arguments["files"] = files
        if reference is not None:
            arguments["reference"] = reference
        if view is not None:
            arguments["view"] = view
        if suppress_embeds:
            arguments["suppress_embeds"] = True
        return await thread.send(**arguments)

    async def post_response(
        self,
        *,
        thread: discord.Thread,
        result: ClaudeResult,
        state: SessionState,
        reference: Optional[discord.Message] = None,
        prompt: Optional[str] = None,
    ) -> None:
        """Posts a run's response into the thread, chunked, with any generated files attached.

        The first chunk replies to `reference` so the answer stays tied to the prompt that produced it.
        Later chunks follow as plain messages, with any generated files on the last one.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread to post into.
        result: :class:`ClaudeResult`
            The run result to render.
        state: :class:`SessionState`
            The session the run belongs to; supplies the model and effort shown in the footer.
        reference: :class:`Optional[discord.Message]`, optional
            The message being answered, replied to so an edited prompt keeps its answer beside it.
        prompt: :class:`Optional[str]`, optional
            The prompt that produced this run, by default `None`. Required for the retry offer under
            a run that had tools denied; without it the denials are reported but not actionable.

        """
        chunks: list[str] = chunk_text(result.text)
        files, skipped = await asyncio.to_thread(_build_reply_files, paths=result.files)

        footer: str = f"-# `{state.model}` · `{state.mode}` · `{state.effort}`"
        if result.cost_usd is not None:
            footer += f" · ${result.cost_usd:.4f}"
        if result.files:
            footer += f" · {len(result.files)} file(s) generated"
        if skipped:
            footer += f" · {len(skipped)} too large to attach, use `.get`"

        for index, chunk in enumerate(chunks):
            is_last: bool = index == len(chunks) - 1
            await self.send_to_thread(
                thread=thread,
                content=f"{chunk}\n{REPLY_SEPARATOR}\n{footer}" if is_last else chunk,
                files=files if is_last else None,
                reference=reference if index == 0 else None,
            )

        await self.post_denials(thread=thread, result=result, state=state, prompt=prompt)

    async def post_denials(
        self,
        *,
        thread: discord.Thread,
        result: ClaudeResult,
        state: SessionState,
        prompt: Optional[str] = None,
    ) -> None:
        """Posts what a run was refused, with a button that grants those tools and runs it again.

        This is a retry offer, not an approval prompt. `claude -p` never asks, so by the time we know a
        tool was blocked the turn is already over. Claude answers with whatever it could do and the
        button re-runs the same prompt with the blocked tools added to the session's allowlist.

        Two kinds of denial arrive here and only one of them is grantable. A tool the mode withheld is
        fixed by adding it to the allowlist; a call that reached outside the session's working directory
        is not fixable by any grant, so it is reported with the reason instead of a button that would
        change nothing and would still mark the allowlist explicit -- which then stops `.mode` managing
        it for the rest of the session.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread to post into.
        result: :class:`ClaudeResult`
            The run result; nothing is posted when it recorded no denials.
        state: :class:`SessionState`
            The session the run belongs to; supplies the owner allowed to press the button.
        prompt: :class:`Optional[str]`, optional
            The prompt to re-run, by default `None`. Without it the denials are still reported, just
            without the button.

        """
        if not result.denials:
            return

        confined: list[ToolDenial] = [denial for denial in result.denials if denial.outside_cwd]
        withheld: list[ToolDenial] = [denial for denial in result.denials if not denial.outside_cwd]

        shown: list[ToolDenial] = result.denials[:MAX_DENIALS_SHOWN]
        # Attributed to the mode only when the mode is actually what did it. A directory refusal has
        # nothing to do with `plan` or `acceptEdits`, and saying so sends the user to change a setting
        # that cannot help.
        heading: str = (
            "🔒 **Blocked during that run** · outside this session's directory:"
            if not withheld
            else f"🔒 **Blocked during that run** · `{state.mode}` did not allow these:"
        )
        lines: list[str] = [heading]
        lines.extend(
            f"- `{denial.tool}` {denial.target}".rstrip() + (" · outside the directory" if denial.outside_cwd and withheld else "")
            for denial in shown
        )
        if len(result.denials) > len(shown):
            lines.append(f"-# …and {len(result.denials) - len(shown)} more.")

        if confined:
            lines.append(
                f"-# This session works in `{self.relative_cwd(state=state)}` and cannot reach outside it; "
                f"no tool grant widens that."
                + ("" if state.access.elevated else " Elevated access is granted per user or role with `/claude access`."),
            )

        # Only tools we have not already granted, and never one that was refused for the directory, so
        # the button is never offered for something it cannot change.
        tools: list[str] = sorted({denial.tool for denial in withheld} - set(state.allowed_tools))
        if prompt is None or not tools:
            if withheld:
                lines.append("-# Grant them with `.tools`, or switch mode, then ask again.")
            await self.send_to_thread(thread=thread, content="\n".join(lines)[:MESSAGE_CHUNK_SIZE])
            return

        lines.append(f"-# Retrying adds `{', '.join(tools)}` to this session for good; a tool name grants every use of it.")
        await self.send_to_thread(
            thread=thread,
            content="\n".join(lines)[:MESSAGE_CHUNK_SIZE],
            view=DenialRetry(prompt=prompt, tools=tools, user_id=state.user_id),
        )

    async def retry_with_tools(self, *, interaction: discord.Interaction, prompt: str, tools: list[str]) -> None:
        """Grants tools to the interaction's session and re-runs a prompt that was blocked without them.

        Parameters
        ----------
        interaction: :class:`discord.Interaction`
            The button press; its thread names the session.
        prompt: :class:`str`
            The prompt to send again.
        tools: :class:`list[str]`
            The tool names to add to the session's allowlist.

        """
        # We resolve here instead of through `panel_state`, which answers failures on
        # `interaction.response`; that's already spent by the button clearing itself. The view checked
        # ownership before we got here.
        thread: Any = interaction.channel
        state: Optional[SessionState] = await self.get_state(thread=thread) if self.is_session_thread(thread) else None
        if state is None:
            await interaction.followup.send(
                content=f"I cannot read this session's state from its opening post, so there is nothing to retry. "
                f"{self.emoji_table.kuma_sad}",
                ephemeral=True,
            )
            return

        # We mark this explicit. The list was still chosen, even if by pressing a button instead of
        # typing `.tools`, and a later mode switch must not quietly take the grant back.
        state.allowed_tools = sorted(set(state.allowed_tools) | set(tools))
        state.tools_explicit = True
        self._sessions[thread.id] = state
        # A widened allowlist is worth writing down. It survives for the life of the session and
        # outlasts the ephemeral reply that announced it, so afterwards there is otherwise nothing to
        # say what a session was allowed to do, or who allowed it.
        LOGGER.info(
            "<%s.%s> | Granted denied tools | Thread: %s | User: %s | Tools: %s",
            __class__.__name__,
            "retry_with_tools",
            thread.id,
            interaction.user.id,
            ", ".join(tools),
        )

        await interaction.followup.send(
            content=f"Granted `{', '.join(tools)}` for this session, running that again. {self.emoji_table.kuma_happy}",
            ephemeral=True,
        )
        await self.run_and_post(thread=thread, state=state, prompt=prompt)

    async def run_and_post(
        self,
        *,
        thread: discord.Thread,
        state: SessionState,
        prompt: str,
        reference: Optional[discord.Message] = None,
    ) -> None:
        """Runs a prompt for a session and posts the answer, keeping the panel in step.

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
        # The whole method is the cancellable unit, the subprocess inside `run_claude` included.
        # Registering our own task is what lets a cancel reach a turn that has only been *queued*:
        # the wait for the per-user lock happens below, and a turn waiting there has no subprocess for
        # `_running` to be found under, yet it will still run. discord.py gives every listener and
        # command callback a task of its own, so this one is ours to cancel and nobody else's.
        task: Optional[asyncio.Task[Any]] = asyncio.current_task()
        if task is not None:
            self._tasks[thread.id] = task

        # Not a parameter, as the caller can't know this reliably. A post whose opening run failed, or
        # that was opened with no prompt at all, has a session ID the CLI has never seen; resuming it
        # would fail on this message and every one after it.
        resume: bool = await asyncio.to_thread(prepare_resume, state=state)
        controls: RunControls = RunControls(thread_id=thread.id, user_id=state.user_id)
        activity: discord.Message = await self.send_to_thread(
            thread=thread,
            content=f"{self.emoji_table.kuma_tea} Working on it...",
            reference=reference,
            view=controls,
            suppress_embeds=True,
        )

        # Every tool the run makes gets a line. We hold the lines here instead of reading them back off
        # the animation so the trailing "pending" entry can be flipped to done in place.
        log: list[str] = []
        # What the trailing line stands for and how many times in a row it has come through. A repeat
        # re-writes that line with an ` x2`, ` x3` tail instead of stacking identical entries, which a
        # run that reads the same file or greps the same pattern over and over does a lot of.
        last_event: Optional[str] = None
        repeats: int = 1
        # `status_last` puts the spinner under the tool log instead of over it, so the run reads like
        # the CLI does; finished work stacks upward and the live line holds the bottom edge.
        status: KumaAnimation = self.animate(
            activity,
            label="Thinking",
            header=f"{self.emoji_table.kuma_tea} Claude Code",
            status_last=True,
        )

        def visible_log() -> list[str]:
            """Returns the tail of the log, with a line standing in for whatever scrolled off."""
            visible: list[str] = log[-TOOL_LOG_VISIBLE:]
            if len(log) > TOOL_LOG_VISIBLE:
                visible = [f"-# …and {len(log) - TOOL_LOG_VISIBLE} earlier", *visible]
            return visible

        def render_log() -> None:
            """Pushes the tail of the log onto the animation."""
            status.clear_body()
            for line in visible_log():
                status.add_line(line)

        def cancelled_note() -> str:
            """Returns the closing content for a cancelled turn, tool log and all.

            The successful path collapses the log deliberately — on scrollback it is just noise
            between the question and the answer. A cancelled turn has no answer coming, so the log is
            the only account of what it managed before it was stopped, and `stop(final=...)` replaces
            the whole message rather than appending to it.
            """
            note: str = f"-# {self.emoji_table.kuma_shrug} Cancelled. Anything it had already written to the workspace is still there."
            lines: list[str] = visible_log()
            if not lines:
                return note
            # The trailing entry was still pending, and leaving it marked that way reads as a tool
            # that is somehow still running on a turn that has stopped.
            lines[-1] = lines[-1].replace(PENDING_MARK, "✖", 1)
            return "\n".join([*lines, "", note])

        async def on_progress(tool: str, target: str) -> None:
            """Ticks off the previous tool call and opens a pending line for the one just started."""
            nonlocal last_event, repeats
            event: str = f"`{tool}` {target}".rstrip()
            verb: str = TOOL_VERBS.get(tool, tool)
            status.label = self.to_progressive(verb)

            if event == last_event:
                # The same call again; count it on the line we already have. It stays pending either
                # way, as the newest of the repeats is the one still running.
                repeats += 1
                log[-1] = f"{PENDING_MARK} {event} x{repeats}"
            else:
                if log:
                    # The previous call must have finished for a new one to start.
                    log[-1] = log[-1].replace(PENDING_MARK, DONE_MARK, 1)
                last_event = event
                repeats = 1
                log.append(f"{PENDING_MARK} {event}")
            render_log()

        try:
            async with status:
                result: ClaudeResult = await self.run_claude(prompt=prompt, state=state, resume=resume, progress=on_progress)

                if result.error is not None:
                    await status.stop(final=result.error)
                    # A run blocked out of every tool it tried can fail with nothing to show for it, so
                    # the denials are still worth posting; they're the explanation the error text lacks.
                    await self.post_denials(thread=thread, result=result, state=state, prompt=prompt)
                    return

                # Collapse to a single line. The detail was useful while it ran, but on scrollback it's
                # just noise between the question and the answer.
                summary: list[str] = [f"{result.tool_calls} tool(s)"] if result.tool_calls else []
                summary.append(f"{result.duration:.0f}s")
                if result.cost_usd is not None:
                    summary.append(f"${result.cost_usd:.4f}")
                await status.stop(final=f"-# {self.emoji_table.kuma_tea} {' · '.join(summary)}")

            # A resumed session can fork to a new ID; follow it so the next reply resumes the right one.
            if result.session_id and result.session_id != state.session_id:
                state.session_id = result.session_id
                await self.update_panel(thread=thread, state=state)

            await self.post_response(thread=thread, result=result, state=state, reference=reference, prompt=prompt)

        except asyncio.CancelledError:
            # `stop()` is safe to call again — the context manager's own call left `_task` as `None` —
            # so this only writes the closing content. Awaiting inside a cancelled task is fine; the
            # cancellation is delivered once, and nothing cancels us twice.
            await status.stop(final=cancelled_note())
            # Re-raised rather than swallowed: the caller asked for this task to end, and discord.py
            # discards a cancelled listener or command task without logging it as an error.
            raise
        finally:
            # The turn is over however we got here, so the button has to go with it; a live Cancel on a
            # finished run is worse than no button at all. The animation only ever wrote `content`, so
            # the components are still exactly what we sent.
            controls.stop()
            with contextlib.suppress(discord.HTTPException):
                await activity.edit(view=None)
            # Guarded on identity: a later turn in the same thread may already have claimed the slot.
            if self._tasks.get(thread.id) is task:
                del self._tasks[thread.id]

    # endregion

    # region --- Listeners

    @commands.Cog.listener(name="on_message")
    async def session_message_listener(self, message: discord.Message) -> None:
        """Treats a message in a session thread as the next turn of that session, or a dot command."""
        if message.author.bot or not self.is_session_thread(message.channel):
            return

        # We check before the status reply so a command in a dormant session stays silent on our end.
        if await self.is_command_invocation(message=message):
            return

        # A ping is not a turn. Our *own* mention is already caught above — `when_mentioned_or()` puts
        # it in the prefix list — but a ping aimed at anyone else lands here as ordinary content and
        # runs, billing a session turn for a message that says nothing. Only a message left empty once
        # the mentions come out is dropped, so `@someone look at this` still asks the question it means
        # to, and an attachment posted with nothing but a ping is still a prompt.
        if not MENTION.sub("", message.content).strip() and not message.attachments:
            return

        thread: discord.Thread = message.channel  # type: ignore[assignment]
        if not self.is_bot_post(thread):
            # A post of theirs we haven't removed yet; either the create listener lost the race with
            # their first message, or it was made while we were offline. Removing it here means they
            # get the DM now rather than at the next sweep, and no reply that implies a session exists.
            await self.discard_foreign_post(thread=thread)
            return

        status: SessionStatus = SessionStatus.of(thread)
        if status.dormant:
            await message.reply(
                content=f"This session is {status.value}; use **Restore Session** on the opening post, or start a "
                f"fresh one with `/claude ask`. {self.emoji_table.kuma_shrug}",
            )
            return

        state: Optional[SessionState] = await self.get_state(thread=thread)
        if state is None:
            # Our post, but we have no state for it, and the two reasons want opposite answers. Re-read
            # the panel to find out which: it costs a call, but only on a path that is already failing.
            lookup: PanelLookup = await self.fetch_panel(thread=thread)
            if not lookup.gone and lookup.message is None:
                await message.reply(
                    content=f"I couldn't read this post's session panel just now, so I don't know where the session "
                    f"left off. Nothing is lost; try again in a moment. {self.emoji_table.kuma_sad}",
                )
            else:
                # Either Discord says the post is gone, or the panel is there and carries no state line.
                # Both mean the session record no longer exists and cannot be rebuilt from here.
                await message.reply(
                    content=f"This post has no session on it any more, and its panel was the only record of one, so "
                    f"there is nothing to resume. Any files are still on disk; start a fresh session with "
                    f"`/claude ask`. {self.emoji_table.kuma_sad}",
                )
            return
        if message.author.id != state.user_id:
            return

        content: str = message.content.strip()
        if content.startswith(".") and len(content) > 1:
            await self.handle_dot_command(message=message, thread=thread, state=state, raw=content[1:])
            return

        await self.handle_prompt(message=message, thread=thread, state=state)

    @commands.Cog.listener(name="on_message_edit")
    async def session_edit_listener(self, before: discord.Message, after: discord.Message) -> None:
        """Re-runs an edited prompt, replying to the edited message so the correction stays in context.

        Only a genuine content change counts. Discord also fires this for embed resolution and pins, and
        re-running on those would double-charge every link a user posts.
        """
        if after.author.bot or before.content == after.content or not self.is_session_thread(after.channel):
            return

        if await self.is_command_invocation(message=after):
            return

        thread: discord.Thread = after.channel  # type: ignore[assignment]
        if SessionStatus.of(thread).dormant:
            return

        state: Optional[SessionState] = await self.get_state(thread=thread)
        if state is None or after.author.id != state.user_id:
            return

        content: str = after.content.strip()
        if content.startswith(".") and len(content) > 1:
            await self.handle_dot_command(message=after, thread=thread, state=state, raw=content[1:])
            return

        await self.handle_prompt(message=after, thread=thread, state=state, edited=True)

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
        """Removes a deleted session's workspace and cached state; the post was the only record of it."""
        state: Optional[SessionState] = self._sessions.pop(payload.thread_id, None)
        # Cancelling rather than dropping the entry: the turn would otherwise keep running against a
        # workspace we are about to delete, and post its answer into a thread that no longer exists.
        self.cancel_run(thread_id=payload.thread_id)
        self._running.pop(payload.thread_id, None)

        if state is not None:
            await asyncio.to_thread(shutil.rmtree, state.workspace, ignore_errors=True)
            return

        # Not cached (a restart, most likely). The thread ID names the directory, so sweep for it.
        for user_directory in await asyncio.to_thread(self._session_directories):
            candidate: Path = user_directory.joinpath(str(payload.thread_id))
            if candidate.is_dir():
                await asyncio.to_thread(shutil.rmtree, candidate, ignore_errors=True)
                return

    @commands.Cog.listener(name="on_guild_channel_delete")
    async def forum_delete_listener(self, channel: discord.abc.GuildChannel) -> None:
        """Drops every workspace behind a deleted session forum, since its posts went with it."""
        if not isinstance(channel, discord.ForumChannel) or channel.category is None or channel.category.name != CATEGORY_NAME:
            return
        for thread in channel.threads:
            state: Optional[SessionState] = self._sessions.pop(thread.id, None)
            self.cancel_run(thread_id=thread.id)
            self._running.pop(thread.id, None)
            if state is not None:
                await asyncio.to_thread(shutil.rmtree, state.workspace, ignore_errors=True)

    @staticmethod
    def _session_directories() -> list[Path]:
        """Returns every per-user workspace directory the cleanup sweep should consider.

        Returns
        -------
        :class:`list[Path]`
            The per-user directories under `.claude_sessions/`.

        """
        container: Path = sessions_root(root=PROJECT_ROOT)
        if not container.is_dir():
            return []
        return [entry for entry in container.iterdir() if entry.is_dir()]

    # endregion

    # region --- Cleanup

    @tasks.loop(hours=CLEANUP_INTERVAL_HOURS, reconnect=True)
    async def cleanup_loop(self) -> None:
        """Expires stale sessions, clears non-session posts and drops workspaces whose post is gone."""
        try:
            snapshotted, expired = await self.sweep_sessions()
        except Exception as e:
            LOGGER.exception("<%s.%s> | Failed to sweep Claude sessions.", __class__.__name__, "cleanup_loop", exc_info=e)
            return

        foreign: int = await self.discard_foreign_posts()
        orphans: int = await self.prune_orphan_workspaces()
        if snapshotted or expired or foreign or orphans:
            LOGGER.info(
                "<%s.%s> | Claude cleanup | Snapshotted: %s | Expired: %s | Non-session posts removed: %s | "
                "Orphaned workspaces removed: %s",
                __class__.__name__,
                "cleanup_loop",
                snapshotted,
                expired,
                foreign,
                orphans,
            )

    @cleanup_loop.before_loop
    async def before_cleanup_loop(self) -> None:
        """Waits for the cache to fill so the first pass can actually see the session forums."""
        await self.bot.wait_until_ready()

    @staticmethod
    def last_active(thread: discord.Thread) -> float:
        """Returns when a thread last saw a message, as a unix timestamp."""
        if thread.last_message_id is not None:
            return discord.utils.snowflake_time(thread.last_message_id).timestamp()
        if thread.created_at is not None:
            return thread.created_at.timestamp()
        return time.time()

    async def sweep_sessions(self) -> tuple[int, int]:
        """Snapshots idle sessions' transcripts and expires the ones that have aged out.

        Both jobs walk the same threads, so they share one pass. The snapshot happens well before expiry
        on purpose; waiting until a session ages out would race Claude Code's own transcript prune, and
        the whole point of the copy is to still have it afterwards.

        Returns
        -------
        :class:`tuple[int, int]`
            How many transcripts were snapshotted and how many sessions were expired.

        """
        now: float = time.time()
        expire_before: float = now - (SESSION_MAX_AGE_DAYS * 86400)
        snapshot_before: float = now - (TRANSCRIPT_IDLE_HOURS * 3600)
        snapshotted: int = 0
        expired: int = 0

        for forum in self.session_forums():
            for thread in self.active_threads(forum=forum):
                idle_since: float = self.last_active(thread)
                if idle_since >= snapshot_before:
                    continue

                if await self.snapshot_session(thread=thread):
                    snapshotted += 1
                if idle_since < expire_before and await self.expire_session(thread=thread):
                    expired += 1
        return snapshotted, expired

    async def snapshot_session(self, *, thread: discord.Thread) -> bool:
        """Copies a session's Claude Code transcript into its workspace, so it survives expiry.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread to snapshot.

        Returns
        -------
        :class:`bool`
            Whether a fresh snapshot was written.

        """
        state: Optional[SessionState] = await self.get_state(thread=thread)
        if state is None:
            return False

        size: Optional[int] = await asyncio.to_thread(snapshot_transcript, state=state)
        if size is None:
            return False

        LOGGER.info(
            "<%s.%s> | Snapshotted a Claude transcript | Thread: %s | Size: %.1fKB",
            __class__.__name__,
            "snapshot_session",
            thread.id,
            size / 1024,
        )
        await self.attach_transcript(thread=thread, state=state)
        return True

    async def expire_session(
        self,
        *,
        thread: discord.Thread,
        status: SessionStatus = SessionStatus.EXPIRED,
        notify: bool = True,
    ) -> bool:
        """Retires one session by renaming the post, disabling its panel, then retiring the thread.

        Both statuses lock the post, which takes Manage Threads to undo; the owner cannot talk their way
        back into a dormant session, only the Restore button can, acting as us. Expiry archives on top of
        that so aged-out posts fall out of the forum listing, while a closed one stays visible.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread to retire.
        status: :class:`SessionStatus`, optional
            Why it is being retired, by default :attr:`SessionStatus.EXPIRED`. The Close button and
            `.close` pass `CLOSED`; the session was ended deliberately rather than aged out, and the
            post should say so.
        notify: :class:`bool`, optional
            Whether to post a closing notice in the thread, by default `True`.

        Returns
        -------
        :class:`bool`
            Whether the session was retired; `False` when Discord refused any step. The sweep retries
            on its next pass, so a refusal here delays a retirement rather than losing it.

        """
        state: Optional[SessionState] = await self.get_state(thread=thread)
        reason: str = f"Claude session {status.value}."
        try:
            if state is not None:
                # A backstop. A session that went from active to aged-out between two sweeps never hit
                # the idle snapshot, and after this it can't be resumed without one.
                await asyncio.to_thread(snapshot_transcript, state=state)
                # Locking a post whose panel still renders as live leaves a session that reads as usable
                # and answers nothing, and the lock is the part only Manage Threads can undo. So a panel
                # we could not re-render aborts the retirement; the sweep comes back to it.
                if not await self.update_panel(thread=thread, state=state, status=status):
                    LOGGER.warning(
                        "<%s.%s> | Left thread %s live; its panel could not be re-rendered as %s.",
                        __class__.__name__,
                        "expire_session",
                        thread.id,
                        status.value,
                    )
                    return False
            if notify:
                await thread.send(content=f"⏹️ {PANEL_NOTICES[status]} {self.emoji_table.kuma_tea}")

            # Retitle off the *stripped* name so a session closed and later expired does not end up
            # carrying both prefixes.
            name: str = f"{status.prefix}{SessionStatus.strip(thread.name)}"[:100]
            # Both dormant statuses lock the post; only Manage Threads can undo that, so the way back in
            # is the panel's Restore button acting as us. Expiry archives on top of it, dropping an
            # aged-out session out of the forum listing; a closed one stays visible until it ages out too.
            edits: dict[str, Any] = {"locked": True, "archived": status is SessionStatus.EXPIRED}
            if name != thread.name:
                edits["name"] = name
            await thread.edit(reason=reason, **edits)
        except discord.HTTPException as e:
            LOGGER.warning("<%s.%s> | Failed to retire thread %s | Error: %s", __class__.__name__, "expire_session", thread.id, e)
            return False

        self._sessions.pop(thread.id, None)
        return True

    async def discard_foreign_post(self, *, thread: discord.Thread) -> bool:
        """Deletes a post in a session forum that we did not open, DMing its author what was removed.

        A hand-made post looks like a session to everything that goes by location — it satisfies
        :meth:`is_session_thread`, so replies in it reached the session listener, and it spent one of the
        author's session slots. It can never become a session either; its opening message is theirs, so
        we cannot write a panel or a state line into it. Removing it is the only outcome that leaves the
        forum in a state the rest of the cog can describe.

        We DM the text back first. It is the author's own writing and this is the one place we destroy
        something a person typed, so it should not disappear without a copy and an explanation.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The post to remove; assumed to already be a non-session post in one of our forums.

        Returns
        -------
        :class:`bool`
            Whether the post was deleted.

        """
        opening: Optional[discord.Message] = (await self.fetch_panel(thread=thread)).message
        text: str = (opening.content if opening is not None else "").strip()
        owner: Optional[discord.Member | discord.User] = thread.owner or self.bot.get_user(thread.owner_id or 0)

        if owner is not None:
            notice: str = (
                f"I removed your post **{thread.name}** from {thread.parent.mention if thread.parent else 'your session forum'}. "
                f"That forum only holds sessions I opened; a post I did not make has nowhere to keep its session state, so it "
                f"cannot be replied to and would have taken up one of your {MAX_SESSIONS_PER_USER} slots. Open one with "
                f"`/claude ask` instead. {self.emoji_table.kuma_tea}"
            )
            if text:
                notice += f"\n\nYour text, so it isn't lost:\n>>> {text[: MESSAGE_CHUNK_SIZE // 2]}"
            # A closed DM is not a reason to leave the post sitting there, so this never blocks the delete.
            with contextlib.suppress(discord.HTTPException):
                await owner.send(content=notice)

        try:
            await thread.delete(reason="Not a Claude session; posts in a session forum must be opened by the bot.")
        except discord.HTTPException as e:
            LOGGER.warning(
                "<%s.%s> | Failed to remove non-session post %s | Error: %s", __class__.__name__, "discard_foreign_post", thread.id, e
            )
            return False
        LOGGER.info("<%s.%s> | Removed non-session post %s (%s).", __class__.__name__, "discard_foreign_post", thread.id, thread.name)
        return True

    async def discard_foreign_posts(self) -> int:
        """Removes every non-session post sitting in a session forum, catching those made while offline.

        Returns
        -------
        :class:`int`
            How many posts were removed.

        """
        removed: int = 0
        for forum in self.session_forums():
            for thread in list(forum.threads):
                if not self.is_bot_post(thread) and await self.discard_foreign_post(thread=thread):
                    removed += 1
        return removed

    async def prune_orphan_workspaces(self) -> int:
        """Removes workspaces whose thread no longer exists, catching deletions missed while offline.

        Returns
        -------
        :class:`int`
            How many workspace directories were removed.

        """
        known: set[int] = set()
        for forum in self.session_forums():
            known.update(thread.id for thread in forum.threads)
            async for thread in forum.archived_threads(limit=None):
                known.add(thread.id)

        removed: int = 0
        for user_directory in await asyncio.to_thread(self._session_directories):
            for workspace in await asyncio.to_thread(lambda directory=user_directory: [e for e in directory.iterdir() if e.is_dir()]):
                if workspace.name.isdigit() and int(workspace.name) not in known:
                    await asyncio.to_thread(shutil.rmtree, workspace, ignore_errors=True)
                    removed += 1
            if not await asyncio.to_thread(lambda directory=user_directory: any(directory.iterdir())):
                await asyncio.to_thread(user_directory.rmdir)
        return removed

    # endregion

    # region --- Dot commands

    async def handle_dot_command(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, raw: str) -> None:
        """Resolves and runs a `.command` typed in a session thread.

        We handle the bot-mapped commands locally. `claude -p` is non-interactive and silently ignores
        real slash commands, so `.model` has to become a `--model` flag instead of prompt text. The
        allowlisted pass-throughs are the exception; the CLI does honour those as a prompt.

        Parameters
        ----------
        message: :class:`discord.Message`
            The message that carried the command.
        thread: :class:`discord.Thread`
            The session thread.
        state: :class:`SessionState`
            The session the command acts on.
        raw: :class:`str`
            The command text with its leading dot already stripped.

        """
        name, _, argument = raw.partition(" ")
        command, candidates = resolve_dot_command(name)

        if command is None:
            hint: str = f" Did you mean {', '.join(f'`.{entry}`' for entry in candidates)}?" if candidates else ""
            await message.reply(content=f"`.{name}` is not a command I know.{hint} Try `.help`. {self.emoji_table.kuma_shrug}")
            return

        if command.handler is None:
            # An allowlisted pass-through, so hand the CLI its own slash command as the prompt.
            passthrough: str = f"/{command.name} {argument.strip()}".strip()
            await self.run_and_post(thread=thread, state=state, prompt=passthrough, reference=message)
            return

        await getattr(self, command.handler)(message=message, thread=thread, state=state, argument=argument.strip())

    async def dot_help(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Lists the in-thread commands."""
        await message.reply(content=self.help_text(user_id=state.user_id))

    def help_text(self, *, user_id: int) -> str:
        """Returns the `.help` listing, with the risky modes mentioned only for users allowed them."""
        lines: list[str] = ["### In-thread commands", "Any unambiguous abbreviation works; `.m opus` is `.model opus`.", ""]
        mapped: list[str] = []
        passthrough: list[str] = []
        for command in DOT_COMMANDS:
            usage: str = f" {command.usage}" if command.usage else ""
            aliases: str = f" *(`.{'`, `.'.join(command.aliases)}`)*" if command.aliases else ""
            entry: str = f"- `.{command.name}{usage}` · {command.summary}{aliases}"
            if command.handler is not None:
                mapped.append(entry)
            else:
                passthrough.append(entry)

        lines.extend(mapped)
        lines.append("")
        lines.append("**Passed through to the CLI:**")
        lines.extend(passthrough)
        if user_id in self._settings.bypass_user_ids:
            lines.append("")
            lines.append("-# `.mode bypass` is available to you; it removes the approval gate on every tool.")
        return "\n".join(lines)[:MESSAGE_CHUNK_SIZE]

    async def dot_model(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:
        """Switches the session's model."""
        choice: str = argument.lower()
        if choice not in MODELS:
            await message.reply(content=f"Pick one of: {', '.join(f'`{name}`' for name in MODELS)}. {self.emoji_table.kuma_shrug}")
            return
        state.model = MODELS[choice]
        rendered: bool = await self.update_panel(thread=thread, state=state)
        await message.reply(
            content=f"Model set to `{state.model}` for this session. {self.emoji_table.kuma_happy}"
            f"{self.stale_panel_note(rendered=rendered)}",
        )

    @staticmethod
    def apply_mode(*, state: SessionState, name: str) -> None:
        """Points a session at a permission mode, carrying that mode's tool preset with it.

        We skip the preset whenever the user picked their own list at `.tools`. A hand-written allowlist
        is a deliberate act, and having it vanish on the next mode change is the kind of silent loss you
        only notice when a run behaves oddly.

        Parameters
        ----------
        state: :class:`SessionState`
            The session to switch; mutated in place.
        name: :class:`str`
            The short mode name, e.g. `plan` or `bypass`.

        """
        state.mode = _mode_value(name)
        if not state.tools_explicit:
            state.allowed_tools = list(_mode(name).tools)

        # `bypass` alone gets a line. The others narrow or hold the gate and are ordinary session
        # settings; this one takes the approval gate off every tool for the rest of the session, and
        # it is reachable from the panel select and `.mode` alike. `cog_load` records who *may* choose
        # it — this records that somebody did.
        if name == "bypass":
            LOGGER.warning(
                "<%s.%s> | Session switched to bypassPermissions | Thread: %s | User: %s",
                __class__.__name__,
                "apply_mode",
                state.thread_id,
                state.user_id,
            )

    def mode_reply(self, *, state: SessionState, name: str) -> str:
        """Returns the confirmation line for a mode switch, spelling out the tool list it landed on."""
        lines: list[str] = [f"Permission mode set to `{state.mode}`. {self.emoji_table.kuma_happy}"]
        if name == "bypass":
            lines.append("⚠️ Every tool now runs without an approval gate.")
        elif state.tools_explicit:
            lines.append(f"-# Keeping your `.tools` list: `{', '.join(state.allowed_tools)}`.")
        elif state.allowed_tools:
            lines.append(f"-# Pre-granted by this mode: `{', '.join(state.allowed_tools)}`. `.tools` replaces the list.")
        return "\n".join(lines)

    async def dot_mode(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:
        """Switches the session's permission mode, gating `bypass` behind the ini allowlist."""
        choice: str = argument.lower()
        allowed: list[str] = self.modes_for(state.user_id)

        if choice == "bypass" and "bypass" not in allowed:
            await message.reply(
                content=f"`bypass` removes the approval gate on every tool, so it is limited to the user IDs listed under "
                f"`[CLAUDE] bypass_user_ids` in `local.ini`. You are not one of them. {self.emoji_table.kuma_pout}",
            )
            return
        if choice not in allowed:
            await message.reply(content=f"Pick one of: {', '.join(f'`{name}`' for name in allowed)}. {self.emoji_table.kuma_shrug}")
            return

        self.apply_mode(state=state, name=choice)
        rendered: bool = await self.update_panel(thread=thread, state=state)
        await message.reply(content=self.mode_reply(state=state, name=choice) + self.stale_panel_note(rendered=rendered))

    async def dot_effort(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:
        """Switches the session's effort level, listing what each one buys when called bare.

        Effort is the one setting most often changed mid-session, usually after watching a run under-
        or over-explore, so a bare `.effort` reports the current level and the menu rather than
        scolding, which is the common case for this command.
        """
        choice: str = argument.lower()
        if not choice:
            menu: str = "\n".join(f"- `{name}` · {EFFORT_DESCRIPTIONS[name]}" for name in EFFORTS)
            await message.reply(content=f"Effort is `{state.effort}`. Pick one with `.effort <level>`:\n{menu}")
            return
        if choice not in EFFORTS:
            await message.reply(content=f"Pick one of: {', '.join(f'`{name}`' for name in EFFORTS)}. {self.emoji_table.kuma_shrug}")
            return

        state.effort = choice
        rendered: bool = await self.update_panel(thread=thread, state=state)
        await message.reply(
            content=f"Effort set to `{state.effort}` · {EFFORT_DESCRIPTIONS[choice]} {self.emoji_table.kuma_happy}"
            f"{self.stale_panel_note(rendered=rendered)}",
        )

    async def dot_plan(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Shortcut for `.mode plan`."""
        await self.dot_mode(message=message, thread=thread, state=state, argument="plan")

    async def dot_edits(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Shortcut for `.mode edits`."""
        await self.dot_mode(message=message, thread=thread, state=state, argument="edits")

    async def dot_new(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Drops the session's transcript by claiming a fresh session ID, keeping the post and its files."""
        state.session_id = str(uuid.uuid4())
        state.started = time.time()
        rendered: bool = await self.update_panel(thread=thread, state=state)
        await message.reply(
            content=f"Started a fresh session in this post; Claude no longer remembers the conversation above. "
            f"Files are untouched. {self.emoji_table.kuma_happy}{self.stale_panel_note(rendered=rendered)}",
        )

    async def dot_ignore(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Toggles whether ordinary messages in this post are run as prompts.

        For talking *about* a session in its own thread — pasting notes to yourself, working a problem
        out with someone else — without every message costing a turn. A bare `.ignore` flips the
        current setting, which is the whole point of it being a toggle; `on` and `off` are there for
        when you would rather say what you mean than remember where you left it.
        """
        choice: str = argument.lower()
        if choice and choice not in {"on", "off"}:
            await message.reply(content=f"`.ignore` takes `on`, `off`, or nothing at all to flip it. {self.emoji_table.kuma_shrug}")
            return

        state.ignoring = choice == "on" if choice else not state.ignoring
        if state.ignoring:
            await message.reply(
                content=f"Ignoring messages in this post; nothing here reaches Claude until `.ignore` again. Commands "
                f"still work, so this one can undo itself. {self.emoji_table.kuma_tea}",
            )
            return
        await message.reply(content=f"Listening again; messages here run as prompts. {self.emoji_table.kuma_happy}")

    async def dot_status(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Reports the session's settings and workspace size."""
        file_count, total_size = await asyncio.to_thread(_dir_stats, directory=state.workspace)
        # We measure off the thread, not off `started`. The age-out runs from the last message, so a
        # session opened weeks ago but replied to today has its full window ahead of it.
        expires: float = self.last_active(thread) + (SESSION_MAX_AGE_DAYS * 86400)
        lines: list[str] = [
            "### Session status",
            f"- Session: `{state.session_id}`",
            f"- Model: `{state.model}`",
            f"- Mode: `{state.mode}`",
            f"- Effort: `{state.effort}`",
            f"- Access: `{state.access.tier.label}` · working in `{self.relative_cwd(state=state)}`",
            f"- Started: <t:{int(state.started)}:R>",
            f"- Expires: <t:{int(expires)}:R> if left idle",
            f"- Workspace: {file_count} file(s), {human_size(total_size)}",
        ]
        if state.mode == BYPASS_MODE:
            lines.append("- ⚠️ `bypass` ignores the working directory; nothing on this machine is out of reach.")
        if state.allowed_tools:
            source: str = "yours, via `.tools`" if state.tools_explicit else f"from `{state.mode}`"
            lines.append(f"- Allowed tools: `{', '.join(state.allowed_tools)}` ({source})")
        if state.ignoring:
            lines.append("- 🔇 Messages here are being ignored; `.ignore` resumes.")
        if state.thread_id in self._running:
            lines.append("- ⏳ A run is in progress; `.cancel` stops it.")
        elif state.thread_id in self._tasks:
            # Queued, not running: another of this user's sessions holds the lock. Worth saying which,
            # or the spinner on screen looks like a run that has stalled.
            lines.append("- ⏳ A turn is queued behind another of your sessions; `.cancel` drops it.")
        await message.reply(content="\n".join(lines))

    async def dot_access(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Explains the session's tier, where it can reach, and what would widen it.

        Read-only on purpose. Membership is changed at `/claude access`, which is admin-gated; a user
        being told their own tier is not the same as being able to alter it.
        """
        tier: AccessTier = state.access.tier
        lines: list[str] = [
            f"### Access · {tier.label}",
            f"-# {tier.description}",
            f"- Working directory: `{self.relative_cwd(state=state)}`",
            f"- Modes you may select: {', '.join(f'`{name}`' for name in self.modes_for(state.user_id))}",
        ]

        if state.mode == BYPASS_MODE:
            lines.append("- ⚠️ `bypass` is active, so the working directory above is not being enforced at all.")
        elif tier is AccessTier.BYPASS:
            lines.append("- You may switch to `bypass` at `.mode`, which lifts every limit including the directory.")

        if tier is AccessTier.STANDARD:
            lines.append(
                "-# Elevated access (the whole project rather than this workspace) is granted per user or role with `/claude access`.",
            )
        # Editing still needs a mode change whatever the tier, which is the most common surprise here.
        if state.mode == MODES["plan"].value:
            lines.append("-# `plan` touches nothing; `.edits` is what lets a run write inside the directory above.")
        await message.reply(content="\n".join(lines))

    async def dot_files(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Lists the session workspace."""
        entries: list[tuple[str, int]] = await asyncio.to_thread(_list_workspace, directory=state.workspace)
        if not entries:
            await message.reply(content=f"Nothing in this session's workspace yet. {self.emoji_table.kuma_shrug}")
            return
        listed: str = "\n".join(f"- `{name}` · {human_size(size)}" for name, size in entries[:40])
        extra: str = f"\n-# …and {len(entries) - 40} more." if len(entries) > 40 else ""
        await message.reply(content=f"### Workspace ({len(entries)} file(s))\n{listed}{extra}"[:MESSAGE_CHUNK_SIZE])

    async def dot_get(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Uploads one file from the session workspace, refusing anything outside it."""
        if not argument:
            await message.reply(content=f"Which file? Try `.files` first. {self.emoji_table.kuma_shrug}")
            return

        target: Path = state.workspace.joinpath(argument)
        # The name came from a Discord message, so confirm it stayed inside the workspace after
        # resolution; `../../local.ini` would otherwise be a perfectly valid join.
        workspace: Path = state.workspace.resolve()
        if not is_inside(target, workspace):
            await message.reply(content=f"`{argument}` is not inside this session's workspace. {self.emoji_table.kuma_pout}")
            return
        if not target.is_file():
            await message.reply(content=f"`{argument}` is not a file I can find. {self.emoji_table.kuma_shrug}")
            return
        if target.stat().st_size > MAX_REPLY_FILE_SIZE:
            await message.reply(
                content=f"`{argument}` is {human_size(target.stat().st_size)}; Discord will not take anything over "
                f"{human_size(MAX_REPLY_FILE_SIZE)}. {self.emoji_table.kuma_pout}",
            )
            return
        await message.reply(file=discord.File(fp=target, filename=target.name))

    async def dot_raw(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:
        """Saves another message's raw content into the workspace so a run can read it back.

        Built for the one case Discord makes impossible to debug from the rendered side; a reply whose
        formatting came out wrong. The rendered message tells you nothing about *which* backtick went
        missing, and pasting it back into the thread just re-renders it. Writing the exact characters
        into the workspace lets the next prompt read them, and the reply reports the fence and tick
        counts up front since an odd one of either is almost always the answer.
        """
        if not argument:
            await message.reply(
                content=f"Which message? Give me a jump link, a `channel-message` pair, or a message ID. {self.emoji_table.kuma_shrug}",
            )
            return

        target, error = await self.resolve_message(raw=argument, origin=thread)
        if target is None or isinstance(target.channel, (discord.DMChannel, discord.GroupChannel)):
            await message.reply(content=error)
            return

        destination: Path = state.workspace.joinpath(f"message_{target.id}.txt")
        await asyncio.to_thread(destination.write_text, target.content, encoding="utf-8")

        fences: int = target.content.count(CODE_FENCE)
        ticks: int = target.content.count("`") - (fences * len(CODE_FENCE))
        balance: str = "balanced" if fences % 2 == 0 else f"{self.emoji_table.kuma_shock} **odd fence count**"
        await message.reply(
            content=(
                f"Saved `{destination.name}` to this session's workspace. {self.emoji_table.kuma_peak}\n"
                f"-# {target.author.display_name} in {target.channel.mention} · {len(target.content)} chars · "
                f"{fences} fence(s) ({balance}) · {ticks} loose backtick(s)"
            ),
            file=discord.File(fp=destination, filename=destination.name),
        )

    async def dot_tools(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Sets `--allowed-tools` for the session, or hands the list back to the mode when cleared."""
        if argument.lower() in {"", "clear", "none", "off", "all"}:
            # Clearing hands the list back to the permission mode instead of emptying it outright, so
            # `.tools clear` undoes the override rather than leaving the session in a state no mode
            # would have produced.
            state.tools_explicit = False
            state.allowed_tools = list(_mode(_mode_name(state.mode)).tools)
            if state.allowed_tools:
                await message.reply(
                    content=f"Your tool list is cleared, back to `{state.mode}`'s own: "
                    f"`{', '.join(state.allowed_tools)}`. {self.emoji_table.kuma_happy}",
                )
            else:
                await message.reply(
                    content=f"Tool allowlist cleared; the permission mode alone decides now. {self.emoji_table.kuma_happy}",
                )
            return

        tools, error = validate_allowed_tools(argument)
        if error is not None:
            await message.reply(content=f"{error} {self.emoji_table.kuma_pout}")
            return
        state.allowed_tools = tools
        state.tools_explicit = True
        # Same reasoning as the grant on `retry_with_tools`: a hand-written allowlist outlives the
        # reply that confirmed it and no longer answers to the mode, so it is worth a line of its own.
        LOGGER.info(
            "<%s.%s> | Allowed tools set by hand | Thread: %s | User: %s | Tools: %s",
            __class__.__name__,
            "dot_tools",
            state.thread_id,
            state.user_id,
            ", ".join(tools),
        )
        await message.reply(
            content=f"Allowed tools set to `{', '.join(tools)}`. {self.emoji_table.kuma_happy}"
            f"\n-# Yours now, so a mode switch will not replace it; `.tools clear` hands it back.",
        )

    async def dot_rename(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Renames the session's post."""
        if not argument:
            await message.reply(content=f"Give me a title. {self.emoji_table.kuma_shrug}")
            return
        await thread.edit(name=thread_title(argument), reason="Claude session renamed by its owner.")
        await message.reply(content=f"Renamed. {self.emoji_table.kuma_happy}")

    async def dot_cancel(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Stops this session's turn, whether it is running or still queued behind another."""
        # Same call the Cancel button on the status message makes; typing it and pressing it are the
        # same action. It used to look in `_running` instead, which answered "nothing is running" to a
        # turn that was genuinely queued — and then let it run.
        if not self.cancel_run(thread_id=state.thread_id):
            await message.reply(content=f"Nothing is running in this session. {self.emoji_table.kuma_shrug}")
            return
        await message.reply(content=f"Cancelled. {self.emoji_table.kuma_tea}")

    async def dot_restore(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Restores this session's transcript, for when the CLI dropped it but the post is still open."""
        _, outcome = await self.restore_session(thread=thread)
        await message.reply(content=outcome)

    async def dot_close(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, argument: str) -> None:  # noqa: ARG002
        """Closes and locks the session on request, without waiting for the age-out.

        Same intent as the panel's Close button, so it marks the post `CLOSED` too; the session was
        ended deliberately and its transcript is still on disk, so the post shouldn't claim it aged out.
        """
        # Answered before the work because closing locks the post, and a reply sent after that lands in a
        # thread nobody can reply to. The follow-up only appears if the close then failed.
        await message.reply(content=f"Closing this session. {self.emoji_table.kuma_tea}")
        if not await self.expire_session(thread=thread, status=SessionStatus.CLOSED, notify=False):
            await message.reply(
                content=f"Discord wouldn't let me close it, so the session is still open; try again in a moment. "
                f"{self.emoji_table.kuma_sad}",
            )

    # endregion

    # region --- Prompts

    async def handle_prompt(self, *, message: discord.Message, thread: discord.Thread, state: SessionState, edited: bool = False) -> None:
        """Runs an ordinary message in a session thread as the next turn of that session.

        Parameters
        ----------
        message: :class:`discord.Message`
            The message carrying the prompt.
        thread: :class:`discord.Thread`
            The session thread.
        state: :class:`SessionState`
            The session to continue.
        edited: :class:`bool`, optional
            Whether this came from an edit rather than a fresh message, by default `False`.

        """
        if state.ignoring:
            # Muted at `.ignore`. Guarded here rather than in the listener so an *edit* is dropped too;
            # both paths arrive through this method. Silent on purpose — a session told to ignore the
            # thread that then answers every message with "I'm ignoring you" has ignored nothing.
            return

        saved, rejected = await self.save_attachments(message=message, state=state)
        if rejected:
            await message.reply(
                content=f"Skipped {', '.join(f'`{name}`' for name in rejected)}; over the "
                f"{human_size(MAX_ATTACHMENT_SIZE)} limit. {self.emoji_table.kuma_pout}",
            )

        # `clean_content` rather than `content`, so a mention arrives as `@Kat` instead of the raw
        # `<@412734157819609090>`. Discord resolves those client-side and we were forwarding the
        # snowflake, which reads as noise: the run cannot tell who was named, and neither can it tell
        # a person from a role or a channel. Falls back to `@deleted-user` when the name is gone,
        # which is still more than an ID says. It leaves markdown and URLs alone, so nothing further
        # down — the link resolver, the chunker — sees a different string than it did before.
        prompt: str = message.clean_content.strip()
        if not prompt and not saved:
            return

        # A post opened without a prompt has nothing to name itself after, so the first real message
        # names it. Guarded on the placeholder so a `.rename` is never quietly overwritten.
        if prompt and thread.name.startswith(PLACEHOLDER_TITLE):
            with contextlib.suppress(discord.HTTPException):
                await thread.edit(name=thread_title(prompt), reason="Claude session named by its first message.")
        if edited:
            prompt = f"[The user corrected their previous message; this replaces it.]\n\n{prompt}"
        prompt += self.describe_attachments(paths=saved, root=state.root)

        # We report a link that can't be followed but never treat it as fatal. The rest of the prompt is
        # still a perfectly good question, and failing the whole turn over one bad paste would be rude.
        linked, problems = await self.resolve_prompt_links(prompt=prompt, thread=thread, state=state)
        prompt += linked
        if problems:
            await message.reply(content=f"{self.emoji_table.kuma_hmm} " + "\n".join(f"-# {entry}" for entry in problems))

        await self.run_and_post(thread=thread, state=state, prompt=prompt, reference=message)

    # endregion

    # region --- Panel callbacks

    async def panel_state(self, interaction: discord.Interaction) -> Optional[tuple[discord.Thread, SessionState]]:
        """Resolves the thread and session behind a panel interaction, answering the user on failure.

        Parameters
        ----------
        interaction: :class:`discord.Interaction`
            The component interaction.

        Returns
        -------
        :class:`Optional[tuple[discord.Thread, SessionState]]`
            The thread and its session, or `None` when either could not be resolved.

        """
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
        """Switches the model from the panel's select."""
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        thread, state = resolved
        state.model = MODELS.get(value, DEFAULT_MODEL)
        await interaction.response.edit_message(view=SessionPanel(state=state, modes=self.modes_for(state.user_id)))
        self._sessions[thread.id] = state

    async def panel_mode(self, interaction: discord.Interaction, *, value: str) -> None:
        """Switches the permission mode from the panel's select, re-checking the bypass allowlist."""
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        thread, state = resolved

        if value == "bypass" and state.user_id not in self._settings.bypass_user_ids:
            await interaction.response.send_message(
                content=f"`bypass` is limited to the user IDs in `[CLAUDE] bypass_user_ids`. {self.emoji_table.kuma_pout}",
                ephemeral=True,
            )
            return

        self.apply_mode(state=state, name=value)
        await interaction.response.edit_message(view=SessionPanel(state=state, modes=self.modes_for(state.user_id)))
        self._sessions[thread.id] = state
        # Ephemeral instead of in-thread. The panel already shows the new mode, so this only needs to
        # carry the part the panel can't; what the switch did to the tool list.
        if not state.tools_explicit and state.allowed_tools:
            await interaction.followup.send(
                content=f"`{state.mode}` pre-grants `{', '.join(state.allowed_tools)}`; `.tools` replaces the list.",
                ephemeral=True,
            )

    async def panel_effort(self, interaction: discord.Interaction, *, value: str) -> None:
        """Switches the effort level from the panel's select."""
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        thread, state = resolved
        state.effort = value if value in EFFORTS else DEFAULT_EFFORT
        await interaction.response.edit_message(view=SessionPanel(state=state, modes=self.modes_for(state.user_id)))
        self._sessions[thread.id] = state

    async def panel_new(self, interaction: discord.Interaction) -> None:
        """Starts a fresh session inside the same post."""
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        thread, state = resolved
        state.session_id = str(uuid.uuid4())
        state.started = time.time()
        self._sessions[thread.id] = state
        await interaction.response.edit_message(view=SessionPanel(state=state, modes=self.modes_for(state.user_id)))
        await interaction.followup.send(
            content=f"Started a fresh session in this post; the conversation above is no longer remembered. {self.emoji_table.kuma_happy}",
            ephemeral=True,
        )

    async def panel_files(self, interaction: discord.Interaction) -> None:
        """Lists the session workspace privately."""
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        _, state = resolved
        entries: list[tuple[str, int]] = await asyncio.to_thread(_list_workspace, directory=state.workspace)
        if not entries:
            await interaction.response.send_message(content="Nothing in this session's workspace yet.", ephemeral=True)
            return
        listed: str = "\n".join(f"- `{name}` · {human_size(size)}" for name, size in entries[:40])
        await interaction.response.send_message(
            content=f"### Workspace ({len(entries)} file(s))\n{listed}"[:MESSAGE_CHUNK_SIZE], ephemeral=True
        )

    async def panel_help(self, interaction: discord.Interaction) -> None:
        """Shows the dot-command listing privately."""
        await interaction.response.send_message(content=self.help_text(user_id=interaction.user.id), ephemeral=True)

    async def recover_snapshot(self, *, thread: discord.Thread, state: SessionState) -> bool:
        """Downloads a session's transcript back from its opening post into the workspace.

        The fallback for when the local snapshot is gone (a deleted workspace, a rebuilt machine)
        but the post still carries the upload.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The session thread.
        state: :class:`SessionState`
            The session to recover.

        Returns
        -------
        :class:`bool`
            Whether a snapshot was written back into the workspace.

        """
        panel: Optional[discord.Message] = (await self.fetch_panel(thread=thread)).message
        if panel is None:
            return False

        attachment: Optional[discord.Attachment] = self.attached_transcript(panel=panel, state=state)
        if attachment is None:
            return False

        try:
            payload: bytes = await attachment.read()
        except (discord.HTTPException, discord.NotFound):
            return False

        target: Path = snapshot_path(workspace=state.workspace, session_id=state.session_id)
        await asyncio.to_thread(prepare_workspace, root=state.root, directory=state.workspace)
        await asyncio.to_thread(target.write_bytes, payload)
        LOGGER.info(
            "<%s.%s> | Recovered a Claude transcript from Discord | Thread: %s | Size: %.1fKB",
            __class__.__name__,
            "recover_snapshot",
            thread.id,
            len(payload) / 1024,
        )
        return True

    async def restore_session(self, *, thread: discord.Thread) -> tuple[bool, str]:
        """Brings an expired session back; the transcript first, then the post itself.

        The transcript is what actually makes `--resume` work again. Unlocking the post without it would
        give us a thread that looks alive and then fails on the first reply.

        Parameters
        ----------
        thread: :class:`discord.Thread`
            The expired session thread.

        Returns
        -------
        :class:`tuple[bool, str]`
            Whether the session is resumable, and a message describing the outcome.

        """
        state: Optional[SessionState] = await self.get_state(thread=thread)
        if state is None:
            return False, f"I cannot read this session's state from its opening post. {self.emoji_table.kuma_sad}"

        # The workspace copy is the fast path; the post's attachment is what survives the workspace
        # being wiped or the machine being rebuilt, which is the whole point of uploading it.
        if not snapshot_path(workspace=state.workspace, session_id=state.session_id).is_file():
            await self.recover_snapshot(thread=thread, state=state)

        restored: bool = await asyncio.to_thread(restore_transcript, state=state)
        if not restored:
            return False, (
                f"There is no transcript snapshot for this session, so Claude has nothing to resume from. "
                f"Its files are still here; start a new session with `/claude ask`. {self.emoji_table.kuma_sad}"
            )

        name: str = SessionStatus.strip(thread.name)
        with contextlib.suppress(discord.HTTPException):
            await thread.edit(name=name, archived=False, locked=False, reason="Claude session restored.")
        await self.update_panel(thread=thread, state=state)
        return True, f"Session restored; reply here to pick it back up. {self.emoji_table.kuma_happy}"

    async def panel_restore(self, interaction: discord.Interaction) -> None:
        """Restores an expired session from its transcript snapshot."""
        thread: Any = interaction.channel
        if not self.is_session_thread(thread):
            await interaction.response.send_message(content="This panel is not attached to a session.", ephemeral=True)
            return

        state: Optional[SessionState] = await self.get_state(thread=thread)
        if state is not None and interaction.user.id != state.user_id:
            await interaction.response.send_message(content="This session isn't yours!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        _, message = await self.restore_session(thread=thread)
        await interaction.followup.send(content=message, ephemeral=True)

    async def panel_close(self, interaction: discord.Interaction) -> None:
        """Closes and locks the session from the panel.

        We mark it closed rather than expired. The user ended this one on purpose and its transcript is
        still on disk, so the post shouldn't claim it aged out of being resumable.
        """
        resolved: Optional[tuple[discord.Thread, SessionState]] = await self.panel_state(interaction)
        if resolved is None:
            return
        thread, _ = resolved
        await interaction.response.send_message(content=f"Closing this session. {self.emoji_table.kuma_tea}", ephemeral=True)
        if not await self.expire_session(thread=thread, status=SessionStatus.CLOSED, notify=False):
            await interaction.edit_original_response(
                content=f"Discord wouldn't let me close it, so the session is still open; try again in a moment. "
                f"{self.emoji_table.kuma_sad}",
            )

    # endregion

    # region --- Commands

    async def prepare_forum(self, *, interaction: discord.Interaction) -> tuple[Optional[discord.ForumChannel], Optional[str]]:
        """Runs every check a new session needs and returns the user's session forum.

        Shared by `/claude ask` and the `Ask Claude` context menu so both refuse for the same reasons in
        the same wording. Only ever one half of the return is set.

        Parameters
        ----------
        interaction: :class:`discord.Interaction`
            The interaction opening the session; its user owns the forum.

        Returns
        -------
        :class:`tuple[Optional[discord.ForumChannel], Optional[str]]`
            The forum to open the post in, or the message explaining why we cannot.

        """
        guild: Optional[discord.Guild] = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return None, f"Sessions live in forum posts, so run this in a guild. {self.emoji_table.kuma_shrug}"

        missing: list[str] = self.missing_permissions(guild=guild)
        if missing:
            return None, (
                f"I need {', '.join(f'`{name}`' for name in missing)} in this guild to build your session forum. "
                f"{self.emoji_table.kuma_pout}"
            )

        try:
            forum: discord.ForumChannel = await self.get_forum(guild=guild, user=interaction.user)
        except discord.Forbidden:
            return None, (
                f"Discord refused to let me create your session forum; check my role's channel permissions. {self.emoji_table.kuma_pout}"
            )

        missing = self.missing_permissions(guild=guild, channel=forum)
        if missing:
            return None, (
                f"I need {', '.join(f'`{name}`' for name in missing)} in {forum.mention} to run sessions there. "
                f"{self.emoji_table.kuma_pout}"
            )

        active: list[discord.Thread] = self.active_threads(forum=forum)
        if len(active) >= MAX_SESSIONS_PER_USER:
            listed: str = "\n".join(f"- {thread.mention}" for thread in active[:MAX_SESSIONS_PER_USER])
            return None, (
                f"You already have {len(active)} of {MAX_SESSIONS_PER_USER} sessions open. Close one with `.close` "
                f"(or its ⏹️ button) first:\n{listed}"
            )
        return forum, None

    async def edit_and_expire(self, *, interaction: discord.Interaction, content: str) -> None:
        """Edits the deferred ephemeral response, then clears it after :attr:`message_timeout` seconds.

        `edit_original_response` has no `delete_after` of its own, so we lean on `Message.delete(delay=)`,
        which schedules the removal in the background instead of holding the command open. The session
        itself lives in the forum post; this reply is only a signpost to it, so it shouldn't linger.
        """
        message: discord.InteractionMessage = await interaction.edit_original_response(content=content)
        await message.delete(delay=self.message_timeout)

    @claude.command(name="ask", description="Open a new Claude Code session as a forum post you can reply in.")
    @app_commands.describe(
        prompt="The opening prompt. Leave it out to just open the post and start talking there.",
        model="Which model to open the session on. Defaults to Sonnet; changeable later.",
        allow_edits="Auto-approve file edits. Off by default, which runs the session in plan mode.",
        attachment="An optional file to share with Claude Code (saved in the session's workspace).",
    )
    @app_commands.choices(model=MODEL_CHOICES)
    @app_commands.guild_only()
    @app_commands.check(_is_owner)
    async def ask(
        self,
        interaction: discord.Interaction,
        prompt: Optional[str] = None,
        model: Optional[str] = None,
        allow_edits: bool = False,
        attachment: Optional[discord.Attachment] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        # `create_forum`'s overwrites take `Member | Role`, never a bare `User`. `@guild_only` above
        # guarantees a Member, so this only narrows the type for the forum call below.
        assert isinstance(interaction.user, discord.Member)  # noqa: S101
        forum, error = await self.prepare_forum(interaction=interaction)
        if forum is None:
            # `error` is only ever None when a forum came back, which the branch above rules out.
            await self.edit_and_expire(interaction=interaction, content=error or "")
            return

        if attachment is not None and attachment.size > MAX_ATTACHMENT_SIZE:
            await self.edit_and_expire(
                interaction=interaction,
                content=f"That file is {human_size(attachment.size)}; the limit is {human_size(MAX_ATTACHMENT_SIZE)}. "
                f"{self.emoji_table.kuma_pout}",
            )
            return

        state: SessionState = SessionState(
            thread_id=0,
            user_id=interaction.user.id,
            session_id=str(uuid.uuid4()),
            model=MODELS.get(model or "", DEFAULT_MODEL),
            mode=MODES["edits"].value if allow_edits else MODES["plan"].value,
            # Set here so the very first render and the workspace creation below already agree with the
            # tier; `get_state` re-resolves it on every turn after this one.
            access=self.access_for(interaction.user),
        )

        created: discord.channel.ThreadWithMessage = await forum.create_thread(
            name=thread_title(prompt) if prompt else placeholder_title(interaction.locale),
            view=SessionPanel(state=state, modes=self.modes_for(state.user_id)),
            reason=f"Claude Code session for {interaction.user}.",
        )
        thread: discord.Thread = created.thread
        # The workspace is named after the thread, which only exists now. The panel itself needs no
        # re-render; the thread ID is not part of the state line.
        state.thread_id = thread.id
        self._sessions[thread.id] = state

        await self.edit_and_expire(
            interaction=interaction,
            content=f"Session opened: {thread.mention} · "
            f"{'reply in there to continue it' if prompt else 'say something in there to start'}. "
            f"{self.emoji_table.kuma_happy}",
        )

        staged: list[Path] = []
        if attachment is not None:
            directory: Path = state.attachments
            await asyncio.to_thread(prepare_workspace, root=state.root, directory=directory)
            name: str = re.sub(r"[^\w.\-]", "_", attachment.filename)
            path: Path = directory.joinpath(f"{int(time.time())}-{name}")
            await attachment.save(fp=path)
            staged.append(path)

        if prompt is None:
            # Nothing to run yet. The panel is live so the model and mode can be set before the first
            # message, and `run_and_post` will open the session instead of resuming it since no
            # transcript exists for this ID yet.
            opener: str = (
                f"{self.emoji_table.kuma_tea} Session ready on `{state.model}` in `{state.mode}` mode; "
                "send a message here to start. Adjust it with the controls above, or `.help` for the commands."
            )
            if staged:
                # Reuses the note meant for the CLI, minus its square brackets. The sentence inside them
                # already reads correctly to a person, so it isn't worth a second phrasing.
                opener += f"\n\nYour upload is at {self.describe_attachments(paths=staged, root=state.root).strip()[1:-1]}."
            await self.send_to_thread(thread=thread, content=opener)
            return

        await self.send_to_thread(thread=thread, content=f"**{interaction.user.display_name}:** {prompt[: MESSAGE_CHUNK_SIZE - 40]}")
        await self.run_and_post(thread=thread, state=state, prompt=prompt + self.describe_attachments(paths=staged, root=state.root))

    @staticmethod
    def source_note(*, message: discord.Message) -> str:
        """Returns the provenance header prefixed to a prompt taken from someone else's message.

        Claude Code only ever sees text, so we have to spell out who said it, where, when and the jump
        link. Otherwise a session opened off a bug report arrives with no idea it's quoting anyone. We
        keep the link verbatim so it can be handed back in an answer.
        """
        channel: str = getattr(message.channel, "mention", "an unknown channel")
        return (
            f"[Quoted Discord message by {message.author.display_name} (`{message.author}`) in {channel}, "
            f"posted {message.created_at.isoformat(sep=' ', timespec='minutes')} UTC · {message.jump_url}]"
        )

    async def ask_message(self, interaction: discord.Interaction, message: discord.Message) -> None:
        """Opens a session seeded from an existing message, text and attachments included.

        The same thing as `/claude ask` minus the options. A context menu carries no parameters, so the
        session opens on the defaults and we leave the panel to adjust them. We save attachments under
        the new session the same way a reply's would be, so the CLI can read them by path.
        """
        await interaction.response.defer(ephemeral=True)
        # A context menu can't be marked `guild_only`, so the Member narrowing `get_forum` needs is a
        # real check here instead of an assert.
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.edit_original_response(
                content=f"Sessions live in forum posts, so run this in a guild. {self.emoji_table.kuma_shrug}"
            )
            return

        content: str = message.content.strip()
        if not content and not message.attachments:
            await interaction.edit_original_response(
                content=f"That message has no text or attachments for me to hand over. {self.emoji_table.kuma_shrug}"
            )
            return

        forum, error = await self.prepare_forum(interaction=interaction)
        if forum is None:
            await interaction.edit_original_response(content=error)
            return

        state: SessionState = SessionState(
            thread_id=0,
            user_id=interaction.user.id,
            session_id=str(uuid.uuid4()),
            model=DEFAULT_MODEL,
            mode=MODES["plan"].value,
            access=self.access_for(interaction.user),
        )

        created: discord.channel.ThreadWithMessage = await forum.create_thread(
            name=thread_title(content) if content else placeholder_title(interaction.locale),
            view=SessionPanel(state=state, modes=self.modes_for(state.user_id)),
            reason=f"Claude Code session opened from a message by {interaction.user}.",
        )
        thread: discord.Thread = created.thread
        state.thread_id = thread.id
        self._sessions[thread.id] = state

        await interaction.edit_original_response(
            content=f"Session opened from that message: {thread.mention} · reply in there to continue it. {self.emoji_table.kuma_happy}",
        )

        saved, rejected = await self.save_attachments(message=message, state=state)
        if rejected:
            await self.send_to_thread(
                thread=thread,
                content=f"Skipped {', '.join(f'`{name}`' for name in rejected)}; over the "
                f"{human_size(MAX_ATTACHMENT_SIZE)} limit. {self.emoji_table.kuma_pout}",
            )

        # The jump link goes in the thread as well as the prompt, so the post stays traceable back to
        # what it was opened from once the session scrolls on.
        await self.send_to_thread(
            thread=thread,
            content=f"{self.emoji_table.kuma_tea} Opened from {message.author.mention}'s message · {message.jump_url}",
        )

        prompt: str = f"{self.source_note(message=message)}\n\n{content}".strip()
        await self.run_and_post(thread=thread, state=state, prompt=prompt + self.describe_attachments(paths=saved, root=state.root))

    @access.command(name="list", description="Show who runs sessions at the project root.")
    @app_commands.check(_is_owner)
    async def access_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        lines: list[str] = [f"### {AccessTier.ELEVATED.label} access"]
        if self._access.empty:
            lines.append(f"Nobody. Every session runs in its own workspace. {self.emoji_table.kuma_shrug}")
        else:
            # Mentions rather than names: a role or member that has since gone renders as a raw ID, which
            # is the honest result and exactly the row worth revoking.
            if self._access.user_ids:
                lines.append(f"- Users: {', '.join(f'<@{entry}>' for entry in sorted(self._access.user_ids))}")
            if self._access.role_ids:
                lines.append(f"- Roles: {', '.join(f'<@&{entry}>' for entry in sorted(self._access.role_ids))}")

        # The ini half is read-only here, so it is reported rather than offered as something to change.
        bypass: frozenset[int] = self._settings.bypass_user_ids
        listed: str = ", ".join(f"<@{entry}>" for entry in sorted(bypass)) if bypass else "nobody"
        lines += [
            f"### {AccessTier.BYPASS.label} access",
            f"- {listed}",
            "-# Set by `[CLAUDE] bypass_user_ids` in `local.ini`; no command can change it.",
        ]
        await interaction.edit_original_response(content="\n".join(lines)[:MESSAGE_CHUNK_SIZE])

    @access.command(name="grant", description="Let a user or role run sessions at the project root.")
    @app_commands.describe(user="The user to elevate.", role="The role whose holders to elevate.")
    @app_commands.guild_only()
    @app_commands.check(_is_owner)
    async def access_grant(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
        role: Optional[discord.Role] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target, error = _access_target(user=user, role=role)
        if target is None:
            await interaction.edit_original_response(content=f"{error} {self.emoji_table.kuma_shrug}")
            return

        entity, entity_id, mention = target
        await self.grant_access(entity_id=entity_id, entity=entity, added_by=interaction.user.id)
        await interaction.edit_original_response(
            content=f"{mention} now runs sessions at the project root. {self.emoji_table.kuma_happy}"
            f"\n-# Takes effect on their next message; writing there still needs `.edits`.",
        )

    @access.command(name="revoke", description="Send a user or role back to their own session workspace.")
    @app_commands.describe(user="The user to revoke.", role="The role to revoke.")
    @app_commands.guild_only()
    @app_commands.check(_is_owner)
    async def access_revoke(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
        role: Optional[discord.Role] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target, error = _access_target(user=user, role=role)
        if target is None:
            await interaction.edit_original_response(content=f"{error} {self.emoji_table.kuma_shrug}")
            return

        entity, entity_id, mention = target
        if not await self.revoke_access(entity_id=entity_id, entity=entity):
            await interaction.edit_original_response(content=f"{mention} was not in the elevated group. {self.emoji_table.kuma_shrug}")
            return
        await interaction.edit_original_response(
            content=f"{mention} is back to their own session workspace. {self.emoji_table.kuma_happy}"
            f"\n-# Applies to running sessions on their next message, not just new ones.",
        )

    @claude.command(name="sessions", description="List your open Claude Code sessions in this guild.")
    @app_commands.guild_only()
    @app_commands.check(_is_owner)
    async def sessions(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        guild: Optional[discord.Guild] = interaction.guild
        if guild is None:
            await interaction.edit_original_response(content=f"Run this in a guild. {self.emoji_table.kuma_shrug}")
            return

        name: str = FORUM_NAME_FORMAT.format(name=interaction.user.name.lower())[:100]
        forum: Optional[discord.ForumChannel] = discord.utils.get(guild.forums, name=name)
        # Only our own posts are sessions; anything else in there is a stray awaiting the next sweep and
        # has no state to report, so it is left out rather than listed as unreadable.
        posts: list[discord.Thread] = [thread for thread in forum.threads if self.is_bot_post(thread)] if forum is not None else []
        if forum is None or not posts:
            await interaction.edit_original_response(
                content=f"No sessions here yet; open one with `/claude ask`. {self.emoji_table.kuma_shrug}",
            )
            return

        lines: list[str] = [f"### Sessions ({len(self.active_threads(forum=forum))}/{MAX_SESSIONS_PER_USER} open)"]
        for thread in sorted(posts, key=lambda entry: entry.created_at or discord.utils.utcnow(), reverse=True):
            state: Optional[SessionState] = await self.get_state(thread=thread)
            file_count, total_size = await asyncio.to_thread(
                _dir_stats,
                directory=session_dir(root=PROJECT_ROOT, user_id=interaction.user.id, thread_id=thread.id),
            )
            marker: str = SESSION_MARKERS[SessionStatus.of(thread)]
            detail: str = "*state unreadable*" if state is None else f"`{state.model}` · `{state.mode}`"
            lines.append(f"{marker} {thread.mention} · {detail} · {file_count} file(s), {human_size(total_size)}")

        await interaction.edit_original_response(content="\n".join(lines)[:MESSAGE_CHUNK_SIZE])

    @claude.command(name="spoof", description="Render the session UI with canned data to check the layout.")
    @app_commands.describe(
        component="Which piece of the UI to render.",
        status="Which session state to render the panel in.",
        pages="How many response chunks to spread the spoofed answer across.",
    )
    @app_commands.choices(
        component=[
            app_commands.Choice(name="Session panel", value="panel"),
            app_commands.Choice(name="Response chunks", value="response"),
            app_commands.Choice(name="Dot command help", value="help"),
        ],
        status=[
            app_commands.Choice(name="Active", value=SessionStatus.ACTIVE.value),
            app_commands.Choice(name="Closed", value=SessionStatus.CLOSED.value),
            app_commands.Choice(name="Expired", value=SessionStatus.EXPIRED.value),
        ],
    )
    @app_commands.check(_is_owner)
    async def spoof(
        self,
        interaction: discord.Interaction,
        component: str = "panel",
        status: str = SessionStatus.ACTIVE.value,
        pages: app_commands.Range[int, 1, 5] = 2,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if component == "help":
            await interaction.edit_original_response(content=self.help_text(user_id=interaction.user.id))
            return

        state: SessionState = SessionState(
            thread_id=interaction.channel_id or 0,
            user_id=interaction.user.id,
            session_id=SPOOF_SESSION_ID,
            model=SPOOF_MODEL,
            # Spoof renders the caller's real tier, so the panel it shows is the one they would get.
            access=self.access_for(interaction.user),
        )

        if component == "panel":
            # Sent as a followup as a Components V2 view can't share a message with content.
            await interaction.edit_original_response(
                content=f"{self.emoji_table.kuma_peak} **Spoofed panel** · this session does not exist."
            )
            await interaction.followup.send(
                view=SessionPanel(state=state, modes=self.modes_for(state.user_id), status=SessionStatus(status)),
                ephemeral=True,
            )
            return

        chunks: list[str] = chunk_text(_spoof_response(pages=pages))
        footer: str = f"-# `{state.model}` · `{state.mode}` · `{state.effort}` · ${SPOOF_COST:.4f}"
        await interaction.edit_original_response(
            content=f"{self.emoji_table.kuma_peak} **Spoofed response** · {len(chunks)} chunk(s), no CLI run.",
        )
        for index, chunk in enumerate(chunks):
            content: str = f"{chunk}\n{REPLY_SEPARATOR}\n{footer}" if index == len(chunks) - 1 else chunk
            await interaction.followup.send(content=content, ephemeral=True)

    # endregion


# ---------------------------------------------------------------------------
# Spoofing
# Canned data behind `/claude spoof`, so we can check layouts without burning a real CLI run.
# ---------------------------------------------------------------------------

SPOOF_SESSION_ID: str = "spoof-0000-0000-0000-000000000000"
SPOOF_MODEL: str = DEFAULT_MODEL
SPOOF_COST: float = 0.0423

SPOOF_BODY: str = """### Spoofed Response

This is **canned** output; no CLI process was started and nothing was written to disk.

Here is what a session run does, step by step:

1. Snapshot every file's mtime *before* the run (`_dir_snapshot`).
2. Hand the prompt to the CLI with the output directory notice appended.
3. Re-scan afterwards and keep anything newer than the snapshot (`_new_files`).

```python
def _new_files(*, directory: Path, before: dict[Path, float]) -> list[Path]:
    after: dict[Path, float] = _dir_snapshot(directory=directory)
    changed: list[Path] = [path for path, mtime in after.items() if before.get(path, 0.0) < mtime]
    changed.sort(key=lambda path: after[path], reverse=True)
    return changed
```

> Note: a resumed session can fork to a new ID, which is why the panel is re-rendered after a run."""

SPOOF_FILLER: str = """#### Filler Section {index}

Padding so the response spans more than one message. The chunker prefers to break on a code
fence, then a blank line, then a single newline, and only makes a hard cut when none of
those land in the back half of the window.

- `MESSAGE_CHUNK_SIZE` is currently {chunk_size} characters.
- Section {index} of the filler run."""


def _spoof_response(*, pages: int) -> str:
    """Builds a canned response long enough to chunk into roughly `pages` messages.

    Parameters
    ----------
    pages: :class:`int`
        How many chunks the response should span.

    Returns
    -------
    :class:`str`
        The spoofed response text.

    """
    text: str = SPOOF_BODY
    index: int = 1
    # Aim for the middle of the last window so we land on `pages` chunks rather than teetering on the edge.
    target: int = (pages - 1) * MESSAGE_CHUNK_SIZE + MESSAGE_CHUNK_SIZE // 2
    while len(text) < target:
        text += "\n\n" + SPOOF_FILLER.format(index=index, chunk_size=MESSAGE_CHUNK_SIZE)
        index += 1
    return text


async def setup(bot: Kuma_Kuma) -> None:
    await bot.add_cog(ClaudeCog(bot=bot))
