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
import logging
from typing import TYPE_CHECKING, Any, NotRequired, Optional, Self, TypedDict, Union, Unpack

import discord

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ._types import ButtonParams
    from .cog import KumaCog
    from .embeds import KumaEmbed

LOGGER = logging.getLogger(__name__)

__all__ = ("GenericButton", "KumaView")


class ViewParams(TypedDict):
    """:class:`KumaView` base parameters.

    Params
    ------
    cog: :class:`KumaCog`
        The Cog that dispatched the view.
    owner: :class:`Union[discord.Member, discord.User]`
        The Member or User who dispatched the view/interaction.
    embeds: :class:`Optional[Sequence[KumaEmbed]]`
        The Embeds associated with the view, if applicable.
    recent_interaction: :class:`NotRequired[Optional[discord.Interaction]]`
        The most recent :class:`discord.Interaction` that sent content.
    components: :class:`NotRequired[list[discord.ui.Item]]`
        Any Items to pre-append to the View and display during ``__init__``.
    dispatched_by: :class:`Optional[Union[KumaView, discord.ui.Button[KumaView]]]`
        The Object that dispatched the View.
    timeout: :class:`NotRequired[Optional[float]]`
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
    cog: :class:`KumaCog`
        The Cog that dispatched the view.
    owner: :class:`Union[discord.Member, discord.User]`
        The Member or User who dispatched the view/interaction.
    recent_interaction: :class:`NotRequired[Optional[discord.Interaction]]`
        The most recent :class:`discord.Interaction` that sent content.
    components: :class:`NotRequired[list[discord.ui.Item]]`
        Any Items to pre-append to the View and display during ``__init__``.
    embeds: :class:`NotRequired[Optional[Sequence[KumaEmbed]]]`
        The Embeds associated with the view, if applicable.
    dispatched_by: :class:`NotRequired[Optional[Union[KumaView, discord.ui.Button[KumaView]]]]`
        The Object that dispatched the View.
    timeout: :class:`NotRequired[Optional[float]]`
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


class KumaView[V: KumaCog](discord.ui.View):
    """Base :class:`discord.ui.View` for Kuma Kuma Bear.

    Already has "Reset", "Previous" and "Next" buttons built in.

    .. warning::
        Overwrite :meth:`reset_view` if you want to implement different reset functionality;
        otherwise it will clear all items and re-add everything in :attr:`components`.

    Attributes
    ----------
    owner: :class:`Union[discord.Member, discord.User]`
        The Discord User or Member who started the interaction.
    cog: :class:`KumaCog`
        The parent Cog.
    recent_interaction: :class:`Optional[discord.Interaction]`
        The most recent interaction that sent content, by default ``None``.
    components: :class:`list[discord.ui.Item[Any]]`
        Items that will be re-added to the view when :meth:`reset_view` is called.
    dispatched_by: :class:`Optional[Union[KumaView, discord.ui.Button[KumaView]]]`
        What spawned this view, if applicable.
    embeds: :class:`Optional[Sequence[KumaEmbed]]`
        The embeds attached to the view, if applicable.
    indx: :class:`int`
        Index key into :attr:`embeds`, by default ``0``.
    ts_string: :class:`str`
        UTC-aware Discord timestamp string generated at construction time.

    """

    owner: Union[discord.Member, discord.User]
    "The Discord User or Member who started the interaction."
    cog: V
    "The parent Cog."
    recent_interaction: Optional[discord.Interaction]
    "The most recent interaction that sent content."
    components: list[discord.ui.Item[Any]]
    "Items to re-add when reset_view() is called."
    dispatched_by: Optional[Union[KumaView, discord.ui.Button[KumaView]]]
    "What spawned this view."
    _embeds: Optional[Sequence[KumaEmbed]]
    _indx: int
    _timeout: Optional[float]
    ts_string: str
    "UTC-aware Discord timestamp string."

    @property
    def indx(self) -> int:
        """Index into :attr:`embeds`, clamped to the last valid index.

        .. note::
            Clamps to ``len(embeds) - 1``, not ``len(embeds)`` — the latter is one past the end and
            would raise :class:`IndexError` on the very lookup this property exists to make safe.
        """
        if self.embeds is not None and self._indx > len(self.embeds) - 1:
            return len(self.embeds) - 1
        return self._indx

    @indx.setter
    def indx(self, value: int = 0) -> None:
        self._indx = value

    @property
    def embeds(self) -> Optional[Sequence[KumaEmbed]]:
        """The embeds attached to the view, if applicable."""
        return self._embeds

    @embeds.setter
    def embeds(self, value: Optional[Sequence[KumaEmbed]] = None) -> None:
        self._embeds = value

    def __init__(
        self,
        *,
        owner: Union[discord.Member, discord.User],
        cog: V,
        embeds: Optional[Sequence[KumaEmbed]] = None,
        components: Optional[list[discord.ui.Item[Any]]] = None,
        recent_interaction: Optional[discord.Interaction] = None,
        dispatched_by: Optional[Union[KumaView, discord.ui.Button[KumaView]]] = None,
        timeout: Optional[float] = 180,
    ) -> None:
        """Create a :class:`KumaView` instance.

        Parameters
        ----------
        owner: :class:`Union[discord.Member, discord.User]`
            The Discord User or Member who started the interaction.
        cog: :class:`KumaCog`
            The parent Cog. Used to generate :attr:`ts_string`.
        embeds: :class:`Optional[Sequence[KumaEmbed]]`, optional
            Embeds to page through. Previous/Next buttons are removed when ``len <= 1``.
        components: :class:`Optional[list[discord.ui.Item[Any]]]`, optional
            Additional items to add immediately and restore on :meth:`reset_view`.
        recent_interaction: :class:`Optional[discord.Interaction]`, optional
            The most recent interaction, by default ``None``.
        dispatched_by: :class:`Optional[Union[Self, discord.ui.Button[Self]]]`, optional
            What spawned this view, by default ``None``.
        timeout: :class:`Optional[float]`, optional
            Seconds before the view stops accepting input, by default ``180``.

        """
        self._indx = 0
        self._embeds = embeds
        self.owner = owner
        self.cog = cog
        self.dispatched_by = dispatched_by
        self.recent_interaction = recent_interaction
        self._timeout = timeout

        now = datetime.datetime.now(tz=datetime.UTC)
        self.ts_string = cog.to_discord_timestamp(now) if cog is not None else f"<t:{int(now.timestamp())}:F>"

        super().__init__(timeout=timeout)
        self.components = []

        if components is not None and len(components) > 0:
            for entry in components:
                self.add_item(item=entry)

        self.components.extend([self.previous_callback, self.next_callback, self.reset_callback])

        if self.embeds is not None and len(self.embeds) <= 1:
            self.remove_item(item=self.previous_callback)
            self.remove_item(item=self.next_callback)

    def add_item(self, item: discord.ui.Item[Any]) -> Self:
        """Adds the item to our `self.components` and calls `super().add_item(item)`."""
        if item not in self.components:
            self.components.append(item)
        return super().add_item(item=item)

    def remove_item(self, item: discord.ui.Item[Any]) -> Self:
        """Removes the item from our `self.components` and calls `super().remove_item(item)`."""
        if item in self.components:
            self.components.remove(item)
        return super().remove_item(item=item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        LOGGER.debug("<%s.%s>", __class__.__name__, "interaction_check")
        if interaction.user != self.owner:
            await interaction.response.send_message(
                "This interaction isn't for you!",
                ephemeral=True,
            )
            return False
        return True

    def reset_view(self) -> Self:
        """Clear items, re-add :attr:`components`, and reset :attr:`indx` to ``0``.

        .. note::
            Override this method per-view to restore a specific button/select layout.

        Returns
        -------
        :class:`Self`
            Returns ``Self`` for fluent chaining.

        """
        LOGGER.warning("<%s.%s> | Resetting View... | Obj: %s", __class__.__name__, "reset_view", self)
        self.clear_items()
        self.indx = 0
        self.recent_interaction = None

        # Reset navigation button states to their initial defaults.
        self.previous_callback.disabled = True
        self.next_callback.disabled = not (self.embeds is not None and len(self.embeds) > 1)
        self.reset_callback.disabled = True

        if self.components is None:
            return self

        for item in self.components:
            if len(self.children) < 25:
                self.add_item(item=item)
            else:
                LOGGER.warning(
                    "<%s.%s> | View has reached max item limit of 25, cannot add more items. | Obj: %s",
                    __class__.__name__,
                    "reset_view",
                    self,
                )
                break

        return self

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, disabled=True, row=4)
    async def reset_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:
        LOGGER.debug("<%s.%s>", __class__.__name__, "reset_callback")
        item.disabled = True
        view = self.reset_view()
        view.recent_interaction = interaction
        if view.embeds is not None:
            embed = view.embeds[0]
            await interaction.response.edit_message(view=view, embed=embed, attachments=embed.attachments)
        else:
            await interaction.response.edit_message(view=view)

    def page_embed(self, embeds: Sequence[KumaEmbed]) -> KumaEmbed:
        """Returns the embed for the current page, adding a page-number footer only if it has none.

        .. warning::
            This deliberately does **not** overwrite an existing footer. Doing so destroys anything the
            caller put there — `claude.py` writes the run cost into the final page's footer and
            `ollama.py` the token count — and since the embed is mutated in place the original text is
            lost for good, not just for the current render. Callers that want page numbers alongside
            their own text should build both into the footer themselves.

        Parameters
        ----------
        embeds: :class:`Sequence[KumaEmbed]`
            The view's embeds; passed in so the caller's `None` check narrows the type.

        Returns
        -------
        :class:`KumaEmbed`
            The embed to display for :attr:`indx`.

        """
        embed: KumaEmbed = embeds[self.indx]
        if embed.footer.text is None:
            embed.set_footer(text=f"{self.indx + 1} out of {len(embeds)} | Kuma Kuma Bear")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.primary, disabled=True, row=1)
    async def previous_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:
        LOGGER.debug("<%s.%s>", __class__.__name__, "previous_callback")
        if self.embeds is None:
            self.reset_view()
            await interaction.response.edit_message(view=self)
            return

        self.recent_interaction = interaction
        # Guard against underflow: on the first page there is nothing to go back to, and decrementing
        # would leave indx at -1, which silently renders `embeds[-1]` — the *last* page.
        if self.indx > 0:
            self.indx -= 1

        if self.indx < len(self.embeds) - 1:
            self.next_callback.disabled = False
        if self.indx == 0:
            item.disabled = True

        embed: KumaEmbed = self.page_embed(self.embeds)
        await interaction.response.edit_message(embed=embed, view=self, attachments=embed.attachments)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.green, disabled=False, row=1)
    async def next_callback(self, interaction: discord.Interaction, item: discord.ui.Button[Self]) -> None:
        LOGGER.debug("<%s.%s>", __class__.__name__, "next_callback")
        if self.embeds is None:
            self.reset_view()
            await interaction.response.edit_message(view=self)
            return

        self.indx += 1
        self.recent_interaction = interaction

        if self.indx <= len(self.embeds) - 1:
            self.reset_callback.disabled = False
            self.previous_callback.disabled = False
            if self.indx == len(self.embeds) - 1:
                item.disabled = True
            embed: KumaEmbed = self.page_embed(self.embeds)
            await interaction.response.edit_message(embed=embed, view=self, attachments=embed.attachments)
            return
        self.reset_view()
        await interaction.response.edit_message(view=self)


class GenericButton(discord.ui.Button):
    view: KumaView

    def __init__(self, **kwargs: Unpack[ButtonParams]) -> None:
        if kwargs.get("style") is None:
            kwargs["style"] = discord.ButtonStyle.primary
        if kwargs.get("label") is None:
            kwargs["label"] = "Generic"

        label = kwargs.get("label")
        if kwargs.get("custom_id") is None and label is not None:
            kwargs["custom_id"] = label.lower().replace(" ", "_")

        super().__init__(**kwargs)

    async def callback(self, interaction: discord.Interaction) -> None:
        LOGGER.info("<%s.%s>", __class__.__name__, "callback")
        if interaction.user == self.view.owner:
            await interaction.response.defer()
        return
