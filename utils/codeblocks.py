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

import copy
import logging
import os
import re
import sys
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, ClassVar, Optional, Union

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = (
    "LOG_LEVEL_NAMES",
    "PALETTE_FORMATS",
    "AnsiBack",
    "AnsiFore",
    "AnsiStyle",
    "CodeFormat",
    "KumaLogFormatter",
    "ansi",
    "ansi_block",
    "code_block",
    "colourise_log",
    "entry_level",
    "parse_levels",
    "split_log_entries",
    "strip_ansi",
)


# The literal ESC byte. Discord only colours an ``ansi`` block when the real
# 0x1B character is in the message payload. Writing the escape sequence as text
# (a literal backslash, 'u', '001b') does not count — it arrives as prose and
# renders as visible garbage. Always build these with ansi().
ESC = "\x1b"

# Zero-width space wedged between backticks, to defuse a fence inside a block.
_FENCE_GUARD = "`\u200b``"

_ANSI_PATTERN: re.Pattern[str] = re.compile(r"\x1b\[[0-9;]*m")


class CodeFormat(StrEnum):
    """Fence tokens Discord's client-side highlight.js understands.

    Discord runs highlight.js over multi-line code blocks, so any language name
    or alias it ships is a valid fence token. Several of these are listed not
    because we are writing that language but because their grammar happens to
    colour plain text in a useful way — see :data:`PALETTE_FORMATS`.

    .. note::
        The token must follow the backticks with **no space**: ```` ```ps ````
        highlights, ```` ``` ps ```` does not. Always build fences with
        :func:`code_block` rather than by hand.

    .. note::
        Upstream language list:
        https://github.com/highlightjs/highlight.js/blob/main/SUPPORTED_LANGUAGES.md

    """

    # — Discord extension, not a highlight.js language ---------------------
    ANSI = "ansi"

    # — Structural: colour follows the shape of the text -------------------
    DIFF = "diff"
    FIX = "fix"
    INI = "ini"
    YAML = "yaml"
    JSON = "json"
    HTTP = "http"
    CSS = "css"
    MARKDOWN = "md"
    BASH = "bash"

    # — Palette: picked for their colours, not their semantics -------------
    EXCEL = "excel"
    GCODE = "nc"
    OCAML = "ml"
    NIM = "nim"
    POWERSHELL = "ps"
    PROLOG = "prolog"
    RUBY = "thor"

    @property
    def note(self) -> str:
        """A one line description of what this token actually colours."""
        return _FORMAT_NOTES[self]


_FORMAT_NOTES: dict[CodeFormat, str] = {
    CodeFormat.ANSI: "Not a highlight.js language -- a Discord extension honouring real SGR escapes. The only arbitrary colour we get.",
    CodeFormat.DIFF: "Leading '+' green, '-' red, '@@ .. @@' teal. Free pass/fail colouring for status lines.",
    CodeFormat.FIX: "Colours the entire block gold. Banners and warnings.",
    CodeFormat.INI: "'[section]' headers, 'key = value' pairs, ';' comments grey. Config-panel look.",
    CodeFormat.YAML: "'key:' coloured, values plain, '#' comments grey. Two-tone key/value tables.",
    CodeFormat.JSON: "Keys, strings and numbers each tinted. Only clean on genuinely valid JSON.",
    CodeFormat.HTTP: "Method, URL and header names each get their own tint. Request/response dumps.",
    CodeFormat.CSS: "Selectors one colour, properties another. Two-tone without needing valid CSS.",
    CodeFormat.MARKDOWN: "'#' headings, '>' quotes and list bullets coloured -- markdown shown, not rendered.",
    CodeFormat.BASH: "Quoted strings and '$VAR' pop against plain text. Command transcripts.",
    CodeFormat.EXCEL: "Excel formulas. Function-like words tint; good for a warm, uniform block.",
    CodeFormat.GCODE: "Alias for G-Code. Bare words tint heavily -- reads as a machine trace.",
    CodeFormat.OCAML: "Alias for OCaml/SML. Capitalised words and quoted strings colour.",
    CodeFormat.NIM: "Quoted strings and numbers pop; a cooler palette than 'ps'.",
    CodeFormat.POWERSHELL: "Alias for PowerShell. Our log default -- '-flags' and quoted paths colour well.",
    CodeFormat.PROLOG: "'word(arg)' forms and quoted strings tint. Warm orange bias.",
    CodeFormat.RUBY: "Alias for Ruby. ':symbols' and quoted strings colour strongly.",
}

#: The subset used purely as colour palettes for log output. Rotating through
#: these is what makes consecutive log dumps visually distinguishable.
PALETTE_FORMATS: tuple[CodeFormat, ...] = (
    CodeFormat.EXCEL,
    CodeFormat.GCODE,
    CodeFormat.OCAML,
    CodeFormat.NIM,
    CodeFormat.POWERSHELL,
    CodeFormat.PROLOG,
    CodeFormat.RUBY,
)


class AnsiStyle(IntEnum):
    """SGR style codes Discord honours. Everything else is dropped silently."""

    RESET = 0
    BOLD = 1
    UNDERLINE = 4


class AnsiFore(IntEnum):
    """Foreground colours. Discord supports these eight and no others."""

    GRAY = 30
    RED = 31
    GREEN = 32
    YELLOW = 33
    BLUE = 34
    PINK = 35
    CYAN = 36
    WHITE = 37


class AnsiBack(IntEnum):
    """Background colours. Names describe how Discord actually renders them."""

    DARK_BLUE = 40
    ORANGE = 41
    MARBLE_BLUE = 42
    TURQUOISE = 43
    GRAY = 44
    INDIGO = 45
    LIGHT_GRAY = 46
    WHITE = 47


def ansi(
    text: str,
    *,
    fore: Optional[AnsiFore] = None,
    back: Optional[AnsiBack] = None,
    style: Optional[AnsiStyle] = None,
) -> str:
    """Wrap ``text`` in SGR escapes, resetting afterwards.

    Only meaningful inside a :attr:`CodeFormat.ANSI` block; anywhere else the
    escapes are invisible at best and literal noise at worst.

    Parameters
    ----------
    text: :class:`str`
        The text to colour.
    fore: :class:`Optional[AnsiFore]`, optional
        Foreground colour, by default None.
    back: :class:`Optional[AnsiBack]`, optional
        Background colour, by default None.
    style: :class:`Optional[AnsiStyle]`, optional
        Bold or underline, by default None.

    Returns
    -------
    :class:`str`
        ``text`` with real escape bytes around it, or ``text`` unchanged if no
        attribute was requested.

    """
    codes: list[int] = [int(code) for code in (style, back, fore) if code is not None]
    if not codes:
        return text
    return f"{ESC}[{';'.join(str(code) for code in codes)}m{text}{ESC}[{AnsiStyle.RESET.value}m"


def code_block(content: str, fmt: Union[CodeFormat, str] = CodeFormat.POWERSHELL) -> str:
    """Fence ``content`` as a Discord multi-line code block.

    Any backticks in ``content`` are neutralised with a zero-width space so a
    stray fence in log output cannot break out of the block.

    Parameters
    ----------
    content: :class:`str`
        The block body.
    fmt: :class:`Union[CodeFormat, str]`, optional
        The fence token, by default :attr:`CodeFormat.POWERSHELL`.

    Returns
    -------
    :class:`str`
        The fenced block, ready to send.

    """
    token: str = str(fmt).strip()
    return f"```{token}\n{content.replace('```', _FENCE_GUARD)}\n```"


def ansi_block(lines: Iterable[str]) -> str:
    """Fence pre-coloured ``lines`` as an ``ansi`` block.

    Parameters
    ----------
    lines: :class:`Iterable[str]`
        Lines, typically already passed through :func:`ansi`.

    Returns
    -------
    :class:`str`
        The fenced block.

    """
    return code_block("\n".join(lines), CodeFormat.ANSI)


def strip_ansi(text: str) -> str:
    """Remove SGR escapes from ``text``.

    Log lines that already carry colour from a terminal handler will show their
    escapes verbatim in any block that is not ``ansi`` — run them through this
    before fencing them with a palette format.

    Parameters
    ----------
    text: :class:`str`
        Text that may contain SGR escapes.

    Returns
    -------
    :class:`str`
        ``text`` with every escape sequence removed.

    """
    return _ANSI_PATTERN.sub("", text)


class KumaLogFormatter(logging.Formatter):
    """A :class:`logging.Formatter` that colours records with Discord-safe SGR escapes.

    Deliberately restricted to the eight colours in :class:`AnsiFore`, even
    though a terminal offers far more. That restraint is the feature: the exact
    bytes this writes to the console are also legal inside a Discord ``ansi``
    block, so a log excerpt can be shipped to a channel with its colour intact
    instead of being re-highlighted on the way out.

    .. warning::
        Attach this to the **stream** handler only. Escapes in the rotating log
        file would break ``grep`` and leak into anything that reads the file
        back. Pass ``colour=False`` for a plain formatter with identical layout.

    """

    #: level -> (foreground, style, background). Anything unmapped stays plain.
    LEVEL_STYLES: ClassVar[dict[int, tuple[AnsiFore, Optional[AnsiStyle], Optional[AnsiBack]]]] = {
        logging.DEBUG: (AnsiFore.GRAY, None, None),
        logging.INFO: (AnsiFore.GREEN, AnsiStyle.BOLD, None),
        logging.WARNING: (AnsiFore.YELLOW, AnsiStyle.BOLD, None),
        logging.ERROR: (AnsiFore.RED, AnsiStyle.BOLD, None),
        logging.CRITICAL: (AnsiFore.WHITE, AnsiStyle.BOLD, AnsiBack.ORANGE),
    }

    def __init__(
        self,
        fmt: str = "%(asctime)s [%(threadName)s] [%(levelname)s] %(name)s  %(message)s",
        datefmt: str = "%m/%d/%Y %I:%M:%S %p",
        *,
        colour: Optional[bool] = None,
    ) -> None:
        """Build the formatter.

        Parameters
        ----------
        fmt: :class:`str`, optional
            A ``%``-style format string, by default the Kuma Kuma log layout.
        datefmt: :class:`str`, optional
            Passed through to :meth:`logging.Formatter.formatTime`.
        colour: :class:`Optional[bool]`, optional
            Force colour on or off. ``None`` (the default) defers to
            :meth:`_colour_supported`, so piped and redirected output stays clean.

        """
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.colour: bool = self._colour_supported() if colour is None else colour

    @staticmethod
    def _colour_supported() -> bool:
        """Decide whether the console on the other end can take SGR escapes.

        ``isatty()`` alone under-reports; a debugger hands us a pipe even when the
        console reading it renders colour perfectly well. ``NO_COLOR`` and
        ``FORCE_COLOR`` are the out-of-band way to say which it is.

        Returns
        -------
        :class:`bool`
            True when escapes should be emitted.

        """
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("FORCE_COLOR"):
            return True
        return sys.stdout.isatty()

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:  # noqa: N802 - stdlib signature
        """Render the timestamp, dimmed to gray so it recedes behind the message."""
        formatted: str = super().formatTime(record, datefmt)
        return ansi(formatted, fore=AnsiFore.GRAY) if self.colour else formatted

    def formatException(self, ei) -> str:  # noqa: ANN001, N802 - stdlib signature
        """Render a traceback in red so it is unmissable in a wall of INFO."""
        formatted: str = super().formatException(ei)
        return ansi(formatted, fore=AnsiFore.RED) if self.colour else formatted

    def format(self, record: logging.LogRecord) -> str:
        """Colour a copy of ``record`` and render it.

        The record is copied first: a :class:`logging.LogRecord` is shared by
        every handler on the logger, so colouring its attributes in place would
        push escape bytes into the rotating log file as well.
        """
        if not self.colour:
            return super().format(record)

        clone: logging.LogRecord = copy.copy(record)
        fore, style, back = self.LEVEL_STYLES.get(record.levelno, (AnsiFore.WHITE, None, None))
        clone.levelname = ansi(f"{record.levelname:<8}", fore=fore, style=style, back=back)
        clone.name = ansi(record.name, fore=AnsiFore.CYAN)
        clone.threadName = ansi(record.threadName or "", fore=AnsiFore.GRAY)
        return super().format(clone)


# Matches the head of one Kuma Kuma log record, as laid out by KumaLogFormatter.
# Anything that does not match is a continuation of the record above it — a
# traceback body, or a message that contained a newline.
_LOG_LINE: re.Pattern[str] = re.compile(
    r"^(?P<ts>\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2} [AP]M)"
    r" \[(?P<thread>[^\]]*)\]"
    r" \[(?P<level>[A-Z]+)\s*\]"
    r"(?P<gap>\s+)"
    r"(?P<msg>.*)$",
)


def split_log_entries(text: str) -> list[str]:
    """Group raw log text into records, keeping multi-line entries intact.

    A traceback is part of the record that raised it. Slicing a log by *lines*
    therefore decapitates exceptions — you get the tail of a traceback with no
    indication of what threw it. Slicing by the entries this returns does not.

    Parameters
    ----------
    text: :class:`str`
        The contents of a log file.

    Returns
    -------
    :class:`list[str]`
        One string per record, each possibly spanning several lines. Any
        preamble before the first recognisable record is returned as entry zero.

    """
    entries: list[str] = []
    current: list[str] = []

    for line in text.splitlines():
        if _LOG_LINE.match(line) and current:
            entries.append("\n".join(current))
            current = []
        current.append(line)

    if current:
        entries.append("\n".join(current))
    return entries


def colourise_log(text: str) -> str:
    """Re-apply :class:`KumaLogFormatter` colours to plain log text.

    The rotating log file is written without escapes on purpose, so anything
    read back off disk is uncoloured. This puts the colour back on the way out,
    letting a log excerpt be fenced as :attr:`CodeFormat.ANSI` and rendered in
    Discord exactly as it looked on the console.

    Traceback bodies are tinted red, but only between a ``Traceback (most recent
    call last):`` line and the next record — an ordinary multi-line message is
    left alone rather than being wrongly painted as an error.

    Parameters
    ----------
    text: :class:`str`
        Plain log text.

    Returns
    -------
    :class:`str`
        The same text with SGR escapes applied.

    """
    by_name: dict[str, tuple[AnsiFore, Optional[AnsiStyle], Optional[AnsiBack]]] = {
        logging.getLevelName(level): style for level, style in KumaLogFormatter.LEVEL_STYLES.items()
    }

    out: list[str] = []
    in_traceback = False

    for line in text.splitlines():
        match: Optional[re.Match[str]] = _LOG_LINE.match(line)
        if match is None:
            if line.startswith("Traceback (most recent call last)"):
                in_traceback = True
            out.append(ansi(line, fore=AnsiFore.RED) if in_traceback and line.strip() else line)
            continue

        in_traceback = False
        fore, style, back = by_name.get(match["level"], (AnsiFore.WHITE, None, None))
        out.append(
            ansi(match["ts"], fore=AnsiFore.GRAY)
            + f" [{ansi(match['thread'], fore=AnsiFore.GRAY)}]"
            + f" [{ansi(match['level'], fore=fore, style=style, back=back)}]"
            + match["gap"]
            + match["msg"],
        )

    return "\n".join(out)


#: Level names ``parse_log`` will filter on, most severe last.
LOG_LEVEL_NAMES: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def entry_level(entry: str) -> Optional[str]:
    """Return the level name of a log record, or None if it has no recognisable head.

    Parameters
    ----------
    entry: :class:`str`
        One record, as produced by :func:`split_log_entries`.

    Returns
    -------
    :class:`Optional[str]`
        e.g. ``"WARNING"``, or None for a fragment with no record head.

    """
    match: Optional[re.Match[str]] = _LOG_LINE.match(entry)
    return match["level"] if match else None


def parse_levels(levels: Optional[Union[str, Iterable[str]]]) -> Optional[frozenset[str]]:
    """Normalise a user-supplied level filter.

    Accepts the shapes a Discord argument actually arrives in — ``"error"``,
    ``"ERROR,WARNING"``, ``"error warning"`` — as well as an iterable.

    Parameters
    ----------
    levels: :class:`Optional[Union[str, Iterable[str]]]`
        The filter. None or empty means "no filtering".

    Returns
    -------
    :class:`Optional[frozenset[str]]`
        Upper-cased level names, or None when nothing was requested.

    Raises
    ------
    ValueError
        A name that is not in :data:`LOG_LEVEL_NAMES` was given.

    """
    if levels is None:
        return None

    tokens: list[str] = re.split(r"[,\s]+", levels) if isinstance(levels, str) else [str(item) for item in levels]
    wanted: set[str] = {token.upper() for token in tokens if token}
    if not wanted:
        return None

    if unknown := sorted(wanted - set(LOG_LEVEL_NAMES)):
        msg = f"Unknown log level(s): {', '.join(unknown)}. Valid: {', '.join(LOG_LEVEL_NAMES)}"
        raise ValueError(msg)
    return frozenset(wanted)
