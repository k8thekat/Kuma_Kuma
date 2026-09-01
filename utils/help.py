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

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

from .codeblocks import AnsiFore, AnsiStyle, CodeFormat, ansi, code_block
from .cog import KumaEmojiTable, KumaResources

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = ("HelpEntry", "HelpSection", "KumaHelpCommand")


#: Inactivity timeout for a help panel, in seconds.
HELP_TIMEOUT: float = 180.0
#: Commands listed per cog before the rest are summarised as a count.
ENTRIES_PER_SECTION: int = 12
#: Discord allows 25 options on a select; every cog past that is unreachable.
MAX_SELECT_OPTIONS: int = 25

#: Attachment filenames the panel's media items point at, as `/about` names them.
BANNER_NAME: str = "banner.png"
THUMBNAIL_NAME: str = "thumbnail.png"

_BLURB: str = "Pick a drawer below to see what is in it, or ask about one command directly."
#: Section name for right-click actions, which are commands but are not typed like one.
CONTEXT_SECTION: str = "Context Menus"
#: What each context menu type is attached to, for its one-line summary.
_CONTEXT_TARGETS: dict[discord.AppCommandType, str] = {
    discord.AppCommandType.message: "Right-click a message → Apps",
    discord.AppCommandType.user: "Right-click a user → Apps",
}
#: Sentinel :func:`_requirement` returns for an owner-gated command; it is already a whole phrase.
OWNER_ONLY: str = "Owner only"
#: `commands.check` factories whose closure holds the permissions they demand.
_PERMISSION_CHECKS: frozenset[str] = frozenset({"has_permissions", "has_guild_permissions"})


def _permission_label(names: Iterable[str]) -> str:
    """Turn permission attribute names into the wording Discord's own UI uses."""
    return ", ".join(name.replace("_", " ").title() for name in sorted(names))


def _requirement(command: Any) -> str:
    """Return what a command demands of its caller, or an empty string.

    Two sources, because the two kinds of command record it in completely different places.
    `default_permissions` is declared data and is read straight off application commands and the app
    half of a hybrid. A prefix-only command has nothing declarative — `commands.has_permissions()`
    builds a closure — so its predicate is unwrapped instead.

    .. warning::
        Reading a closure is reaching into discord.py's internals, and a rewrite of those decorators
        would quietly return nothing. That is why it is suppressed rather than allowed to raise: an
        unlabelled command is a cosmetic loss, a help command that crashes is not.

    """
    permissions: Optional[discord.Permissions] = getattr(command, "default_permissions", None) or getattr(
        getattr(command, "app_command", None), "default_permissions", None
    )
    if permissions is not None:
        return _permission_label(name for name, value in permissions if value)

    for check in getattr(command, "checks", ()):
        factory: str = getattr(check, "__qualname__", "").split(".")[0]
        if factory == "is_owner":
            return OWNER_ONLY
        if factory in _PERMISSION_CHECKS:
            with contextlib.suppress(Exception):
                closure: dict[str, Any] = dict(
                    zip(check.__code__.co_freevars, (cell.cell_contents for cell in check.__closure__ or ()), strict=False),
                )
                wanted: dict[str, bool] = closure.get("perms") or {}
                if wanted:
                    return _permission_label(name for name, value in wanted.items() if value)
    return ""


def _plural(count: int, noun: str, suffix: str = "s") -> str:
    """Return ``count`` and ``noun``, agreeing in number."""
    return f"{count} {noun}{'' if count == 1 else suffix}"


def _truncate(text: str, limit: int) -> str:
    """Shorten ``text`` to ``limit`` characters, ellipsis included."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


#: Attachment name -> the :class:`KumaResources` attribute it is uploaded from.
_MEDIA_SOURCES: dict[str, str] = {BANNER_NAME: "banner", THUMBNAIL_NAME: "sticker2"}


def help_files(names: Iterable[str]) -> list[discord.File]:
    """Return fresh attachments for a help panel.

    A :class:`discord.File` wraps a handle that is consumed on send, so these are built per message
    rather than shared. Only what the panel actually references is uploaded — a Components V2
    message carries attachments but does not render them, so an unreferenced one is dead weight.

    Parameters
    ----------
    names: :class:`Iterable[str]`
        Attachment filenames, from :data:`_MEDIA_SOURCES`.

    Returns
    -------
    :class:`list[discord.File]`
        One file per requested name.

    """
    resources: KumaResources = KumaResources()
    return [discord.File(getattr(resources, _MEDIA_SOURCES[name]), filename=name) for name in names]


@dataclass(slots=True, frozen=True)
class PanelMedia:
    """Where a panel's images live, for one particular message.

    ``attachment://`` resolves **only** against files uploaded in the same request. On an edit that
    keeps its existing attachments there is nothing to resolve against, and Discord rejects the whole
    payload with `50035 ... The referenced attachment was not found`. An already-uploaded image has
    to be pointed at by its CDN URL instead, which is what this carries.

    """

    banner: str
    thumbnail: str


#: Media for a message that is uploading its images alongside the components.
UPLOADED_MEDIA: PanelMedia = PanelMedia(banner=f"attachment://{BANNER_NAME}", thumbnail=f"attachment://{THUMBNAIL_NAME}")


def media_from(message: Optional[discord.Message], *, needed: Iterable[str]) -> Optional[PanelMedia]:
    """Return CDN-backed media for ``message``, or None if it is missing anything ``needed``.

    Parameters
    ----------
    message: :class:`Optional[discord.Message]`
        The message being edited.
    needed: :class:`Iterable[str]`
        Attachment names the replacement panel will reference.

    Returns
    -------
    :class:`Optional[PanelMedia]`
        URLs to reuse, or None when the caller should re-upload instead.

    """
    by_name: dict[str, str] = {attachment.filename: attachment.url for attachment in (message.attachments if message else [])}
    if any(name not in by_name for name in needed):
        return None
    return PanelMedia(
        banner=by_name.get(BANNER_NAME, UPLOADED_MEDIA.banner),
        thumbnail=by_name.get(THUMBNAIL_NAME, UPLOADED_MEDIA.thumbnail),
    )


@dataclass(slots=True)
class HelpEntry:
    """One command, flattened into just what the panel renders."""

    name: str
    params: str
    summary: str
    aliases: tuple[str, ...] = ()
    #: Rendered as `/name`, and invoked through the client rather than a prefix.
    slash: bool = False
    #: What the caller needs, e.g. "Manage Messages" or "Owner only". Shown, never used to filter.
    requires: str = ""


@dataclass(slots=True)
class HelpSection:
    """One cog and the commands the invoker is actually allowed to run."""

    name: str
    description: str
    entries: list[HelpEntry] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Stable identifier used as the select option value."""
        return self.name.lower()


class HelpSelect(discord.ui.Select["KumaHelpPanel"]):
    """Jumps between cogs without re-running the command."""

    def __init__(self, *, sections: Sequence[HelpSection], selected: Optional[str] = None) -> None:
        options: list[discord.SelectOption] = [
            discord.SelectOption(
                label=_truncate(section.name, 100),
                value=section.key,
                description=_truncate(section.description, 100) or None,
                default=section.key == selected,
            )
            for section in sections[:MAX_SELECT_OPTIONS]
        ]
        super().__init__(placeholder="Which drawer shall we open?", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Re-render the panel in place, focused on the chosen cog."""
        view: Optional[KumaHelpPanel] = self.view
        if view is None:
            return
        await view.rerender(interaction=interaction, selected=self.values[0])


class HelpHomeButton(discord.ui.Button["KumaHelpPanel"]):
    """Returns a focused panel to the overview."""

    def __init__(self) -> None:
        super().__init__(label="Overview", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Re-render the panel with no cog selected."""
        view: Optional[KumaHelpPanel] = self.view
        if view is None:
            return
        await view.rerender(interaction=interaction, selected=None)


class KumaHelpPanel(discord.ui.LayoutView):
    """The `help` panel.

    Renders from a snapshot of :class:`HelpSection` taken when the command ran, so paging between
    cogs never re-runs command checks or touches the bot.

    .. warning::
        A Components V2 message cannot carry `content` or `embeds`, but it *can* carry attachments —
        which is how the banner and thumbnail get here. How they are *referenced* differs between the
        first send and a later edit; see :class:`PanelMedia` and :meth:`rerender`.

    """

    def __init__(
        self,
        *,
        sections: Sequence[HelpSection],
        user_id: int,
        prefixes: Sequence[str],
        selected: Optional[str] = None,
        media: PanelMedia = UPLOADED_MEDIA,
    ) -> None:
        super().__init__(timeout=HELP_TIMEOUT)
        self.sections: Sequence[HelpSection] = sections
        self.user_id: int = user_id
        self.prefixes: Sequence[str] = prefixes
        self.selected: Optional[str] = selected
        self.media: PanelMedia = media

        focus: Optional[HelpSection] = next((section for section in sections if section.key == selected), None)
        # The banner rides on the overview only, so a focused panel must not keep it attached: an
        # attachment no component points at is uploaded for nothing.
        self.media_names: tuple[str, ...] = (THUMBNAIL_NAME,) if focus is not None else (BANNER_NAME, THUMBNAIL_NAME)

        container: discord.ui.Container = discord.ui.Container(accent_colour=discord.Color.og_blurple())
        if focus is None:
            self._build_home(container)
        else:
            self._build_section(container, focus)

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# {self._summary()}"))
        # The banner closes the panel the way `/about` uses `set_image`, and only on the overview —
        # a focused list is a working reference, and wants the room for commands instead.
        if focus is None:
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(self.media.banner)))
        self.add_item(container)

        # Outside the container: navigation acts *on* the panel rather than being part of what it is
        # showing, and the border is what says so.
        if sections:
            self.add_item(discord.ui.ActionRow(HelpSelect(sections=sections, selected=selected)))
        if focus is not None:
            self.add_item(discord.ui.ActionRow(HelpHomeButton()))

    def _header(self, *, title: str, blurb: str) -> discord.ui.Section:
        """Return the titled header, with the bear sitting beside it."""
        return discord.ui.Section(
            f"## {title}",
            f"-# {blurb}",
            accessory=discord.ui.Thumbnail(media=self.media.thumbnail),
        )

    def _build_home(self, container: discord.ui.Container) -> None:
        """Render the overview: every cog, one line each."""
        container.add_item(self._header(title="Kuma Kuma Bear", blurb=_BLURB))
        container.add_item(discord.ui.TextDisplay(f"-# {self._prefix_line()}"))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        sections_text: str = "\n".join(
            f"**{section.name}** · {len(section.entries)}\n-# {section.description}" for section in self.sections
        )
        if not sections_text:
            sections_text = f"-# {KumaEmojiTable.kuma_shrug} Nothing here you can run right now."
        container.add_item(discord.ui.TextDisplay(sections_text))

    def _build_section(self, container: discord.ui.Container, section: HelpSection) -> None:
        """Render one cog's commands."""
        container.add_item(self._header(title=section.name, blurb=section.description))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        window: list[HelpEntry] = section.entries[:ENTRIES_PER_SECTION]
        lines: list[str] = []
        for entry in window:
            label: str = f"/{entry.name}" if entry.slash else entry.name
            params: str = f" `{entry.params}`" if entry.params else ""
            # Aliases ride on the summary line rather than the name line: `-#` is only subtext at the
            # start of a line, so a second one further along would render as literal text.
            names: str = ", ".join(f"`{name}`" for name in entry.aliases)
            alias: str = f" · {_plural(len(entry.aliases), 'alias', 'es')}: {names}" if entry.aliases else ""
            # Shown, never used to filter: a moderator who cannot run something should still be able
            # to see it exists and find out why, rather than have it silently missing.
            needs: str = ""
            if entry.requires:
                # "needs Owner only" does not parse; that one is already a complete phrase.
                needs = f" · **{entry.requires}**" if entry.requires == OWNER_ONLY else f" · needs **{entry.requires}**"
            lines.append(f"**{label}**{params}\n-# {entry.summary}{alias}{needs}")

        if remaining := len(section.entries) - len(window):
            lines.append(f"-# …and {remaining} more. Ask about one by name for the details.")

        container.add_item(
            discord.ui.TextDisplay("\n".join(lines) or f"-# {KumaEmojiTable.kuma_shrug} This drawer is empty."),
        )

    def _prefix_line(self) -> str:
        """How to address the bot, said once here rather than repeated on every command line."""
        listed: str = " ".join(f"`{prefix.strip()}`" for prefix in self.prefixes)
        # A mention is always a prefix — `_get_prefix` wraps the guild's own in `when_mentioned_or`
        # — and is the only way in when a guild has set none of its own.
        return f"Prefix: {listed} · or just mention me" if listed else "Mention me to run a command."

    def _summary(self) -> str:
        """The counts line under the panel, scoped to whatever is on screen."""
        focus: Optional[HelpSection] = next((section for section in self.sections if section.key == self.selected), None)
        if focus is not None:
            # Context menus have no name you can type, so pointing at `help <command>` would be a
            # dead end for that one section.
            if focus.name == CONTEXT_SECTION:
                return f"{_plural(len(focus.entries), 'action')} · found under Apps on the right-click menu"
            return f"{_plural(len(focus.entries), 'command')} in {focus.name} · `help <command>` for detail"

        total: int = sum(len(section.entries) for section in self.sections)
        hidden: str = (
            f" · {_plural(len(self.sections) - MAX_SELECT_OPTIONS, 'cog')} not listed" if len(self.sections) > MAX_SELECT_OPTIONS else ""
        )
        return f"{_plural(total, 'command')} · {_plural(len(self.sections), 'cog')}{hidden}"

    async def rerender(self, *, interaction: discord.Interaction, selected: Optional[str]) -> None:
        """Replace the panel in place, keeping its images where it can.

        The images are already on the message, so the replacement points at their CDN URLs rather
        than re-uploading. If anything it needs is missing — going back to the overview after the
        banner was dropped — it uploads afresh instead, which makes the panel self-healing.

        """
        needed: tuple[str, ...] = (THUMBNAIL_NAME,) if selected else (BANNER_NAME, THUMBNAIL_NAME)
        media: Optional[PanelMedia] = media_from(interaction.message, needed=needed)

        panel = KumaHelpPanel(
            sections=self.sections,
            user_id=self.user_id,
            prefixes=self.prefixes,
            selected=selected,
            media=media or UPLOADED_MEDIA,
        )

        keep: dict[str, discord.Attachment] = {
            attachment.filename: attachment for attachment in (interaction.message.attachments if interaction.message else [])
        }
        attachments: list[Any] = [keep[name] for name in needed] if media is not None else list(help_files(needed))
        await interaction.response.edit_message(view=panel, attachments=attachments)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Rejects anyone but the person who asked for help."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                content=f"That panel isn't yours! {KumaEmojiTable.kuma_shrug} Ask me for your own.",
                ephemeral=True,
            )
            return False
        return True


class KumaCommandPanel(discord.ui.LayoutView):
    """Detail for a single command or group."""

    def __init__(
        self,
        *,
        command: commands.Command[Any, ..., Any],
        subcommands: Sequence[HelpEntry],
        media: PanelMedia = UPLOADED_MEDIA,
    ) -> None:
        super().__init__(timeout=HELP_TIMEOUT)
        self.media_names: tuple[str, ...] = (THUMBNAIL_NAME,)

        container: discord.ui.Container = discord.ui.Container(accent_colour=discord.Color.og_blurple())
        container.add_item(
            discord.ui.Section(
                f"## {command.qualified_name}",
                f"-# {command.short_doc or 'No description given.'}",
                accessory=discord.ui.Thumbnail(media=media.thumbnail),
            ),
        )

        # Bot-authored, so the ESC bytes survive: the command name reads cyan and its parameters gray,
        # the same vocabulary the console logger uses.
        invocation: str = ansi(command.qualified_name, fore=AnsiFore.CYAN, style=AnsiStyle.BOLD)
        if command.signature:
            invocation += " " + ansi(command.signature, fore=AnsiFore.GRAY)
        container.add_item(discord.ui.TextDisplay(code_block(invocation, CodeFormat.ANSI)))

        # Only what the header has not already said. `short_doc` is the first line of `help`, so a
        # one-line docstring would otherwise appear twice on the same small panel.
        detail: str = "\n".join((command.help or "").splitlines()[1:]).strip()
        if detail:
            container.add_item(discord.ui.TextDisplay(f"> {detail}"))

        if command.aliases:
            container.add_item(
                discord.ui.TextDisplay(
                    f"-# {_plural(len(command.aliases), 'alias', 'es')}: {', '.join(f'`{alias}`' for alias in command.aliases)}",
                ),
            )

        if subcommands:
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    "\n".join(
                        f"**{'/' if entry.slash else ''}{entry.name}**{f' `{entry.params}`' if entry.params else ''}\n-# {entry.summary}"
                        for entry in subcommands
                    ),
                ),
            )

        self.add_item(container)


class KumaHelpCommand(commands.HelpCommand):
    """A Components V2 help command for Kuma Kuma Bear.

    Replaces discord.py's `DefaultHelpCommand`, which paginates into plain code blocks. Every
    response here is a `LayoutView`, so nothing carries `content` or `embeds`.

    """

    def __init__(self, **options: Any) -> None:
        options.setdefault(
            "command_attrs",
            {
                "help": "Shows what Kuma Kuma Bear can do.",
                "aliases": ["commands", "h"],
                # Keeps `help` out of its own listing — `filter_commands` drops hidden commands, and
                # anyone reading this panel has evidently already found it. `help help` still works.
                "hidden": True,
            },
        )
        super().__init__(**options)

    async def resolve_prefixes(self) -> list[str]:
        """Return the guild's own text prefixes, mention forms stripped out.

        `_get_prefix` wraps whatever the guild has configured in `when_mentioned_or`, so what comes
        back is the mention forms plus any text prefixes. The mentions are dropped here: they are
        rendered as raw `<@id>` inside a code span, they are far wider than the command beside them,
        and the panel says "or just mention me" in words instead.

        Returns
        -------
        :class:`list[str]`
            Text prefixes, deduplicated, in the order `_get_prefix` gave them. Empty when the guild
            has set none — in which case a mention is the only way in, and the panel says so.

        """
        with contextlib.suppress(Exception):
            resolved: Any = await self.context.bot.get_prefix(self.context.message)
            candidates: list[str] = [resolved] if isinstance(resolved, str) else list(resolved)
            seen: dict[str, None] = {}
            for candidate in candidates:
                if candidate and not candidate.startswith("<@"):
                    seen.setdefault(candidate, None)
            return list(seen)
        return []

    async def _send(self, view: Union[KumaHelpPanel, KumaCommandPanel]) -> None:
        """Send a panel, with its images, without pinging anybody.

        A help reply that mentions its asker produces a badge for something they already have open,
        so mentions are suppressed explicitly rather than being left to the client default.

        """
        await self.get_destination().send(
            view=view,
            files=help_files(view.media_names),
            allowed_mentions=discord.AllowedMentions.none(),
            silent=True,
        )

    def _entry(self, command: commands.Command[Any, ..., Any]) -> HelpEntry:
        """Flatten one command into a :class:`HelpEntry`."""
        return HelpEntry(
            name=command.qualified_name,
            params=command.signature,
            summary=_truncate(command.short_doc or "No description given.", 120),
            aliases=tuple(command.aliases),
            requires=_requirement(command),
        )

    def _app_entry(self, command: app_commands.Command[Any, ..., Any]) -> HelpEntry:
        """Flatten one application command into a :class:`HelpEntry`."""
        params: str = " ".join(
            f"<{parameter.display_name}>" if parameter.required else f"[{parameter.display_name}]" for parameter in command.parameters
        )
        return HelpEntry(
            name=command.qualified_name,
            params=params,
            summary=_truncate(command.description or "No description given.", 120),
            slash=True,
            requires=_requirement(command),
        )

    def _app_entries(self, cog: Optional[commands.Cog]) -> list[HelpEntry]:
        """Flatten a cog's application commands, groups walked for their subcommands.

        `HelpCommand` only ever sees `cog.get_commands()`, which is prefix and hybrid commands. A cog
        whose surface is pure `app_commands` — `ClaudeCog` is entirely one `app_commands.Group` —
        is therefore invisible to it, cog and all. Hybrids are not double counted: they appear in
        `get_commands()` and are absent from `get_app_commands()`.

        """
        if cog is None:
            return []
        entries: list[HelpEntry] = []
        for command in cog.get_app_commands():
            if isinstance(command, app_commands.Group):
                entries.extend(self._app_entry(child) for child in command.walk_commands() if isinstance(child, app_commands.Command))
            elif isinstance(command, app_commands.Command):
                entries.append(self._app_entry(command))
        return entries

    def _context_section(self) -> Optional[HelpSection]:
        """Return the right-click actions as their own section, or None if there are none.

        Context menus are registered straight onto the tree in a cog's `__init__` rather than owned
        by it, so no cog claims them and `get_bot_mapping` never sees them. They also read nothing
        like a command — there is no name to type — which is why they get a section instead of
        being scattered through the others.

        """
        tree: app_commands.CommandTree[Any] = self.context.bot.tree
        found: list[app_commands.ContextMenu] = []
        for scope in (None, self.context.guild):
            for command in tree.get_commands(guild=scope):
                if isinstance(command, app_commands.ContextMenu) and command not in found:
                    found.append(command)

        if not found:
            return None

        entries: list[HelpEntry] = [
            HelpEntry(
                name=menu.name,
                params="",
                summary=_CONTEXT_TARGETS.get(menu.type, "Right-click → Apps"),
                requires=_requirement(menu),
            )
            for menu in sorted(found, key=lambda menu: menu.name.lower())
        ]
        return HelpSection(
            name=CONTEXT_SECTION,
            description="Right-click actions, run from the Apps menu rather than typed.",
            entries=entries,
        )

    async def _sections(
        self,
        mapping: Mapping[Optional[commands.Cog], list[commands.Command[Any, ..., Any]]],
    ) -> list[HelpSection]:
        """Build the panel snapshot, dropping anything the invoker cannot run."""
        sections: list[HelpSection] = []
        for cog, cog_commands in mapping.items():
            usable: list[commands.Command[Any, ..., Any]] = await self.filter_commands(cog_commands, sort=True)
            entries: list[HelpEntry] = [self._entry(command) for command in usable]
            entries.extend(self._app_entries(cog))
            if not entries:
                continue
            entries.sort(key=lambda entry: entry.name.lower())
            description: str = (cog.description or "").split("\n")[0] if cog else "Bits that belong nowhere else."
            sections.append(
                HelpSection(
                    name=cog.qualified_name if cog else "Odds and Ends",
                    description=_truncate(description, 90),
                    entries=entries,
                ),
            )
        sections.sort(key=lambda section: section.name.lower())
        context: Optional[HelpSection] = self._context_section()
        if context is not None:
            sections.append(context)
        return sections

    async def send_bot_help(
        self,
        mapping: Mapping[Optional[commands.Cog], list[commands.Command[Any, ..., Any]]],
        /,
    ) -> None:
        """Render the overview panel."""
        sections: list[HelpSection] = await self._sections(mapping)
        await self._send(
            KumaHelpPanel(sections=sections, user_id=self.context.author.id, prefixes=await self.resolve_prefixes()),
        )

    async def send_cog_help(self, cog: commands.Cog, /) -> None:
        """Render the overview panel, opened on ``cog``."""
        sections: list[HelpSection] = await self._sections(self.get_bot_mapping())
        await self._send(
            KumaHelpPanel(
                sections=sections,
                user_id=self.context.author.id,
                prefixes=await self.resolve_prefixes(),
                selected=cog.qualified_name.lower(),
            ),
        )

    async def send_command_help(self, command: commands.Command[Any, ..., Any], /) -> None:
        """Render a single command's detail."""
        await self._send(KumaCommandPanel(command=command, subcommands=()))

    async def send_group_help(self, group: commands.Group[Any, ..., Any], /) -> None:
        """Render a group's detail, listing the subcommands the invoker can run."""
        usable: list[commands.Command[Any, ..., Any]] = await self.filter_commands(group.commands, sort=True)
        await self._send(
            KumaCommandPanel(
                command=group,
                subcommands=[self._entry(command) for command in usable],
            ),
        )

    async def send_error_message(self, error: str, /) -> None:
        """Report an unknown command in Kuma's voice rather than a bare string."""
        await self.get_destination().send(
            content=f"{KumaEmojiTable.kuma_hmm} {error}",
            allowed_mentions=discord.AllowedMentions.none(),
            silent=True,
        )
