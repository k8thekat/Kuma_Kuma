# Changelog

## Error reports go to the owner, and the restart stops breaking things

# kuma_kuma.py
- `on_command_error` rewired. The caller is told first and separately; the report to the owner goes through `report_error` (from `utils.error`) afterwards, wrapped in its own `try/except` so a failed report can never take the user reply down with it. `CommandNotFound` on a `None` command is handled rather than falling through, and every branch uses the emoji table directly (`self.emoji_table.kuma_sad`) instead of the retired `to_inline_emoji`.
- `KumaCommandTree.on_app_command_error` rewired the same way. `answer_caller` closes the interaction's spinner first — a deferred command that raised used to hang "thinking…" forever — then `report_error` sends the traceback to the owner. The old inline embed builder is gone.
- `restart()` is new. Flags `restart_requested` and hands `close()` to the loop as a task rather than awaiting it. Awaiting closed the client from inside the invocation chain still running the command; `Bot.invoke` has a `command_completion` left to dispatch, and `close()` having set `Client.loop` to `MISSING` meant that dispatch raised `'_MissingSentinel' object has no attribute 'create_task'`.
    - `_restart_close_task` holds a strong reference so the task cannot be collected mid-flight.
    - `on_command_completion` returns early when `restart_requested` is `True`, since the restart deletes its own invocation before scheduling the close and the cleanup below it races the shutdown.
- `main()` returns `bool` — whether the bot asked to be restarted. `__main__` reads it and `os.execv`s the same command line (`sys.orig_argv`) when it is `True`, so the PID stays the same and `Kuma.bash`'s pidfile keeps working.
- `_on_terminate` turns `SIGTERM` into `KeyboardInterrupt`, so `Kuma.bash -stop` unwinds through the same path as Ctrl-C. Without it Python's default SIGTERM killed the process outright — no `cog_unload`, no closed pool, and `ClaudeCog.cog_unload` never killed in-flight `claude` subprocesses.
- `parse_args` reads `--live`/`--dev` and `--log-level` from the command line. A bare `python kuma_kuma.py` defaults to live, matching the old behaviour; `Kuma.bash` passes the flag through.
- `_get_prefix` reads from the new `guild_prefixes` cache instead of hitting the database on every message. `DEFAULT_PREFIX` (`"kuma"`) is always included so it works in guilds whose `prefix` table row was never written.
- `guild_prefixes` caches per-guild prefix lookups in `_prefix_cache`, reading the database only on a miss. `invalidate_prefixes` drops a guild's entry so the next message re-reads.
- `on_command_completion` includes the guild's own prefixes alongside the global ones when deciding whether to clean up the invocation, so a command invoked with a guild-specific prefix is cleaned up on the spot rather than waiting for `message_timeout`.
- `add_cog` / `remove_cog` overrides maintain `command_owners`, mapping every command name to the cog that owns it. Built incrementally — indexed after `super()` so it describes what actually registered.
- `command_names()` (module-level) reads both `get_commands` and `get_app_commands` and deduplicates hybrids through a set.
- `is_me` broadened to accept `discord.Thread` as well as `discord.Message`. Thread ownership is checked by `owner_id` rather than `.owner`, which resolves through the member cache and returns `None` for uncached members.
- `commands_enabled_for` gates `process_commands` in Claude session threads — only a mention of the bot passes through, so a message typed into a live session is not accidentally run as a prefix command.
- `refresh_help_command` reimports `utils.help` and rebinds the help command, so a `reload` picks up edits without a full restart.
- `LogHandler.__init__` takes the `aiohttp.ClientSession` as a parameter instead of creating its own later, and installs `KumaLogFormatter` on the console stream handler while leaving the rotating file handler plain-text.
- `LogHandler.parse_log` rewritten with `_tail_entries`: seeks backwards from EOF in doubling windows until enough matching records are in hand, instead of reading the whole file. Capped at `LOG_TAIL_MAX_BYTES` (4 MiB).
- `LogHandler.code_formats` is now `tuple[CodeFormat, ...]` backed by `PALETTE_FORMATS`, and `default_code_format` is `CodeFormat.POWERSHELL`, replacing the bare string lists.
- `kuma_claude` loaded explicitly after the extension loop (`load_extension("kuma_claude")`), since it is a separately installed package rather than a module under `extensions/`.
- `LogHandler` session creation order swapped: `CachedSession` is opened *before* `Kuma_Kuma`, since `LogHandler.__init__` now needs it.
- `owners` table: `ownerid` column gains `UNIQUE`, preventing duplicate rows from repeated `trust add` calls.

# utils/__init__.py
- Re-exports from `utils.error`, `utils.codeblocks`, `utils.animation`, and `utils.help` added, so `kuma_kuma.py` can import `report_error`, `setup_errors`, `KumaLogFormatter`, `KumaHelpCommand`, `CodeFormat`, `PALETTE_FORMATS`, and the log-entry helpers from the package root.

# utils/cog.py
- `KumaCog.__init__` uses `type(self).__name__` instead of `__class__.__name__` for the metrics key, so every cog keys its metrics under its own name rather than all of them keying under `"KumaCog"`.
- `KumaCog.settings_choices` (staticmethod) turns a settings `TypedDict` into `app_commands.Choice` entries, keeping the TypedDict as the single place a setting is declared. Excludes `settings_excluded_keys` (`id`, `serverid`, `userid`).
- `KumaCog.rotate_pick` (staticmethod, generic) chooses one of several entries using the wall clock as a seed — no RNG and nothing stored. `ROTATE_WINDOW` (3 600 s) is the default hold time.
- `KumaCog.to_progressive` inflects only the first word of a label (e.g. `"Read"` → `"Reading"`, `"Run tests"` → `"Running tests"`), restoring the caller's capitalisation.
- `KumaCog.animate` builds a `KumaAnimation` as an async context manager, so a failure cannot strand a message animating forever.
- `KumaEmojiTable.to_inline_emoji` retired (commented out with rationale). The `int` branch never worked — it compared the ID against the whole `<:name:id>` string. `get_emoji` remains for runtime name lookups.
- `KumaEmojiTable.to_cdn_url` returns the CDN image URL for an emoji by name, so Components V2 parts that need a URL (`Thumbnail`, `MediaGalleryItem`) can point at our emojis without uploading an attachment.
- `UnicodeTable` gains eight new emoji entries and `right_hook_arrow`.

# utils/embeds.py
- `KumaEmbed.set_footer`, `set_image`, and `set_thumbnail` all default their URL parameter to `None` instead of to an `attachment://` path. The old defaults pointed at a file that was never uploaded whenever the caller did not explicitly set one, leaving every such embed with a broken image. When `None` is passed but a file was attached earlier, the methods now keep the existing attachment reference rather than silently dropping it.
- `add_blank_field` defaults `index` to `None` (append) instead of `-1`, which is one-before-the-end — a spacer on a single-field embed went above it.
- `add_seperator` replaces the literal `"test"` value field with a zero-width space. `"test"` was rendering as visible text.
- `icons` property: the `[entry for entry in icons if not None]` filter was always `True` (it tested the literal `None`, not `entry`). Already safe — `None` entries were dropped earlier by the `isinstance` check — so the redundant comprehension is replaced with a plain `return icons`.

# utils/timezones.py
- `parse_bcp47_timezones` uses `async with` for both the session and the request, so a non-200 response no longer leaks the session. The early `return` on failure used to jump past `await session.close()` at the bottom.
- Typo fixed: `Kyev` → `Kyiv` in the timezone comment.
- `convert_timezones` documented and simplified to a single return.

# utils/ui.py
- `KumaView.indx` clamps to `len(embeds) - 1`, not `len(embeds)`. The latter is one past the end and would raise `IndexError` on the lookup this property exists to make safe.
- `previous_callback` guards against underflow at `0` instead of `>= 0`. Decrementing from zero left `indx` at `-1`, which silently rendered `embeds[-1]` — the last page.
- `page_embed` is new: returns the embed for the current page, adding a page-number footer only when the embed does not already carry one. Both `previous_callback` and `next_callback` use it instead of inlining the footer.

## A rolled tail keeps its header

# utils/animation.py
- `KumaRollingAnimation._open` posts a new tail with its header already on it. `KumaAnimation.start` renders again a round trip later, so a tail used to spend that round trip one line higher than where it settled — a visible jump, and on a rolled tail a moment spent hard against the message above it.
- `compose_final` puts a footer-only close through `_compose` rather than returning it bare, so a closing frame carries the same header the live frames did. The empty-string return is unchanged and still means "nothing worth keeping, put something here yourself" — a header with no content under it would be a message saying only that it exists.
    - Both matter now that `kuma_claude`'s `chatter` level rolls: its tails never hold a transcript, so *every* frame it draws is one of these two cases, and the continuation header is the only thing holding the spinner off the reply above it.

## How much a Claude session says is the session owner's choice

# extensions/claude.py
- `Verbosity` is four levels a session can be narrated at, replacing the `INLINE_TRANSCRIPT` module flag. VERBOSE is the rolling transcript as it was; DEFAULT is the pinned status box the one-shot cog used, with tool and target only; CHATTER keeps the box but writes nothing into it, so no path, command or search pattern reaches a thread other people can read; SILENT holds every reply until the turn ends and posts them in order. Prompts are shown at all four — a session that quietly stopped asking would wedge.
    - Two of the four are display *classes* rather than branches: `ChatterTurnContext` and `SilentTurnContext` subclass `TurnContext` and override only the narration, so `VERBOSITY_CONTEXTS` is the whole of the dispatch and every level is closed out and cleaned up by the same code.
    - Only VERBOSE changes the runner. A rolling transcript is a different flow of messages, not a different filter on one, so it stays `_run_turn_inline`; the other three are `_run_turn` with a different context.
    - `TurnContext.flush()` is a no-op the closing paths call unconditionally. SILENT is the only override, and it empties its queue before sending, so the normal path and the interrupt path can both call it without either knowing about the other.
- `tool_line()` is the one place a call becomes text, so the two displays cannot drift apart on what one looks like. The target is italicised and the tool kept in a code span; the target is also `escape_markdown`'d, since it is a glob or a command as often as it is a path and a `**/*.py` inside an italic span would swallow the emphasis and the rest of the line with it.
- The repeat count is a raised, italic `ˣ³` rather than the ` -# x3` tail it was. Discord has no subtext inside a line — `-#` renders literally anywhere but the start of one — so the old form was showing the marker itself on screen.
- `tool_target()` returns plain text instead of a code span. The status log italicises it and `ToolApproval` puts it in a code span, and only the caller knows which; markup applied at the source had to be unpicked by one of them.
- `ToolEntry.signature` compares the raw tool and target rather than the rendered line, so a change to `tool_line()` cannot quietly change what counts as a repeat.
- The level is read per turn, not held on the session: a queued turn is narrated the way its owner wants when it runs, not when the message was sent. An unloaded `preferences` answers DEFAULT rather than failing the turn.

# extensions/preferences.py
- A cog declares its own settings on `__preferences__`, walked from the loaded cogs exactly as `HintsCog` walks `__hints__`. A preference arrives with its extension and leaves with it, and adding one is a single dataclass in the cog that owns it.
- They are stored in `user_preferences`, keyed by `(userid, pref_key)`, rather than as columns on `user_settings`. A column per declaration would put every extension's vocabulary into core's schema and leave it there once the extension was gone; it would also make `migrate()` depend on which cogs happened to be loaded first, and cost `Preferences.migrate` its staticmethod.
- `value()` and `set_value()` are the read and write. Both validate against the declaration — a stored value the cog no longer offers reads as the default rather than being handed back, and a write of one is refused, since both arrive through a component's custom ID and are not ours by the time they come back.
- `PreferenceSelect` renders a choice as a text display with a select beneath it, not as a `Section` accessory: the API takes only a button or a thumbnail there. Measured at 24 of Discord's 40 components with one declared.
- `PreferencesPanel.build()` is an async classmethod because a constructor cannot await and the values live in a second table; the four call sites otherwise had to know that.
- `reset` clears the declared preferences too. An absent row *is* the default, so there is nothing to write back.
- No `from __future__ import annotations` here, deliberately: discord.py resolves a hybrid command's annotations against the module at runtime, and ruff would move `Choice` into a type-checking block where the resolution would fail.

## async_sonarr moves out to its own repo

# async_sonarr/
- Removed. The package now lives at `~/gitHub/async_sonarr` with the scaffolding the other API wrappers have — `pyproject.toml`, `README.md`, `sample.py`, `local.py`, `LICENSE`, `.github/` workflows, `.vscode/`, `numpy_template/` — and a `py.typed` it was missing. Nothing in it ever imported `discord` or `utils`, so the directory moved unchanged.
    - Its ruff config came from here rather than from `GarlandToolsAPI_wrapper`; that is the config the code was written under, and it uses the current `[tool.ruff.lint]` shape rather than the deprecated top-level one. `ruff check .` is clean in the new repo.
    - GPLv3, matching the source headers. `GarlandToolsAPI_wrapper` ships MIT only because it is a fork of an MIT project, so copying its `LICENSE` wholesale would have been wrong.

# extensions/sonarr.py
- The import is unchanged but is now third party, so isort regrouped it. It resolves through an editable install (`pip install -e ~/gitHub/async_sonarr --no-deps`) until the package is published; a comment above the import says so, and TODO.md records it as a setup step for a fresh venv.

## Hints a cog offers after any of its commands

# extensions/hints.py
- `Hint.cog_wide` offers a hint after *any* command the declaring cog owns, rather than only where a call site names it by key. Named around the reserved word `global`, which cannot be an attribute at all.
- Two listeners drive it, and they have to agree about hybrids. A `HybridCommand` run as a slash dispatches `command_completion` *and* `app_command_completion` — `ext/commands/hybrid.py:454` and `:485` alongside `app_commands/tree.py:1304` — so `hint_after_command` returns early when `Context.interaction` is set and lets the app listener own that invocation. 22 of the bot's commands are hybrids; without the guard each would offer its hint twice.
- `owner_of()` resolves an app command's cog through `Kuma_Kuma.command_owners` rather than `app_commands.Command.binding`. The latter holds the same answer but is absent from that class's documented attributes, so it can change without a deprecation.
- `last_hint_at()` reads `MAX(last_seen)`, a column the ledger has always written and nothing has ever read, and `COG_WIDE_COOLDOWN` throttles on it. `retire_at` counts showings rather than time, so three commands in a minute would otherwise spend a hint's whole allowance before it was read once.
- The pick rotates through a cog's still-active hints with `KumaCog.rotate_pick`, filtered *before* the pick — rotating first and finding the chosen hint retired would skip a turn and show nothing on a cog that still had something to say.
- Listeners are named `hint_after_*` rather than `cog_wide_after_*`: `CogMeta` reserves the `cog_` and `bot_` prefixes and raises at class creation, so the extension fails to import outright.
- `HintsCog.send()` gets its first caller. It had none — only `send_into()` was wired, from `claude.py:6156` — so the `Context`/`Interaction` half of the API had never run.

# kuma_kuma.py
- `Kuma_Kuma.command_owners` maps a command name to the cog that owns it, maintained a cog at a time by `add_cog`/`remove_cog` overrides rather than rebuilt. Every load, unload and reload routes through those two — `reload_extension` is remove then add — so no cog can arrive or leave without the map hearing about it, and load order stops mattering.
- `command_names()` reads `get_commands` and `get_app_commands`, both of which return the top level only, so a group contributes its own name and none of its children. A hybrid appears in both lists under one name; the set dedupes it.

# utils/cog.py
- `KumaCog.rotate_pick` chooses one of several entries with the wall clock as the seed — no RNG and nothing stored, so the same caller inside one window is handed the same entry. A staticmethod and generic, since nothing about it is hint shaped.
- Seven emoji added to `UnicodeTable`, in the doubled-glyph form `charinfo` emits.

## The restart command stops taking the loop with it

# kuma_kuma.py
- `restart()` hands `close()` to the loop instead of awaiting it. Awaiting closed the client from inside the invocation chain still running the command, and `Bot.invoke` has a `command_completion` left to dispatch after the callback returns — `close()` having set `Client.loop` to `MISSING`, that dispatch raised `'_MissingSentinel' object has no attribute 'create_task'` on every restart.
    - The guard in `on_command_completion` could never have helped; the failure was in scheduling the handler, not in running it.
    - The task is held on `_restart_close_task`, since a task with no strong reference can be collected before it finishes. `main()` still returns `restart_requested` once `start()` unwinds, so the re-exec is unchanged.
- Reproduced against real discord.py machinery before and after: the old ordering raises the reported `AttributeError` with the same "coroutine never awaited" warning, the new one dispatches cleanly and still ends `is_closed()`.

## Reddit posts as Components V2, previewable before they are switched on

# extensions/reddit.py
- `RedditPost` is the Components V2 counterpart of `RedditEmbed`: the title carries the permalink as a masked link, the age is a live `<t:…:R>` rather than a footer frozen at send time, and the image is a single item `MediaGallery`, which renders at the size `set_image` did.
    - `link_label()` backslash escapes `[` and `]` in the title. Reddit titles are full of `[OC]` and `[Serious]` tags, and an unescaped `]` closes the masked link — the rest of the title then arrives as literal text with a bare URL trailing it.
    - `media_filename()` names the in-line attachment after the extension its URL claims. A media gallery item is served off the attachment's name, so the extensionless `image` the embed path uses renders as a broken tile rather than a photo.
    - `use_remote_media()` is the 413 escape hatch in V2 terms: it repoints the gallery at Reddit's CDN and empties `files`, where the embed path mutated `field_image` and re-called `set_image`.
- `RedditPagedView` holds the paging shared by the new panels. A `LayoutView` has no `embed=` to swap on the message, so a page turn is a rebuild — subclasses implement `rebuild()` and one `PageButton` drives both.
- `RedditPagePanel` is the same idea for text: the counterpart of the `KumaEmbed` + `KumaView` pairing behind the listings and the crawler metrics. Fields become a `- **Key** — value` bullet list, there being no inline fields in V2 and no markdown tables in Discord.
- `reddit_preview` renders all four shapes against made up data, owner only. There was no way to eyeball the crawler's post without waiting for a crawl to find something new, and no way at all to see a shape that is not wired up yet; nothing here touches Reddit or downloads an image. The paged post uses avatar URLs on purpose — a `discord.File` is consumed by the send that uploads it, so a paged post must point at remote media.
- Nothing calls the new views yet. The existing `RedditEmbed` path is untouched; TODO.md records the three things that have to move together when it is swapped.
- Every view built offline and its `to_components()` audited: buttons in an `ActionRow`, gallery inside the `Container`, 312 of the 4000 characters discord.py does not check.

## Sonarr from Discord, with a cache the instance keeps honest

# async_sonarr/
- New wrapper for the Sonarr v3 API, laid out like `async_universalis` and `async_garlandtools` — `__init__.py`, `_types.py`, `_enums.py`, `modules.py`, `errors.py` — and importing neither `discord` nor `utils`, so it lifts out into its own repo unchanged.
    - Written rather than adopted. `pyarr` is sync first and covers eight apps for the eight endpoints wanted here; `aiopyarr` is async but has not shipped since January 2022. Neither speaks SignalR, which is the part that makes the cache work, so both would have been a large dependency plus the interesting half written by hand anyway.
- `SonarrAPI` keeps the library in memory and the SignalR hub invalidates it. `GET /series` is un-paginated — the whole library in one response — so a command that called it per invocation would move megabytes for data that changes a few times a day. A hub `series` message patches one entry with the resource it already carries, an `episodefile` message refetches that one series, and a bodyless `sync` marks the library stale for the next read.
- `signalr.py` implements the hub by hand: negotiate for a connection token, open the websocket, send the `{"protocol":"json","version":1}` handshake, then split frames on the `\x1e` record separator. Pings every 12s, because the server drops a client it has not heard from inside 30. Reconnects with exponential backoff to a two minute ceiling, and treats a subscriber that raises as the subscriber's problem rather than the connection's.
- The API key travels in `X-Api-Key` and never in the query string. Sonarr accepts `?apikey=` and the hub only reads `access_token` from the query, so the one URL that must carry it is scrubbed before it reaches a log line.
- Detects an `aiohttp_client_cache.CachedSession` and sends `expire_after=0` on every request. The bot's shared session caches for 24 hours, which for a live library is worse than no cache — and nothing could invalidate it.
- `_normalise()` accepts a bare host and adds `:8989`; a borrowed session is never closed on teardown, only one the client created itself.

# extensions/sonarr.py
- New cog. `/sonarr list`, `info`, `add`, `remove` and `status`, all Components V2 panels, all ephemeral.
- Gated to k8thekat through `Cog.interaction_check`, which registers once for every app command in the cog rather than a decorator per command. Checks `bot.owner_user_id`, not `owner_ids` — the latter grows at runtime through `trusted`, and these commands write to disk and start downloads.
- Artwork is the layout. A series' fanart becomes the `MediaGallery` across the top of a panel, its poster the `Thumbnail` accessory beside the overview, and its status picks the container's accent — green continuing, grey ended, gold upcoming, red for a removal. A series with no art falls back to a button accessory, since a `Section` accessory may only be a button or a thumbnail.
- `RemovePanel` holds its own `expires_at` and carries it across a re-render. `View.timeout` measures inactivity and restarts on every press, so toggling the files switch would otherwise extend a stated deadline indefinitely.
- Autocomplete searches the cache, never the network, which is what makes it fast enough to fire per keystroke. A caller who types free text instead of picking a choice falls back to a title search.
- The cog reads `[SONARR]` from `local.ini` itself rather than through `KumaConfig`, so an instance nobody has configured disables one cog instead of stopping the bot from starting. A Sonarr that is down at load is the same: the cog loads, and the listener reconnects on its own.
- Verified against a mock Sonarr — REST, error mapping, cache hits, and the hub's update/delete/import/sync paths — and every panel built offline and audited: no select outside an `ActionRow`, no illegal `Section` accessory, worst case 26 of the 40 components and 1426 of the 4000 characters discord.py does not check.

# local.ini
- Added a commented `[SONARR]` template.

## Coloured logs survive the debugger

# utils/codeblocks.py
- `KumaLogFormatter` now decides on colour through `_colour_supported()` instead of `sys.stdout.isatty()` alone. A debugger hands the process a pipe, so the sniff answered "no colour" even when the console reading it renders escapes perfectly well — VS Code's Debug Console being the case that prompted this.
    - `NO_COLOR` disables, `FORCE_COLOR` enables, `isatty()` decides when neither is set. `NO_COLOR` wins if both are present.
    - An explicit `colour=` argument still overrides everything; only the `None` default consults the environment.

# .vscode/launch.json
- Set `FORCE_COLOR=1` on the debug configuration so a launched session gets the same coloured console as a plain terminal run.

# extensions/claude.py
- Added `to_discord_markdown()`, called from `on_text` before the chunker. Assistant text was posted exactly as written, and the model writes *standard* markdown while Discord is not CommonMark — so a table arrived as raw pipes with its `|---|` rule sitting in the middle, which is unreadable rather than merely plain.
    - Tables become an aligned fenced block, since monospace is the only place column padding holds. Detected by the delimiter rule rather than by the pipes, so `use \`a | b\` for either` is not mistaken for a one row table.
    - `---`, `***` and `___` rules become `REPLY_SEPARATOR`; `- [ ]` and `- [x]` become ☐ and ☑ keeping their indent.
    - Emphasis markers are stripped from table cells, because nothing renders inside a fence and `**Yes**` would otherwise arrive with its asterisks showing. Links are left whole.
    - `__text__` is deliberately left alone. Discord reads it as underline rather than bold, but which was meant is a guess, and this pass only does what is mechanical.
- Added `walk_markup()`, now the only place a code fence is recognised. `balance_markup` reads from it too rather than keeping its own scan, so a rewrite can never disagree with the chunker about which lines are code — which matters because a table *inside* a fence is somebody's actual output and must survive untouched.
- Runs before chunking so a table is whole when it is measured, and `result.blocks` still records the raw text, which is the answer as the model wrote it.
- Verified: a table inside a code block is untouched, a pipe in prose is not converted, ragged rows and alignment colons and missing outer pipes all parse, a 60 row table chunks with every fence balanced and every chunk under 2000, and `balance_markup`'s contract is unchanged after the refactor.

# extensions/utility.py
- Reverted the attachment listing added to the GitHub issue body; it was not wanted, and the original `TODO` about it is back where it was. The **Source** field stays — `build_body()` now only appends the jump link, and answers with the bare body when the field is cleared.

## One dialog to open an issue, and a note that Discord has no tables

# extensions/utility.py
- Rewrote the GitHub issue flow as a single modal. `discord.ui.Label` (discord.py 2.6) wraps a `Select` as well as a `TextInput`, so repository and submission type are fields *inside* the modal rather than a view of two selects that opened it once both were answered — that two step only ever existed because a modal could not hold a select.
    - Removed `GithubIssueSubmissionView`, `GithubIssueSubmissionSelect`, `GithubIssueSubmissionResult` and `check_results()`, and with them the `is_done`/`result` state that was being threaded across two components to decide when the modal could open.
    - The context menu sends the modal straight away; the "please select a GitHub Repository" message it used to post first has nothing left to ask.
    - Moved off `TextInput(label=...)`, deprecated in 2.6. The deprecation is quiet where it is used — the parameter warns about nothing and only the `.label` property raises — so it would never have surfaced in the log.
    - Added a fifth **Source** field, pre-filled with the message's jump URL and optional. The title already said "submitted via Discord" without saying *which* message; this makes it a link, and clearing the field leaves it out for an issue that has outgrown the message that started it. The modal is now full at five children.
    - Added the message's attachments to the issue body — filename, size and link — via `build_body()`, split out of `on_submit` so the assembly is readable without an interaction. Discord signs its CDN URLs and they expire within about a day, so the body says as much in a blockquote; the issues API takes no file upload, and re-hosting somebody's attachment is a larger decision than this command should make. That retires the last `TODO` in the flow.
    - Verified offline against 2.6.3: five `Label`s build (the cap), the body pre-fills from the message content cut to 4000, Source pre-fills with the jump URL, a simulated submit reads every value back, and `build_body()` is checked with attachments and source present and with neither — the second case produces the bare body, no orphaned rules or headings.
    - Fixed the issue POST holding its connection open on failure, and gave both outcomes a log line. A failed submission answers ephemerally now; a created issue still posts its embed.

# CLAUDE.md, and `~/.claude/skills/discord-py/`
- Added the rule that **markdown tables do not render in Discord** — `| a | b |` with a `|---|` rule arrives as literal pipes and dashes, separator row included. Recorded with the substitutes rather than just the prohibition: a bullet list for two columns (keeps bold, `code`, links and emoji), a fenced code block for three or more or anything needing alignment (accepting that nothing inside a fence renders), grouped `###` headings where each row needs a live link, and `·` as the inline separator. Applies to every string the bot sends and to every reply written into a thread.

## Comments in my own hand, and the housing cog actually keeps the thread it builds

# CLAUDE.md
- Added the comment and logging rules that were being broken: no ` -- ` as an aside separator (use `—`), keep comments to a line or two, and `__class__.__name__` over `type(self).__name__`. Measured before writing the rule — ` -- ` appeared 0 times in any comment or docstring k8thekat wrote and 217 times in Claude-authored ones, so it was a tell, not a convention.

# Everywhere
- Replaced ` -- ` with `—` across every comment and docstring in the live tree, 217 of them. Done by token position rather than a text substitution, so code, CLI flags and log strings were untouched — `"Sentry SDK is Enabled -- Flag: %s"` is k8thekat's and still reads that way.
- Changed `Preferences.migrate` and `Moderator.migrate` to log through `__class__.__name__`; they had a hardcoded class name because `__class__` was assumed not to resolve in a `@staticmethod`. It does. Verified they emit `<Preferences.migrate>` and `<Moderator.migrate>`.

# kuma_kuma.py
- Changed `owners.ownerid` to carry a UNIQUE constraint, and added `_migrate_owners()` to rebuild an existing table into it. SQLite has no `ALTER TABLE ADD CONSTRAINT`, so the table is recreated and copied; duplicates are collapsed to the lowest `id` first or the copy would fail on the new constraint. Verified against a table seeded with a doubled-up owner: one row removed, the constraint now refuses a repeat, and a second run is a no-op.

# extensions/moderator.py
- Changed `trusted add` to `ON CONFLICT(ownerid) DO NOTHING`, matching how `preferences` and `reddit` write, now that the constraint exists to conflict on.

# extensions/private/housing.py
- Fixed the rebuilt listing thread being the one deleted. `thread, _ = await thread.parent.create_thread(...)` rebound the name, so `thread.delete()` took the post that had just been built and left the raw URL one standing — and every send after it went to a thread that no longer existed. The original is held separately now.
- Fixed `schools` never being modelled. It arrives as a *list* of dicts and the branch tested `isinstance(value, dict)`, so `Zillow_Schools` was dead code and the raw payload was printed through `list(...)`. Added `gen_schools_content()` to render it as name, level, grades, distance and rating.
- Fixed `bool("false")` being `True`, so every string flag in `resoFacts` came out True whichever way it read — a listing with no AC reported having it and then listed its type as "None".
- Fixed `?parse` and `?close` resolving the thread through `PartialMessage.thread`, which answers off the cache and returns `None` when it misses; `message.channel` *is* the thread. `?close` writes `[CLOSED] - ` itself, which is the marker `Moderator.mod_on_thread_update` skips on, so the two no longer both want to rename it.
- Fixed `content.find("URL:") + 5` slicing from index 4 when the marker is absent, handing the parser a few characters of the listing body.
- Added a bots guard to the `on_message` listener, and `async with` on the listing fetch so a non-200 releases the connection instead of holding it open.
- Brought the file up to house standard: the GPL header, docstrings on the new helpers, the `<Class.method> | ` logging shape, and named constants for the forum, title and content limits. Ruff went from 71 errors to clean; the `Zillow_*` names are held with a file-level `noqa` and a note, since they mirror the payload keys `setattr` maps by.

## The prefix works everywhere, and trusting someone is three commands

# kuma_kuma.py
- Added `DEFAULT_PREFIX`, applied by `_get_prefix` in every guild. `kuma` was documented as the default but only ever came out of the `prefix` table, which held exactly one row — so in every guild but Neko Neko Cafe the documented prefix did not exist and only a mention worked.
- Added `guild_prefixes()` and `invalidate_prefixes()`, a per-guild prefix cache. `_get_prefix` runs for every message the gateway hands us and `Moderator`'s listener asks again for the same message, so this was the most frequent query the bot made; it is now one read per guild, dropped by the `prefix` commands rather than given a lifetime. Measured: three lookups cost one read, and an unconfigured guild caches its empty answer instead of re-reading forever.
- Changed `on_command_completion` to check the guild's own prefixes as well as the global one. It read `_prefixes`, which only ever held `kuma`, so a command invoked with an added prefix kept its invocation for the full `message_timeout` instead of being cleaned up on the spot.
- Changed `is_me()` to compare `Thread.owner_id` rather than `Thread.owner`, which resolves through the member cache and answers `None` for an uncached member — reading as "not mine" for a thread that is.
- Fixed `webhook_send_log` building a `ClientSession` inline and never closing it; it uses the bot's session, like `upload_log` already did.
- Fixed `FileNotFoundError` and `OverflowError` messages built as logging calls — `"... | path: %s"` with the value passed as a second argument never interpolates, so the raw `%s` reached the reader.
- Changed `on_command`'s Repl exemption to guard `context.cog` being `None`, and finished the comment that stopped mid-sentence: a session keeps answering the message that started it, so deleting the invocation would hang the whole session off "Original message was deleted".

# extensions/moderator.py
- Split `trusted` into a hybrid group with `add`, `remove` and `list`. `remove` carries `autocomplete_trusted`, so the member parameter is picked from who is *actually* trusted rather than typed as a snowflake — which is the whole reason for the split.
    - Fixed `list` selecting `ownderid` (a typo, so the query raised) and then reading `entry["id"]`, the autoincrement column, which would have looked up members `1, 2, 3`. It lists the union of the table and `owner_ids`, since my own ID is seeded at startup and has no row.
    - Fixed `list` resolving owners with `Guild.fetch_member`; a trusted user does not have to share a guild with the command, and one `NotFound` took the whole listing down.
    - Fixed `remove` calling `owner_ids.remove()`, which raises `KeyError` for anyone not in the set — after the row had already been deleted. It is `discard` now, and it refuses `owner_user_id` outright.
    - Fixed `add` writing a second row for someone already stored; `owners` has no UNIQUE constraint, so the insert is `WHERE NOT EXISTS`.
- Fixed the mystbin half of `on_message_listener` never once firing. It tested `res["use_mystbin"] is True`, and SQLite hands an INT column back as `1`, which `is True` rejects. The `spam_filter` line beside it was already using `bool()`.
- Added `auto_mystbin` and `thread_rename` as user preferences, read through `preference()` — `get_cog` rather than an import, so the two cogs stay independently reloadable. The guild setting still turns mystbin on at all; the author now decides whether their own messages are moved out from under them.
- Fixed `mod_on_thread_update` renaming threads the bot owns. The `locked` branch guarded against it and the `archived` branch did not — and a Claude session close is exactly an archive, so closing a session had `moderator` rewriting the title `claude.py` had just written and parses its session state back out of.
    - Added `LOCKED_PREFIX` / `CLOSED_PREFIX`, checked on both branches. The two spellings disagreed about their trailing space, so a thread locked *and* archived in one edit could end up carrying both markers.
    - The thread's owner decides via `thread_rename`, not whoever pressed lock.
- Fixed `sync` being unable to sync globally. `local=False, reset=False` fell through every branch and returned `None`, so the command answered with nothing at all; the scope is decided once up front and both paths report what they did. The `tree.sync()` calls are hoisted out of the `LOGGER.info` arguments they were hiding in.
- Added `Moderator.migrate()`, collapsing duplicate `moderator` rows and adding the UNIQUE index that stops more appearing. `set_mod_settings(default=True)` was a bare INSERT against a table with no constraint, so one live guild had two rows; `fetchone` then answers with whichever SQLite reaches first. Rows that *disagree* are reported and left alone rather than having a winner picked for them.
- Changed the `prefix` commands to invalidate the cache, refuse a duplicate, report a delete that matched nothing, and say that `kuma` still works after a clear. `clear` gained the `@commands.guild_only()` it was using `context.guild` without.

# extensions/preferences.py
- Added `auto_mystbin` and `thread_rename`, and `Preferences.migrate()` to add them to a table that already exists — `CREATE TABLE IF NOT EXISTS` is a no-op against one, so a new column would never reach a live database and every read of it would raise.
- Added `USER_SETTING_DEFAULTS`, the one place a preference's default is written. `enabled()` derived its fallback from `setting == "hints_enabled"`, so every other preference answered `False` the moment the database hiccuped — including two that default to on. It is also the allowlist `set_user_settings` validates against, so a preference is now declared in exactly two places: the schema and that dict.

# extensions/gatekeeper.py
- Fixed `whitelist` sending the enum rather than its value; the console received `/whitelist WhitelistActions.add <name>`, which Minecraft rejected every time.
- Fixed both autocompletes searching only their first field. `query in (a or b)` hands back the first truthy operand, so searching an instance by ID silently did nothing. Added `_matches()` and the 25-choice cap Discord enforces.
- Fixed `instance_info` fetching status, instance info and analytics and then sending none of it — the "appears to be broken and won't handle a message" TODO. It renders a `KumaEmbed` with state, uptime, players, CPU, memory, disk and top players; analytics is a soft failure, since not every module reports it.
- Fixed `duplicate_role` reading `res.result` before the `isinstance` check, which raised `AttributeError` on exactly the failure that check exists to handle.
- Fixed `instance_app_control` answering nothing when the instance is not running.
- Fixed `ini_load` raising credential errors written as logging calls, so the message arrived with literal `%s` in it. It now names which options are missing without echoing their values.

# extensions/utility.py
- Fixed `/about` reporting `1` for User/Members forever — `len([self.bot.get_all_members()])` measures a one-element list wrapped around the generator. It reports unique users and total members, which answer different questions.
- Fixed `/about` printing latency in seconds with an `ms` suffix.
- Changed the Intents line to name the three *privileged* intents that are actually granted, plus the raw bitfield. An `Intents` object reprs as all twenty-odd flags, most of them `False`.
- Fixed GitHub issues never being assigned: the field was spelled `assigness`, and GitHub drops unknown fields silently. It is `assignees`, taken from the configured repo owner rather than a hardcoded name.
- Fixed Yoink's Copy to Guild always erroring after succeeding. The sticker branch ran and then fell into the emoji `else`, answering an interaction it had already deferred — an `InteractionResponded` every time, and the "Oops" fallback was unreachable.

# extensions/reddit.py
- Fixed `LOGGER.warning` in `subreddit_media_handler` carrying two placeholders and no arguments, so every interrupt logged a traceback instead of the one line explaining why the crawler stopped.
- Changed the compare reaction to ✅ and scoped it to the crawler's own posts, matched on `Message.webhook_id`. It answered any ✅ anywhere the bot could see and downloaded whatever URL it scraped out of the message; comparing arbitrary images is what `hash_comparison` and `edge_comparison` are for.
- Added `normalize_subreddit()`. `/add_subreddit` advertises that it takes a full reddit URL, but the string went to the API untouched and 404'd every time; a URL, `/r/name`, `r/name` and a bare name all reduce to the name now.

# extensions/repl_cog.py
- Fixed the duplicate-session guard looking a channel ID up in a dict keyed by user ID, so it never matched and a second `repl` replaced the first session's bookkeeping while the first loop carried on using it.
- Fixed a timed-out session reporting a `StopIteration` error and leaking both `wait_for` listeners. `asyncio.wait` *returns* on timeout rather than raising, so the `except TimeoutError` was unreachable; pending waiters are now cancelled on every path out, since `wait_for` only drops its listener when the future resolves.
- Added `end_session()`, so the session is dropped and the goodbye written exactly once from every exit — one path used to send the same message twice.
- Removed a dead `on_message` that carried no listener decorator and compared a `Session` dict to a channel ID.

# utils/cog.py
- Retired `KumaEmojiTable.to_inline_emoji()`, commented out rather than deleted. Every entry is a plain class attribute, so `table.kuma_happy` says the same thing without a lookup that can raise — and its `int` branch never worked, comparing an ID against the whole `<:name:id>` string. Call sites in `kuma_kuma.py`, `gatekeeper.py` and `private/housing.py` moved to the attribute.
- Fixed `KumaCog.__init__` keying metrics under `__class__.__name__`, which is bound to the class the method is *written* in — so every cog filed its metrics under `KumaCog` and the last one loaded won. `FFXIV` only worked because it overwrites the dict itself.

# utils/embeds.py
- Fixed `add_blank_field`'s default `index=-1`, which is one *before* the end — `insert_field_at(-1)` on a single-field embed puts the spacer above it. All seven bare callers wanted an append.
- Fixed `add_seperator` rendering the literal string `test` as a field value.

# utils/timezones.py
- Fixed `parse_bcp47_timezones` leaking its session on a non-200: the early `return` jumped out with the session open, and the `close()` at the bottom only ever ran on the happy path.

# utils/help.py
- Added `KumaHelpCommand`, a Components V2 replacement for `DefaultHelpCommand`, which paginated into plain code blocks. Every response is a `LayoutView`, so none carry `content` or `embeds`.
- Added `KumaHelpPanel` and `KumaCommandPanel`. The panel renders from a `HelpSection` snapshot taken when the command ran, so paging between cogs never re-runs a check or touches the bot.
- Added application commands to the listing. `HelpCommand` only ever sees `cog.get_commands()`, which is prefix and hybrid commands, so a cog whose surface is pure `app_commands` was invisible cog and all — `ClaudeCog` is one `app_commands.Group` and did not appear at all; `FFXIV` showed three of its seven.
    - `_app_entries()` walks `cog.get_app_commands()`, recursing into `app_commands.Group`. Hybrids are not double counted: they appear in `get_commands()` and are absent from `get_app_commands()`.
- Added a `Context Menus` section, built from `bot.tree` rather than any cog. Context menus are registered onto the tree in a cog's `__init__`, so no cog owns them and `get_bot_mapping()` never sees them.
- Added a required-permission note per command, shown and never used to filter. A moderator who cannot run something should still see it exists and why.
    - Two sources, because the two kinds of command record it in different places. `default_permissions` is read off application commands and the app half of a hybrid; a prefix-only command has nothing declarative, so `commands.has_permissions()`'s closure is unwrapped instead. That second path is suppressed rather than allowed to raise — an unlabelled command is cosmetic, a help command that crashes is not.
- Added `PanelMedia`, carrying `/about`'s banner and thumbnail. `attachment://` resolves only against files uploaded in the same request, so an edit that keeps its existing attachments was rejected with `50035 ... The referenced attachment was not found`; a re-render points at their CDN URLs instead, and re-uploads when the message is missing one.
- Changed the prefix out of the command lines. `Context.clean_prefix` rewrites a mention invocation to `@Kuma Kuma Bear `, which rendered as `` `@Kuma Kuma Bear about` `` — enormous, and not a mention inside a code span. `resolve_prefixes()` states the guild's text prefixes once in the header instead.
- Hid `help` from its own listing via `command_attrs`, which also retired the `Odds and Ends` section it was the only member of.

# kuma_kuma.py
- Added `help_command=KumaHelpCommand()`; nothing had ever set it, so `DefaultHelpCommand` was still in play.
- Added `refresh_help_command()`. `help_command` holds an instance built once in `__init__`, so reloading `utils.help` left the old object still answering; `reload` now rebinds it.

# extensions/moderator.py
- Changed `reload` to call `refresh_help_command()` after reloading extensions.

## Clearing back to a message, and not one message further

# extensions/moderator.py
- Added reply support to `clear`. Replying to a message clears everything after it, the anchor itself surviving as the marker — `purge(after=...)` is exclusive, which is the wanted behaviour and matches the existing message ID path.
    - Guarded on `MessageReferenceType.default`: a forward carries a reference too, pointing at another channel. Costs no API call, since `reference.message_id` is already on the message.
- Fixed a failed message ID fetch reporting and then continuing. Without the `return`, `amount` stayed the purge limit, so a mistyped ID asked Discord to delete a nineteen digit number of messages.
- Changed the message ID anchor from `after=created_at` to `after=Object(id=...)`; same exclusive semantics, but ID ordering is exact where timestamps can tie.

## Exactly one handler claims a message

# kuma_kuma.py
- Added `commands_enabled_for()`, gating `process_commands`. `Bot.on_message` and `ClaudeCog.session_message_listener` fire independently, so anything opening with a prefix was handled twice — `@Kuma Kuma Bear restart` invoked the command *and* arrived as a prompt.
    - Inside a session thread only an explicit mention counts as a command. The naive fix, deferring to `ctx.valid`, silently ate ordinary prose: `kuma help me debug this scraper` invoked `help`.

# extensions/claude.py
- Changed `session_message_listener` to consult the same predicate, so the two can never disagree about who owns a message.

## A reload no longer leaves a turn spinning

# extensions/claude.py
- Added `seal_turns()`, replacing the bare cancel loop in `cog_unload`. `task.cancel()` only requests cancellation, and `run_turn` needs a further round trip to write its closing frame, so the seal was a race teardown usually lost on a reload and always lost on a restart — leaving a spinner on a turn that no longer existed.
    - Cancels, then waits for the `CancelledError` handlers that already seal correctly, and only reaches into the display for whatever missed the window. Bounded at `SEAL_TIMEOUT`: a hung Discord edit must not hold a reload open.
- Added `TurnContext.stop_reason`, set before the cancel so both `interrupted_note()` implementations say the right thing without the seal logic being duplicated.

## Log output that reads the same in the console and in Discord

# utils/codeblocks.py
- Added the module: `CodeFormat` (the highlight.js fence tokens Discord understands, several of them palettes wearing a language costume), `AnsiFore`/`AnsiBack`/`AnsiStyle`, and `ansi()`/`code_block()`/`ansi_block()`/`strip_ansi()`.
- Added `KumaLogFormatter`, restricted to the eight colours Discord honours even though a terminal offers more. That restraint is the point: the bytes it writes to the console are legal inside a Discord `ansi` block.
- Added `colourise_log()` and `split_log_entries()`, so a plain excerpt read back off disk can be recoloured on the way out.

# kuma_kuma.py
- Changed the stream handler to use `KumaLogFormatter`; the rotating file stays plain so `grep` keeps working.
    - The formatter colours a `copy.copy()` of the record. A `LogRecord` is shared by every handler, so colouring it in place would push escape bytes into the log file and from there into anything that reads it back.
- Fixed `LogHandler.code_formats`, three of whose seven entries carried a leading space (`" nim"`, `" ps"`, `" prolog"`). A fence token must sit flush against the backticks, so those three would have rendered uncoloured. The list was never referenced, so it had not yet bitten.

## Reading the tail of a log without reading the log

# kuma_kuma.py
- Changed `parse_log` to seek from EOF, doubling a 16KB window until enough records are found. It previously read the whole file to keep the last handful: 0.4ms and 16KB against a 3MB, 40k record file.
- Changed it to slice by *record* rather than characters, so a traceback always arrives attached to the message that raised it.
- Added a `levels` filter accepting the shapes a Discord argument arrives in — `ERROR`, `ERROR,WARNING`, `error warning` — validating against the known level names rather than silently returning nothing.
- Added `max_bytes`, which matters when a narrow filter would otherwise walk the entire file looking for one CRITICAL.
- Ordered the colouring after the trim: trimming coloured text eventually slices an escape sequence in half, leaving a visible fragment and colour bleeding down the block.

# extensions/utility.py
- Changed `logs` to take `entries`, `colour` and `levels`, and to fence through `code_block()` so a stray fence in a log message cannot break out.

## Saying what each cog is for

# extensions/gatekeeper.py
- Added the class docstring; it had none, so the help panel had nothing to show. Written from what the cog imports and does — the `ampapi` surface and its three task loops — with the unported GatekeeperV2 scope marked as intent rather than implied to exist.

# extensions/private/housing.py
- Added the class docstring, including the `?` verbs a thread answers, which were otherwise discoverable only by typing a bare `?`.

# extensions/utility.py
- Fixed "it's code" and "A elongated ... seperated" in strings the help panel now surfaces.

# extensions/moderator.py
- Fixed "all messges" in `clear`'s help text.

# CLAUDE.md
- Added the code style section: `Optional[X]` over `X | None` (`UP045` is disabled on purpose), annotated locals, numpydoc with `:class:` roles, no abbreviated names, the `<Class.method> | Thing | Key: value` logging shape.

## A session that only rings when it needs you

# extensions/claude.py
- Changed every message a session sends to go out silent, with the prompt as the sole exception. A turn is a stream — one question can produce a dozen messages — so notifying on each trains the reader to ignore the session, which is the reader you cannot afford when a prompt finally needs answering.
    - `send_to_thread()` takes `silent`, defaulting true, and everything a session narrates already routes through it: answer text, notices, and every frame the status animation posts.
    - Added `reply_to()`, loosely typed and passing through to `Message.reply`, so the ~30 command replies get `silent` without restating each one's arguments. A caller wanting the notification passes `silent=False`.
    - The prompt send passes `silent=False` explicitly beside its `allowed_mentions`. Both halves are load-bearing: `silent` decides whether anything fires, `allowed_mentions` decides whether the owner's line pings or merely renders blue. The oversized-plan attachment above it stays silent, since ringing twice for one decision is worse than not ringing.

## Coming back up with the same PID

# extensions/moderator.py
- Added `restart`, a prefix only command beside `reload`. Deletes its own invocation, replies, then calls `Kuma_Kuma.restart()`.
    - Checks `Kuma_Kuma.owner_user_id` rather than `@commands.is_owner()`; `owner_ids` is editable at runtime via `trusted`, which is a wider door than a whole-process restart should open.

# kuma_kuma.py
- Added `Kuma_Kuma.restart()` and `restart_requested`; `main()` now returns that flag.
    - The re-exec runs in `__main__` after `asyncio.run()` returns, never inside the command. `os.execv` replaces the process image on the spot and would abandon a live event loop, skipping `cog_unload`, the pool and the session.
    - Uses `execv` rather than a spawn to keep the PID `Kuma.bash` wrote to `kuma_kuma.py.pid`; a child would leave that file pointing at a dead process. `sys.orig_argv` preserves `-u` and the `--live`/`--dev` mode.
    - Only the command sets the flag. Ctrl-C and SIGTERM still stop the bot.
- Added `owner_user_id`, seeded into `owner_ids` by `__init__` so the ID is written once.
- Fixed `on_command_completion` returning early during a restart; it is dispatched after `close()`, so its invocation-delete could only raise over a dead session.

## A question answers once, and always has a way out

# extensions/claude.py
- Added `ClaudePrompt.transient`, set by `QuestionPrompt`. An answered question deletes its message instead of leaving a `SettledPrompt`; the answer goes back as the tool's own result. Approvals and plans are unchanged.
    - Added `ClaudePrompt.retire()`, shared by the timeout and withdrawal paths, falling back to settling when a delete is refused.
    - `InteractionResponded` (a `ClientException`, not an `HTTPException`) joined the fallback's suppress; without it a failed delete escaped `resolve` and blocked the turn to `TURN_TIMEOUT`.
- Added an owner mention to every live prompt. A blocked turn is silent by definition, so a prompt raised during a long task could sit unseen until it timed out and denied itself.
    - The mention lives inside the view, since a Components V2 message cannot carry `content`. `allowed_mentions` is passed explicitly at the send.
    - `SettledPrompt` drops the line; an answered prompt is nobody's turn to act.
- Changed single-choice questions to lay out like `SessionPanel` — one `Section` per option, label in bold over the model's description, button beside it as the accessory, cut to `OPTION_BUTTON_SIZE`.
    - A repeated "Choose" label said nothing about which button was which, and bare numbers read as too sparse. The button is cut; the answer sent back is the whole label.
    - Sections spend the view's 4000 characters where a menu spends none, so options are costed before the question is fitted. Overrunning raises at construction, and `on_control_request` answers a prompt it could not build by denying it.
- Added `QuestionSelect` for questions marked `multiSelect`. A row of buttons silently answered "several" with one.
    - Option values are positions, not labels: a value is capped at 100 characters, so two long labels sharing a prefix would truncate into the same value. The answer is assembled in the model's option order, and an out-of-range value answers nothing.
    - Added `QuestionOption` / `_question_options()` so the menu and the buttons read the same list. Descriptions are carried on the menu.
- Added `OtherAnswerButton` / `OtherAnswerModal`, the free-text answer, capped at `OTHER_ANSWER_LIMIT`. A message sent while a turn is blocked queues behind it rather than reaching the question, so there was previously no escape but the timeout.

## Nothing is left behind: posts, workspaces or processes

# extensions/claude.py
- Fixed a closed session never expiring. `close_session` sets `archived=True` and `ForumChannel.threads` is the cache, which holds only what is not archived, so the sweep could not see a closed post at all.
    - Added `all_session_threads()`, walking `forum.archived_threads()` alongside the cache, deduplicated by ID and capped at `ARCHIVED_SWEEP_LIMIT`.
    - Added `expire_sessions()`, split out of `cleanup_loop`. It skips only `EXPIRED`, so a `CLOSED` post keeps ageing.
- Added `sweep_orphan_processes()` for CLI processes left by a bot killed outright. `cog_unload` covers a reload; nothing covered a hard kill, and the children hold ~300MB of node each and can still spend the shared account.
    - Matched on two signals together: the process works inside `USERS_ROOT`, and its argv carries `--permission-prompt-tool` and `--session-id`. PIDs this cog drives are excluded.
    - Runs at `cog_load` before anything of ours is up, and again on the cleanup tick.
- Added `prune_orphan_workspaces()` / `prune_workspaces()` for deletions that happened while the bot was down. Fails closed twice: enumerating zero forums aborts the sweep, and a directory younger than `WORKSPACE_GRACE_HOURS` is left alone.
- Added `discard_foreign_post()` / `discard_foreign_posts()` and the `on_thread_create` listener. A hand-made post satisfies `is_session_thread` but can never become a session. The author is DM'd their text before it goes.
- Added inline Discord message links. A jump URL in a prompt is fetched, written into the workspace with a provenance header, and handed to the CLI as a path. Capped at `MAX_PROMPT_LINKS`, full jump URLs only.
- Changed `PLACEHOLDER_TITLE` to a static emoji form. A thread name is not message content, so `<t:…:R>` arrives as literal text and custom app emoji never render.
- Changed Restore Session on an `EXPIRED` post to check the snapshot exists first. Past 30 days Claude Code has dropped its own transcript, so `--resume` has nothing to read.
- Not done, per instruction: `on_message_edit`, the project scope guards, and `.raw`.

## The panel stops repeating itself, and there is somewhere to put news

# extensions/claude.py
- Added `/claude announcement`, one standing notice shown to anyone opening a session. Owner only; setting a new one replaces the old outright, with no history.
    - Stored as JSON at `~/.kuma_claude/announcement.json`, beside the user roots rather than in the repo, and on disk so a restart does not drop it. JSON so who set it and when travel with it.
    - Added `Announcement`, `read_announcement()`, `write_announcement()`. Every read failure answers "no announcement"; a corrupt file must never stop a session opening.
- Changed the `.help` hint to post into the session post rather than back to the command, where it was landing in whatever channel `/claude ask` was run from.
- Changed the `.help` pointer out of `PANEL_NOTICES[ACTIVE]` and into a hint on `/claude ask`, retiring after `DEFAULT_RETIRE_AT` showings or on Dismiss.
- Added `ClaudeCog.__hints__`, the first in the codebase, proving `HintsCog`'s cog-walking registry works from outside its own module.
- Added `show_announcement()` and `show_hint()`. Neither is allowed to be the reason opening a session appears to have failed; the hint path no-ops when `hints` is not loaded.

# extensions/hints.py
- Added `HintsCog.send_into()`, and split `prepare()` out of `send()`. `send()` could only answer an interaction or a context, so a hint about a place had nowhere to go.
    - `send_into` takes the user explicitly: a destination cannot imply one, and the gating, count and buttons are all per person.

## One turn at a time, and a stopped turn stays stopped

# extensions/claude.py
- Fixed two messages sent close together starting two concurrent turns on one CLI process. `run_turn` checked `thread.id in self._turns` and registered the context three awaits later, so both cleared the check, their events interleaved, and the first turn's context was orphaned until `TURN_TIMEOUT`.
    - Changed messages sent during a turn to queue rather than being refused. The CLI takes input mid-turn and works it in order. `LiveSession.turn` is what they queue on, and `asyncio.Lock` hands out in request order.
    - `run_turn` is the queue gate; `_run_turn` is the body and runs holding the lock.
- Fixed `.stop` not reaching a queued turn. Stopping only the turn in flight let the next start the instant it ended, and with nothing running `.stop` claimed nothing was running while a queued turn ran anyway.
    - Added `ClaudeCog._queued`. `interrupt_turn` cancels those first, then interrupts the live one.
- Fixed a stopped turn continuing to post. `.stop` resolves the turn locally without waiting for the CLI, so blocks already in the pipe arrived under the "Stopped." notice. `on_text` and `on_tool` now drop late events.
- Fixed `LiveSession.turn` being created and never acquired. Its comment described the bug above; the lock was the intended mechanism and was never wired up.

## The event stream is read for what it actually sends

# extensions/claude.py
- Probed `claude 2.1.220` directly. The stream carries 11 top-level `type` values; `on_cli_event` was acting on four.
- Fixed a prompt the CLI withdrew staying live and pressable. Interrupting a turn blocked on `can_use_tool` makes the CLI send `control_cancel_request`, which was ignored, so a press answered a retired request ID and did nothing.
    - Added `ClaudeCog.on_control_cancelled()` and `ClaudePrompt.withdraw()`. `withdraw` is the only path that ends a prompt without answering the CLI.
    - Added `ClaudeCog._prompts`, live prompts by request ID. A cancellation arrives with nothing but the ID.
    - Verified on the wire: interrupting a pending `Write` approval returns exactly `{"type": "control_cancel_request", "request_id": …}`. It carries no `session_id`, so `track_session_id` ignores it.
    - Added `TurnContext.finished`; `on_resumed()` returns early on a finished turn.
- Fixed an aborted turn reporting itself as an empty code block. `TurnContext.finish()` read `event["result"]`, and a turn aborted at a tool has no `result` key — it returns `subtype: "error_during_execution"`, `terminal_reason: "aborted_tools"`.
- Added `rate_limit_event` handling via `ClaudeCog.on_rate_limited()` and `LiveSession.invalidate_usage()`. The CLI pushes the account's standing unasked and mid-turn, which `blocked_by_usage` cannot see. Says nothing to the thread deliberately.
- Fixed `block.get("input")` / `request.get("input")` calling `.get()` twice across an `isinstance`, which narrows nothing.

## Sessions are live now — the CLI stays up and asks you things

# extensions/claude.py
- Rewritten around a live CLI process per session instead of a one-shot run per message. Verified against `claude 2.1.220`.
- Added `LiveSession`, one long-lived `claude` process per forum post. Turns are written to its stdin as user events and it keeps the conversation, so there is no `--resume` between messages and no transcript replay per turn.
    - `--input-format stream-json` only works with `-p`, and in that combination `-p` is not one-shot: the process stays up across turns, `returncode` stays `None`, and one `session_id` carries the lot. Dropping `-p` starts the interactive TUI, which needs a PTY.
    - `--permission-prompt-tool stdio` is what routes approvals to us and is undocumented, absent from `--help` entirely. Without it the CLI answers its own permission questions.
    - Writes are serialised behind a lock; a control response from a button press and a turn submission would otherwise corrupt the newline framing.
- Added permission prompts in Discord as `LayoutView`s with plain Yes/No buttons. All three kinds arrive on the one `can_use_tool` channel and are told apart by `tool_name`.
    - `ToolApproval` gains a third Always button only when the CLI offered a `setMode` in `permission_suggestions`, labelled with the mode it will set.
    - `PlanApproval` renders the plan itself and switches to `acceptEdits` on approval; leaving plan mode alone would refuse the very next tool call.
    - `QuestionPrompt` is answered by denying with the chosen option in the message. Allowing is not enough: the CLI ran the tool, found no terminal, and reported the question was never answered.
    - Every prompt must resolve, since the CLI blocks its whole turn on one. The timeout therefore denies.
- Added mode and model changes that take effect immediately via the `set_permission_mode` and `set_model` control requests, applied in place with no respawn.
- Added live effort changes, so nothing restarts a session's process any more. Effort has no control request but has a slash command, which the CLI handles itself at `num_turns: 0` and no model call.
    - This travels in band as a user message, so the CLI answers with a `result` event. `apply_effort` defers the change while a turn is running and `run_turn` drains it on the way out, after deregistering.
    - Added `auto` as an effort level. `auto` is command only: `--effort auto` warns and quietly runs at the default, so `spawn` omits the flag for any level outside `EFFORT_FLAG_VALUES` and re-applies it by command after the handshake.
- Added `TurnContext`, which keeps the animated tool log working against a stream that no longer maps one process per turn. Adds a paused state for "waiting on a person".
- Added per-user isolation. Every user's sessions run with their own directory outside this repository as the cwd (`~/.kuma_claude/<user_id>/`), seeded with a personal `CLAUDE.md` on first use.
    - The placement is load-bearing twice: `CLAUDE.md` is discovered by walking up from the cwd, and the CLI names its memory directory after the cwd, so a per-user cwd gets a per-user memory.
    - The cwd is the user's root, not the session's; per-thread would key the memory to one forum post rather than to the person.
    - `--add-dir` grants the project root back.
- Added `claude_users`, its own access table, with `/claude access <member> <grant_access>`. Deliberately not the bot's owner list.
- Added an idle reaper. A session quiet for 30 minutes has its process closed; the next message respawns it with `--resume`. Verified nothing is lost by killing a process and finding the respawn still knew a planted codeword.
- Added `.stop`, sending the CLI's own `interrupt`. The process and conversation survive.
- Added assistant text posted per completed block rather than accumulated to the end. Not token-level: `--include-partial-messages` would mean an edit every few hundred milliseconds.
- Fixed the CLI re-keying a session without us following it. `/clear` mints a new `session_id`, verified, and a fork does the same. `track_session_id` now follows it off the event stream and re-renders the panel.
- Renamed `.new` to `.clear`, matching the CLI command it delegates to. `new` and `reset` remain aliases; the panel button is Clear.
    - `/clear` works in place, so clearing no longer kills and respawns the process. A parked session mints an ID locally.
    - Routed through `run_turn`, since `/clear` is in band and its `result` has to be consumed.
- Restored the pre-turn usage check lost in the rewrite. `blocked_by_usage` refuses up front and says when the window resets.
    - Costs no tokens: measured over twenty consecutive `get_usage` calls the context moved by 0 tokens, the session cost by $0.000000, and no `result` event was produced.
    - Costs ~275ms, so the reading is cached for `USAGE_CACHE_SECONDS` (120). 50 cached reads total 0.03ms against 594ms for the cold one.
    - A cached reading claiming exhaustion is confirmed with a fresh one before anything is refused.
    - Never having read the usage is not grounds to refuse. Warns once at `USAGE_WARN_PERCENT` (80) without blocking, re-arming when the account recovers.
- Audited every dot command against the live CLI:
    - Fixed `.rename` renaming only the Discord post. The CLI keeps a session name of its own, which `claude --resume`'s picker lists. Now does both via the `rename_session` control request.
    - Fixed `.status` only repeating our own state; it now also reports live context usage and the account's rate-limit windows.
    - Added `.usage` (alias `.cost`), reading `get_usage`. Out of band, so unlike forwarding `/usage` it needs no turn.
    - `.compact` gained its documented `[what to keep]` argument.
    - Validated as correct with no change: `.mode` (no `/mode` exists), `.model`, `.effort`, `.stop`, `.context`, `.compact` and `.memory`.
    - The fallthrough is safe: an unrecognised `.name` becomes `/name`, answered locally at `num_turns: 0`. `/todos` and `/pr-comments` no longer exist; `/cost` is an alias of `/usage`.
- Added context and compaction reporting via the `get_context_usage` control request, wrapped as `ContextUsage` and shown by `.context` with a usage bar.
    - The CLI compacts by itself at `autoCompactThreshold` (167k on a 200k window) and announces it as `system` / `compact_boundary`. The event pump was dropping every `system` event, so this would have been silent.
    - Added a one-shot warning at `CONTEXT_WARN_RATIO` (85% of the threshold), taken after the answer is posted. Re-armed when a compaction lands.
    - `categories` carries a "Free space" entry that is what remains, not what is used, so it is dropped.
- Added `LiveSession.request()`, a general control-request helper with a timeout. An unsupported subtype logs and returns `None` rather than hanging.
- Removed the entire tool/permission apparatus: `Access`/`AccessTier`/`AccessConfig`, the `claude_access` table, `PROJECT_DENY`/`WORKSPACE_DENY`, `PermissionMode.tools`, `validate_allowed_tools`, `ToolDenial`/`parse_denials`, `DenialRetry`, `.tools` and `.access`. Everyone reaching this cog is already trusted, so a mode is a convenience, not a security boundary.
    - The denial-retry flow went with it; it only existed because `claude -p` could not ask.
- Fixed `balance_markup` counting a carried code fence twice, inverting every chunk after the first. Both counters now start closed and the scan discovers state from the body.
- Changed dot commands to fall through to the CLI, replacing the fixed `PASSTHROUGH_COMMANDS` allowlist.

## A turn can be stopped by pressing the thing on screen, queued or not

# extensions/claude.py
- Added a Cancel button on the status message every turn posts. `RunControls` rides on the message the animation is already writing to.
    - `KumaAnimation` writes `content` alone and a partial edit leaves components untouched, so the button survives every frame. `run_and_post` removes it in a `finally`.
    - Only the session owner may press; a press after the turn ended answers ephemerally.
- Added `cancel_run()` and `_tasks`, the task driving each session's turn by thread ID. Both `.cancel` and the button call it.
- Fixed a queued turn being uncancellable and lying about it. `run_and_post` posts the status and starts the spinner before `run_claude` takes the per-user lock, so `.cancel` found nothing in `_running` and the turn ran anyway. Cancelling the task rather than the subprocess makes queued and running one case.
    - Removed `_cancelled`; `CancelledError` already carries the distinction.
    - Moved `process.kill()` into `run_claude`'s `finally`. A cancel arrives as an exception at whatever line the run is on, so there is no `return` to hang the kill off. The timeout path falls through the same `finally`, so it no longer kills twice.
    - `cog_unload` cancels tasks before killing processes; the thread- and forum-delete listeners cancel too.
- Changed a cancelled turn to keep its tool log, since it has no answer coming. The trailing pending entry is marked to stop reading as still running, and the closing line says anything already written to the workspace is still there.
- Changed `.status` to say whether a turn is running or queued behind another of your sessions.
- Added log lines for run and permission events that previously left no trace: `cancel_run()`, `run_claude()` on timeout and on an exit with no result, `limit_error()`, `retry_with_tools()` / `.tools`, and `apply_mode()` for `bypass` only.

## A mention reaches the run as a name

# extensions/claude.py
- Changed `handle_prompt()` to build the prompt from `Message.clean_content` instead of `Message.content`, so a mention arrives as a name rather than a snowflake.
    - Roles and channels come through the same way; an unresolvable name falls back to `@deleted-user`.
    - Markdown and URLs are untouched, so `resolve_prompt_links()` and the chunker see the string they always did.
    - `clean_content` ends in `escape_mentions()`, so an echoed prompt can never carry a live mass ping back out.
    - The mention-only guard still reads raw `content`, since `clean_content` renders a bare ping as text.

## `.ignore` mutes a post without closing it

# extensions/claude.py
- Added `.ignore` (`.mute`) and `dot_ignore()`. Bare, it flips the setting; `on` and `off` set it outright. For talking about a session in its own thread without every message costing a turn.
    - Added `SessionState.ignoring` and its guard at the top of `handle_prompt()`, so an edit made while muted is dropped as well.
    - Commands are never ignored, and the drop is silent.
    - `.status` grows a muted line while it is on.
    - Does not survive a restart, deliberately: a reboot should not leave a post mute with no sign of why.

## A bare ping stops burning a turn

# extensions/claude.py
- Added `MENTION` and a guard in `session_message_listener()` dropping a message with nothing left once mentions are removed. Our own mention was already handled by `is_command_invocation()`, but a ping aimed at anyone else ran as the next turn.
    - Only an empty remainder is dropped; a ping carrying an attachment is still a prompt.
    - `on_message_edit` is untouched; editing a message down to a bare ping still re-runs it.

## The status message stops unfurling what the run fetched

# utils/animation.py
- Changed `KumaAnimation._edit()` to pass `suppress=True` on every edit. A tool log line for `WebFetch` or `WebSearch` carries the raw URL, which Discord unfurled into a link embed parked at the bottom of the message and re-rendered every frame.
    - `discord.Interaction.edit_original_response()` has no equivalent argument, so an interaction target has to be sent suppressed; the flag then rides along on later edits.
    - `Message.edit()` spells it `suppress`, `Messageable.send()` spells it `suppress_embeds`.

# extensions/claude.py
- Added a `suppress_embeds` argument to `send_to_thread()`, passed for the activity message in `run_and_post()`. Kept opt-in, since a posted answer that links something should still unfurl it.
- Added `repair_code_escapes()`, rewriting an inline code span that quotes a backtick the GitHub way into the double-tick span Discord understands. Discord has no escapes inside code, so the span closed on the escaped tick.
    - Called from `chunk_text()` behind `REPAIR_CODE_ESCAPES`; setting it `False` hands Discord the text exactly as written.
    - Runs before the split, so a widened span is measured at the width it will be sent at.
    - Added `_widen_code_span()` and `ESCAPED_CODE_SPAN`. The pattern's content alternation cannot swallow a bare tick, which is what stops the lazy match ending the span on the quoted tick.
    - A span whose content starts or ends with a backtick is padded with a space, or the rewrite emits three ticks and Discord reads a fence.
    - Fenced blocks are skipped, and escapes outside a span are left alone.

## A repeated tool call counts up instead of stacking

# extensions/claude.py
- Changed `on_progress()` to compare the call against the one the trailing log line stands for, re-writing it with an `x2`, `x3` tail rather than appending an identical entry.
    - The comparison is on the rendered tool and target string, so the same tool against a different file is not a repeat.
    - A counted line stays pending; the newest repeat is the one still running.
    - Worth having because the log only shows the last `TOOL_LOG_VISIBLE` calls.

## Hints: say it three times, then stop

# extensions/hints.py
- Added a cog that shows a piece of advice a few times and then retires it. A cog offers hints by declaring `__hints__ = (Hint(...),)`; the registry is walked from the loaded cogs on each call, so a hint appears when its cog loads and leaves with it.
- Two render styles, both inside a blockquote. `INLINE` is the default and stays readable with buttons under it; `BLOCK` is opt-in per hint via `Hint.style`.
    - A markdown heading was tried and dropped; `###` renders at the same size as a real message header.
    - The footer sits outside the quote, and on the last showing swaps to "Last time you'll see this one".
- Got it dismisses; Learn more renders only when `Hint.url` is set; Remind me later appears only on the final showing and grants exactly one more.
    - Remind me later raises `retire_at` and must never decrement `seen`. `seen` is a fact, `retire_at` a preference; folding one into the other makes a snoozed view indistinguishable from a real one and corrupts the counts.
    - A global kill switch was deliberately left off the hint.
- Added `/hints list`, a Components V2 panel with a `Section` per hint, its on/off button as the accessory, grouped counts, and the panel-wide actions outside the container.
    - Re-enabling a hint also zeroes `seen`, or turning a retired hint back on does nothing visible.
    - `HINTS_PER_PAGE` is 8 because a full page measures 35 of the 40-component cap. Nine is 38, ten is 41.
    - Deliberately not persistent; its custom IDs encode the hint key, so they cannot be pre-registered.
- Added `HintButton`, routing a press back to the view that built it. Both views are built imperatively, so there is no fixed layout to declare with `@discord.ui.button`.
- `send()` is safe to call unconditionally: a dismissed, retired or unregistered hint is a no-op and a Discord failure is swallowed.
- No global `users` table. A snowflake is already a stable 64-bit natural key, so a surrogate would add an ordering rule to every write and a JOIN to every read.
    - One argument made for this during design was wrong and should not be re-quoted: `asqlite` does set `pragma foreign_keys=ON` per connection.

# extensions/preferences.py
- Added `/preferences view|set|reset` and the `user_settings` table, open to everyone and usable in DMs.
- The split from `moderator`'s `settings` is structural. Discord applies `default_permissions` per top-level command and it cannot vary by subcommand, so guild settings and user settings cannot live under one command. The rule: `settings` is about a place, `preferences` is about a person.
- Two settings to start: `hints_enabled` and `hint_style_block`.
- Added `PreferencesPanel` and `PreferenceButton`, the same shape as `/hints list`. Every command replies with the panel.
    - Removed `UserSettingsEmbed`; a Components V2 message cannot carry `content` or `embeds`.
    - Rows are built from the row the database hands back, minus `settings_excluded_keys`, so a new column shows up with a working toggle on its own.
    - Added `reset_user_settings()`, shared by the button and `/preferences reset`. It deletes the row and lets `get_user_settings()` re-create it at whatever defaults it declares.
    - A toggle sends its column name back through the custom ID, so `set_user_settings()`'s allowlist is what stands between the panel and the SQL string.
    - The panel's timeout is `message_timeout`, the same value the reply is sent with as `delete_after`.
- Follows `moderator`'s `get_`/`set_` shape including the `_USER_SETTING_COLUMNS` allowlist. Verified a setting name carrying `; DROP TABLE …` raises `ValueError` before reaching the database.
    - The default-row insert is `ON CONFLICT (userid) DO NOTHING`, or two commands racing on a first use would have the loser raise.
    - Added `enabled()`, which answers with the column default rather than raising, so a preference lookup is never why another cog's command fails.
- `HintsCog.preference()` reaches it through `get_cog` rather than an import, so the two stay independently reloadable. Verified by unloading it mid-test.

# extensions/moderator.py
- Fixed the `settings` group being declared without `invoke_without_command=True`. Confirmed against discord.py's source: `ext/commands/core.py:1659` sets `early_invoke = not self.invoke_without_command`, so the parent callback ran at line 1673 and the subcommand at 1680. Slash invocation was unaffected, which is why it went unnoticed.
- Removed `_mod_settings_choices()`, now a duplicate of `KumaCog.settings_choices()`. Verified to produce an identical choice list.
- Added `ModeratorSettingsPanel` and `SettingButton`, the same panel `preferences` got. The header carries the guild's icon where `preferences` carries a user's avatar.
    - Removed `ModeratorSettingsEmbed`.
    - Added `settings view` and `settings reset`, so both halves of the split read `view|set|reset`.
    - Fixed the group callback building its embed from `get_guild()`, the owner's guild, while reading settings from `interaction.guild`.
    - Fixed `get_mod_settings()` inserting defaults for an unseen guild and then falling out of the `try` returning `None`, so a guild's first `/settings` answered with an error and only the second showed anything.
    - A guild is not required to have an icon, so the header drops to a plain `TextDisplay` rather than a `Section` whose accessory has gone missing.
    - Added `MOD_SETTINGS_EXCLUDED`, shared by the panel and the `settings set` choices.
    - Only the admin who ran the command may press anything. Deliberately not persistent.

# utils/cog.py
- Added `KumaCog.settings_choices()`, generalised from `_mod_settings_choices()`, plus `settings_excluded_keys` and `user_id_column`.
    - A `staticmethod` by necessity: choices are handed to `@app_commands.choices(...)`, which runs in the class body at import time, long before any cog instance exists.

## A session's identity is mirrored to disk, so losing Discord doesn't lose the transcript

# extensions/claude.py
- Added `session.json`, a sidecar written into each session workspace alongside the state line. The opening post stays the source of truth; this buys findability, since the CLI's transcripts are a flat directory of bare UUIDs with nothing tying one to a Discord thread.
    - Added `write_session_index()`, `read_session_index()`, `session_index_path()` and the `SessionIndex` TypedDict. Written from `update_panel()` and from `get_state()`.
    - Written before the panel edit. An index naming a session Discord failed to re-render is harmless; a successful edit we failed to mirror is the gap this closes.
    - `SessionIndex.lineage` records superseded IDs, most recent first, capped at `SESSION_LINEAGE_LIMIT`. `.new` and `.restore` mint a fresh ID onto the same thread, orphaning the previous transcript. Verified idempotent.
    - Staged and `Path.replace`d rather than written in place, so a crash mid-write leaves the previous index intact.
    - Added `find_session_index()`, answering "which CLI session was thread N" straight off the filesystem. Globs `<sessions>/*/<thread_id>` rather than walking every user.
    - A corrupt or hand-edited index degrades to `None` with a warning and never raises.

## The CLI is found by path, not by inherited PATH

# extensions/claude.py
- Fixed every run failing with "`claude` was not found on PATH" on a bot that has Claude Code installed. The bare name resolves against whatever PATH the bot inherited, and the CLI installs to `~/.local/bin`, which is on an interactive shell's PATH via `.profile` but not on the bare system PATH a detached process gets.
    - Added `claude_binary()`: `shutil.which()` first, then `CLAUDE_FALLBACK_PATHS`. Checked with `os.access(X_OK)` rather than `is_file()`, since those launchers are symlinks into a versions directory the installer prunes.
    - Resolved per call, not at import, since the CLI updates in place.
    - `build_command()` uses the resolved absolute path, falling back to the bare name.
- Fixed the `FileNotFoundError` handler blaming the CLI for both causes. `create_subprocess_exec` raises it for a missing `cwd` just as readily, so a deleted project directory reported itself as a missing install.

## The session panel reads as a settings sheet

# extensions/claude.py
- Changed the panel's three selects to each carry a heading and, in small text under it, what the currently selected option does. A closed panel previously could not tell you what mode the session was in.
    - Added `SessionPanel.add_control()`, rendering one heading, summary and select from a list of `PanelChoice`. `_model_choices()`, `_mode_choices()` and `_effort_choices()` are the adapters, replacing three near-identical blocks.
    - Added `SessionPanel.add_header()` and `add_footer()`.
    - Added `_model_name()`, the inverse of `MODELS`.
- Changed the header to a `discord.ui.Section` so it can carry an accessory. A section takes exactly one, and it must be a button or a thumbnail, never a select — which is why the controls get headings above them rather than beside them.
    - A dormant panel puts Restore Session there; it was previously below four rows of greyed-out controls.
- Added `KumaEmojiTable.to_cdn_url()` in `utils/cog.py`. The panel re-renders on every select change and `Message.edit` replaces the whole attachment list, so an `attachment://` thumbnail would re-upload artwork on every edit.
- Moved the four action buttons out of the container, and the state line and transcript below their own divider.
- Fixed the dormant disable pass walking a hardcoded tuple of four action rows, which no longer reached the section accessory or the view's own actions. `SessionPanel.disable_controls()` walks with `walk_children()`, exempting the restore button by custom ID.
- Added `SessionPanel.warn_if_oversized()`. Discord answers a view over either Components V2 ceiling with a 400, which for this panel means the post never sends. A full panel measures 23 components and ~530 characters.
- Changed session state recovery to address the state line by numeric component ID rather than flattening every text display and pattern-matching each string.
    - Added `PANEL_HEADER_COMPONENT_ID`, `PANEL_STATE_COMPONENT_ID`, `PANEL_TRANSCRIPT_COMPONENT_ID`, and `_find_text()`.
    - Removed `_walk_text()`, `STATE_LINE_SEPARATOR` and `OWNER_PATTERN`. `STATE_LINE_PATTERN` is now a plain transcription of `STATE_LINE_FORMAT`.
    - Panels posted before this change carry no component IDs and will not be read back. Their threads and files are untouched; the sessions are not resumable.
- `send_to_thread()` accepts `Union[discord.ui.View, discord.ui.LayoutView]`; `LayoutView` does not subclass `View`.

# utils/animation.py
- Documented that `KumaAnimation` cannot drive a Components V2 message. It animates by editing `content`, and those messages carry components instead.

## The panel names its owner instead of printing an ID

# extensions/claude.py
- Changed the state line's `owner` field to render as a mention rather than a bare ID in backticks. `SessionState` only holds the ID, so no member lookup is involved.
- Added `OWNER_PATTERN`, accepting the mention and the old backticked ID, since every post written before this carries the old form and the panel is a session's only record.
    - An existing session's panel gains the mention the first time it re-renders, which notifies the owner once.

## `/claude ask` cleans up after itself

# extensions/claude.py
- Added `edit_and_expire()`, editing the deferred ephemeral response and removing it after `message_timeout` seconds. `edit_original_response()` has no `delete_after`, so it hands off to `Message.delete(delay=)`.
- `/claude ask` uses it for all three replies: the two refusals and the Session opened pointer.

## A panel we can't read is no longer treated as a panel that's gone

# extensions/claude.py
- Fixed `fetch_panel()` catching `(discord.NotFound, discord.HTTPException)` and returning `None` for both. `NotFound` subclasses `HTTPException`, so a 5xx, a gateway timeout or an exhausted rate limit was indistinguishable from a deleted post.
    - Added `PanelLookup`, the `(message, gone)` pair `fetch_panel()` returns. `gone` is `True` only on a 404.
    - A forum post's opening message and the post share an ID and cannot outlive each other, so `gone` is all but unreachable; the transient branch is the one that fires.
- Fixed replying during a blip telling the user their session panel was missing and to start a fresh one. The listener now separates unreadable, confirmed-gone and no-state-line.
- Fixed `update_panel()` swallowing both a failed fetch and a failed `Message.edit`, so `.model`, `.mode`, `.effort` and `.new` reported a change and dropped the written copy. It returns `bool` now; the four callers append `stale_panel_note()`.
- Fixed `expire_session()` re-rendering the panel, ignoring the result and locking the post regardless, leaving a dormant session showing a live panel behind a lock only Manage Threads can undo.

## Only the bot's own posts count as sessions

# extensions/claude.py
- Added `is_bot_post()`. A forum post's opening message belongs to whoever created it, so a post we did not open can never hold a panel we can write to. `is_session_thread()` went purely by location.
- Fixed a hand-made post consuming one of the author's `MAX_SESSIONS_PER_USER` slots. `active_threads()` counted every non-dormant thread, so five stray posts locked the user out with no session open.
- Fixed replying in a stateless post answering that the opening post could not be found. The two causes are now separated.
- Added deletion of posts in a session forum the bot did not open. `discard_foreign_post()` DMs the author first with what was removed, why, and their own text copied back. A closed DM never blocks the delete.
    - Three entry points: `on_thread_create`, the message listener for one whose first message beat the create event, and `discard_foreign_posts()` in `cleanup_loop` for those made while offline.
- `/claude list` lists only our posts.
- Added `session_forums()`, replacing three hand-rolled `guild.forums` walks.

## Closing a session no longer reads as an expiry

# extensions/claude.py
- Fixed the in-thread `.close` command retiring sessions as `EXPIRED` while the panel's Close button passed `CLOSED`, so the same intent produced two different posts. `.close` claimed the transcript was gone when `expire_session()` snapshots it on the way out.
- Changed `expire_session()` to pick the archive flag from the reason. Both statuses lock the post, so Restore Session stays the only way back in; expiry archives on top so aged-out sessions drop out of the forum listing.
    - `active_threads()` filters on `dormant` before `archived`, so a closed post left unarchived is not retired a second time.

## Comment and docstring pass over the session cog

# extensions/claude.py
- Documentation review across the file; no behaviour changed by these:
    - `SessionState`'s restart note claimed `system_prompt`, `allowed_tools` and `tools_explicit` were all rebuilt from the mode. Only `allowed_tools` is.
    - `PermissionMode`'s note cited the `--allowed-tools` measurement without saying it is why presets go out as `--settings`.
    - `parse_reset_time()` described a missing year being filled in from today; the CLI never prints a year at all.
    - `_search_message()`'s probe order was missing two of its five candidate groups.
    - `placeholder_title()`'s example rendered the wrong separator character.
    - `on_progress()` gained a docstring.
- Removed `EXTENSIONS_ROOT` and `CLAUDE_ICON`, carry-overs from the embed-based cog with no reader since `ClaudeEmbed` went.
- Fixed `.status` dating a session's expiry from `started` while the age-out sweep measures idleness from the last message. It now reads `last_active(thread)`.
- `.status` also reports the effort level.

## Permission modes carry tool presets, and denied tools are offered a retry

# extensions/claude.py
- Changed `MODES` to a `dict[str, PermissionMode]`. Each mode carries its CLI value, its select description and an optional set of pre-granted permission rules. `_mode_description()`'s parallel dict is gone.
    - Rules go out as `--settings`, not `--allowed-tools`. Measured against `claude 2.1.220`: a run given `--allowed-tools Read,Edit,Write,Grep,Glob` ran Bash regardless, so that flag grants but cannot confine.
    - Only `allow` is ever written. A `deny` rule removes the tool outright, so the run produces no `permission_denials` and the retry offer goes blind.
    - Presets are thin. Read-only shell is auto-approved by the CLI's sandbox whatever the rules say. Only `edits` carries any, the `ruff check`/`ruff format` pair, listed in both bare and `.venv/bin/` forms since these are prefix rules over the literal command string.
    - Added `manual` to the offered modes; `auto` and `dontAsk` are left out as their behaviour is not documented well enough to describe honestly.
    - `build_command()` gained a warning that `.claude/settings.json` does not apply to `claude -p` runs.
    - Only bare tool names and `Bash(cmd:*)` prefix rules are sent inline. Path-scoped file rules were measured not to work as inline JSON in any form tried.
    - Added `_mode()` and `_mode_name()`.
- Added `SessionState.tools_explicit`. A list set by hand at `.tools` outranks every mode preset. `ClaudeCog.apply_mode()` is the single place the rule is applied.
    - `.tools clear` hands the list back to the mode rather than emptying it.
    - Both `.mode` and the panel say which tool list the switch landed on.
    - `parse_state()` re-applies the mode's preset on restore; a hand-set list does not survive a restart.
- Added denial reporting under the answer with an Allow & Retry button granting the tools for the session and re-running the prompt.
    - This is a retry offer, not an approval prompt, and the CLI forces that: `--permission-mode manual` with `--input-format stream-json` emits no `control_request` and reports in the result event's `permission_denials`.
    - Added `ToolDenial` and `parse_denials()`, de-duplicating by tool and target.
    - `ClaudeResult.denials` is carried on the empty-response path too.
    - The button only offers tools not already granted. Granting a bare tool name grants every use of it, and the message says so.
    - `DenialRetry` is deliberately not persistent; it holds the prompt and tools to grant, neither of which survives a restart. One hour timeout, and it stops itself on first press.
- `send_to_thread()` takes a `view` and builds its call keyword by keyword instead of branching over every combination.

## Closed vs expired sessions, panel legibility and CLI-style status

# extensions/claude.py
- Fixed the panel's Close button marking the session expired. Both retirement paths went through `expire_session()`, so a deliberate ending was renamed `[EXPIRED]` and claimed 30 days of inactivity.
    - Added `SessionStatus` (`ACTIVE`/`CLOSED`/`EXPIRED`), a `StrEnum` whose `prefix` is the thread-title marker that stores it. Replaces the scattered `startswith(EXPIRED_PREFIX)` checks.
    - `expire_session()` takes the status to retire under. Both stay restorable; the difference is what the post says.
    - The retitle builds off the stripped name, so a post cannot end up carrying both prefixes. `THREAD_TITLE_SIZE` reserves the longer.
    - `/claude sessions` markers split three ways, and `/claude spoof` swaps its `expired` boolean for a `status` choice.
- Changed the panel state line to one key per line below its own separator. Discord renders `-#` per line, so each carries its own marker.
    - `STATE_LINE_PATTERN` accepts either separator, since a panel written before this still has to parse.
- Added a rule between a reply's answer and its footer, drawn literally in small text. `MESSAGE_CHUNK_SIZE` dropped to 1850 to keep it inside the 2000 character limit.
- Anchored the in-run status line below the tool log, so finished calls stack upward and the spinner holds the bottom edge.

# utils/animation.py
- Added `status_last` to `KumaAnimation`, flipping the render order to header, body, status, footer, and flipping the overflow trim with it so the oldest body lines go instead of the live line.

# utils/cog.py
- `KumaCog.animate()` documents `status_last` among the forwarded keyword arguments.

## UnicodeTable additions

# utils/cog.py
- Added `right_hook_arrow` to `UnicodeTable`, available on any cog as `self.unicode.right_hook_arrow`.

## Session thread naming

# extensions/claude.py
- Fixed a promptless session's thread being named with a Discord `<t:...>` timestamp. That markdown only renders inside message content, so every placeholder post was literally titled with the raw tag and they no longer told each other apart.
- Added `locale_timezone()` / `placeholder_title()`, rendering the timestamp as plain text in the opener's own timezone where it can be known. Discord has no timezone field, so the interaction locale is used and trusted only when a country's zones currently agree on one UTC offset. The abbreviation is always in the title.

## Kuma.bash modes and graceful shutdown

# Kuma.bash
- Added `-live` / `-dev` mode flags, which stop whatever is running and start fresh in that mode. A bare invocation keeps the keep-alive behaviour, or a periodic crontab entry would bounce the bot every tick. Added `-stop`, `-status` and `-h`.
- Records the mode next to the pid, so a bare restart after a crash comes back in the mode it was last run in.
- Fixed the pid check trusting the pid file alone. `ps -p` only proves a process holds that pid, so after a reboot or pid wraparound an unrelated process could inherit it. The pid is confirmed to belong to this service before it is honoured, and before anything is killed.
- Fixed the backgrounded bot inheriting the caller's stderr, so `out=$(./Kuma.bash)` blocked until the bot exited. stderr goes to `logs/kuma_kuma.err`, keeping startup tracebacks recoverable.
- Stops with SIGTERM and a 15s grace period before SIGKILL, and `disown`s the child.
- Hardened against empty, non-numeric and stale pid files, and quoted `SCRIPT_DIR`.

# kuma_kuma.py
- Added argument parsing. `__main__` previously called `main()` with no arguments, so `local_dev` was unreachable and any flag was silently ignored. `--dev` disables Sentry, `--live` is the default, and `--log-level` is settable.
- Added a SIGTERM handler raising `KeyboardInterrupt`, reusing the interrupt path `__main__` already suppresses. Python's default handler kills the process outright, so `Bot.close()` and every `cog_unload` were skipped — which is what kills in-flight `claude` subprocesses.

## Animated status messages, CLI-style tool output and transcript snapshots

# utils/animation.py
- Added `KumaAnimation`, a self-updating status message driven by a background task, generalising `ffxiv.py`'s `processing_replies` loop. Used as an async context manager, so the task is cancelled on exit even when the body raises.
    - The cadence is dynamic: re-renders as soon as `min_interval` allows on a change, settles to `idle_interval`, then eases toward `max_interval` after `decay_after`. A 429 widens the cadence; a deleted target stops the loop.
    - `min_interval` is clamped to a 1.0s floor.
    - Targets a `Message`, `WebhookMessage`, `InteractionMessage` or `Interaction`.
- Added `AnimationStyle` and the `AnimationFrames` presets `BRAILLE`, `DOTS`, `PULSE`, `CLOCK`, plus `AnimationFrames.toggle(*frames)`.

# utils/cog.py
- Added `KumaCog.animate()`, a factory returning a `KumaAnimation` bound to a target.
- Added `KumaCog.to_progressive()`, inflecting only a label's first word and restoring the caller's leading capital.

# utils/__init__.py
- Exported the animation module.

# extensions/claude.py
- Fixed a session whose opening run failed being permanently unusable. `resume` was hard-coded `True` on every path except `/claude ask`, so a failed opening run left an ID the CLI never registered, and `.new` did not recover it either.
    - `run_and_post` derives it from `session_is_known()`, so the flag is gone from every call site. Self-correcting and survives a restart.
- Added an optional `model` to `/claude ask`, sharing the short-name vocabulary with `.model`. The opening prompt was previously the only one that could not choose a model.
- Added an optional `prompt` to `/claude ask`. Omitting it opens the post with a live panel and runs nothing, so model and mode can be set before the first message. An upload given without a prompt is staged into the workspace.
    - A promptless post renames itself from its first message, guarded on the placeholder so a `.rename` is never overwritten.
- Added optional transcript attachment behind `[CLAUDE] attach_transcripts`, uploading a snapshot onto the opening post as a Components V2 file component. `.restore` falls back to downloading it when the local snapshot is gone.
    - Off by default. A transcript holds everything Claude read, so enabling it moves file contents and any credential it opened onto Discord's CDN.
    - `Message.edit` keeps attachments it is not told about, so later panel edits re-reference the same upload; the view must keep pointing at it or the component silently disappears.
    - Matched by exact filename, which encodes the session ID, so `.new` does not re-offer the previous generation's upload.
    - Guarded against the guild's `filesize_limit`.
- Replaced the ini reader with a `ClaudeSettings` dataclass covering the whole `[CLAUDE]` section, so a typo in one option no longer takes `bypass_user_ids` down with it.
- Replaced the single-line status with a live CLI-style tool log accumulating one line per tool call, collapsing to a one-line summary once the answer arrives.
    - Tool results are deliberately never shown; they are roughly a third of a transcript by volume and routinely contain whole files.
    - `run_claude`'s progress callback reports `(tool name, target)`; `ClaudeResult` carries `tool_calls` and `duration`.
    - Paths are shown relative to the session root.
- Added transcript snapshots. Claude Code prunes its own log on `cleanupPeriodDays`, the same 30-day window sessions expire on, so an expired session previously lost its history for good.
    - The cleanup sweep gzips a transcript once idle for 24 hours, re-taking it only when the source changed, with a backstop at expiry.
    - Snapshots are named per session ID, not per workspace; `.new` rotates the ID while keeping the thread, and a shared filename would let `.restore` resurrect the conversation `.new` was asked to forget.
- Added session restore: a `.restore` dot command and a Restore button, the one live control on an expired panel since the thread is locked. Restoring rewrites the transcript before unlocking.
- Renamed `expire_stale_sessions` to `sweep_sessions`, doing the snapshot and expiry passes over one walk.

## Claude sessions as forum posts

# extensions/claude.py
- Rewritten. A session is a forum post rather than a paginated embed: one private forum per user inside a `Claude Sessions` category, created in whichever guild `/claude ask` was run in.
    - `/claude ask` opens the post and runs the opening prompt; every subsequent message continues the session. `/claude sessions` lists them, `/claude spoof` renders the layouts with canned data.
    - Removed the active session pointer, `/claude plan`, `/claude history`, the reply modal, the Yes/No question heuristic and the git working-tree diff field.
- Dropped all SQL. The post's opening message is the only record of a session, so its Components V2 panel carries a small-text state line re-parsed on restart.
    - The owner is written into that line rather than read from `Thread.owner_id`, since the bot creates the posts.
- Added a persistent Components V2 session panel with static `custom_id`s registered through `bot.add_view`.
- Added in-thread `.commands` resolved by unambiguous prefix. These are mapped to CLI flags rather than forwarded, because `claude -p` is non-interactive and silently ignores real slash commands.
- Added `on_message_edit` handling that re-runs a corrected prompt, guarded on an actual content change so embed resolution and pins do not trigger a re-run.
- Added lifecycle management: 5 active sessions per user, 30 days idle marks a post `[EXPIRED]` and locks/archives it, delete listeners remove workspaces, and a 6-hourly loop sweeps ones orphaned while offline.
- Moved workspaces to `<root>/.claude_sessions/<user_id>/<thread_id>/`, with uploads in an `attachments/` subdir the generated-file diff skips.
- Added a project selector gated on `[CLAUDE] bypass_user_ids`, chosen over `--add-dir` so a session keeps exactly one containment boundary.
    - `prepare_workspace` drops a `.gitignore` of `*` at the top of each `.claude_sessions/` so the directory ignores itself.
- Added a permission pre-flight (`missing_permissions`) so a shortfall is one clear message instead of a `Forbidden` part way through creating a category, forum and thread.
- Security: `.mode bypass` is gated on an ini allowlist; `.tools` and `.system` are pattern- and length-validated; `.get` re-resolves its path and requires it to stay inside the session workspace; `--add-dir` is deliberately not implemented.

# extensions/_claude_old.py
- The previous embed-based cog, renamed. The leading `_` excludes it from `discover_extensions()`.

## Claude session workspaces

# .gitignore
- Ignored any `*.claude*` path, covering the per-user `.claude_asks/` and `.claude_attachments/` workspaces under `extensions/`.

## Command Tree logging, resilient requests and a fully re-worked Embed/View core

# .gitignore
- Ignored the `.claude_attachments/` directory.

# kuma_kuma.py
- Added a `_DiscordReconnectFilter` logging filter downgrading discord.py "Attempting a reconnect" `ERROR` records to `WARNING` so Sentry ignores them, attached to the `discord.client` logger during `LogHandler` setup.
- Re-enabled `KumaCommandTree.on_error`; simplified the location/channel handling and now DMs the owner silently instead of using a webhook.
- Added `KumaCommandTree.interaction_check` to log who invoked which cog and command.
- Moved `_mention_app_commands` initialization into `KumaCommandTree.__init__`.
- Registered `KumaCommandTree` as the bot's `tree_cls`.

# pyproject.toml
- Moved `select`/`ignore` under `[tool.ruff.lint]`.
- Ignored `ASYNC240`, `COM812`, `D203` and `D213` to resolve formatter and rule conflicts.

# requirements.txt
- Added `imagehash`.

# utils/cog.py
- Documented `UnicodeTable` with a full attribute reference docstring.
- Added `inbox_tray`, `loud_speaker`, `no_entry`, `warning_sign` and `speed_bubble` Unicode attributes.
- Reworked `KumaCog.get_request` to wrap the whole request in a `try`, catch only `TimeoutError` and `aiohttp.ClientError`, and log a warning instead of raising `RuntimeError`.

# utils/embeds.py
- Overhauled `KumaEmbed`:
    - Converted `footer_icon`, `thumbnail_icon`, `avatar_icon` and `field_image` into property/setter pairs backed by private attributes; setters auto-name the `discord.File` for inline attachments and accept `URL` or `None`.
    - Rebuilt the `attachments` property to collect only `discord.File` icons and skip deleted or unset ones.
    - Replaced the `info` init parameter with a `defaults` flag applying the default author, footer, thumbnail and banner image.
    - Added `set_image`, `set_author` and `set_thumbnail` overrides, and extended `set_footer` to accept a `discord.File`.

# utils/ui.py
- Removed the old `GenericView`; added a generic `KumaView[V: KumaCog]` base view.
    - Built-in Reset, Previous and Next pagination buttons with `embeds` paging and index clamping.
    - `reset_view`, `add_item`/`remove_item` overrides tracking a `components` list, and a 25-item cap safeguard.
    - Added `ViewParams` and `ViewParamsPartial` TypedDicts.
- Updated `GenericButton` and ownership checks to use `KumaView`/`owner`.

# utils/embed_paginator.py
- Removed; pagination now lives in `KumaView`.

# resources/numpy_templates/numpy_overwrite.mustache
- Added an `Attributes` section and tidied section spacing.
