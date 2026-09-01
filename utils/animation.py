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
from typing import TYPE_CHECKING, Optional, Self, Union

import discord

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

LOGGER = logging.getLogger(__name__)

__all__ = ("AnimationFrames", "AnimationStyle", "AnimationTarget", "KumaAnimation", "KumaRollingAnimation", "TranscriptBlock")

# Anything a status line can be rendered onto; an Interaction is accepted so a deferred
# slash command can animate without first fetching its original response.
AnimationTarget = Union[discord.Message, discord.WebhookMessage, discord.InteractionMessage, discord.Interaction]

# Defaults sit at or under half the channel rate-limit budget (~5 edits / 5s) so an
# animation never starves real content edits running alongside it.
DEFAULT_MIN_INTERVAL: float = 1.5
DEFAULT_IDLE_INTERVAL: float = 2.5
DEFAULT_MAX_INTERVAL: float = 6.0
DEFAULT_DECAY_AFTER: float = 20.0
# Multiplier applied to the current delay each time Discord answers with a 429.
RATE_LIMIT_BACKOFF: float = 1.5
# Lines held on a live tail before it is sealed and a fresh one opened.
# Kept well under the length budget so a tail stays readable.
DEFAULT_MAX_TAIL_LINES: int = 14


@dataclass(frozen=True)
class AnimationStyle:
    """A frame sequence and the template that positions it around the label.

    Attributes
    ----------
    frames: :class:`tuple[str, ...]`
        The frames, cycled in order. A single frame renders as a static suffix.
    template: :class:`str`
        A format string accepting ``{label}`` and ``{frame}``.

    """

    frames: tuple[str, ...]
    template: str = field(default="{label}… {frame}")

    def render(self, *, label: str, index: int) -> str:
        """Returns the rendered status line for a frame index, wrapping around.

        Parameters
        ----------
        label: :class:`str`
            The leading text of the status line.
        index: :class:`int`
            The frame index to render.

        Returns
        -------
        :class:`str`
            The rendered line.

        """
        frame: str = self.frames[index % len(self.frames)] if self.frames else ""
        return self.template.format(label=label, frame=frame)


class AnimationFrames:
    """Ready-made :class:`AnimationStyle` presets."""

    BRAILLE: AnimationStyle = AnimationStyle(frames=("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"))
    "Eight-frame spinner. Reads clearly as motion; depends on the viewer's font."

    DOTS: AnimationStyle = AnimationStyle(frames=("", ".", "..", "..."), template="{label}{frame}")
    "Trailing dots. No emoji, no font dependency, quietest preset."

    PULSE: AnimationStyle = AnimationStyle(frames=("◐", "◓", "◑", "◒"))
    "Four-frame rotating disc; heavier than braille, lighter than emoji."

    CLOCK: AnimationStyle = AnimationStyle(frames=("🕐", "🕒", "🕔", "🕖", "🕘", "🕚"))
    "Unicode clock faces — obvious at a glance that time is passing."

    @staticmethod
    def toggle(*frames: str, template: str = "{label}… {frame}") -> AnimationStyle:
        """Builds a style that cycles the given frames.

        Parameters
        ----------
        *frames: :class:`str`
            The frames to cycle, in order.
        template: :class:`str`, optional
            The format string accepting ``{label}`` and ``{frame}``.

        Returns
        -------
        :class:`AnimationStyle`
            The built style.

        Raises
        ------
        :exc:`ValueError`
            No frames were provided.

        """
        if not frames:
            error_message = "An AnimationStyle needs at least one frame."
            raise ValueError(error_message)
        return AnimationStyle(frames=tuple(frames), template=template)


class KumaAnimation:
    """A self-updating status message driven by a background task.

    Use as an async context manager — the task starts on entry and is always
    cancelled on exit, so a failure never strands a message mid-animation.

    .. code-block:: python

        async with cog.animate(message, label="Fetch") as status:
            status.label = "Build"
            status.add_line("✓ 42 items")

    .. warning::
        Animates by editing ``content``, so it cannot drive a Components V2 message.

    Parameters
    ----------
    target: :class:`AnimationTarget`
        The message (or interaction) to edit.
    label: :class:`str`, optional
        The leading text, by default ``"Working"``.
    style: :class:`Optional[AnimationStyle]`, optional
        The frame style, by default :attr:`AnimationFrames.BRAILLE`.
    header: :class:`Optional[str]`, optional
        A fixed line rendered above the status line, by default ``None``.
    footer: :class:`Optional[str]`, optional
        A fixed line rendered below the body, by default ``None``.
    status_last: :class:`bool`, optional
        Whether the status line sits below the body rather than above it, by default ``False``.
    min_interval: :class:`float`, optional
        The floor between two edits, by default :attr:`DEFAULT_MIN_INTERVAL`.
    idle_interval: :class:`float`, optional
        The cadence while nothing is changing, by default :attr:`DEFAULT_IDLE_INTERVAL`.
    max_interval: :class:`float`, optional
        The cadence a long-idle animation eases out to, by default :attr:`DEFAULT_MAX_INTERVAL`.
    decay_after: :class:`float`, optional
        Seconds without a change before the cadence starts easing, by default :attr:`DEFAULT_DECAY_AFTER`.
    max_length: :class:`int`, optional
        The rendered content is truncated to this, by default ``1990``.

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
        """Replaces a body line in place."""
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
        """Marks the content changed so the next edit happens promptly."""
        self._last_change = time.monotonic()
        self._changed.set()

    def render(self) -> str:
        """Returns the full message content for the current frame."""
        status: str = self.style.render(label=self._label, index=self._frame)
        body: Optional[str] = "\n".join(self._body) if self._body else None

        parts: list[Optional[str]] = [self.header]
        # Status pinned to the bottom reads like a CLI — finished work stacks upward.
        parts.extend([body, status] if self.status_last else [status, body])
        parts.append(self.footer)

        content: str = "\n\n".join(part for part in parts if part)
        if len(content) <= self.max_length:
            return content
        if self.status_last is False:
            return content[: self.max_length]

        # Bottom-anchored status must survive the cut; oldest body lines go first.
        header_text: str = f"{self.header}\n\n" if self.header else ""
        budget: int = max(self.max_length - len(header_text) - 2, 0)
        kept: str = content[len(header_text) :][-budget:]
        # Snap forward to a line break so the trim never opens on half a line.
        _, newline, remainder = kept.partition("\n")
        return f"{header_text}…\n{remainder if newline else kept}"

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
        # If the target is unreachable the loop would just rediscover that every frame.
        if await self._edit(self.render()) is False:
            return self
        self._task = asyncio.create_task(self._run())
        return self

    async def stop(self, *, final: Optional[str] = None) -> None:
        """Stops the loop and optionally writes one last content.

        Parameters
        ----------
        final: :class:`Optional[str]`, optional
            Content to write after stopping. ``None`` leaves the last frame in place.

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
            if await self._edit(self.render()) is False:
                return

    def _next_delay(self) -> float:
        """Returns how long to wait before the next frame, easing out over time."""
        idle: float = time.monotonic() - self._last_change
        if idle <= self.decay_after or self.decay_after == 0:
            delay: float = self.idle_interval
        else:
            # Ease linearly from idle to max over a second decay window, then hold.
            progress: float = min(1.0, (idle - self.decay_after) / self.decay_after)
            delay = self.idle_interval + (self.max_interval - self.idle_interval) * progress
        return min(delay + self._penalty, self.max_interval + self._penalty)

    async def _edit(self, content: str) -> bool:
        """Writes ``content`` to the target; returns ``False`` once it is unreachable.

        .. note::
            Embeds are suppressed on every edit so URLs in the status line do not unfurl.
            :meth:`discord.Interaction.edit_original_response` has no ``suppress`` arg —
            send the interaction with ``suppress_embeds=True`` in the first place.

        """
        self._last_edit = time.monotonic()
        try:
            if isinstance(self.target, discord.Interaction):
                await self.target.edit_original_response(content=content)
            elif isinstance(self.target, (discord.WebhookMessage, discord.InteractionMessage)):
                # Webhook and interaction edits do not expose a `suppress` param.
                await self.target.edit(content=content)
            else:
                # `Message.edit` spells it `suppress`; `Messageable.send` spells it `suppress_embeds`.
                await self.target.edit(content=content, suppress=True)
        except (discord.NotFound, discord.Forbidden):
            LOGGER.debug("<%s.%s> | Animation target is gone; stopping.", __class__.__name__, "_edit")
            return False
        except discord.HTTPException as exception:
            if exception.status == 429:
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


@dataclass(frozen=True)
class TranscriptBlock:
    """One indivisible unit of a rolling transcript.

    Attributes
    ----------
    lines: :class:`tuple[str, ...]`
        The block's lines, rendered.
    final: :class:`bool`
        Whether the block has finished changing. Only final blocks are sealed onto a
        message that will never be edited again.

    """

    lines: tuple[str, ...]
    final: bool = True


def _flatten(blocks: Sequence[TranscriptBlock]) -> list[str]:
    """Returns every line of ``blocks``, in order."""
    return [line for block in blocks for line in block.lines]


class KumaRollingAnimation:
    """A status line that stays at the bottom of a channel by rolling onto new messages.

    :class:`KumaAnimation` animates a single fixed message. This owns a *succession* — the
    live one is the tail, and when content overflows the tail is sealed and a new one opens
    underneath. Finished work stacks upward; the spinner is always the bottom edge.

    .. code-block:: python

        tail = KumaRollingAnimation(send=poster, label="Thinking")
        await tail.render(["✓ `Read` `main.py`", "▸ `Bash` `pytest`"])
        await tail.interject(["Here is what I found."])
        await tail.stop(final=tail.compose_final(footer="-# 2 tools · 8s"))

    .. note::
        Each :meth:`interject` costs a message *create* on top of the edits.
        Pass ``reopen=False`` when a burst of interjections is likely.

    Parameters
    ----------
    send: :class:`Callable[[str], Awaitable[discord.Message]]`
        Posts one message and returns it. Should suppress embeds.
    label: :class:`str`, optional
        The leading text of the status line, by default ``"Working"``.
    style: :class:`Optional[AnimationStyle]`, optional
        The frame style, by default :attr:`AnimationFrames.BRAILLE`.
    header: :class:`Optional[str]`, optional
        A fixed line above the *first* tail only, by default ``None``.
    continuation_header: :class:`Optional[str]`, optional
        The header for tails after the first, by default ``None``.
    max_length: :class:`int`, optional
        The length budget for one tail, by default ``1990``.
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
        # Blocks already frozen onto earlier messages; the caller passes its whole transcript
        # and stays ignorant of where it was cut.
        self._offset: int = 0
        self._prefix: Optional[str] = None
        self._live: list[TranscriptBlock] = []
        self._started: float = time.monotonic()

    @property
    def label(self) -> str:
        """The leading text of the status line; assigning re-renders the live tail."""
        return self._label

    @label.setter
    def label(self, value: str) -> None:
        self._label = value
        if self._panel is not None:
            self._panel.label = value

    @property
    def body(self) -> Sequence[str]:
        """The lines on the live tail."""
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

        Not part of a block — it reappears at the top of each new tail for as long as it is
        set and disappears when cleared.

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
        """Draws ``blocks`` as the transcript, rolling onto a fresh message when the tail is full.

        Pass the *entire* transcript every time — blocks already sealed are skipped automatically.

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
            # Cap at one short of the whole tail so the newest block stays on screen.
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
        """Posts ``contents`` above the status line, sealing the tail and opening a new one below.

        Parameters
        ----------
        contents: :class:`Sequence[str]`
            Already-chunked message contents, posted in order.
        reopen: :class:`bool`, optional
            Whether to open a fresh tail immediately, by default ``True``.

        """
        live: list[TranscriptBlock] = self._live
        # Only finished blocks are sealed above; unfinished ones move below the interjection.
        cut: int = self._sealable(live, limit=len(live))
        carried: list[TranscriptBlock] = live[cut:]

        # A tail with nothing to seal is deleted — a dead spinner in the scrollback is a lie.
        await self._seal(final=self._compose(_flatten(live[:cut])) if cut else None, discard=not cut)
        self._offset += cut
        self._live = carried

        for content in contents:
            with contextlib.suppress(discord.HTTPException):
                await self._send(content)

        if reopen is True:
            await self._open()
            # Redraw immediately so carried blocks are never off screen.
            if self._panel is not None:
                if self._prefix is not None:
                    self._panel.add_line(self._prefix)
                for line in _flatten(carried):
                    self._panel.add_line(line)

    def compose_final(self, *, footer: str, blocks: Optional[Sequence[TranscriptBlock]] = None) -> str:
        """Returns the live tail's lines with ``footer`` beneath them for a closing :meth:`stop`.

        Parameters
        ----------
        footer: :class:`str`
            The closing line beneath the transcript.
        blocks: :class:`Optional[Sequence[TranscriptBlock]]`, optional
            The full transcript, by default ``None`` — uses whatever was last rendered.

        Returns
        -------
        :class:`str`
            Content for :meth:`stop`, or an empty string when there was nothing to say.

        """
        live: list[str] = _flatten(list(blocks[self._offset :]) if blocks is not None else self._live)
        if live:
            return self._compose([*live, "", footer])
        # A bare footer still needs the header spacing so it sits where the live frames sat.
        return self._compose([footer]) if footer else ""

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
            # Header posted immediately so the tail does not jump up a line on the first render.
            message: discord.Message = await self._send(f"{header}\n\n{self._label}…" if header else f"{self._label}…")
        except discord.HTTPException:
            LOGGER.warning("<%s.%s> | Could not open a status tail.", __class__.__name__, "_open")
            return

        self._opened += 1
        # Interval settings are forwarded explicitly; generic `**dict[str, float]` unpacking
        # would collide with non-float params like `footer` in pyright's overload resolution.
        panel: KumaAnimation = KumaAnimation(
            message,
            label=self._label,
            style=self._style,
            header=header,
            status_last=True,
            max_length=self._max_length,
            min_interval=self._options.get("min_interval", DEFAULT_MIN_INTERVAL),
            idle_interval=self._options.get("idle_interval", DEFAULT_IDLE_INTERVAL),
            max_interval=self._options.get("max_interval", DEFAULT_MAX_INTERVAL),
            decay_after=self._options.get("decay_after", DEFAULT_DECAY_AFTER),
        )
        self._panel = panel
        await panel.start()

    async def _seal(self, *, final: Optional[str] = None, discard: bool = False) -> None:
        """Freezes the live tail and forgets it.

        Parameters
        ----------
        final: :class:`Optional[str]`, optional
            Content to write in place of the last frame, by default ``None``.
        discard: :class:`bool`, optional
            Whether to delete the message outright, by default ``False``.

        """
        panel: Optional[KumaAnimation] = self._panel
        self._panel = None
        if panel is None:
            return

        # Stop first — a running loop would redraw a frame over the frozen text.
        await panel.stop(final=final)
        if discard is True and isinstance(panel.target, discord.Message):
            with contextlib.suppress(discord.HTTPException):
                await panel.target.delete()

    def _compose(self, lines: Sequence[str]) -> str:
        """Returns the static content for a sealed tail, header included.

        Oversize content loses its oldest lines so the footer always survives.

        """
        header: Optional[str] = self._header if self._opened <= 1 else self._continuation_header
        prefix: str = f"{header}\n\n" if header else ""
        kept: list[str] = list(lines)
        trimmed: bool = False
        # Two characters of headroom for the "…" standing in for whatever is dropped.
        budget: int = self._max_length - len(prefix) - 2

        while kept and sum(len(line) + 1 for line in kept) > budget:
            kept.pop(0)
            trimmed = True
        if trimmed is True:
            kept.insert(0, "…")

        body: str = "\n".join(kept)
        return f"{prefix}{body}"[: self._max_length]

    @staticmethod
    def _sealable(blocks: Sequence[TranscriptBlock], *, limit: int) -> int:
        """Returns how many leading blocks may be frozen — the final ones, up to ``limit``."""
        count: int = 0
        for block in blocks[: max(limit, 0)]:
            if block.final is False:
                break
            count += 1
        return count

    def _full(self, lines: Sequence[str]) -> bool:
        """Returns whether ``lines`` have outgrown one tail, leaving room for the status line."""
        if len(lines) > self._max_lines:
            return True
        header_length: int = len(self._header or self._continuation_header or "") + 2
        # The 96 covers the status line and the blank line above it, with slack for a long label.
        return header_length + sum(len(line) + 1 for line in lines) + 96 > self._max_length
