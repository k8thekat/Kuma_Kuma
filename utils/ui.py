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
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, Self, TypeVar, Union, Unpack

import discord

if TYPE_CHECKING:
    from collections.abc import Sequence

    from discord.ui.item import Item

    from kuma_kuma import Kuma_Kuma

    from ._types import ButtonParams, ContainerParams
    from .cog import KumaCog
    from .embeds import KumaEmbed


LOGGER: logging.Logger = logging.getLogger(__name__)

__all__ = (
    "GenericButton",
    "KumaContainer",
    "KumaLayoutView",
    "KumaView",
    "PanelAccess",
)

V = TypeVar("V", bound="KumaLayoutView", covariant=True)  # noqa: PLC0105


class PanelAccess(Enum):
    """Who may use a panel's buttons, chosen per command invocation.

    :meth:`owner` maps each level onto a view owner: the invoker for :attr:`only_me`, the bot for
    :attr:`public`, or ``None`` for :attr:`preview` - a display-only panel no one may interact with.

    """

    public = 0
    preview = 1
    only_me = 2

    @property
    def name(self) -> str:
        """The Discord-facing choice label; ``only_me`` renders as ``Only Me``."""
        return super().name.replace("_", " ").title()

    def owner(
        self,
        *,
        user: Union[discord.Member, discord.User],
        bot: Kuma_Kuma,
    ) -> Optional[Union[discord.Member, discord.User, discord.ClientUser]]:
        """Return the view owner for this access level.

        Parameters
        ----------
        user : :class:`Union[discord.Member, discord.User]`
            The command invoker; the owner for :attr:`only_me`.
        bot : :class:`Kuma_Kuma`
            The bot, whose user becomes the owner for :attr:`public`.

        Returns
        -------
        :class:`Optional[Union[discord.Member, discord.User, discord.ClientUser]]`
            The invoker, the bot, or ``None`` for :attr:`preview`.

        """
        if self is PanelAccess.public:
            return bot.user
        if self is PanelAccess.only_me:
            return user
        return None

    def owner_id(self, *, user: Union[discord.Member, discord.User], bot: Kuma_Kuma) -> int:
        """Return the owner id for panels that gate on an id; ``0`` for :attr:`preview`, which no one matches."""
        owner: Optional[Union[discord.Member, discord.User, discord.ClientUser]] = self.owner(user=user, bot=bot)
        return owner.id if owner is not None else 0


class KumaLayoutView(discord.ui.LayoutView):
    """Base :class:`discord.ui.LayoutView` for Kuma Kuma Bear.

    Stores the parent cog and the person who owns the panel, gates every interaction to that
    person, and provides layout helpers that every CV2 panel repeats.

    .. warning::
        A Components V2 message cannot carry ``content`` or ``embeds``, so anything a reader needs
        has to be a :class:`discord.ui.TextDisplay` inside the layout.

    Attributes
    ----------
    cog : :class:`Optional[KumaCog]`
        The parent cog.
    owner : :class:`Optional[Union[discord.Member, discord.User, discord.ClientUser]]`
        The Discord user or member the panel belongs to.

    """

    cog: Optional[KumaCog]
    owner: Optional[Union[discord.Member, discord.User, discord.ClientUser]]
    _containers: Sequence[KumaContainer] = []
    _indx: int
    _prev: NavButton
    _next: NavButton
    _nav_row: discord.ui.ActionRow[KumaLayoutView]

    @property
    def container(self) -> KumaContainer:
        """The current container in the :class:`KumaLayoutView` based upon its `indx` property."""
        return self._containers[self._indx]

    @property
    def containers(self) -> Sequence[KumaContainer]:
        """All the container pages to be displayed via page turns."""
        return self._containers

    @property
    def indx(self) -> int:
        """Index into :attr:`containers`; kept within range by the setter."""
        return self._indx

    @indx.setter
    def indx(self, value: int = 0) -> None:
        self._indx = max(0, min(value, self.c_length - 1))

    @property
    def c_length(self) -> int:
        """Number of container pages."""
        return len(self._containers)

    def __init__(
        self,
        *,
        cog: Optional[KumaCog] = None,
        owner: Optional[Union[discord.Member, discord.User, discord.ClientUser]],
        # container: Optional[KumaContainer] = None,
        # containers: Optional[Sequence[KumaContainer]] = None,
        # include_footer: Optional[bool] = True,
        timeout: Optional[float] = 180.0,
    ) -> None:
        """Create a :class:`KumaLayoutView` instance.

        .. note::
            Content is attached after construction via :meth:`add_containers`.

        Parameters
        ----------
        cog : :class:`Optional[KumaCog]`, optional
            The parent cog; `None` for non-interactive views (e.g. DM reports) where
            :meth:`interaction_check` is never reached, by default `None`.
        owner : :class:`Optional[Union[discord.Member, discord.User, discord.ClientUser]]`
            The person allowed to interact with the panel; `None` for preview style views no one can interact with.
        timeout : :class:`Optional[float]`, optional
            Seconds before the view stops accepting input, by default ``180.0``.

        """
        super().__init__(timeout=timeout)

        # Default new view index to 0.
        self.indx = 0
        self.cog = cog
        self.owner = owner

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Reject anyone but the person the panel was opened for."""
        # No cog means the view is non-interactive (e.g. a DM report); reject all input.
        if self.cog is None:
            return False

        # Shortcut bool flip for "preview" style interactions only. (No owner = failed checks)
        if self.owner is None:
            return False

        # Shortcut for bot created views that are not from an "interaction".
        if isinstance(self.owner, discord.ClientUser):
            # Obvious path...
            if self.owner == self.cog.bot.user:
                return True

            # Just in case?
            LOGGER.warning(
                "<%s.%s> | Owner object is not us. | Owner: %s | Bot: %s ",
                __class__.__name__,
                "interaction_check",
                type(self.owner),
                type(self.cog.bot.user),
            )
            return False

        # User generated views.
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                content=f"That panel isn't yours! {self.cog.emoji_table.kuma_bleh}",
                ephemeral=True,
                delete_after=self.cog.bot.message_timeout,
            )
            return False
        return True

    async def page_turn(self, interaction: discord.Interaction, step: int) -> None:
        """Swap the visible container by *step* and refresh the navigation state.

        Parameters
        ----------
        interaction : :class:`discord.Interaction`
            The interaction from the pressed :class:`NavButton`.
        step : :class:`int`
            The offset applied to :attr:`indx`; ``-1`` for the previous page, ``1`` for the next.

        """
        self.remove_item(self.container)
        self.indx += step
        await self._kuma_mount_container(self.container)

        if self.c_length > 1:
            self.remove_item(self._nav_row)
            self.add_item(self._nav_row)

        self._prev.disabled = self.indx == 0
        self._next.disabled = self.indx == self.c_length - 1
        await interaction.response.edit_message(view=self)
        return

    def navigation_row(self) -> discord.ui.ActionRow[KumaLayoutView]:
        """Build the Previous/Next row and store its buttons for :meth:`page_turn` to toggle.

        Returns
        -------
        :class:`discord.ui.ActionRow`
            The row holding the paging buttons.

        """
        self._prev = NavButton(step=-1, label="Previous", emoji="\U00002b05", disabled=self.indx == 0)
        self._next = NavButton(step=1, label="Next", emoji="\U000027a1", disabled=self.indx == self.c_length - 1)
        self._nav_row = discord.ui.ActionRow(self._prev, self._next)
        return self._nav_row

    async def _kuma_mount_container(self, container: KumaContainer) -> None:
        """Attach *container* to the view and run its :meth:`~KumaContainer._kuma_prepare` hook.

        Mounting is attach plus initialize: the container is added to the tree, then its own prepare
        hook builds whatever content needs a live :attr:`~KumaContainer.view`.

        Parameters
        ----------
        container : :class:`KumaContainer`
            The container to attach and prepare.

        """
        self.add_item(container)
        await container._kuma_prepare()  # noqa: SLF001

    async def add_containers(self, containers: KumaContainer | Sequence[KumaContainer], position: int = 0) -> Self:
        """Set the container pages and attach the current one; adds navigation if there is more than one.

        Parameters
        ----------
        containers : :class:`Union[KumaContainer, Sequence[KumaContainer]]`
            A single container or the ordered pages to page through.
        position : :class:`int`, optional
            The page to open on, clamped to the page count, by default ``0``.

        Returns
        -------
        :class:`Self`
            Returns :class:`Self` for fluent chaining.

        """
        self._containers = [containers] if isinstance(containers, KumaContainer) else containers
        self.indx = position
        await self._kuma_mount_container(self.container)

        if len(self._containers) > 1:
            self.add_item(self.navigation_row())
        return self

    # def add_item(self, item: Item[Any], default: bool = False) -> Self:
    #     """Generic overwrite of `add_item` to handle setting our :class:`Self.container` property. Adds an item to the view.

    #     This function returns the class instance to allow for fluent-style
    #     chaining.

    #     Parameters
    #     ----------
    #     item: :class:`Item`
    #         The item to add to the view.
    #     default: :class:`bool`, default `False`
    #         Set's our :class:`Self` container property.

    #     Raises
    #     ------
    #     TypeError
    #         An :class:`Item` was not passed.
    #     ValueError
    #         Maximum number of children has been exceeded, the
    #         row the item is trying to be added to is full or the item
    #         you tried to add is not allowed in this View.

    #     """
    #     if default:
    #         if isinstance(item, KumaContainer):
    #             self._container = item
    #         else:
    #             # This is going to be mainly for early debugging.
    #             LOGGER.warning("<%s.%s> | Add item was called with a object != `discord.ui.Container`.", type(self).__name__, "add_item")

    #     return super().add_item(item)


class KumaContainer(discord.ui.Container[KumaLayoutView]):
    """Base :class:`discord.ui.Container` for Kuma Kuma Bear; a single page inside a :class:`KumaLayoutView`.

    Async or view-dependent content goes in :meth:`_kuma_populate`, which runs once the container is
    attached and :attr:`view` is live. Static content can be added in ``__init__`` as usual.

    .. note::
        Subclasses override :meth:`_kuma_populate` for content that needs the cog or the page position,
        and :meth:`_paginator_footer` to restyle the page footer.

    """

    view: KumaLayoutView

    @property
    def view_pos(self) -> int:
        """The position of this container inside its :class:`KumaLayoutView`'s :attr:`~KumaLayoutView.containers`."""
        return self.view.containers.index(self)

    @property
    def _middle_dot(self) -> str:
        """The cog's middle-dot separator, falling back to the literal glyph for a cog-less view.

        A container in a view built without a cog (e.g. a DM report) still needs a separator; the
        panel's cosmetic dot should never be the thing that raises.
        """
        return self.view.cog.unicode.middle_dot if self.view.cog is not None else "·"

    def __init__(self, *children: Item[V], include_footer: bool = False, **kwargs: Unpack[ContainerParams]) -> None:
        """Create a :class:`KumaContainer`.

        Parameters
        ----------
        *children : :class:`discord.ui.Item`
            Initial child items, forwarded to :class:`discord.ui.Container`.
        include_footer : :class:`bool`, optional
            Add a footer during :meth:`_kuma_prepare` - a page counter while the view paginates, a
            plain credit line otherwise, by default ``False``.
        **kwargs : :class:`Unpack[ContainerParams]`
            Remaining :class:`discord.ui.Container` keyword arguments (``accent_colour``, ``spoiler``, ``id``).

        """
        super().__init__(*children, **kwargs)
        self._include_footer: bool = include_footer
        self._kuma_prepared: bool = False

    async def _kuma_prepare(self) -> None:
        """Mount-time entry point: populate the container once, then add the footer.

        Called by :meth:`KumaLayoutView._kuma_mount_container` after attach, so :attr:`view` is live.
        Owns the run-once guard and the footer; subclasses override :meth:`_kuma_populate` for their
        content rather than this method, so neither the guard nor a ``super()`` call is their concern.

        """
        if self._kuma_prepared:
            return
        self._kuma_prepared = True

        await self._kuma_populate()

        if self._include_footer:
            if self.view.c_length == 1:
                self._footer()
            else:
                self._paginator_footer()

    async def _kuma_populate(self) -> None:
        """Build async or view-dependent content once the container is attached; the base adds nothing.

        Override for content that needs the cog or the page position. The base :meth:`_kuma_prepare`
        runs the run-once guard and adds the footer around this call, so a subclass writes only its
        own items here.

        .. warning::
            This is awaited on the interaction path (:meth:`KumaLayoutView.page_turn`); heavy async
            work here stalls the page turn and can hang the interaction. Call
            ``await interaction.response.defer()`` in the turn first if a container needs it.

        """

    def _footer(self, include_sep: bool = False) -> Self:
        """Add the plain credit footer, optionally preceded by a separator.

        Parameters
        ----------
        include_sep : :class:`bool`, optional
            Add a :class:`discord.ui.Separator` before the footer text, by default `False`.

        Returns
        -------
        :class:`Self`
            Returns :class:`Self` for fluent chaining.

        """
        if include_sep:
            self.add_separator()
        return self.add_item(discord.ui.TextDisplay(content="-# Kuma Kuma Bear"))

    def add_separator(self, /, large: bool = False, visible: bool = True) -> Self:
        """Add a :class:`discord.ui.Separator` and return :class:`Self` for fluent chaining.

        Parameters
        ----------
        large : :class:`bool`, optional
            Use :attr:`discord.SeparatorSpacing.large` instead of the default small spacing,
            by default `False`.
        visible : :class:`bool`, optional
            Show or hide the separator, by default `True`.

        Returns
        -------
        :class:`Self`
            Returns :class:`Self` for fluent chaining.

        """
        spacing: discord.SeparatorSpacing = discord.SeparatorSpacing.large if large else discord.SeparatorSpacing.small
        return self.add_item(discord.ui.Separator(spacing=spacing, visible=visible))

    def _paginator_footer(self) -> Self:
        """Add a footer showing the current page position.

        Returns
        -------
        :class:`Self`
            Returns :class:`Self` for fluent chaining.

        """
        count: str = f"{self.view_pos + 1}/{self.view.c_length}"
        return self.add_item(discord.ui.TextDisplay(content=f"-# Page {count} {self._middle_dot} Kuma Kuma Bear"))


class NavButton(discord.ui.Button[KumaLayoutView]):
    """Steps a :class:`KumaLayoutView` one page in either direction."""

    view: KumaLayoutView

    def __init__(self, *, step: int, **kwargs: Unpack[ButtonParams]) -> None:
        """Create a :class:`NavButton`.

        Parameters
        ----------
        step : :class:`int`
            The offset applied to the view's :attr:`~KumaLayoutView.indx` on press; ``-1`` or ``1``.
        **kwargs : :class:`Unpack[ButtonParams]`
            :class:`discord.ui.Button` keyword arguments; ``style`` defaults to :attr:`discord.ButtonStyle.blurple`.

        """
        if kwargs.get("style") is None:
            kwargs["style"] = discord.ButtonStyle.blurple

        super().__init__(**kwargs)
        self.step: int = step

    async def callback(self, interaction: discord.Interaction) -> None:
        """Turn the panel; the view's :meth:`interaction_check` has already gated the caller."""
        await self.view.page_turn(interaction=interaction, step=self.step)


class KumaView(discord.ui.View):
    """Base :class:`discord.ui.View` for Kuma Kuma Bear.

    Already has "Reset", "Previous" and "Next" buttons built in.

    .. warning::
        Overwrite :meth:`reset_view` if you want to implement different reset functionality;
        otherwise it will clear all items and re-add everything in :attr:`components`.

    Attributes
    ----------
    owner : :class:`Union[discord.Member, discord.User]`
        The Discord User or Member who started the interaction.
    cog : :class:`KumaCog`
        The parent Cog.
    recent_interaction : :class:`Optional[discord.Interaction]`
        The most recent interaction that sent content, by default ``None``.
    components : :class:`list[discord.ui.Item[Any]]`
        Items that will be re-added to the view when :meth:`reset_view` is called.
    dispatched_by : :class:`Optional[Union[KumaView, discord.ui.Button[KumaView]]]`
        What spawned this view, if applicable.
    embeds : :class:`Optional[Sequence[KumaEmbed]]`
        The embeds attached to the view, if applicable.
    indx : :class:`int`
        Index key into :attr:`embeds`, by default ``0``.
    ts_string : :class:`str`
        UTC-aware Discord timestamp string generated at construction time.

    """

    owner: Optional[Union[discord.Member, discord.User, discord.ClientUser]]
    "The invoker; the bot for a public view, or ``None`` for a preview no one may interact with."
    cog: KumaCog
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
            Clamps to ``len(embeds) - 1``, not ``len(embeds)`` - the latter is one past the end and
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
        owner: Optional[Union[discord.Member, discord.User, discord.ClientUser]],
        cog: KumaCog,
        embeds: Optional[Sequence[KumaEmbed]] = None,
        components: Optional[list[discord.ui.Item[Any]]] = None,
        recent_interaction: Optional[discord.Interaction] = None,
        dispatched_by: Optional[Union[KumaView, discord.ui.Button[KumaView]]] = None,
        timeout: Optional[float] = 180,
    ) -> None:
        """Create a :class:`KumaView` instance.

        Parameters
        ----------
        owner : :class:`Optional[Union[discord.Member, discord.User, discord.ClientUser]]`
            The invoker; pass the bot for a public view anyone may interact with, or ``None`` for a
            preview no one may interact with.
        cog : :class:`KumaCog`
            The parent Cog. Used to generate :attr:`ts_string`.
        embeds : :class:`Optional[Sequence[KumaEmbed]]`, optional
            Embeds to page through. Previous/Next buttons are removed when ``len <= 1``.
        components : :class:`Optional[list[discord.ui.Item[Any]]]`, optional
            Additional items to add immediately and restore on :meth:`reset_view`.
        recent_interaction : :class:`Optional[discord.Interaction]`, optional
            The most recent interaction, by default ``None``.
        dispatched_by : :class:`Optional[Union[Self, discord.ui.Button[Self]]]`, optional
            What spawned this view, by default ``None``.
        timeout : :class:`Optional[float]`, optional
            Seconds before the view stops accepting input, by default ``180``.

        """
        self._indx = 0
        self._embeds = embeds
        self.owner = owner
        self.cog = cog
        self.dispatched_by = dispatched_by
        self.recent_interaction = recent_interaction
        self._timeout = timeout

        now: datetime.datetime = datetime.datetime.now(tz=datetime.UTC)
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
        """Add the item to our `self.components` and call `super().add_item(item)`."""
        if item not in self.components:
            self.components.append(item)
        return super().add_item(item=item)

    def remove_item(self, item: discord.ui.Item[Any]) -> Self:
        """Remove the item from our `self.components` and call `super().remove_item(item)`."""
        if item in self.components:
            self.components.remove(item)
        return super().remove_item(item=item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Reject anyone but the view owner; a bot-owned view is public and a preview lets no one in."""
        LOGGER.debug("<%s.%s>", __class__.__name__, "interaction_check")
        # No owner is a preview; no one may interact.
        if self.owner is None:
            return False
        # A view owned by the bot is public; anyone may interact.
        if isinstance(self.owner, discord.ClientUser):
            return True
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
        """Reset the view to its initial layout and re-render the first page."""
        LOGGER.debug("<%s.%s>", __class__.__name__, "reset_callback")
        item.disabled = True
        view: KumaView = self.reset_view()
        view.recent_interaction = interaction
        if view.embeds is not None:
            embed: KumaEmbed = view.embeds[0]
            await interaction.response.edit_message(view=view, embed=embed, attachments=embed.attachments)
        else:
            await interaction.response.edit_message(view=view)

    def page_embed(self, embeds: Sequence[KumaEmbed]) -> KumaEmbed:
        """Returns the embed for the current page, adding a page-number footer only if it has none.

        .. warning::
            This deliberately does **not** overwrite an existing footer. Doing so destroys anything the
            caller put there - `claude.py` writes the run cost into the final page's footer and
            `ollama.py` the token count - and since the embed is mutated in place the original text is
            lost for good, not just for the current render. Callers that want page numbers alongside
            their own text should build both into the footer themselves.

        Parameters
        ----------
        embeds : :class:`Sequence[KumaEmbed]`
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
        """Step back one page, toggling the navigation buttons at the bounds."""
        LOGGER.debug("<%s.%s>", __class__.__name__, "previous_callback")
        if self.embeds is None:
            self.reset_view()
            await interaction.response.edit_message(view=self)
            return

        self.recent_interaction = interaction
        # Guard against underflow: on the first page there is nothing to go back to, and decrementing
        # would leave indx at -1, which silently renders `embeds[-1]` - the *last* page.
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
        """Step forward one page, resetting the view once past the last embed."""
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
    """A defaulted :class:`discord.ui.Button` for a :class:`KumaView`.

    Fills in a ``primary`` style, a ``Generic`` label and a slugified ``custom_id`` when none are
    given, so a button can be dropped into a view without spelling every field out.

    """

    view: KumaView

    def __init__(self, **kwargs: Unpack[ButtonParams]) -> None:
        if kwargs.get("style") is None:
            kwargs["style"] = discord.ButtonStyle.primary
        if kwargs.get("label") is None:
            kwargs["label"] = "Generic"

        # Derive a custom_id from the label so distinct buttons stay distinguishable by default.
        label: Optional[str] = kwargs.get("label")
        if kwargs.get("custom_id") is None and label is not None:
            kwargs["custom_id"] = label.lower().replace(" ", "_")

        super().__init__(**kwargs)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Defer silently for the owner; the view's :meth:`interaction_check` gates everyone else."""
        LOGGER.info("<%s.%s>", __class__.__name__, "callback")
        if interaction.user == self.view.owner:
            await interaction.response.defer()
        return
