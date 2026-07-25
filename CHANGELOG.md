# Changelog

## Command Tree logging, resilient requests and a fully re-worked Embed/View core

#### .gitignore
- Ignored the `.claude_attachments/` directory.

#### kuma_kuma.py
- Added a `_DiscordReconnectFilter` logging filter that downgrades discord.py "Attempting a reconnect" `ERROR` records to `WARNING` so Sentry ignores them.
	- Attached the filter to the `discord.client` logger during `LogHandler` setup.
- Re-enabled `KumaCommandTree.on_error`; simplified the location/channel handling and now DM's the owner silently instead of using a webhook.
- Added `KumaCommandTree.interaction_check` to log who invoked which cog/command.
- Moved `_mention_app_commands` initialization into `KumaCommandTree.__init__`.
- Registered `KumaCommandTree` as the bot's `tree_cls`.
- Cleaned up import list (`LF`, `Generic` no longer imported) and minor formatting/`ruff` passes throughout.

#### pyproject.toml
- Moved `select`/`ignore` under `[tool.ruff.lint]`.
- Ignored `ASYNC240`, `COM812`, `D203` and `D213` to resolve formatter/rule conflicts.

#### requirements.txt
- Added `imagehash`.

#### utils/cog.py
- Documented `UnicodeTable` with a full attribute reference docstring.
- Added `inbox_tray`, `loud_speaker`, `no_entry`, `warning_sign` and `speed_bubble` Unicode attributes.
- Reworked `KumaCog.get_request` to wrap the whole request in a `try`, catch only `TimeoutError`/`aiohttp.ClientError`, and log a warning instead of raising `RuntimeError`.

#### utils/embeds.py
- Massive overhaul of `KumaEmbed`:
	- Converted `footer_icon`, `thumbnail_icon`, `avatar_icon` and `field_image` into property/setter pairs backed by private attributes; setters auto-name the `discord.File` for inline attachments and accept `URL`/`None`.
	- Rebuilt the `attachments` property to safely collect only `discord.File` icons and skip deleted/unset ones.
	- Replaced the `info` init parameter with a `defaults` flag that applies the default author, footer, thumbnail and banner image.
	- Added `set_image`, `set_author` and `set_thumbnail` overrides (with `img`/`url` handling), and extended `set_footer` to accept a `discord.File`.

#### utils/ui.py
- Removed the old `GenericView`; added a generic `KumaView[V: KumaCog]` base view.
	- Built-in "Reset", "Previous" and "Next" pagination buttons with `embeds` paging and index clamping.
	- `reset_view`, `add_item`/`remove_item` overrides that track a `components` list, and a 25-item cap safeguard.
	- Added `ViewParams` and `ViewParamsPartial` TypedDicts.
- Updated `GenericButton` and ownership checks to use `KumaView`/`owner`.
- Exported `GenericButton` and `KumaView` in `__all__`.

#### utils/embed_paginator.py
- ⛔ Removed; pagination now lives in `KumaView`.

#### resources/numpy_templates/numpy_overwrite.mustache
- Added an `Attributes` section and tidied section spacing.
