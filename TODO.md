# TODO
- ! Work on readme for cogs/etc.
- Add Helper replies to `support-forums` or any forum.
- Improve help command (Possibly try out Components V2)
- Online Member Count for Discord Guild via locked channel and edit channel name to current count. Use emojis for channel names.
- Double check sqlite connection pool and or other sqlite connections.

## Modules
**Claude**:
- *FIXED* - Claude `LimitOverrunError` — increased subprocess StreamReader limit to 1 MiB.
- *FIXED* - `LimitOverrunError` when Claude Code emits large JSON lines (added `limit=1 MiB` to subprocess).
- Change from claude reset to claude clear.
- Lock any downloaded files or asks requests that want file output to `.claude_asks/`
- Add support for Claude to attach files as replies.
    - Add this support to History? For longer context replies allow attaching a "conversation" text file?
- Add support for the Plan command of Claude.
- Add support for session resumes 
    - store sessions per user to DB
        - allow nick naming a session for easier recovery? 
        - Maybe a summary tied to the session to help discern?
    - Unique sessions per User (allows for multple ask sessions)
- Automate handling Sentry IO issues and writeout proposed POA(Plan of Action/Code) for fix?
    - Create GH issues? Show "traffic/usage".

**Sonarr and Radarr**: (Planned)
- Project scope, API research (endpoints + JSON shapes), and example clients written up in `.claude_asks/sonarr_radarr/README.md`, `.claude_asks/sonarr_radarr/sonarr/` and `.claude_asks/sonarr_radarr/radarr/`.
- Try to use TVDB for movie ID to search Sonarr better.
    - 🗨 Radarr's lookup accepts `imdb:tt…` terms — likely the better bridge between the two apps (see `.claude_asks/sonarr_radarr/API_RESEARCH.md`).

**Moderator**:
- ? Add Moderator settings as needed.
- Add a function to start and stop `self.bot.task_loops`
- If `clear` is called in a reply, clear messages up until the "reply" message.
- Change `settings` command to `moderator_settings`.
- Consider making the interaction ephemeral.


**Utility**:
- Add a generic command to take either a image URL/etc and turn the image into an app emoji.
- Expand `source` command to search any Object and its associated functions (not just registered bot commands).
    - Resolve arbitrary objects/attributes (classes, methods, functions) via dotted paths, not just `bot.get_command`.
    - Support returning multiple matches when a query is ambiguous.
    - Present the matches as a selectable list (View/Select menu) so the user can pick one.
    - On selection, return the full content (source link + code/snippet) for that specific result.
- Add auto-thread creation for a "set" channel, with specific rulesets (eg. Contains Images, @mentions, etc...)
- `/about` make it cover `/info` via aliases
    <!-- - See about listning number of "guilds" and "users" seen. -->
    - List/mention intents and other misc dev information. 
        *BUG* - Fix Intents object.
        *BUG* - Members/User list is incorrect.
    - See about labeling the commit/update information?

**Reddit Image Crawler**:
- Change compare to a slash command 
    - support RedditEmbeds, file attachments and urls.
    - support message links/IDs
    - Store last 50/60 Embeds? Use Title for listings?
- See about getting the Subreddits Banner/Icon and using that to populate the Embed.
- `check_subreddit` (line ~1315) uses `search_by_name` which hits the Reddit API and iterates a generator. Expensive for a simple existence check — could cache results or use a HEAD request to `/r/{sub}/about.json`.
- Verify pHash implementation.
- !ISSUE! - UserWarning: Palette images with Transparency expressed in bytes should be converted to RGBA images
- **DONE** - Update async.PRAW -> Resume session
- **DONE** - Include subreddit and webhook name/id inside of `update_subreddit`

- **DONE** - Add similar to `add_subreddit`, possibly allow a webhook parameter too?
- **DONE** - See about combining the list of `No Webhook URL` subreddits into a single message. (Cut down on console flooding)
    - Changed the LOGGER to a "debug". No longer needed.
- **DONE** - Add webhook Name to the subreddit list?
    - **DONE** - See about making the list far more persistent? Remove Interaction Timeout?
- **DONE** - Reset on Embed/View for list_subreddit doesn't reset the "Next" button if on the last page.
- **DONE** - URL: https://v.redd.it/623prlsy85bh1 is not an image ->
- **DONE** - Verify a subreddit exists when called via `add_subreddit`? A simple `get` should suffice?
- **DONE** - `del_subreddit` needs more information. At least include the subreddit name?
    - Now shows webhook name that was linked prior to deletion.
- **DONE** - Turn every post into an Embed. | Easier to format info and may look cleaner.
    - **DONE** - First post of the Day as an Emoji Link in the above embeds.
    - **DONE** - Subreddit first post jump link.
    - **DONE** - Timestamps
    - **DONE** - Add the image as an attachment.
- **DONE** - Install/build `xy_binfind` into the venv to re-enable edge comparison (currently hash-only dedupe).
- **DONE** - Store metrics per subreddit crawler run: total posts seen, images found, duplicates skipped, and webhooks sent.
    - Added `/crawler_stats` command with paginated embed display.
- **DONE** - !ISSUE! - PIL `Palette images with Transparency` warning in `_convert()`.
- **DONE** - !ISSUE! - PIL `Palette images with Transparency` warning — convert P+transparency to RGBA before grayscale.
- **DONE** - `on_reaction_compare` scoped per user — `reaction_compare_urls` keyed by `user.id` to prevent cross-contamination.
- **DONE** - `check_subreddit` bare `print(type(e))` replaced with `LOGGER.exception(...)`.
- **DONE** - `_get_all_subreddits` replaced N+1 queries with a single `LEFT JOIN` query.
- **DONE** - `hash_list` / `url_list` converted from lists to sets for O(1) lookups.
- **DONE** - `save_array` / `read_array` sync file I/O wrapped in `asyncio.to_thread()`.
- **DONE** - `webhook_send` 413 Payload Too Large fallback — retries with image URL as link instead of file attachment. Uses `KumaEmbed.field_image`.
- **DONE** - `get_subreddit` command now uses `KumaView` pagination instead of sending N individual messages.
- **DONE** - `ImageComparison` class moved above `RedditEmbed`, near the other dataclasses/TypedDicts.
- **DONE** - Removed unused `timezone` import.
- **DONE** - `RedditEmbed` switched from custom `_image_file` to `KumaEmbed.field_image` property.


**Google Keep**: (Planned)
- Interface with Google Keep via the unofficial `gkeepapi` library (no official REST API exists).
    - `gkeepapi` authenticates with a Google account master token via `keep.login()` or `keep.resume()`.
    - Token should be stored securely (env var or secrets file) and reused across sessions via `keep.getMasterToken()`.
    - Sync is manual: call `keep.sync()` to pull remote changes; `keep.createNote()` / `note.delete()` / `keep.sync()` to push.
- Commands to consider:
    - Create a note with an optional title and body.
    - Append to an existing note by label or title search.
    - List notes (filterable by label or pinned status).
    - Delete or archive a note by title/label.
    - Checklist support: create a `gkeepapi.node.List` node and add `ListItem` children.
- Labels map to Discord command groups or filters; consider a `label` parameter on create/list.
- Rate limiting: `gkeepapi` is unofficial and undocumented — avoid hammering sync; debounce writes and sync lazily.
- `gkeepapi` is sync-only; wrap calls in `asyncio.to_thread()` to avoid blocking the event loop.


**Gatekeeper**: 
- *DONE* -  Keeping server status or current Online player count/etc.
    - Change channel type from VC to TextChannel and pull ongoing server chat.
    - Global chat between each Server if MC.
- *DONE* - Start, Stop and Restart of any server.
- Take Announcements channel that @role and pass those into the Server Console for each server to notify players.
- Notification when server is online. Server thread/WARN: Dedicated server took 88.557 seconds to load
- Fixed Server list updating in realtime.
    - Fix logger prints when updating "available Instances" (It appears ~ 4 times)


**FFXIV**:
- Store a Users inventory via `Item.id` and `Item.quantity`
    - Create a to_dict() function for an Inventory Item.
- Switch to V2 COMPs -> https://gist.github.com/pythonmcpi/83b95f6e86a8155c07d4ff924967b325
<!-- - Fix `on_timeout` error for BaseView. -->

- Change most button styles to secondary


- ConsoleGamesWiki parsing via url searching.
    - Useful URL - https://ffxiv.consolegameswiki.com/mediawiki/index.php?search=Magitek&title=Special%3ASearch&profile=default&fulltext=1

- Convert saved gifs/video into animated Emojis. (Downloads folder)

### ISSUES:
#### Item/Recipe
- Show Crafting Ingredients breakdown entirely
    - Possibly show first way to obtain~
- Add Recipes related to an Ingredient via Button?

#### SpearFishing Embed
- Need to create one...

### Features/Improvements:
- Timers for Housing, Journal, etc...
- Add wild card searching (multi item lookup)
- Add item watch list functionality
- Add Inventory functionality
- Add Collect list for glamour/etc.?
- Store a users recent item lookups? (3 - 4?)


