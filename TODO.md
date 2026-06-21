# TODO
- Add Helper replies to `support-forums` or any forum.
- Online Member Count for Discord Guild via locked channel and edit channel name to current count. Use emojis for channel names.
- Work on readme for cogs/etc.
- Double check sqlite connection pool and or other sqlite connections.

## Known Bugs:
- 

## Modules
**Sonarr and Radarr** Module
- Try to use TVDB for movie ID to search Sonarr better.

### **Utility**
- Add auto-thread creation for a "set" channel, with specific rulesets (eg. Contains Images, @mentions, etc...)
- `/about` make it cover `/info` via aliases
    - See about listning number of "guilds" and "users" seen.
    - List/mention intents and other misc dev information.
    - See about labeling the commit/update information?

**Reddit Image Scraper**:
- Turn every post into an Embed. | Easier to format info and may look cleaner.
    - First post of the Day as an Emoji Link in the above embeds.
    - Subreddit first post jump link.
    - Timestamps
    - Add the image as an attachment.

**Sticker Yoink**
- App Emojis? - Add support for making App Emoji's.
    - https://discordpy.readthedocs.io/en/stable/ext/commands/api.html?highlight=get_context#discord.ext.commands.Bot.create_application_emoji

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

### Item/Recipe

- Show Crafting Ingredients breakdown entirely
    - Possibly show first way to obtain~
- Add Recipes related to an Ingredient via Button?

### SpearFishing Embed
- Need to create one...

### Features:
- Timers for Housing, Journal, etc...
- Add wild card searching (multi item lookup)
- Add item watch list functionality
- Add Inventory functionality
- Add Collect list for glamour/etc.?
- Store a users recent item lookups? (3 - 4?)


**Moderator**:
- Add Moderator settings as needed.
- Add a function to start and stop `self.bot.task_loops`