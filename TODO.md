# TODO
- Add Helper replies to `support-forums` or any forum.
- Online Member Count for Discord Guild via locked channel and edit channel name to current count. Use emojis for channel names.
- Work on readme for cogs/etc.
- Make a unicode class library for easier usage/access throughout bot code.

## Known Bugs:
- Address the repl session not handling `await` style expressions.


## Modules
**Sonarr and Radarr** Module
- Try to use TVDB for movie ID to search Sonarr better.

**Reddit Image Scraper**:
- Turn every post into an Embed. | Easier to format info and may look cleaner.
    - First post of the Day as an Emoji Link in the above embeds.
    - Subreddit first post jump link.
    - Timestamps
    - Add the image as an attachment.

**Sticker Yoink**
- Add an Emoji Yoinker?
    - Support animated emojis?

**Gatekeeper**: 
- *DONE* -  Keeping server status or current Online player count/etc.
    - Change channel type from VC to TextChannel and pull ongoing server chat.
    - Global chat between each Server if MC.
- *DONE* - Start, Stop and Restart of any server.
- Take Announcements channel that @role and pass those into the Server Console for each server to notify players.
- Notification when server is online. Server thread/WARN: Dedicated server took 88.557 seconds to load


**FFXIV**:
- *MOSTLY-DONE* - Finish development of `moogles_intuition`.
- Add item watch list functionality


**Moderator**:
- Add Moderator settings as needed.
- Add a function to start and stop `self.bot.task_loops`