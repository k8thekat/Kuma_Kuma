# TODO

## General
- Work on readme for cogs/etc.
- Add Helper replies to `support-forums` or any forum.
- Online Member Count for Discord Guild via locked channel and edit channel name to current count. Use emojis for channel names.
- Double check sqlite connection pool and or other sqlite connections.
- Automate handling Sentry IO issues and write out a proposed POA for a fix.
    - Create GH issues? Show "traffic/usage".
    - Needs the Sentry API token/scope decided first.
- Decide whether the no-abbreviations rule applies repo-wide. `conn` and `row` are kept as established pattern.
- Pick one docstring mood convention (`"Get the…"` vs `"Set the…"` vs `"Returns…"`) before the next big pass.
- `moderator.clear` and `moderator.who_is` carry dead `@app_commands.default_permissions` — make them hybrid or gate with a real check.
- Decide whether `extensions/private/` should be held to `ruff` standard or excluded explicitly.

## Modules

### Claude (`kuma_claude/`)
- Verify cleanup against Discord: expired post rename, hand-made post removal + DM, orphan workspace grace window.
- Inline transcript costs extra message creates per text block; `interject(reopen=False)` trades spinner continuity for one fewer message if 429s show up.
- Revisit the prompt-view structure: one builder for container, budget and component count; settled form as the same view with actions swapped. Fold `SessionPanel` into the same builder.
    - CV2 enforces limits at **construction**, so a builder catches oversized views before they raise inside an event handler.
- Fix `.claude/settings.json` Bash rules — written `Bash(python*)`, missing the `:*` for prefix matching. See NOTES.md for the recommended set and measured findings.
- Confirm what `auto`, `dontAsk` and `manual` modes do before relying on them.
- `thinking` events dropped by `on_cli_event` — could drive a progress line via `system/thinking_tokens`.
- Tune `IDLE_REAP_MINUTES` (30) and verify `USAGE_BLOCK_PERCENT` once there is real usage / an exhausted account.
- Read model-specific usage limits — only `five_hour` and `seven_day` are checked; `seven_day_opus`, `seven_day_sonnet` and `extra_usage` are all `null` so far.
- `.help` could list the CLI's 45 slash commands or offer them as autocomplete.
- Capture `/effort` reply so a rejected level is reported back instead of the panel claiming it.
- `panel_files` could render images as a `MediaGallery` instead of a filename list.
- Move pre-existing research folders in `extensions/.claude_asks/` under a `_shared/` folder.
- A bare message ID probes channels one at a time, capped at `MESSAGE_SEARCH_LIMIT` (40). Pasting the full link is always cheaper.

#### Claude — shelved variants (`.archive/`)
- Decide what comes forward from `.archive/_claude.py` — access tiers, cwd-boundary denial handling, `tools_explicit`, `/claude spoof`. See NOTES.md for the measured findings on each.

### KumaAnimation (`utils/animation.py`)
- Migrate `ffxiv.py` off its own `processing_replies` loop onto `self.animate(...)` — would drop the shared `reply_messages` list, `reply_flag` toggle and `_reply_lock`.

### BaseView / paging (`extensions/ffxiv.py`)
- `CurrencyView.reset_view` neither calls `super()` nor resets `indx` — override looks deliberately trimmed (commented-out `super()`). Needs a decision.
    - `UserView`, `RecipeView` and `FishingView` also skip `super()` but don't page.
- `BaseView` does not inherit `KumaView` — every fix has to be applied twice. Decide whether the fork can be collapsed.
- Footers written unconditionally at `ffxiv.py:1730`/`:1758` — latent only, no caller builds its own footer today. Port `page_embed()` if one ever does.

### UnicodeTable (`utils/cog.py`)
- `__Current Attributes__` docstring block is a hand-maintained mirror — every new character has to be written twice.

### Hints (`extensions/hints.py`)
- `HINTS_PER_PAGE` is 8 — nine items hit 38 of the 40-component CV2 cap.
- No cog declares `__hints__` yet. First planned: on `/settings`, telling admins their options live under `/preferences`.

### Preferences (`extensions/preferences.py`)
- `PreferencesPanel` is CV2 — replies cannot carry `content` or `embeds`.
- A choice preference costs three components to a switch's two; past ~5 choices the panel needs paging.
- `/preferences set` only offers switches; cog-declared choices are panel-only (`KumaCog.settings_choices` resolves at import time).
- Two kinds of preference:
    - Core **switch**: column on `USER_SETTINGS_SETUP_SQL` + entry in `USER_SETTING_DEFAULTS`.
    - Cog **choice**: a `Preference` on the cog's `__preferences__`, read with `Preferences.value()`.

### Housing (`extensions/private/housing.py`)
- Untested against Discord — thread-swap fix is the one that matters.
- `?parse` is a no-op on an already-rebuilt thread; should say so when there's nothing to re-parse.
- Forum matched by name (`house-hunting`) and family role ID is a class attribute — move to settings table.
- The `?` commands are an if-chain; docstring already calls for a mapping.

### AutoMod (`extensions/automod.py`)
- **The cog never kicks.** Docstring and `/mention_spam create` promise "established members are kicked" but `on_mention_spam` only bans new members and does nothing for everyone else. Fix is written up and ready to apply.
- `new_member_role_ids` declared, read by `_is_new_member`, never populated — "has a new-member role" check is dead.
- `_track_rule` adds to in-memory set on a failed insert — rule works until restart, then quietly stops.
- No `on_automod_rule_delete` listener — a Discord-UI deletion leaves a stale row and `tracked_rule_ids` entry.
- `AutoModEmbed` lives in `moderator.py` — move to `utils/embeds.py` if AutoMod needs to report escalations.

### Moderator (`extensions/moderator.py`)
- `guild_settings` on the class is `dict[int, ModeratorSettings]` — type object assigned as value, nothing reads it. Annotate and use, or drop.
- `ModeratorSettingsPanel` is CV2 — replies cannot carry `content` or `embeds`.
- Add a function to start and stop `self.bot.task_loops`.
- Change `settings` command to `moderator_settings`.
- Consider making remaining interactions ephemeral — most already are, but the `settings` group invoke itself is not.

### Gatekeeper (`extensions/gatekeeper.py`)
- Take Announcements channel that @role and pass into the Server Console for each server.
- Notification when server is online.
- Fixed Server list updating in realtime.
    - Fix logger prints when updating "available Instances" (appears ~4 times).

### Reddit Image Crawler (`extensions/reddit.py`)
- Change compare (`on_reaction_compare`) to a slash command.
    - Support `RedditEmbeds`, file attachments and urls.
    - Support message links/IDs.
    - Store last 50/60 Embeds? Use Title for listings?
- See about getting the Subreddit's Banner/Icon for the Embed.
- Components V2 conversion — `RedditPost`, `RedditPagePanel` and `reddit_preview` exist but call sites still send `RedditEmbed`. Three things need fixing together:
    - `on_reaction_compare` reads `embeds[0].image.url` — V2 has no embeds, read `attachments[0].url`.
    - `webhook_send`'s 413 retry mutates embed fields — use `RedditPost.use_remote_media()`.
    - `KumaView` can't drive a `LayoutView` — need `RedditPagedView`.
- `UnicodeTable.em_dash` is `\Ufe31` (vertical form `︱`) — use a literal `—` for bullet lists.
- `check_subreddit` hits the Reddit API via `search_by_name` — expensive, could cache or HEAD `/r/{sub}/about.json`.
- `normalize_subreddit()` only applied by `add_subreddit`; other commands take raw input.

### Sonarr / Radarr (`extensions/sonarr_radarr.py`)
- Try TVDB for movie ID to search Sonarr — Radarr's lookup accepts `imdb:tt…` terms, likely the better bridge.
- Needs `[SONARR]` and/or `[RADARR]` sections in `local.ini` before commands work.
- Untested against a live instance — check `/sonarr status` and `/radarr status` first.
- `a_sonarr_radarr` needs editable install (`pip install -e ~/gitHub/a_sonarr_radarr --no-deps`) before the cog loads.
- Hub carries `queue` messages — live-updating queue panel possible without Refresh.
- Episode-level commands modelled but not exposed — tracked in the wrapper's own `TODO.md`.

### Diablo 4 (`extensions/diablo4.py`)
- Paginated inventory browser with filtering and comparison (currently a flat list, capped at 25).
- In-game item icon resolver — needs a CDN or asset table from d4-cauldron to populate the `Thumbnail`.
- Local LLM pipeline via Ollama — requires a vision-capable model (e.g. llava) with the `/api/generate` `images` parameter.
- `DispatchedTask` keyed by a unique task ID to support multiple concurrent tasks per user.
- Roll quality comparison and best-in-slot tracking across saved items.
- Affix search — find saved items matching a stat filter.

### Google Keep (planned)
- Interface with Google Keep via `gkeepapi` (unofficial, no official REST API).
    - Auth: master token via `keep.login()`/`keep.resume()`, stored securely, reused via `keep.getMasterToken()`.
    - Sync is manual: `keep.sync()` to pull, create/delete + `keep.sync()` to push.
- Commands: create note, append to note, list (filter by label/pinned), delete/archive, checklist support.
- Labels map to command groups or filters.
- `gkeepapi` is sync-only — wrap in `asyncio.to_thread()`.

### Utility (`extensions/utility.py`)
- GitHub issue attachment links expire in ~a day (Discord signs CDN URLs). Making them durable means re-hosting the bytes.
    - Untested against Discord — verified offline only.
- Expand `source` command: resolve arbitrary objects via dotted paths, support multiple matches, present as selectable list.
- Add auto-thread creation for a "set" channel with rulesets (contains images, @mentions, etc.).

### FFXIV (`extensions/ffxiv.py`)
- Store a user's inventory via `Item.id` and `Item.quantity`.
    - Create a `to_dict()` function for an Inventory Item.
- Switch to V2 Components.
- Change most button styles to secondary.
- ConsoleGamesWiki parsing via url searching.
- Convert saved gifs/video into animated Emojis.

#### Item/Recipe
- Show crafting ingredients breakdown entirely.
    - Possibly show first way to obtain.
- Add recipes related to an ingredient via button.

#### Features/Improvements
- Timers for Housing, Journal, etc.
- Add wildcard searching (multi item lookup).
- Add item watch list.
- Add inventory functionality.
- Add collect list for glamour/etc.
- Store a user's recent item lookups (3–4).
