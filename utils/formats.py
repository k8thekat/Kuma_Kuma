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

import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ("format_dt", "human_join", "plural")


class plural:  # noqa: N801 — lowercase is intentional; used inside f-string format specs
    """Formats a number with its noun, pluralising automatically.

    Usage inside an f-string format spec::

        f"{plural(count):item}"        # "1 item" / "3 items"
        f"{plural(count):ox|oxen}"     # "1 ox"   / "3 oxen"

    """

    def __init__(self, value: int) -> None:
        self.value: int = value

    def __format__(self, format_spec: str) -> str:
        value: int = self.value
        singular, _sep, plural_form = format_spec.partition("|")
        plural_form = plural_form or f"{singular}s"
        if abs(value) != 1:
            return f"{value} {plural_form}"
        return f"{value} {singular}"


def human_join(sequence: Sequence[str], delimiter: str = ", ", final: str = "or") -> str:
    """Join a sequence of strings with a final conjunction.

    Parameters
    ----------
    sequence: :class:`Sequence[str]`
        The strings to join.
    delimiter: :class:`str`, optional
        The delimiter between items, by default ``", "``.
    final: :class:`str`, optional
        The conjunction before the last item, by default ``"or"``.

    Returns
    -------
    :class:`str`
        The joined string.

    """
    size: int = len(sequence)
    if size == 0:
        return ""

    if size == 1:
        return sequence[0]

    if size == 2:
        return f"{sequence[0]} {final} {sequence[1]}"

    return delimiter.join(sequence[:-1]) + f" {final} {sequence[-1]}"


def format_dt(date_time: datetime.datetime, style: Optional[str] = None) -> str:
    """Format a datetime as a Discord timestamp markdown string.

    Parameters
    ----------
    date_time: :class:`datetime.datetime`
        The datetime to format.
    style: :class:`Optional[str]`, optional
        The Discord timestamp style character, by default None (default style).

    Returns
    -------
    :class:`str`
        The Discord timestamp markdown string.

    """
    if date_time.tzinfo is None:
        date_time = date_time.replace(tzinfo=datetime.UTC)

    if style is None:
        return f"<t:{int(date_time.timestamp())}>"
    return f"<t:{int(date_time.timestamp())}:{style}>"
