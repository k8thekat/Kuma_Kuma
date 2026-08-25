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
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Self

import discord

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

LOGGER = logging.getLogger()

__all__ = ("AnimationFrames", "AnimationStyle", "AnimationTarget", "KumaAnimation", "KumaRollingAnimation", "TranscriptBlock")

# Anything a status line can be rendered onto. An `Interaction` is accepted so a deferred slash
# command can be animated without the caller first fetching its original response.
AnimationTarget = discord.Message | discord.WebhookMessage | discord.InteractionMessage | discord.Interaction

# Rate limits are per channel (a thread counts as its own channel), roughly five edits per five
# seconds. Every default below is chosen to sit at or under half that budget, so an animation never
# starves the real content edits happening alongside it.
DEFAULT_MIN_INTERVAL: float = 1.5
DEFAULT_IDLE_INTERVAL: float = 2.5
DEFAULT_MAX_INTERVAL: float = 6.0
DEFAULT_DECAY_AFTER: float = 20.0
# Multiplier applied to the current delay each time Discord answers with a 429.
RATE_LIMIT_BACKOFF: float = 1.5
# Lines held on a live tail before it is sealed and a fresh one opened. Kept well under what the
# length budget alone would allow, so a tail stays readable rather than merely legal.
DEFAULT_MAX_TAIL_LINES: int = 14


@dataclass(frozen=True)
class AnimationStyle:
    """A frame sequence and the template that positions it around the label.

    Bundling the two matters because they are not independent: trailing dots belong *directly*
    after the label, while a spinner glyph wants an ellipsis and a space in front of it. Keeping
    them together means a style can be swapped for another without the caller re-deriving a template.

    Attributes
    ----------
    frames: :class:`tuple[str, ...]`
        The frames, cycled in order. A single frame renders as a static suffix.
    template: :class:`str`
        A format string accepting `{label}` and `{frame}`.

    """

    frames: tuple[str, ...]
    template: str = field(default="{label}… {frame}")

    def render(self, *, label: str, index: int) -> str:
        """Returns the rendered status line for a frame index, wrapping the index around."""
        frame: str = self.frames[index % len(self.frames)] if self.frames else ""
        return self.template.format(label=label, frame=frame)


class AnimationFrames:
    """Ready-made :class:`AnimationStyle` presets, plus a builder for your own.

    Every preset is a plain :class:`AnimationStyle`, so a caller can take one and rebuild it with a
    different template rather than being limited to what is listed here.
    """

    BRAILLE: AnimationStyle = AnimationStyle(frames=("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"))
    "An eight frame spinner. Reads clearly as motion, but depends on the viewer's font."

    DOTS: AnimationStyle = AnimationStyle(frames=("", ".", "..", "..."), template="{label}{frame}")
    "Trailing dots. No emoji, no font dependency, quietest of the presets."

    PULSE: AnimationStyle = AnimationStyle(frames=("◐", "◓", "◑", "◒"))
    "A four frame rotating disc; heavier than braille, lighter than an emoji."

    CLOCK: AnimationStyle = AnimationStyle(frames=("🕐", "🕒", "🕔", "🕖", "🕘", "🕚"))
    "Unicode clock faces — obvious at a glance that time is passing."

    @staticmethod
    def toggle(*frames: str, template: str = "{label}… {frame}") -> AnimationStyle:
        """Builds a style that cycles the frames given, e.g. a pair of application emoji.

        This is the generalisation of the two-emoji toggle in `ffxiv.py` — pass the emoji strings
        and the animation alternates between them exactly as before.

        Parameters
        ----------
        *frames: :class:`str`
            The frames to cycle, in order.
        template: :class:`str`, optional
            The format string, accepting `{label}` and `{frame}`.

        Returns
        -------
        :class:`AnimationStyle`
            The built style.

        Raises
        ------
        :exc:`ValueError`
            You passed no frames.

        """
        if not frames:
            err = "An AnimationStyle needs at least one frame."
            raise ValueError(err)
        return AnimationStyle(frames=tuple(frames), template=template)


class KumaAnimation:
    """A self-updating status message, driven by a background task.

    Use it as an async context manager: the task starts on entry and is always cancelled on exit,
    including when the body raises, so a failure can never strand a message mid-animation.

    .. code-block:: python

        async with cog.animate(message, label="Fetch") as status:
            status.label = "Build"          # re-renders promptly
            status.add_line("✓ 42 items")   # append to the growing body
        # stopped and tidied automatically

    The edit cadence is deliberately *not* a fixed interval. A change to :attr:`label` or the body
    re-renders as soon as :attr:`min_interval` allows, so the message reacts to real events; with
    nothing changing it settles to :attr:`idle_interval` and then eases out toward
    :attr:`max_interval`, which keeps a long run from spending its whole rate-limit budget on
    frames nobody is reading.

    .. warning::
        Animates by editing a message's `content`, so it cannot drive a Components V2 message; those
        carry components instead of content and there is nothing here for it to write to. Animating a
        layout would mean re-rendering the whole view on every frame.

    Parameters
    ----------
    target: :class:`AnimationTarget`
        The message (or interaction) to edit.
    label: :class:`str`, optional
        The leading text, by default `"Working"`.
    style: :class:`Optional[AnimationStyle]`, optional
        The frame style, by default :attr:`AnimationFrames.BRAILLE`.
    header: :class:`Optional[str]`, optional
        A fixed line rendered above the status line, by default `None`.
    footer: :class:`Optional[str]`, optional
        A fixed line rendered below the body, by default `None`.
    status_last: :class:`bool`, optional
        Whether the status line sits *below* the body rather than above it, by default `False`.
        `True` gives the CLI reading order — finished work stacks upward and the spinner stays
        pinned to the bottom edge, which is where the reader's eye already is.
    min_interval: :class:`float`, optional
        The floor between two edits, by default :attr:`DEFAULT_MIN_INTERVAL`.
    idle_interval: :class:`float`, optional
        The cadence while nothing is changing, by default :attr:`DEFAULT_IDLE_INTERVAL`.
    max_interval: :class:`float`, optional
        The cadence a long-idle animation eases out to, by default :attr:`DEFAULT_MAX_INTERVAL`.
    decay_after: :class:`float`, optional
        Seconds without a change before the cadence starts easing out, by default
        :attr:`DEFAULT_DECAY_AFTER`.
    max_length: :class:`int`, optional
        The rendered content is truncated to this, by default `1990` — Discord rejects 2000+.

    """

    def __init__(
        self,
        target: AnimationTarget,
        *,
        label: str = "Working",
        style: Optional[AnimationStyle] = None,
        header: Optional[str] = None,
        footer: Optional[str] = None,
        status_last: bool = False,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        idle_interval: float = DEFAULT_IDLE_INTERVAL,
        max_interval: float = DEFAULT_MAX_INTERVAL,
        decay_after: float = DEFAULT_DECAY_AFTER,
        max_length: int = 1990,
    ) -> None:
        self.target: AnimationTarget = target
        self.style: AnimationStyle = style if style is not None else AnimationFrames.BRAILLE
        self.header: Optional[str] = header
        self.footer: Optional[str] = footer
        self.status_last: bool = status_last
        self.max_length: int = max_length

        # Guard the floor so a caller cannot configure themselves into a 429 loop.
        self.min_interval: float = max(min_interval, 1.0)
        self.idle_interval: float = max(idle_interval, self.min_interval)
        self.max_interval: float = max(max_interval, self.idle_interval)
        self.decay_after: float = max(decay_after, 0.0)

        self._label: str = label
        self._body: list[str] = []
        self._frame: int = 0
        self._started: float = time.monotonic()
        self._last_change: float = self._started
        self._last_edit: float = 0.0
        self._penalty: float = 0.0
        self._changed: asyncio.Event = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None

    # region --- Content

    @property
    def label(self) -> str:
        """The leading text of the status line; assigning re-renders promptly."""
        return self._label

    @label.setter
    def label(self, value: str) -> None:
        if value != self._label:
            self._label = value
            self.touch()

    @property
    def body(self) -> Sequence[str]:
        """The lines rendered between the status line and the footer."""
        return tuple(self._body)

    @property
    def elapsed(self) -> float:
        """Seconds since the animation started."""
        return time.monotonic() - self._started

    def add_line(self, line: str) -> Self:
        """Appends a line to the body and re-renders promptly."""
        self._body.append(line)
        self.touch()
        return self

    def replace_line(self, index: int, line: str) -> Self:
        """Replaces a body line in place, for turning a pending entry into a finished one."""
        if -len(self._body) <= index < len(self._body):
            self._body[index] = line
            self.touch()
        return self

    def clear_body(self) -> Self:
        """Drops every body line."""
        self._body.clear()
        self.touch()
        return self

    def touch(self) -> None:
        """Marks the content changed, waking the loop so the next edit happens promptly."""
        self._last_change = time.monotonic()
        self._changed.set()

    def render(self) -> str:
        """Returns the full message content for the current frame."""
        status: str = self.style.render(label=self._label, index=self._frame)
        body: Optional[str] = "\n".join(self._body) if self._body else None

        parts: list[Optional[str]] = [self.header]
        # With the status pinned to the bottom the body reads as a log growing toward it, the way a
        # CLI scrolls its finished work up above the live line.
        parts.extend([body, status] if self.status_last else [status, body])
        parts.append(self.footer)

        content: str = "\n\n".join(part for part in parts if part)
        if len(content) <= self.max_length:
            return content
        if not self.status_last:
            return content[: self.max_length]

        # A bottom-anchored status has to survive the cut, so the oldest body lines are what goes.
        # The header is held back out of the trim; it is the one line that stays put on screen.
        head: str = f"{self.header}\n\n" if self.header else ""
        budget: int = max(self.max_length - len(head) - 2, 0)
        kept: str = content[len(head) :][-budget:]
        # Snap forward to a line break so the trim never opens on half a line.
        _, newline, remainder = kept.partition("\n")
        return f"{head}…\n{remainder if newline else kept}"

    # endregion

    # region --- Lifecycle

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    async def start(self) -> Self:
        """Renders once immediately, then starts the background loop."""
        if self._task is not None:
            return self
        self._started = time.monotonic()
        self._last_change = self._started
        # If the opening edit already proves the target is unreachable there is nothing to animate;
        # spawning the loop would only rediscover that once per frame.
        if not await self._edit(self.render()):
            return self
        self._task = asyncio.create_task(self._run())
        return self

    async def stop(self, *, final: Optional[str] = None) -> None:
        """Stops the loop and optionally writes one last content.

        Parameters
        ----------
        final: :class:`Optional[str]`, optional
            Content to write after stopping. `None` leaves the last rendered frame in place.

        """
        task: Optional[asyncio.Task[None]] = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if final is not None:
            await self._edit(final[: self.max_length])

    async def _run(self) -> None:
        """Edits the target on a cadence that tightens on change and eases out when idle."""
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), timeout=self._next_delay())
            self._changed.clear()

            # Never edit faster than the floor, however often the content changes.
            waited: float = time.monotonic() - self._last_edit
            if waited < self.min_interval:
                await asyncio.sleep(self.min_interval - waited)

            self._frame += 1
            if not await self._edit(self.render()):
                return

    def _next_delay(self) -> float:
        """Returns how long to wait before the next frame, easing out the longer nothing changes."""
        idle: float = time.monotonic() - self._last_change
        if idle <= self.decay_after or self.decay_after == 0:
            delay: float = self.idle_interval
        else:
            # Ease linearly from idle to max over a second decay window, then hold at max.
            progress: float = min(1.0, (idle - self.decay_after) / self.decay_after)
            delay = self.idle_interval + (self.max_interval - self.idle_interval) * progress
        return min(delay + self._penalty, self.max_interval + self._penalty)

    async def _edit(self, content: str) -> bool:
        """Writes `content` to the target, returning whether the animation should keep running.

        A vanished or forbidden target stops the loop rather than raising on every frame; a 429
        widens the cadence so the animation backs off instead of fighting the rate limiter.

        Embeds are suppressed on every edit. A status line is transient chrome and any URL in it —
        a fetched page, a search — otherwise unfurls into a link embed that Discord parks at the
        *bottom* of the message, below the whole animation, where it is neither wanted nor tied to
        the line that produced it.

        .. note::
            :meth:`discord.Interaction.edit_original_response` has no such argument, so an
            interaction target has to be sent with `suppress_embeds=True` in the first place; the
            flag then rides along on every later edit.

        Parameters
        ----------
        content: :class:`str`
            The content to write.

        Returns
        -------
        :class:`bool`
            `False` once the target can no longer be edited.

        """
        self._last_edit = time.monotonic()
        try:
            if isinstance(self.target, discord.Interaction):
                await self.target.edit_original_response(content=content)
            else:
                # `Message.edit` spells it `suppress`; `Messageable.send` spells it `suppress_embeds`.
                await self.target.edit(content=content, suppress=True)
        except (discord.NotFound, discord.Forbidden):
            LOGGER.debug("<%s.%s> | Animation target is gone; stopping.", __class__.__name__, "_edit")
            return False
        except discord.HTTPException as e:
            if e.status == 429:
                self._penalty = min(self._penalty * RATE_LIMIT_BACKOFF or self.min_interval, self.max_interval)
                LOGGER.warning(
                    "<%s.%s> | Rate limited; backing the animation off to +%.1fs.",
                    __class__.__name__,
                    "_edit",
                    self._penalty,
                )
            return True
        else:
            self._penalty = 0.0
            return True

    # endregion


@dataclass(frozen=True)
class TranscriptBlock:
    """One indivisible unit of a rolling transcript.

    :class:`KumaRollingAnimation` cuts between blocks and never inside one, which is why the unit is
    not the line. A tool call and the result written beneath it are one thing; a cut between them
    strands the result on the previous message with nothing above it saying what produced it.

    Attributes
    ----------
    lines: :class:`tuple[str, ...]`
        The block's lines, rendered.
    final: :class:`bool`
        Whether the block has finished changing. Only a final block is sealed onto a message that
        will never be edited again — an unfinished one is carried forward instead, since freezing
        it would leave whatever it says at that moment standing as the permanent record.

    """

    lines: tuple[str, ...]
    final: bool = True


def _flatten(blocks: Sequence[TranscriptBlock]) -> list[str]:
    """Returns every line of `blocks`, in order."""
    return [line for block in blocks for line in block.lines]


class KumaRollingAnimation:
    """A status line that keeps itself at the *bottom* of a channel by rolling onto new messages.

    :class:`KumaAnimation` animates one fixed message, so anything posted while it runs lands below
    it and the status line stops being the last thing on screen. This owns a *succession* of
    animated messages instead: the live one is the tail, and when content has to be printed above
    it the tail is sealed into a static message and a new one is opened underneath.

    That is what makes a CLI-style transcript possible in a channel — finished work stacks upward in
    the order it happened, and the spinner is always the bottom edge.

    .. code-block:: python

        tail = KumaRollingAnimation(send=poster, label="Thinking")
        await tail.render(["✓ `Read` `main.py`", "▸ `Bash` `pytest`"])
        await tail.interject(["Here is what I found."])  # seals, posts, reopens below
        await tail.stop(final=tail.compose_final(footer="-# 2 tools · 8s"))

    .. note::
        Every message it opens is animated by a :class:`KumaAnimation`, so the same rate limit
        reasoning applies — but each :meth:`interject` costs a message *create* on top of the edits,
        and creates share the channel's budget with them. Pass `reopen=False` when a burst of
        interjections is likely and a gap in the spinner is acceptable.

    Parameters
    ----------
    send: :class:`Callable[[str], Awaitable[discord.Message]]`
        Posts one message and returns it. Should suppress embeds — see :meth:`KumaAnimation._edit`.
    label: :class:`str`, optional
        The leading text of the status line, by default `"Working"`.
    style: :class:`Optional[AnimationStyle]`, optional
        The frame style, by default :attr:`AnimationFrames.BRAILLE`.
    header: :class:`Optional[str]`, optional
        A fixed line above the *first* tail only, by default `None`.
    continuation_header: :class:`Optional[str]`, optional
        The header for tails after the first, by default `None`. Repeating the opening header on
        every rolled message turns a heading into wallpaper.
    max_length: :class:`int`, optional
        The length budget for one tail, by default `1990`.
    max_lines: :class:`int`, optional
        The lines held on one tail, by default :attr:`DEFAULT_MAX_TAIL_LINES`.
    **options: :class:`float`
        Forwarded to every :class:`KumaAnimation` — the interval and decay settings.

    """

    def __init__(
        self,
        *,
        send: Callable[[str], Awaitable[discord.Message]],
        label: str = "Working",
        style: Optional[AnimationStyle] = None,
        header: Optional[str] = None,
        continuation_header: Optional[str] = None,
        max_length: int = 1990,
        max_lines: int = DEFAULT_MAX_TAIL_LINES,
        **options: float,
    ) -> None:
        self._send: Callable[[str], Awaitable[discord.Message]] = send
        self._label: str = label
        self._style: Optional[AnimationStyle] = style
        self._header: Optional[str] = header
        self._continuation_header: Optional[str] = continuation_header
        self._max_length: int = max_length
        self._max_lines: int = max_lines
        self._options: dict[str, float] = dict(options)

        self._panel: Optional[KumaAnimation] = None
        self._opened: int = 0
        # How many of the caller's blocks are already frozen onto earlier messages. The caller passes
        # its whole transcript every render and stays ignorant of where it was cut.
        self._offset: int = 0
        self._prefix: Optional[str] = None
        self._live: list[TranscriptBlock] = []
        self._started: float = time.monotonic()

    # region --- Content

    @property
    def label(self) -> str:
        """The leading text of the status line; assigning re-renders the live tail promptly."""
        return self._label

    @label.setter
    def label(self, value: str) -> None:
        self._label = value
        if self._panel is not None:
            self._panel.label = value

    @property
    def body(self) -> Sequence[str]:
        """The lines on the live tail — a *window* onto the caller's transcript, not all of it."""
        return self._panel.body if self._panel is not None else ()

    @property
    def blocks(self) -> Sequence[TranscriptBlock]:
        """The blocks on the live tail, i.e. those not yet sealed onto an earlier message."""
        return tuple(self._live)

    @property
    def offset(self) -> int:
        """How many of the caller's transcript blocks have been sealed onto earlier messages."""
        return self._offset

    @property
    def prefix(self) -> Optional[str]:
        """A line drawn above the transcript on every live tail, or ``None``.

        Set by the caller to inject a compaction note or any other per-render preamble. The line is
        *not* part of a block, so sealing never captures it — it reappears at the top of each new
        tail for as long as it is set, and disappears as soon as it is cleared.
        """
        return self._prefix

    @prefix.setter
    def prefix(self, value: Optional[str]) -> None:
        self._prefix = value

    @property
    def elapsed(self) -> float:
        """Seconds since the first tail was opened."""
        return time.monotonic() - self._started

    async def render(self, blocks: Sequence[TranscriptBlock]) -> None:
        """Draws `blocks` as the transcript, rolling onto a fresh message once the tail is full.

        Pass the *entire* transcript every time. Blocks already sealed onto an earlier message are
        skipped here rather than tracked by the caller, so a caller only ever describes what it
        knows — the whole state of its log — and never where the message boundaries happened to fall.

        A full tail that has nothing safe to seal — every block on it still unfinished — is left to
        grow rather than rolled. :meth:`KumaAnimation.render` then trims its own oldest lines, which
        is recoverable; freezing a block that has not stopped changing is not.

        Parameters
        ----------
        blocks: :class:`Sequence[TranscriptBlock]`
            The full transcript, oldest first.

        """
        if self._panel is None:
            await self._open()
        if self._panel is None:
            return

        live: list[TranscriptBlock] = list(blocks[self._offset :])
        if self._full(_flatten(live)):
            # Capped at one short of the whole tail: the newest block is what the status line is
            # about, and sealing it would leave a fresh tail with nothing on it but a spinner.
            cut: int = self._sealable(live, limit=len(live) - 1)
            if cut:
                await self._seal(final=self._compose(_flatten(live[:cut])))
                self._offset += cut
                await self._open()
                if self._panel is None:
                    return
                live = list(blocks[self._offset :])

        self._live = live
        self._panel.clear_body()
        if self._prefix is not None:
            self._panel.add_line(self._prefix)
        for line in _flatten(live):
            self._panel.add_line(line)

    async def interject(self, contents: Sequence[str], *, reopen: bool = True) -> None:
        """Posts `contents` *above* the status line, sealing the tail and opening a new one below.

        This is the whole point of the class. The sealed tail keeps its lines, so a transcript reads
        in event order rather than being sorted into a box of tools and a pile of prose beneath it.

        Parameters
        ----------
        contents: :class:`Sequence[str]`
            Already-chunked message contents, posted in order.
        reopen: :class:`bool`, optional
            Whether to open a fresh tail immediately, by default `True`. `False` leaves the status
            line off screen until the next :meth:`render`, at one message fewer.

        """
        live: list[TranscriptBlock] = self._live
        # Only what has finished is sealed above the interjection. An unfinished block moves *below*
        # it onto the new tail, which is where it belongs anyway — work still running should sit at
        # the live edge, under the text, not frozen above it.
        cut: int = self._sealable(live, limit=len(live))
        carried: list[TranscriptBlock] = live[cut:]

        # A tail with nothing to seal is deleted rather than left behind; a dead "Thinking…" in the
        # scrollback is a lie about what the session was doing.
        await self._seal(final=self._compose(_flatten(live[:cut])) if cut else None, discard=not cut)
        self._offset += cut
        self._live = carried

        for content in contents:
            with contextlib.suppress(discord.HTTPException):
                await self._send(content)

        if reopen:
            await self._open()
            # Redrawn at once so a carried block is never off screen between the two messages.
            if self._panel is not None:
                if self._prefix is not None:
                    self._panel.add_line(self._prefix)
                for line in _flatten(carried):
                    self._panel.add_line(line)

    def compose_final(self, *, footer: str, blocks: Optional[Sequence[TranscriptBlock]] = None) -> str:
        """Returns the live tail's lines with `footer` beneath them, for a closing :meth:`stop`.

        :meth:`stop` replaces the tail outright, matching :meth:`KumaAnimation.stop`. Pass this to it
        when the transcript on the last message should survive the turn that wrote it.

        Parameters
        ----------
        footer: :class:`str`
            The closing line, written beneath the transcript.
        blocks: :class:`Optional[Sequence[TranscriptBlock]]`, optional
            The caller's full transcript, by default `None` — in which case whatever was last
            rendered onto the tail is used. Pass it when the transcript has changed since the last
            :meth:`render`, as a caller closing out its still-pending blocks has; the sealed portion
            is sliced off here, so the caller does not have to know where the cut fell.

        Returns
        -------
        :class:`str`
            The content to hand to :meth:`stop`, or an empty string when there was nothing to say.

        """
        live: list[str] = _flatten(list(blocks[self._offset :]) if blocks is not None else self._live)
        if live:
            return self._compose([*live, "", footer])
        # A tail with no transcript on it is still a *rolled* message, so it still needs the header —
        # which for a continuation is the spacer holding it off the message above. A closing frame
        # sits exactly where the live ones sat and has to be spaced exactly as they were.
        #
        # Nothing at all is still nothing: a bare header would be a message that says only that it
        # exists, and the caller reads the empty string as "put something here yourself".
        return self._compose([footer]) if footer else ""

    # endregion

    # region --- Lifecycle

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    async def stop(self, *, final: Optional[str] = None) -> None:
        """Stops the live tail, optionally replacing its content with one last write."""
        await self._seal(final=final)

    async def _open(self) -> None:
        """Posts a new message and starts animating it."""
        header: Optional[str] = self._header if self._opened == 0 else self._continuation_header
        try:
            # Posted with its header already in place. :meth:`KumaAnimation.start` renders again a
            # round trip later, and without this the tail spends that round trip one line higher than
            # where it settles — which on a rolled tail is hard against the message above it, exactly
            # the collision the continuation header exists to prevent.
            message: discord.Message = await self._send(f"{header}\n\n{self._label}…" if header else f"{self._label}…")
        except discord.HTTPException:
            LOGGER.warning("<%s.%s> | Could not open a status tail.", __class__.__name__, "_open")
            return

        self._opened += 1
        panel: KumaAnimation = KumaAnimation(
            message,
            label=self._label,
            style=self._style,
            header=header,
            status_last=True,
            max_length=self._max_length,
            **self._options,
        )
        self._panel = panel
        await panel.start()

    async def _seal(self, *, final: Optional[str] = None, discard: bool = False) -> None:
        """Freezes the live tail — spinner gone — and forgets it.

        Parameters
        ----------
        final: :class:`Optional[str]`, optional
            Content to write in place of the last frame, by default `None`.
        discard: :class:`bool`, optional
            Whether to delete the message outright, by default `False`.

        """
        panel: Optional[KumaAnimation] = self._panel
        self._panel = None
        if panel is None:
            return

        # Stopped first, always: a running loop would otherwise redraw a frame over the frozen text.
        await panel.stop(final=final)
        if discard and isinstance(panel.target, discord.Message):
            with contextlib.suppress(discord.HTTPException):
                await panel.target.delete()

    def _compose(self, lines: Sequence[str]) -> str:
        """Returns the static content for a sealed tail, header included.

        Oversize content loses its *oldest* lines, never its newest. A closing footer is the last
        line and the one thing on a sealed tail that has to survive, so trimming from the end — the
        obvious way round — would drop precisely what the trim is happening in service of.
        """
        header: Optional[str] = self._header if self._opened <= 1 else self._continuation_header
        prefix: str = f"{header}\n\n" if header else ""
        kept: list[str] = list(lines)
        trimmed: bool = False
        # Two characters of headroom for the "…" that stands in for whatever is dropped.
        budget: int = self._max_length - len(prefix) - 2

        while kept and sum(len(line) + 1 for line in kept) > budget:
            kept.pop(0)
            trimmed = True
        if trimmed:
            kept.insert(0, "…")

        body: str = "\n".join(kept)
        return f"{prefix}{body}"[: self._max_length]

    @staticmethod
    def _sealable(blocks: Sequence[TranscriptBlock], *, limit: int) -> int:
        """Returns how many leading blocks may be frozen: the final ones, up to `limit`.

        Stops at the first unfinished block rather than skipping past it, because a block sealed out
        of order would put the transcript out of order on screen.
        """
        count: int = 0
        for block in blocks[: max(limit, 0)]:
            if not block.final:
                break
            count += 1
        return count

    def _full(self, lines: Sequence[str]) -> bool:
        """Returns whether `lines` have outgrown one tail, leaving room for the status line."""
        if len(lines) > self._max_lines:
            return True
        header: int = len(self._header or self._continuation_header or "") + 2
        # The 96 covers the status line and the blank line above it, with slack for a long label.
        return header + sum(len(line) + 1 for line in lines) + 96 > self._max_length

    # endregion
