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
import io
import json
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Self, Unpack

import discord
from discord import app_commands
from discord.ext import tasks

from utils import KumaCog as Cog, KumaEmbed, KumaView

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence
    from typing import Any

    from kuma_kuma import Kuma_Kuma
    from utils._types import EmbedParams
    from utils.ui import ViewParams

LOGGER = logging.getLogger()
__VERSION__ = "1.0.0"

# All subprocess calls are locked to this directory — no path traversal possible.
PROJECT_ROOT: Path = Path(__file__).parent.parent
EXTENSIONS_ROOT: Path = Path(__file__).parent
# Discord attachments are saved per user (`<user_id>/`) so the CLI can read them.
ATTACHMENTS_DIR: Path = EXTENSIONS_ROOT.joinpath(".claude_attachments")
# Generated files live in a per user, per session workspace (`<user_id>/<session slug>/`).
ASKS_DIR: Path = EXTENSIONS_ROOT.joinpath(".claude_asks")
CLAUDE_ICON: Path = PROJECT_ROOT.joinpath("resources/claude_icon.png")
# `KumaEmbed.avatar_icon`'s setter renames whatever File it is handed to this, and `KumaEmbed.set_author`
# points at it by default. Kept as constants so the embed's reference and the uploaded attachment agree —
# a mismatch (or a reference to a file that was never uploaded) leaves the icon blank.
AVATAR_ICON_FILENAME: str = "avatar-icon.png"
AVATAR_ICON_URL: str = f"attachment://{AVATAR_ICON_FILENAME}"

CLAUDE_TIMEOUT: int = 900
CHUNK_SIZE: int = 3800
MAX_ATTACHMENT_SIZE: int = 25 * 1024 * 1024  # 25MB
ATTACHMENT_MAX_AGE: int = 86400  # Saved attachments older than this (seconds) are removed on cog load.
PROGRESS_INTERVAL: float = 4.0  # Minimum seconds between progress edits to stay under Discord rate limits.
MAX_REPLY_FILES: int = 8  # Most generated files we will attach to a single reply.
MAX_REPLY_FILE_SIZE: int = 8 * 1024 * 1024  # 8MB — anything larger is listed by name instead of attached.
FULL_RESPONSE_THRESHOLD: int = CHUNK_SIZE * 2  # Responses longer than this also ride along as a Markdown file.
SESSION_SLUG_SIZE: int = 8  # Session IDs are UUIDs; the leading chunk is plenty to keep workspace paths short.
SESSION_SNIPPET_SIZE: int = 140  # Preview length of a session's opening prompt in `/claude sessions`.
ENTRIES_PER_PAGE: int = 5  # Stored sessions shown per `/claude sessions` embed.
# Claude Code prunes its own transcripts on `cleanupPeriodDays`; we mirror that window so we never offer a
# session the CLI can no longer resume. Read from the user's settings when set, otherwise the CLI default.
CLAUDE_SETTINGS: Path = Path.home().joinpath(".claude", "settings.json")
DEFAULT_SESSION_MAX_AGE_DAYS: int = 30
MAX_TRANSIENT_PER_USER: int = 20  # Unstored sessions past this (per user) are pruned oldest first.
PRUNE_INTERVAL_HOURS: int = 24

# Appended to every prompt so file output never escapes the session's own workspace.
OUTPUT_DIR_NOTICE: str = (
    "\n\n[Any file you create, download or write out for this request must live inside the `{directory}/` "
    "directory of this project. Create it if it does not exist. Do not write generated files anywhere else.]"
)

# Every session a user chooses to keep lands here; the opening prompt (from history) labels it — no nick names.
SESSIONS_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS claude_sessions (
    user_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    expired_at REAL,
    PRIMARY KEY (user_id, session_id)
)"""

HISTORY_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS claude_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    session_id TEXT,
    prompt TEXT NOT NULL,
    response TEXT NOT NULL,
    model TEXT NOT NULL,
    cost_usd REAL,
    type TEXT NOT NULL DEFAULT 'ask',
    created_at REAL NOT NULL
)"""

# Carry previously nick named/summarized sessions across as stored; everything else was transient anyway.
LEGACY_MIGRATE_SQL = """
INSERT OR IGNORE INTO claude_sessions(user_id, session_id, created_at, expired_at)
SELECT user_id, session_id, created_at, expired_at FROM claude_session_store
WHERE name IS NOT NULL OR summary IS NOT NULL"""

# The model used when `/claude ask` and `/claude plan` are invoked without one. Must stay in
# step with MODEL_CHOICES below — a default that is not one of the choices still reaches the CLI.
DEFAULT_MODEL: str = "claude-sonnet-5"

# Shared by `/claude ask` and `/claude plan`.
MODEL_CHOICES: list[app_commands.Choice[str]] = [
    app_commands.Choice(name="Sonnet 5 (default)", value=DEFAULT_MODEL),
    app_commands.Choice(name="Opus 5", value="claude-opus-5"),
    app_commands.Choice(name="Haiku 4.5", value="claude-haiku-4-5"),
    app_commands.Choice(name="*Fable 5", value="claude-fable-5"),
]

YES_NO_CUES: tuple[str, ...] = (
    "shall i",
    "should i",
    "would you like",
    "do you want",
    "want me to",
    "proceed",
    "continue",
    "confirm",
    "apply",
)

# ---------------------------------------------------------------------------
# Spoofing — everything below backs `/claude spoof`, which renders the UI with
# canned data so layouts can be checked without burning a real CLI run.
# ---------------------------------------------------------------------------

# A fixed ID keeps every spoof run in one throwaway workspace instead of littering `.claude_asks/`.
SPOOF_SESSION_ID: str = "spoof-0000-0000-0000-000000000000"
SPOOF_MODEL: str = "claude-sonnet-5"
SPOOF_COST: float = 0.0423
SPOOF_PROMPT: str = (
    "Walk me through how the session workspace diffing works and show me what a long, "
    "code heavy answer looks like once it has been chunked across a few embeds."
)

SPOOF_COMPONENT_CHOICES: list[app_commands.Choice[str]] = [
    app_commands.Choice(name="Reply view (embeds + ClaudeView)", value="view"),
    app_commands.Choice(name="Embeds only (no components)", value="embed"),
    app_commands.Choice(name="Session list (SessionListView)", value="sessions"),
    app_commands.Choice(name="Reply modal (ClaudeReplyModal)", value="modal"),
]

# Markdown deliberately mixes headers, lists, fences and inline code so the chunker's
# split points and Discord's own rendering both get exercised.
SPOOF_BODY: str = """### Spoofed Response

This is **canned** output — no CLI process was started and nothing was written to `claude_history`.

Here is what the workspace diff does, step by step:

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

> Note: a resumed session can fork to a new ID, which is why `_move_workspace` exists."""

# Repeated to pad a spoof response out to the requested embed count.
SPOOF_FILLER: str = """#### Filler Section {index}

Padding so the response spans more than one embed. The chunker prefers to break on a code
fence, then a blank line, then a single newline, and only makes a hard cut when none of
those land in the back half of the window.

- `CHUNK_SIZE` is currently {chunk_size} characters.
- Anything past {threshold} characters also rides along as a Markdown attachment.
- Section {index} of the filler run."""

SPOOF_QUESTION_TAIL: str = "\n\nThat covers the diffing path. Shall I walk through the prune loop as well?"

SPOOF_GIT_STATUS: str = " M extensions/claude.py\n M TODO.md\n?? extensions/.claude_asks/spoof/notes.md"

# Written into the spoof workspace on demand so the real attachment path is exercised.
SPOOF_FILES: dict[str, str] = {
    "spoof_notes.md": "# Spoof Notes\n\nGenerated by `/claude spoof` — safe to delete.\n",
    "spoof_snippet.py": '"""Generated by `/claude spoof`."""\n\n\ndef hello() -> str:\n    return "Kuma Kuma"\n',
    "spoof_data.json": json.dumps({"spoof": True, "session": SPOOF_SESSION_ID}, indent=4),
}


async def _is_owner(interaction: discord.Interaction) -> bool:
    """Returns whether the invoking user is a bot owner; the check guarding every `/claude` command."""
    return await interaction.client.is_owner(interaction.user)  # type: ignore[arg-type]


def _is_yes_no_question(text: str) -> bool:
    """Returns whether the response ends in a yes/no style question worth offering quick-reply buttons for.

    Checks that the last non-empty line ends with a `?` and contains one of :attr:`YES_NO_CUES`.

    Parameters
    ----------
    text: :class:`str`
        The full response text from Claude Code.

    Returns
    -------
    :class:`bool`
        `True` when the Yes/No buttons should be shown.

    """
    lines: list[str] = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) == 0:
        return False
    last_line: str = lines[-1].lower()
    return last_line.endswith("?") and any(cue in last_line for cue in YES_NO_CUES)


def _chunk_response(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Returns the response split into embed-sized chunks, preferring natural break points.

    Splits preferentially at code-fence boundaries, then double newlines, then single newlines,
    falling back to a hard cut only when none of those land in the second half of the window —
    that keeps a fence from being torn in half across two embeds.

    Parameters
    ----------
    text: :class:`str`
        The full response text to split.
    size: :class:`int`, optional
        The maximum characters per chunk, by default :attr:`CHUNK_SIZE`.

    Returns
    -------
    :class:`list[str]`
        The chunks in order; empty chunks are dropped.

    """
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
    return [chunk for chunk in chunks if chunk]


def _prompt_snippet(prompt: str, size: int = SESSION_SNIPPET_SIZE) -> str:
    """Returns a single line, quote-safe preview of a stored prompt.

    Drops the bracketed notes we append to prompts ourselves (the attachment path, the output
    directory notice) and collapses whitespace so a multi line ask still fits on one embed line.

    Parameters
    ----------
    prompt: :class:`str`
        The prompt as stored in `claude_history`.
    size: :class:`int`, optional
        The maximum length of the preview, by default :attr:`SESSION_SNIPPET_SIZE`.

    Returns
    -------
    :class:`str`
        The trimmed preview; empty when the prompt was nothing but our own notes.

    """
    cleaned: str = re.sub(r"\[(?:The user attached a file|Any file you create)[^\]]*\]", "", prompt)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > size:
        cleaned = f"{cleaned[:size].rstrip()}…"
    return cleaned


def session_slug(session_id: str) -> str:
    """Returns the short, path-safe form of a session ID used for workspace directory names."""
    return re.sub(r"[^\w\-]", "_", session_id)[:SESSION_SLUG_SIZE]


def session_dir(*, user_id: int, session_id: str) -> Path:
    """Returns the workspace directory for a user's session — `.claude_asks/<user_id>/<session slug>/`."""
    return ASKS_DIR.joinpath(str(user_id), session_slug(session_id))


def attachments_dir(*, user_id: int) -> Path:
    """Returns the saved attachment directory for a user — `.claude_attachments/<user_id>/`."""
    return ATTACHMENTS_DIR.joinpath(str(user_id))


def _session_max_age() -> float:
    """Returns the session retention window in seconds.

    Mirrors Claude Code's own `cleanupPeriodDays` setting when it is configured — once the CLI deletes a
    transcript the session cannot be resumed, so keeping the row past that point only offers a broken session.

    Returns
    -------
    :class:`float`
        The retention window in seconds.

    """
    days: int = DEFAULT_SESSION_MAX_AGE_DAYS
    try:
        settings: dict = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return days * 86400

    cleanup_period: Any = settings.get("cleanupPeriodDays")
    if isinstance(cleanup_period, (int, float)) and cleanup_period > 0:
        days = int(cleanup_period)
    return days * 86400


def _dir_snapshot(*, directory: Path) -> dict[Path, float]:
    """Returns the modified time of every file currently inside `directory`, keyed by path."""
    if not directory.is_dir():
        return {}
    return {entry: entry.stat().st_mtime for entry in directory.rglob("*") if entry.is_file()}


def _new_files(*, directory: Path, before: dict[Path, float]) -> list[Path]:
    """Returns any file inside `directory` that was created or touched since `before` was taken.

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
    changed: list[Path] = [path for path, mtime in after.items() if before.get(path, 0.0) < mtime]
    changed.sort(key=lambda path: after[path], reverse=True)
    return changed


def _dir_stats(*, directory: Path) -> tuple[int, int]:
    """Returns the file count and total byte size of `directory`; `(0, 0)` when it does not exist."""
    if not directory.is_dir():
        return 0, 0
    files: list[Path] = [entry for entry in directory.rglob("*") if entry.is_file()]
    return len(files), sum(entry.stat().st_size for entry in files)


def _move_workspace(*, user_id: int, old_id: str, new_id: str) -> Path:
    """Moves a session workspace when the CLI hands back a different session ID than we asked for.

    Parameters
    ----------
    user_id: :class:`int`
        The Discord user the workspace belongs to.
    old_id: :class:`str`
        The session ID the workspace was created under.
    new_id: :class:`str`
        The session ID the CLI actually used.

    Returns
    -------
    :class:`Path`
        The workspace directory for `new_id`.

    """
    source: Path = session_dir(user_id=user_id, session_id=old_id)
    target: Path = session_dir(user_id=user_id, session_id=new_id)
    if source == target or not source.is_dir():
        return target

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        return target

    # Target already exists (a slug collision or a resumed fork) — merge the files across instead.
    for entry in source.rglob("*"):
        if entry.is_file():
            destination: Path = target.joinpath(entry.relative_to(source))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src=entry, dst=destination)
    shutil.rmtree(path=source, ignore_errors=True)
    return target


def _claude_icon_file() -> Optional[discord.File]:
    """Returns a freshly opened Claude icon attachment, or `None` when the icon is missing from disk.

    A :class:`discord.File` is single use — the upload reads its buffer to EOF — so a new one is built
    per send rather than one being cached on the embed. The missing-file guard keeps a deleted or
    renamed `resources/claude_icon.png` from turning every reply into a `FileNotFoundError`.

    Returns
    -------
    :class:`Optional[discord.File]`
        The icon, named :attr:`AVATAR_ICON_FILENAME` so the embed's author `icon_url` resolves to it.

    """
    if not CLAUDE_ICON.is_file():
        return None
    return discord.File(fp=CLAUDE_ICON, filename=AVATAR_ICON_FILENAME)


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
            skipped.append(path.relative_to(PROJECT_ROOT).as_posix())
            continue
        files.append(discord.File(fp=path, filename=path.name))
    return files, skipped


def _spoof_response(*, pages: int, question: bool) -> str:
    """Builds a canned response long enough to chunk into roughly `pages` embeds.

    Parameters
    ----------
    pages: :class:`int`
        How many embeds the response should span.
    question: :class:`bool`
        Whether to end on a yes/no style question so :func:`_is_yes_no_question` trips.

    Returns
    -------
    :class:`str`
        The spoofed response text.

    """
    text: str = SPOOF_BODY
    index: int = 1
    # Aim for the middle of the last window so we land on `pages` chunks rather than teetering on the edge.
    target: int = (pages - 1) * CHUNK_SIZE + CHUNK_SIZE // 2
    while len(text) < target:
        text += "\n\n" + SPOOF_FILLER.format(index=index, chunk_size=CHUNK_SIZE, threshold=FULL_RESPONSE_THRESHOLD)
        index += 1

    if question:
        text += SPOOF_QUESTION_TAIL
    return text


def _write_spoof_files(*, user_id: int) -> list[Path]:
    """Writes the sample files from :attr:`SPOOF_FILES` into the spoof workspace and returns their paths.

    Re-uses fixed filenames so repeated spoof runs overwrite instead of piling up.

    Parameters
    ----------
    user_id: :class:`int`
        The Discord user whose spoof workspace to write into.

    Returns
    -------
    :class:`list[Path]`
        The written file paths.

    """
    workspace: Path = session_dir(user_id=user_id, session_id=SPOOF_SESSION_ID)
    workspace.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for name, content in SPOOF_FILES.items():
        path: Path = workspace.joinpath(name)
        path.write_text(data=content, encoding="utf-8")
        paths.append(path)
    return paths


@dataclass
class SessionRef:
    """A pointer to a resumable session — everything a Resume button needs to pick it back up.

    Attributes
    ----------
    session_id: :class:`str`
        The Claude Code CLI session ID to resume.
    model: :class:`str`
        The model the session last ran with, reused when resuming.

    """

    session_id: str
    model: str


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

    """

    text: str = field(default="")
    session_id: str = field(default="")
    cost_usd: Optional[float] = field(default=None)
    error: Optional[str] = field(default=None)
    files: list[Path] = field(default_factory=list)


class ClaudeEmbed(KumaEmbed):
    """Embed displaying a single chunk of a Claude Code response.

    Parameters
    ----------
    cog: :class:`KumaCog`
        The parent Cog, passed through to :class:`KumaEmbed`.
    prompt: :class:`str`
        The original prompt, displayed as a field (truncated to 200 chars).
    chunk: :class:`str`
        The response text slice to display in the embed description.
    model: :class:`str`
        The Claude model used, displayed in the embed title.
    cost_usd: :class:`Optional[float]`
        The API cost reported by Claude Code, displayed in the footer if provided.
    icon: :class:`bool`, optional
        Whether to attach the Claude icon as the author avatar, by default `True`. `/claude spoof`
        turns it off to compare the two renders side by side.
    **kwargs: :class:`Unpack[EmbedParams]`
        Any additional keyword arguments forwarded to :class:`KumaEmbed`.

    """

    def __init__(
        self,
        cog: Cog,
        *,
        prompt: str,
        chunk: str,
        model: str,
        cost_usd: Optional[float] = None,
        icon: bool = True,
        **kwargs: Unpack[EmbedParams],
    ) -> None:
        kwargs.setdefault("title", f"Claude Code — `{model}`")
        kwargs.setdefault("color", discord.Color.blurple())
        kwargs.setdefault("description", chunk)
        super().__init__(cog=cog, **kwargs)

        # Only claim the author icon when we can actually deliver the attachment; pointing at an
        # `attachment://` URL we never upload is what leaves the avatar blank.
        self.use_icon: bool = icon and CLAUDE_ICON.is_file()
        self.set_author(name="Claude Code", icon_url=AVATAR_ICON_URL if self.use_icon else None)

        short_prompt: str = f"`{prompt[:200]}{'...' if len(prompt) > 200 else ''}`"
        self.add_field(name="Prompt:", value=short_prompt, inline=False)
        if cost_usd is not None:
            self.set_footer(text=f"**Cost:** ${cost_usd:.4f} | Kuma Kuma Bear")

    @property
    def attachments(self) -> Sequence[discord.File]:
        """Returns the inline attachments this embed references — the Claude icon, freshly opened.

        Deliberately overrides :attr:`KumaEmbed.attachments` rather than stashing a
        :class:`discord.File` on the instance. A File is single use: the upload drains its buffer, so
        the object cached at construction time only survives one send. Chunked replies are paged by
        :class:`KumaView`, which re-reads this property on every page turn and would otherwise
        re-upload an exhausted handle — the icon renders blank from the second visit onwards. Building
        it per read also means N chunked embeds no longer hold N open file handles for one image.

        Returns
        -------
        :class:`Sequence[discord.File]`
            The icon attachment, or empty when the icon is disabled or missing from disk.

        """
        if not self.use_icon:
            return []
        icon: Optional[discord.File] = _claude_icon_file()
        return [] if icon is None else [icon]


class ClaudeReplyModal(discord.ui.Modal, title="Reply to Claude Code"):
    """Free-form reply box that resumes `session_id` with whatever the user types.

    Shared by the Reply button on :class:`ClaudeView` and the Resume button on
    :class:`SessionListView` — both just need to continue a session from text.

    Parameters
    ----------
    cog: :class:`ClaudeCog`
        The parent Cog used to run the CLI.
    session_id: :class:`str`
        The session to resume on submit.
    model: :class:`str`
        The model to run the reply with.
    permission_mode: :class:`Optional[str]`, optional
        Forwarded to :meth:`ClaudeCog.run_claude`, by default `None`.

    """

    reply: discord.ui.TextInput[Self] = discord.ui.TextInput(
        label="Reply",
        style=discord.TextStyle.paragraph,
        placeholder="Your reply to Claude...",
        max_length=4000,
    )

    def __init__(self, *, cog: ClaudeCog, session_id: str, model: str, permission_mode: Optional[str] = None) -> None:
        super().__init__()
        self.cog: ClaudeCog = cog
        self.session_id: str = session_id
        self.model: str = model
        self.permission_mode: Optional[str] = permission_mode

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.continue_session(
            interaction,
            session_id=self.session_id,
            model=self.model,
            permission_mode=self.permission_mode,
            reply=self.reply.value,
        )


class ClaudeView(KumaView):
    """Paginated view for a Claude Code response, bound to the session that produced it.

    Always offers a Reply button that opens a :class:`ClaudeReplyModal` to continue the
    session, and a Store button to keep the session past the next prune. Adds Yes/No
    quick-reply buttons when the response ends in a yes/no style question.

    Parameters
    ----------
    session_id: :class:`str`
        The session this response belongs to; carried into replies so they resume it.
    model: :class:`str`
        The Claude model to use for replies.
    permission_mode: :class:`Optional[str]`, optional
        Passed through to :meth:`ClaudeCog.run_claude` on replies, by default `None`.
    is_question: :class:`bool`, optional
        Whether to show the Yes/No quick-reply buttons, by default `False`.
    stored: :class:`bool`, optional
        Whether the session is already stored (starts the Store button disabled), by default `False`.
    **kwargs: :class:`Unpack[ViewParams]`
        Any additional keyword arguments forwarded to :class:`KumaView`.

    """

    cog: ClaudeCog

    def __init__(
        self,
        *,
        session_id: str,
        model: str,
        permission_mode: Optional[str] = None,
        is_question: bool = False,
        stored: bool = False,
        **kwargs: Unpack[ViewParams],
    ) -> None:
        super().__init__(**kwargs)
        self.session_id: str = session_id
        self.model: str = model
        self.permission_mode: Optional[str] = permission_mode

        # Decorator buttons are not tracked by add_item; extend manually so reset_view keeps them.
        self.components.extend([self.reply_callback, self.store_callback])
        if is_question:
            self.components.extend([self.yes_callback, self.no_callback])
        else:
            self.remove_item(item=self.yes_callback)
            self.remove_item(item=self.no_callback)

        if stored:
            self.store_callback.disabled = True
            self.store_callback.label = "Stored"

    @discord.ui.button(label="Reply...", style=discord.ButtonStyle.blurple, row=2)
    async def reply_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:  # noqa: ARG002
        await interaction.response.send_modal(
            ClaudeReplyModal(cog=self.cog, session_id=self.session_id, model=self.model, permission_mode=self.permission_mode),
        )

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green, row=2)
    async def yes_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:  # noqa: ARG002
        await self.cog.continue_session(
            interaction, session_id=self.session_id, model=self.model, permission_mode=self.permission_mode, reply="Yes"
        )

    @discord.ui.button(label="No", style=discord.ButtonStyle.red, row=2)
    async def no_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:  # noqa: ARG002
        await self.cog.continue_session(
            interaction, session_id=self.session_id, model=self.model, permission_mode=self.permission_mode, reply="No"
        )

    @discord.ui.button(label="⭐ Store", style=discord.ButtonStyle.secondary, row=2)
    async def store_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:
        await self.cog.store_session(user_id=self.owner.id, session_id=self.session_id)
        item.disabled = True
        item.label = "Stored"
        await interaction.response.edit_message(view=self)


class SessionListView(KumaView):
    """Paginated list of resumable sessions with a Resume button for the current page.

    Used by both `/claude history` and `/claude sessions`; the Resume button acts on the
    session shown on the current page. When `allow_delete` is set (the stored-session list)
    a Delete button also removes the shown session.

    Parameters
    ----------
    refs: :class:`list[SessionRef]`
        One entry per embed, aligned by index, describing what each page resumes.
    allow_delete: :class:`bool`, optional
        Whether to offer a Delete button, by default `False`.
    **kwargs: :class:`Unpack[ViewParams]`
        Any additional keyword arguments forwarded to :class:`KumaView`.

    """

    cog: ClaudeCog

    def __init__(self, *, refs: list[SessionRef], allow_delete: bool = False, **kwargs: Unpack[ViewParams]) -> None:
        super().__init__(**kwargs)
        self.refs: list[SessionRef] = refs
        self.components.append(self.resume_callback)
        if allow_delete:
            self.components.append(self.delete_callback)
        else:
            self.remove_item(item=self.delete_callback)

    @property
    def current_ref(self) -> SessionRef:
        """Returns the session pointer for the page currently on screen."""
        return self.refs[min(self.indx, len(self.refs) - 1)]

    @discord.ui.button(label="Resume", style=discord.ButtonStyle.blurple, row=2)
    async def resume_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:  # noqa: ARG002
        session_ref: SessionRef = self.current_ref
        await interaction.response.send_modal(
            ClaudeReplyModal(cog=self.cog, session_id=session_ref.session_id, model=session_ref.model),
        )

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.red, row=2)
    async def delete_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:  # noqa: ARG002
        session_ref: SessionRef = self.current_ref
        # Count the workspace before deleting it, so the confirmation can say what was cleaned up.
        file_count, total_size = await asyncio.to_thread(
            _dir_stats, directory=session_dir(user_id=self.owner.id, session_id=session_ref.session_id)
        )
        await self.cog.delete_session(user_id=self.owner.id, session_id=session_ref.session_id)
        files_message: str = f" Cleaned up {file_count} file(s) ({total_size / 1024:.1f}KB)." if file_count else ""
        self.stop()
        await interaction.response.edit_message(
            content=f"Deleted session `{session_ref.session_id}`.{files_message} {self.cog.emoji_table.kuma_happy}",
            embed=None,
            attachments=[],
            view=None,
        )


async def _spoof_notice(interaction: discord.Interaction, *, cog: ClaudeCog, action: str, detail: str = "") -> None:
    """Reports which spoofed callback fired instead of touching the CLI or the database.

    Parameters
    ----------
    interaction: :class:`discord.Interaction`
        The component or modal-submit interaction to respond through.
    cog: :class:`ClaudeCog`
        The parent Cog, used for emoji.
    action: :class:`str`
        The callback name that fired.
    detail: :class:`str`, optional
        Extra context appended on its own line, by default `""`.

    """
    content: str = f"{cog.emoji_table.kuma_peak} **Spoof** — `{action}` fired. No CLI run, no database write."
    if detail:
        content += f"\n{detail}"

    if interaction.response.is_done():
        await interaction.followup.send(content=content, ephemeral=True)
    else:
        await interaction.response.send_message(content=content, ephemeral=True)


class SpoofReplyModal(ClaudeReplyModal, title="Spoof — Reply to Claude Code"):
    """A :class:`ClaudeReplyModal` that echoes what you typed instead of resuming a session.

    Same text input and submit path as the real modal; only :meth:`on_submit` is swapped out.
    """

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _spoof_notice(
            interaction,
            cog=self.cog,
            action="ClaudeReplyModal.on_submit",
            detail=f"Session: `{self.session_id}`\nModel: `{self.model}`\nYou typed:\n>>> {self.reply.value[:1500]}",
        )


class SpoofClaudeView(ClaudeView):
    """A :class:`ClaudeView` whose session buttons report themselves rather than calling the CLI.

    Paging, Reset and the Store button's disabled/label swap all behave exactly as they do on a
    real reply — only the work behind Reply/Yes/No/Store is stubbed out.
    """

    @discord.ui.button(label="Reply...", style=discord.ButtonStyle.blurple, row=2)
    async def reply_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:  # noqa: ARG002
        await interaction.response.send_modal(
            SpoofReplyModal(cog=self.cog, session_id=self.session_id, model=self.model, permission_mode=self.permission_mode),
        )

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green, row=2)
    async def yes_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:  # noqa: ARG002
        await _spoof_notice(interaction, cog=self.cog, action="ClaudeView.yes_callback", detail=f"Session: `{self.session_id}`")

    @discord.ui.button(label="No", style=discord.ButtonStyle.red, row=2)
    async def no_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:  # noqa: ARG002
        await _spoof_notice(interaction, cog=self.cog, action="ClaudeView.no_callback", detail=f"Session: `{self.session_id}`")

    @discord.ui.button(label="⭐ Store", style=discord.ButtonStyle.secondary, row=2)
    async def store_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:
        # Flip the button the same way the real one does so the disabled state can be eyeballed.
        item.disabled = True
        item.label = "Stored"
        await interaction.response.edit_message(view=self)
        await _spoof_notice(interaction, cog=self.cog, action="ClaudeView.store_callback")


class SpoofSessionListView(SessionListView):
    """A :class:`SessionListView` whose Resume/Delete buttons are inert.

    Delete still tears the message down so the "session removed" end state can be seen, but
    nothing is removed from the database or from disk.
    """

    @discord.ui.button(label="Resume", style=discord.ButtonStyle.blurple, row=2)
    async def resume_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:  # noqa: ARG002
        session_ref: SessionRef = self.current_ref
        await interaction.response.send_modal(
            SpoofReplyModal(cog=self.cog, session_id=session_ref.session_id, model=session_ref.model),
        )

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.red, row=2)
    async def delete_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:  # noqa: ARG002
        session_ref: SessionRef = self.current_ref
        self.stop()
        await interaction.response.edit_message(
            content=f"{self.cog.emoji_table.kuma_peak} **Spoof** — would have deleted session `{session_ref.session_id}`. "
            f"Nothing was removed. {self.cog.emoji_table.kuma_happy}",
            embed=None,
            attachments=[],
            view=None,
        )


class ClaudeCog(Cog, name="Claude"):
    """Cog providing a Discord slash command interface to the Claude Code CLI.

    All commands are restricted to bot owners. The subprocess is always run with
    :attr:`PROJECT_ROOT` as its working directory so file operations stay inside the
    Kuma_Kuma project.

    Every `/claude ask` (or `/claude plan`) starts a brand new session — there is no
    global "active" session. A session is only remembered past the next prune if the
    user presses **Store** on its reply view; those land in `claude_session` and can
    be resumed from `/claude sessions`. Every prompt/response exchange is logged to
    `claude_history` regardless, and each history entry can be resumed on its own.

    Files are kept per user: generated files land in a session workspace under
    :attr:`ASKS_DIR` (`<user_id>/<session slug>/`) and Discord attachments are saved
    under :attr:`ATTACHMENTS_DIR` (`<user_id>/`) so the CLI can read them.
    """

    claude = app_commands.Group(name="claude", description="Claude Code CLI integration.")

    def __init__(self, bot: Kuma_Kuma) -> None:
        super().__init__(bot=bot)
        # One lock per user serializes their CLI runs so two overlapping asks/replies never share a
        # session transcript or race the workspace file-diff.
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock_for(self, user_id: int) -> asyncio.Lock:
        """Returns the per-user run lock, creating it on first use."""
        return self._locks.setdefault(user_id, asyncio.Lock())

    async def cog_load(self) -> None:
        """Creates the session tables, migrates the legacy schema and starts the daily prune loop."""
        async with self.bot.pool.acquire() as conn:
            await conn.execute(SESSIONS_SETUP_SQL)
            await conn.execute(HISTORY_SETUP_SQL)

            # One-time migration off the old three-table model: keep previously nick named sessions as
            # stored, then drop the legacy active-pointer and store tables.
            tables = await conn.fetchall("""SELECT name FROM sqlite_master WHERE type = 'table'""")
            names: set[str] = {row["name"] for row in tables}
            if "claude_session_store" in names:
                await conn.execute(LEGACY_MIGRATE_SQL)
                await conn.execute("""DROP TABLE claude_session_store""")
            if "claude_session" in names:
                await conn.execute("""DROP TABLE claude_session""")

        ASKS_DIR.mkdir(exist_ok=True)
        ATTACHMENTS_DIR.mkdir(exist_ok=True)

        if self.prune_loop.is_running() is False:
            LOGGER.info(
                "<%s.%s> | Starting the Claude prune loop | Interval: %s hours",
                __class__.__name__,
                "cog_load",
                self.prune_loop.hours,
            )
            self.prune_loop.start()
            self.bot.task_loops.append(self.prune_loop)

    async def cog_unload(self) -> None:
        """Stops the prune loop and un-registers it, so a reload does not stack duplicates in `bot.task_loops`."""
        if self.prune_loop.is_running():
            self.prune_loop.cancel()
        if self.prune_loop in self.bot.task_loops:
            self.bot.task_loops.remove(self.prune_loop)

    @tasks.loop(hours=PRUNE_INTERVAL_HOURS, reconnect=True)
    async def prune_loop(self) -> None:
        """Prunes stale attachments and sessions once a day; also runs immediately on cog load."""
        await asyncio.to_thread(self._prune_attachments)
        try:
            expired, deleted = await self.prune_sessions()
        except Exception as e:
            LOGGER.exception("<%s.%s> | Failed to prune sessions.", __class__.__name__, "prune_loop", exc_info=e)
            return
        if expired or deleted:
            LOGGER.info(
                "<%s.%s> | Pruned Claude sessions | Expired: %s | Deleted: %s",
                __class__.__name__,
                "prune_loop",
                expired,
                deleted,
            )

    async def prune_sessions(self) -> tuple[int, int]:
        """Expires aged-out stored sessions and clears out old or surplus transient ones.

        Sessions are grouped from `claude_history` (its `session_id`/`created_at`) and flagged stored
        by a matching `claude_sessions` row. Once past :func:`_session_max_age` Claude Code has dropped
        the transcript, so:

        - stored sessions keep their row and files but are flagged `expired_at` (no longer resumable);
        - transient sessions are deleted outright (history rows + workspace).

        Transient sessions are additionally capped at :attr:`MAX_TRANSIENT_PER_USER`, oldest pruned first.

        Returns
        -------
        :class:`tuple[int, int]`
            How many sessions were flagged expired and how many were deleted.

        """
        cutoff: float = time.time() - _session_max_age()
        now: float = time.time()
        expired: int = 0
        deleted: int = 0

        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetchall(
                """
                SELECT h.user_id AS user_id, h.session_id AS session_id, MAX(h.created_at) AS last_used,
                       s.session_id IS NOT NULL AS stored, s.expired_at AS expired_at
                FROM claude_history AS h
                LEFT JOIN claude_sessions AS s ON s.user_id = h.user_id AND s.session_id = h.session_id
                WHERE h.session_id IS NOT NULL
                GROUP BY h.user_id, h.session_id
                ORDER BY h.user_id, last_used DESC""",
            )

        per_user: dict[int, list[Any]] = {}
        for row in rows:
            per_user.setdefault(row["user_id"], []).append(row)

        for user_id, sessions in per_user.items():
            transient_kept: int = 0
            for row in sessions:
                aged_out: bool = row["last_used"] < cutoff
                if row["stored"]:
                    if aged_out and row["expired_at"] is None:
                        async with self.bot.pool.acquire() as conn:
                            await conn.execute(
                                """UPDATE claude_sessions SET expired_at = ? WHERE user_id = ? AND session_id = ?""",
                                now,
                                user_id,
                                row["session_id"],
                            )
                        expired += 1
                    continue
                # Transient: drop it if it aged out or we are past the per-user cap.
                transient_kept += 1
                if aged_out or transient_kept > MAX_TRANSIENT_PER_USER:
                    await self.delete_session(user_id=user_id, session_id=row["session_id"])
                    deleted += 1

        await asyncio.to_thread(self._prune_empty_workspaces)
        return expired, deleted

    @staticmethod
    def _prune_empty_workspaces() -> None:
        """Removes any per user directory in :attr:`ASKS_DIR` left empty after a prune."""
        for entry in ASKS_DIR.iterdir():
            # Only touch the numeric per user directories; shared research folders are left alone.
            if entry.is_dir() and entry.name.isdigit() and not any(entry.iterdir()):
                entry.rmdir()

    @staticmethod
    def _prune_attachments() -> None:
        """Removes saved attachments older than :attr:`ATTACHMENT_MAX_AGE`, then any user directory left empty."""
        cutoff: float = time.time() - ATTACHMENT_MAX_AGE
        for entry in ATTACHMENTS_DIR.rglob("*"):
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
        for entry in ATTACHMENTS_DIR.iterdir():
            if entry.is_dir() and not any(entry.iterdir()):
                entry.rmdir()

    async def git_status(self) -> str:
        """Returns the short-format git status of :attr:`PROJECT_ROOT`; empty string when the tree is clean or git fails."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "status",
                "--short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=PROJECT_ROOT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except (TimeoutError, FileNotFoundError):
            return ""
        return stdout.decode().strip()

    def append_git_changes(self, *, embeds: list[ClaudeEmbed], changes: str) -> None:
        """Adds a `Working Tree Changes:` field with the git status to the final embed."""
        embeds[-1].add_field(name="Working Tree Changes:", value=f"```\n{changes[:1000]}\n```", inline=False)

    async def save_history(
        self,
        *,
        user_id: int,
        session_id: str,
        prompt: str,
        response: str,
        model: str,
        cost_usd: Optional[float] = None,
        history_type: str = "ask",
    ) -> None:
        """Persists a prompt/response exchange to the `claude_history` table."""
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO claude_history(user_id, session_id, prompt, response, model, cost_usd, type, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                user_id,
                session_id,
                prompt,
                response,
                model,
                cost_usd,
                history_type,
                time.time(),
            )

    async def store_session(self, *, user_id: int, session_id: str) -> None:
        """Marks a session as one to keep, so the daily prune leaves it (and its files) alone.

        Idempotent — pressing Store again on an already-stored session just un-expires it.

        Parameters
        ----------
        user_id: :class:`int`
            The Discord user the session belongs to.
        session_id: :class:`str`
            The Claude Code CLI session ID to keep.

        """
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO claude_sessions(user_id, session_id, created_at, expired_at) VALUES(?, ?, ?, NULL)
                ON CONFLICT(user_id, session_id) DO UPDATE SET expired_at = NULL""",
                user_id,
                session_id,
                time.time(),
            )

    async def is_stored(self, *, user_id: int, session_id: str) -> bool:
        """Returns whether `session_id` is in the user's stored-session list."""
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchone(
                """SELECT 1 FROM claude_sessions WHERE user_id = ? AND session_id = ?""",
                user_id,
                session_id,
            )
        return row is not None

    async def delete_session(self, *, user_id: int, session_id: str, keep_files: bool = False) -> None:
        """Forgets a session entirely — its stored flag, its history and its workspace.

        Parameters
        ----------
        user_id: :class:`int`
            The Discord user the session belongs to.
        session_id: :class:`str`
            The session to delete.
        keep_files: :class:`bool`, optional
            Leave the session's generated files on disk, by default `False`.

        """
        async with self.bot.pool.acquire() as conn:
            await conn.execute("""DELETE FROM claude_sessions WHERE user_id = ? AND session_id = ?""", user_id, session_id)
            await conn.execute("""DELETE FROM claude_history WHERE user_id = ? AND session_id = ?""", user_id, session_id)
        if not keep_files:
            workspace: Path = session_dir(user_id=user_id, session_id=session_id)
            await asyncio.to_thread(shutil.rmtree, workspace, ignore_errors=True)

    async def get_stored_sessions(self, *, user_id: int) -> list[Any]:
        """Returns the user's stored sessions, most recently used first.

        Each row carries `first_prompt`, `model` and `last_used` pulled from `claude_history` so an
        unnamed session still says what it is tied to and can be resumed with the right model.

        Parameters
        ----------
        user_id: :class:`int`
            The Discord user to look up.

        Returns
        -------
        :class:`list[Any]`
            The joined `claude_sessions` rows.

        """
        query: str = """
        SELECT store.session_id AS session_id, store.expired_at AS expired_at,
            (SELECT hist.prompt FROM claude_history AS hist
             WHERE hist.user_id = store.user_id AND hist.session_id = store.session_id
             ORDER BY hist.created_at ASC LIMIT 1) AS first_prompt,
            (SELECT hist.model FROM claude_history AS hist
             WHERE hist.user_id = store.user_id AND hist.session_id = store.session_id
             ORDER BY hist.created_at DESC LIMIT 1) AS model,
            (SELECT MAX(hist.created_at) FROM claude_history AS hist
             WHERE hist.user_id = store.user_id AND hist.session_id = store.session_id) AS last_used
        FROM claude_sessions AS store WHERE store.user_id = ?
        ORDER BY last_used DESC"""
        async with self.bot.pool.acquire() as conn:
            return await conn.fetchall(query, user_id)

    async def collect_reply_files(self, *, embeds: list[ClaudeEmbed], response: str, paths: list[Path]) -> list[discord.File]:
        """Builds the file attachments for a reply and notes them on the final embed.

        Attaches anything the CLI wrote into the session workspace, plus the full response as a
        Markdown file when it was long enough to be split across multiple embeds.

        Parameters
        ----------
        embeds: :class:`list[ClaudeEmbed]`
            The response embeds; a `Generated Files:` field is added to the last one when files were made.
        response: :class:`str`
            The full response text, used for the Markdown transcript.
        paths: :class:`list[Path]`
            The generated files from :attr:`ClaudeResult.files`.

        Returns
        -------
        :class:`list[discord.File]`
            The files to send alongside the embed.

        """
        files, skipped = await asyncio.to_thread(_build_reply_files, paths=paths)

        # A long answer is split across embeds, so ship the unbroken text as a file too.
        if len(response) > FULL_RESPONSE_THRESHOLD:
            buffer: io.BytesIO = io.BytesIO(response.encode())
            files.append(discord.File(fp=buffer, filename="claude_response.md"))

        if paths:
            listed_files: str = "\n".join(f"- `{path.relative_to(PROJECT_ROOT).as_posix()}`" for path in paths[:MAX_REPLY_FILES])
            if skipped:
                # Skipped covers both oversized files and anything past the per reply cap.
                listed_files += f"\n*{len(skipped)} file(s) could not be attached; grab them from the session workspace.*"
            embeds[-1].add_field(name="Generated Files:", value=listed_files[:1000], inline=False)
        return files

    async def run_claude(
        self,
        *,
        prompt: str,
        model: str,
        user_id: int,
        permission_mode: Optional[str] = None,
        resume_session_id: Optional[str] = None,
        progress: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None,
    ) -> ClaudeResult:
        """Runs the Claude Code CLI with the prompt, starting a new session or resuming `resume_session_id`.

        The whole run is serialized per user by :meth:`_lock_for`, so two overlapping asks or replies never
        share a session transcript or race the workspace file diff. The prompt is always suffixed with
        :attr:`OUTPUT_DIR_NOTICE` so generated files stay in the session workspace.

        Parameters
        ----------
        prompt: :class:`str`
            The prompt to send to Claude Code.
        model: :class:`str`
            The Claude model to use.
        user_id: :class:`int`
            The Discord user ID; used to key the run lock and the workspace.
        permission_mode: :class:`Optional[str]`, optional
            Passed to the CLI's `--permission-mode` flag; `'acceptEdits'` auto-approves file edits and
            `'plan'` restricts the run to planning, by default `None` (the CLI default).
        resume_session_id: :class:`Optional[str]`, optional
            An existing session to `--resume`; `None` starts a fresh session, by default `None`.
        progress: :class:`Optional[Callable[[str], Coroutine[Any, Any, None]]]`, optional
            An async callback invoked with a short status string as the CLI uses tools, by default `None`.

        Returns
        -------
        :class:`ClaudeResult`
            The response text, session ID, cost and generated files, or a user-displayable error message.

        """
        async with self._lock_for(user_id):
            # New sessions get their ID up front via `--session-id` so the workspace path is known before the run.
            resuming: bool = resume_session_id is not None
            wanted_id: str = resume_session_id if resuming else str(uuid.uuid4())
            workspace: Path = session_dir(user_id=user_id, session_id=wanted_id)
            before: dict[Path, float] = await asyncio.to_thread(_dir_snapshot, directory=workspace)

            prompt += OUTPUT_DIR_NOTICE.format(directory=workspace.relative_to(PROJECT_ROOT).as_posix())
            command_args: list[str] = ["claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose"]
            if permission_mode is not None:
                command_args += ["--permission-mode", permission_mode]
            command_args += ["--resume", wanted_id] if resuming else ["--session-id", wanted_id]

            try:
                proc = await asyncio.create_subprocess_exec(
                    *command_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=1024 * 1024,
                    cwd=PROJECT_ROOT,
                )
            except FileNotFoundError:
                return ClaudeResult(error=f"`claude` was not found on PATH. Is Claude Code installed? {self.emoji_table.kuma_shock}")

            assert proc.stdout is not None and proc.stderr is not None  # noqa: PT018, S101
            stderr_task: asyncio.Task[bytes] = asyncio.create_task(proc.stderr.read())
            # The CLI streams one JSON event per line; the single `result` event carries the answer.
            result_event: Optional[dict] = None

            try:
                async with asyncio.timeout(delay=CLAUDE_TIMEOUT):
                    while line := await proc.stdout.readline():
                        try:
                            event: dict = json.loads(line.decode().strip() or "{}")
                        except json.JSONDecodeError:
                            continue

                        if event.get("type") == "assistant" and progress is not None:
                            for block in event.get("message", {}).get("content", []):
                                if block.get("type") == "tool_use":
                                    await progress(f"`{block.get('name', 'tool')}`")
                        elif event.get("type") == "result":
                            result_event = event
                    await proc.wait()

            except TimeoutError:
                proc.kill()
                stderr_task.cancel()
                return ClaudeResult(error=f"Claude Code timed out after {CLAUDE_TIMEOUT} seconds. {self.emoji_table.kuma_head_clench}")

            stderr_output: str = (await stderr_task).decode().strip()
            if result_event is None:
                error_block: str = f"\n```\n{stderr_output[:1000]}\n```" if stderr_output else ""
                return ClaudeResult(error=f"Claude Code exited without a result. {self.emoji_table.kuma_crying}{error_block}")

            if result_event.get("is_error"):
                return ClaudeResult(
                    error=f"Claude returned an error. {self.emoji_table.kuma_sad}\n```\n{result_event.get('result', 'Unknown error')}\n```",
                )

            # The CLI can hand back a different ID than we asked for (a fork, for example); follow it.
            session_id: str = str(result_event.get("session_id") or wanted_id)
            if session_id != wanted_id:
                await asyncio.to_thread(_move_workspace, user_id=user_id, old_id=wanted_id, new_id=session_id)

            text: str = str(result_event.get("result", "")).strip()
            if not text:
                return ClaudeResult(session_id=session_id, error=f"Claude returned an empty response. {self.emoji_table.kuma_shrug}")
            files: list[Path] = await asyncio.to_thread(
                _new_files, directory=session_dir(user_id=user_id, session_id=session_id), before=before
            )
            return ClaudeResult(text=text, session_id=session_id, cost_usd=result_event.get("total_cost_usd"), files=files)

    def build_embeds(
        self, *, prompt: str, response: str, model: str, cost_usd: Optional[float] = None, icon: bool = True
    ) -> list[ClaudeEmbed]:
        """Chunks the response text and builds a :class:`ClaudeEmbed` per chunk with page number footers.

        Parameters
        ----------
        prompt: :class:`str`
            The prompt that generated the response, displayed as a field on each embed.
        response: :class:`str`
            The full response text to chunk.
        model: :class:`str`
            The Claude model used, displayed in the embed titles.
        cost_usd: :class:`Optional[float]`, optional
            The API cost, displayed in the final embed's footer, by default `None`.
        icon: :class:`bool`, optional
            Whether the embeds carry the Claude author icon, by default `True`.

        Returns
        -------
        :class:`list[ClaudeEmbed]`
            The built embeds; always at least one.

        """
        chunks: list[str] = _chunk_response(response)
        embeds: list[ClaudeEmbed] = [
            ClaudeEmbed(
                cog=self,
                prompt=prompt,
                chunk=chunk,
                model=model,
                # Only the final chunk carries the cost; the rest would just repeat it.
                cost_usd=cost_usd if index == len(chunks) - 1 else None,
                icon=icon,
            )
            for index, chunk in enumerate(chunks)
        ]
        if len(embeds) > 1:
            for index, embed in enumerate(embeds):
                cost_text: str = f" | **Cost:** ${cost_usd:.4f}" if cost_usd is not None and index == len(embeds) - 1 else ""
                embed.set_footer(text=f"{index + 1}/{len(embeds)}{cost_text} | Kuma Kuma Bear")
        return embeds

    async def save_attachment(self, *, attachment: discord.Attachment, user_id: int) -> Path:
        """Saves a Discord attachment into the user's attachment directory with a sanitized, timestamped filename.

        Parameters
        ----------
        attachment: :class:`discord.Attachment`
            The Discord attachment to save.
        user_id: :class:`int`
            The Discord user the attachment belongs to.

        Returns
        -------
        :class:`Path`
            The path the attachment was saved to.

        """
        directory: Path = attachments_dir(user_id=user_id)
        directory.mkdir(parents=True, exist_ok=True)
        name: str = re.sub(r"[^\w.\-]", "_", attachment.filename)
        path: Path = directory.joinpath(f"{int(time.time())}-{name}")
        await attachment.save(fp=path)
        return path

    async def run_and_render(
        self,
        *,
        owner: discord.Member | discord.User,
        prompt: str,
        model: str,
        permission_mode: Optional[str],
        resume_session_id: Optional[str],
        history_type: str,
        target: discord.Message | discord.WebhookMessage,
    ) -> None:
        """Runs a prompt through the CLI and edits `target` into the paginated response.

        The single render path shared by fresh asks/plans and session replies: it shows live tool
        progress, stores the exchange, appends any working-tree changes and generated files, and
        finishes with a :class:`ClaudeView` bound to whatever session the run landed on.

        Parameters
        ----------
        owner: :class:`discord.Member | discord.User`
            The user the run belongs to; owns the resulting view.
        prompt: :class:`str`
            The prompt to send to Claude Code.
        model: :class:`str`
            The Claude model to use.
        permission_mode: :class:`Optional[str]`
            Forwarded to :meth:`run_claude`.
        resume_session_id: :class:`Optional[str]`
            An existing session to resume, or `None` to start a fresh one.
        history_type: :class:`str`
            The interaction type tag stored in `claude_history`.
        target: :class:`discord.Message | discord.WebhookMessage`
            The (already sent) message to edit with progress and the final response.

        """
        await target.edit(content=f"{self.emoji_table.kuma_tea} Working on it...")

        last_update: float = 0.0

        async def on_progress(status: str) -> None:
            nonlocal last_update
            now: float = time.monotonic()
            if now - last_update < PROGRESS_INTERVAL:
                return
            last_update = now
            try:
                await target.edit(content=f"{self.emoji_table.kuma_tea} Working on it... {status}")
            except discord.HTTPException:
                pass

        git_before: str = await self.git_status()
        result: ClaudeResult = await self.run_claude(
            prompt=prompt,
            model=model,
            user_id=owner.id,
            permission_mode=permission_mode,
            resume_session_id=resume_session_id,
            progress=on_progress,
        )
        if result.error is not None:
            await target.edit(content=result.error)
            return

        await self.save_history(
            user_id=owner.id,
            session_id=result.session_id,
            prompt=prompt,
            response=result.text,
            model=model,
            cost_usd=result.cost_usd,
            history_type=history_type,
        )

        embeds: list[ClaudeEmbed] = self.build_embeds(prompt=prompt, response=result.text, model=model, cost_usd=result.cost_usd)
        git_after: str = await self.git_status()
        if git_after != git_before:
            self.append_git_changes(embeds=embeds, changes=git_after)
        files: list[discord.File] = await self.collect_reply_files(embeds=embeds, response=result.text, paths=result.files)

        view = ClaudeView(
            session_id=result.session_id,
            model=model,
            permission_mode=permission_mode,
            is_question=_is_yes_no_question(result.text),
            stored=await self.is_stored(user_id=owner.id, session_id=result.session_id),
            owner=owner,
            cog=self,
            embeds=embeds,
            timeout=None,
        )
        # `first_embed.attachments` hands back a freshly opened icon; see `ClaudeEmbed.attachments`.
        first_embed: ClaudeEmbed = embeds[0]
        await target.edit(content=None, embed=first_embed, attachments=[*first_embed.attachments, *files], view=view)

    async def dispatch_prompt(
        self,
        interaction: discord.Interaction,
        *,
        prompt: str,
        model: str,
        attachment: Optional[discord.Attachment] = None,
        permission_mode: Optional[str] = None,
        history_type: str = "ask",
    ) -> None:
        """Starts a fresh session from a slash command and renders the response in place.

        Shared by :meth:`ask` and :meth:`plan`; the interaction must already be deferred.

        Parameters
        ----------
        interaction: :class:`discord.Interaction`
            The deferred interaction to reply through.
        prompt: :class:`str`
            The prompt to send to Claude Code.
        model: :class:`str`
            The Claude model to use.
        attachment: :class:`Optional[discord.Attachment]`, optional
            A file to save into the user's attachment directory and point the CLI at, by default `None`.
        permission_mode: :class:`Optional[str]`, optional
            Forwarded to :meth:`run_and_render`, by default `None`.
        history_type: :class:`str`, optional
            The interaction type tag for history storage, by default `ask`.

        """
        if attachment is not None:
            if attachment.size > MAX_ATTACHMENT_SIZE:
                await interaction.edit_original_response(
                    content=f"That file is too big for me to carry ({attachment.size / 1024 / 1024:.1f}MB); "
                    f"the limit is {MAX_ATTACHMENT_SIZE // 1024 // 1024}MB. {self.emoji_table.kuma_pout}",
                )
                return
            path: Path = await self.save_attachment(attachment=attachment, user_id=interaction.user.id)
            prompt = f"{prompt}\n\n[The user attached a file, saved at `{path.relative_to(PROJECT_ROOT)}`]"

        target: discord.InteractionMessage = await interaction.original_response()
        await self.run_and_render(
            owner=interaction.user,
            prompt=prompt,
            model=model,
            permission_mode=permission_mode,
            resume_session_id=None,
            history_type=history_type,
            target=target,
        )

    async def continue_session(
        self,
        interaction: discord.Interaction,
        *,
        session_id: str,
        model: str,
        permission_mode: Optional[str] = None,
        reply: str,
        history_type: str = "reply",
    ) -> None:
        """Resumes `session_id` with `reply` and posts the response as a fresh followup.

        Backs the Reply/Yes/No buttons and the Resume button; the reply lands as a new ephemeral
        message so the message it was dispatched from stays put.

        Parameters
        ----------
        interaction: :class:`discord.Interaction`
            The component or modal-submit interaction to respond through.
        session_id: :class:`str`
            The session to resume.
        model: :class:`str`
            The model to run the reply with.
        permission_mode: :class:`Optional[str]`, optional
            Forwarded to :meth:`run_and_render`, by default `None`.
        reply: :class:`str`
            The reply text to send to the CLI.
        history_type: :class:`str`, optional
            The interaction type tag for history storage, by default `reply`.

        """
        await interaction.response.defer(ephemeral=True)
        target: discord.WebhookMessage = await interaction.followup.send(
            content=f"{self.emoji_table.kuma_tea} Working on your reply...",
            ephemeral=True,
            wait=True,
        )
        await self.run_and_render(
            owner=interaction.user,
            prompt=reply,
            model=model,
            permission_mode=permission_mode,
            resume_session_id=session_id,
            history_type=history_type,
            target=target,
        )

    @claude.command(name="ask", description="Send a prompt to Claude Code in the Kuma_Kuma project directory.")
    @app_commands.describe(
        prompt="The prompt to send to Claude Code.",
        model="The Claude model to use. Defaults to sonnet.",
        attachment="An optional file to share with Claude Code (saved inside the project).",
        allow_edits="Auto-approve file edits inside the project directory. Defaults to False.",
    )
    @app_commands.choices(model=MODEL_CHOICES)
    @app_commands.check(_is_owner)
    async def ask(
        self,
        interaction: discord.Interaction,
        prompt: str,
        model: str = DEFAULT_MODEL,
        attachment: Optional[discord.Attachment] = None,
        allow_edits: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.dispatch_prompt(
            interaction,
            prompt=prompt,
            model=model,
            attachment=attachment,
            permission_mode="acceptEdits" if allow_edits else None,
            history_type="ask",
        )

    @claude.command(name="plan", description="Ask Claude Code to plan an approach without touching any files.")
    @app_commands.describe(
        prompt="What you want Claude Code to plan out.",
        model="The Claude model to use. Defaults to sonnet.",
        attachment="An optional file to share with Claude Code (saved inside the project).",
    )
    @app_commands.choices(model=MODEL_CHOICES)
    @app_commands.check(_is_owner)
    async def plan(
        self,
        interaction: discord.Interaction,
        prompt: str,
        model: str = DEFAULT_MODEL,
        attachment: Optional[discord.Attachment] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.dispatch_prompt(
            interaction,
            prompt=prompt,
            model=model,
            attachment=attachment,
            permission_mode="plan",
            history_type="plan",
        )

    @claude.command(name="history", description="Browse your Claude Code history and resume any entry.")
    @app_commands.describe(limit="Number of entries to show (default 10, max 50).")
    @app_commands.check(_is_owner)
    async def history(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 50] = 10) -> None:
        await interaction.response.defer(ephemeral=True)

        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetchall(
                """SELECT * FROM claude_history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?""",
                interaction.user.id,
                limit,
            )

        if not rows:
            await interaction.edit_original_response(content=f"No history found. {self.emoji_table.kuma_shrug}")
            return

        # One embed per entry so the Resume button can act on the exact exchange on screen.
        embeds: list[KumaEmbed] = []
        refs: list[SessionRef] = []
        for row in rows:
            timestamp: str = f"<t:{int(row['created_at'])}:R>"
            prompt_preview: str = row["prompt"][:200] + ("..." if len(row["prompt"]) > 200 else "")
            response_preview: str = row["response"][:4090] + ("..." if len(row["response"]) > 4090 else "")
            embed = KumaEmbed(
                cog=self,
                title=f"Claude History — `{row['model']}`",
                color=discord.Color.greyple(),
                description=response_preview,
            )
            embed.add_field(name="Prompt:", value=f"`{prompt_preview}`", inline=False)
            embed.add_field(name="Type:", value=f"`{row['type']}`", inline=True)
            embed.add_field(name="When:", value=timestamp, inline=True)
            if row["cost_usd"] is not None:
                embed.add_field(name="Cost:", value=f"${row['cost_usd']:.4f}", inline=True)
            embeds.append(embed)
            refs.append(SessionRef(session_id=row["session_id"], model=row["model"]))

        if len(embeds) > 1:
            for index, embed in enumerate(embeds):
                embed.set_footer(text=f"{index + 1}/{len(embeds)} | Kuma Kuma Bear")

        view = SessionListView(refs=refs, owner=interaction.user, cog=self, embeds=embeds, timeout=None)
        first_embed: KumaEmbed = embeds[0]
        await interaction.edit_original_response(content=None, embed=first_embed, attachments=first_embed.attachments, view=view)

    @claude.command(name="sessions", description="Browse your stored Claude Code sessions.")
    @app_commands.check(_is_owner)
    async def sessions(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        rows = await self.get_stored_sessions(user_id=interaction.user.id)
        # self.user_sessions[interaction.user.id] = [row["session_id"] for row in rows]
        if not rows:
            await interaction.edit_original_response(
                content=f"No stored sessions yet — press ⭐ **Store** on a reply to keep one. {self.emoji_table.kuma_shrug}",
            )
            return

        # One page per session keeps Resume/Delete tied to the entry on screen.
        embeds: list[KumaEmbed] = []
        refs: list[SessionRef] = []
        for row in rows:
            embed = KumaEmbed(
                cog=self,
                title="Claude Code — Stored Session",
                color=discord.Color.blurple(),
                description=f"Resume or delete this session below. {self.emoji_table.kuma_tea}",
            )
            session_details: str = f"`{row['session_id']}`\nModel: `{row['model']}`"
            if row["last_used"] is not None:
                session_details += f"\nLast used <t:{int(row['last_used'])}:R>"
            if row["expired_at"] is not None:
                session_details += (
                    f"\n⚠️ *Expired* — Claude Code dropped the transcript <t:{int(row['expired_at'])}:R>, so it can no "
                    "longer be resumed. Files are kept until you delete it."
                )
            file_count, total_size = await asyncio.to_thread(
                _dir_stats, directory=session_dir(user_id=interaction.user.id, session_id=row["session_id"])
            )
            if file_count:
                session_details += f"\n**Files:** {file_count} ({total_size / 1024:.1f}KB)"
            if row["first_prompt"] and (snippet := _prompt_snippet(row["first_prompt"])):
                session_details += f"\n> {snippet}"
            embed.add_field(name="Session", value=session_details, inline=False)
            embeds.append(embed)
            refs.append(SessionRef(session_id=row["session_id"], model=row["model"] or DEFAULT_MODEL))

        if len(embeds) > 1:
            for index, embed in enumerate(embeds):
                embed.set_footer(text=f"{index + 1}/{len(embeds)} | Kuma Kuma Bear")

        view = SessionListView(refs=refs, allow_delete=True, owner=interaction.user, cog=self, embeds=embeds, timeout=None)
        first_embed: KumaEmbed = embeds[0]
        await interaction.edit_original_response(content=None, embed=first_embed, attachments=first_embed.attachments, view=view)

    async def render_spoof_sessions(self, interaction: discord.Interaction, *, pages: int) -> None:
        """Renders a fake stored-session list so :class:`SessionListView` can be checked without stored sessions.

        Mirrors the layout `/claude sessions` builds — one page per session, with the last page
        flagged expired so that branch is visible too.

        Parameters
        ----------
        interaction: :class:`discord.Interaction`
            The already deferred interaction to reply through.
        pages: :class:`int`
            How many fake sessions to list.

        """
        now: float = time.time()
        embeds: list[KumaEmbed] = []
        refs: list[SessionRef] = []
        for index in range(pages):
            session_id: str = f"{SPOOF_SESSION_ID}-{index + 1}"
            embed = KumaEmbed(
                cog=self,
                title="Claude Code — Stored Session (Spoofed)",
                color=discord.Color.blurple(),
                description=f"Resume or delete this session below. {self.emoji_table.kuma_tea}",
            )
            session_details: str = f"`{session_id}`\nModel: `{SPOOF_MODEL}`\nLast used <t:{int(now - (index + 1) * 3600)}:R>"
            # Flag the last page expired so the "transcript is gone" branch gets rendered at least once.
            if pages > 1 and index == pages - 1:
                session_details += (
                    f"\n⚠️ *Expired* — Claude Code dropped the transcript <t:{int(now - 86400)}:R>, so it can no "
                    "longer be resumed. Files are kept until you delete it."
                )
            session_details += f"\n**Files:** {len(SPOOF_FILES)} (1.2KB)\n> {_prompt_snippet(SPOOF_PROMPT)}"
            embed.add_field(name="Session", value=session_details, inline=False)
            embeds.append(embed)
            refs.append(SessionRef(session_id=session_id, model=SPOOF_MODEL))

        if len(embeds) > 1:
            for index, embed in enumerate(embeds):
                embed.set_footer(text=f"{index + 1}/{len(embeds)} | Kuma Kuma Bear")

        view = SpoofSessionListView(refs=refs, allow_delete=True, owner=interaction.user, cog=self, embeds=embeds, timeout=None)
        first_embed: KumaEmbed = embeds[0]
        await interaction.edit_original_response(
            content=f"{self.emoji_table.kuma_peak} **Spoofed session list** — none of these sessions exist.",
            embed=first_embed,
            attachments=first_embed.attachments,
            view=view,
        )

    def append_icon_report(self, *, embeds: list[ClaudeEmbed], attached: list[str]) -> None:
        """Adds a field spelling out the embed's icon wiring, so a blank avatar can be diagnosed on Discord itself.

        Every `attachment://` URL an embed references must name a file in the same message's payload —
        an unmatched reference is what renders as a missing image. This lists both sides for comparison.

        Parameters
        ----------
        embeds: :class:`list[ClaudeEmbed]`
            The response embeds; the report lands on the last one.
        attached: :class:`list[str]`
            The filenames actually being uploaded alongside the embed.

        """
        first_embed: ClaudeEmbed = embeds[0]
        embed_json: dict = first_embed.to_dict()
        author_icon: str = embed_json.get("author", {}).get("icon_url", "*(none)*")
        footer_icon: str = embed_json.get("footer", {}).get("icon_url", "*(none)*")

        lines: list[str] = [
            f"`CLAUDE_ICON` exists: `{CLAUDE_ICON.is_file()}`",
            f"Embed author `icon_url`: `{author_icon}`",
            f"Embed footer `icon_url`: `{footer_icon}`",
            f"Uploaded attachments: `{', '.join(attached) or '(none)'}`",
        ]
        embeds[-1].add_field(name="Icon Wiring:", value="\n".join(lines)[:1000], inline=False)

    @claude.command(name="spoof", description="Render a fake Claude Code reply to check the embed/view/modal layouts.")
    @app_commands.describe(
        component="Which piece of the UI to render. Defaults to the full reply view.",
        pages="How many embeds/sessions to spread the spoof across (default 2).",
        question="End the response on a yes/no question so the Yes/No buttons appear.",
        stored="Start the reply view with its Store button already disabled.",
        files="Write sample files into the spoof workspace and attach them.",
        git_changes="Append a fake `Working Tree Changes:` field to the last embed.",
        icon="Attach the Claude author icon. Turn off to compare against a render without it.",
        icon_report="Append a field listing what the embed references vs what is uploaded.",
    )
    @app_commands.choices(component=SPOOF_COMPONENT_CHOICES)
    @app_commands.check(_is_owner)
    async def spoof(
        self,
        interaction: discord.Interaction,
        component: str = "view",
        pages: app_commands.Range[int, 1, 5] = 2,
        question: bool = False,
        stored: bool = False,
        files: bool = False,
        git_changes: bool = False,
        icon: bool = True,
        icon_report: bool = False,
    ) -> None:
        # A modal has to be the *initial* response to an interaction, so this branch never defers.
        if component == "modal":
            await interaction.response.send_modal(SpoofReplyModal(cog=self, session_id=SPOOF_SESSION_ID, model=SPOOF_MODEL))
            return

        await interaction.response.defer(ephemeral=True)

        if component == "sessions":
            await self.render_spoof_sessions(interaction, pages=pages)
            return

        response: str = _spoof_response(pages=pages, question=question)
        embeds: list[ClaudeEmbed] = self.build_embeds(
            prompt=SPOOF_PROMPT, response=response, model=SPOOF_MODEL, cost_usd=SPOOF_COST, icon=icon
        )
        if git_changes:
            self.append_git_changes(embeds=embeds, changes=SPOOF_GIT_STATUS)

        paths: list[Path] = await asyncio.to_thread(_write_spoof_files, user_id=interaction.user.id) if files else []
        reply_files: list[discord.File] = await self.collect_reply_files(embeds=embeds, response=response, paths=paths)

        first_embed: ClaudeEmbed = embeds[0]
        attachments: list[discord.File] = [*first_embed.attachments, *reply_files]
        if icon_report:
            self.append_icon_report(embeds=embeds, attached=[entry.filename for entry in attachments if entry.filename])

        # `embed` renders the embeds bare; `view` wires up the spoofed components underneath them.
        view: Optional[SpoofClaudeView] = None
        if component == "view":
            view = SpoofClaudeView(
                session_id=SPOOF_SESSION_ID,
                model=SPOOF_MODEL,
                permission_mode=None,
                is_question=_is_yes_no_question(response),
                stored=stored,
                owner=interaction.user,
                cog=self,
                embeds=embeds,
                timeout=None,
            )

        await interaction.edit_original_response(
            content=f"{self.emoji_table.kuma_peak} **Spoofed reply** — {len(embeds)} embed(s), no CLI run.",
            embed=first_embed,
            attachments=attachments,
            view=view,
        )


async def setup(bot: Kuma_Kuma) -> None:  # noqa: D103
    await bot.add_cog(ClaudeCog(bot=bot))
