import datetime
from datetime import timedelta
import pytz
from math import floor


from cogs.love_cog_utils.db import LoverEntry


class TimeTable:
    table: list[str] = []

    ante_post = ["AM", "PM"]
    hour_values = [str(x) for x in range(1, 13)]
    min_values = [str(x) for x in range(00, 60, 15)]

    def create_table(self):
        res = list(self.table)
        for entry in self.ante_post:
            for hour in self.hour_values:
                for min in self.min_values:
                    if min == "0":
                        min = "00"
                    res.append(f"{hour}:{min} {entry}")
        return res

    async def suggestion_time_diff(self, time: str, lover: LoverEntry) -> int:
        """Take's in a `H:MM AM` or `H:MM PM` format (respective of `lover` timezone) and returns the offset from UTC midnight in minutes."""
        # Let's parse our provided time str into int's.
        res = time.split(":")
        hour = int(res[0])
        min = int(res[1][0:-2])  # strip off "AM" or "PM"
        if res[1][-2:] == "PM":
            hour += 12
        # If we land on the hour; turn the "00" back into "0" minutes.
        if min == "00":
            min = 0
        lover_timezone = await lover.get_timezone()

        # We create our datetime object in the users local time zone using the values they want. (eg. 3:30am)
        today = datetime.date.today()
        s_time = datetime.time(hour=hour, minute=min)
        lover_cur_time_inTZ = pytz.timezone(lover_timezone["timezone"]).localize(datetime.datetime.combine(today, s_time))

        # We create a UTC datetime object at midnight
        utc_time = datetime.time(hour=0, minute=0)
        utc_midnight = pytz.timezone('UTC').localize(datetime.datetime.combine(today, utc_time))

        # We then get the difference between the two datetime objects into minutes as an offset from UTC Midnight.
        time_diff = lover_cur_time_inTZ - utc_midnight
        time_diff_minutes = time_diff.total_seconds() / 60
        if time_diff_minutes < 0:
            # add 24 hours
            time_diff_minutes += 60 * 24

        # Since UTC can be on a different DATE than the users Timezone,
        # we need to respectively remove 24hours or add 24hours.
        if time_diff_minutes > 1440:
            time_diff_minutes -= 1440
        elif time_diff_minutes < 0:
            time_diff_minutes = + 1440

        time_diff = timedelta(minutes=time_diff_minutes)

        return int(time_diff_minutes)

    async def localize_suggestion_time(self, suggestion_time: int, lover: LoverEntry) -> datetime.datetime:
        """Use the offset on UTC Midnight time and then convert that time to the lovers timezone"""
        lover_tz = await lover.get_timezone()
        today = datetime.date.today()
        hours = floor(suggestion_time / 60)
        minutes = (suggestion_time - (hours * 60))
        s_time = datetime.time(minute=minutes, hour=hours)
        utc_cur_time = pytz.timezone("UTC").localize(datetime.datetime.combine(today, s_time))
        return utc_cur_time.astimezone(tz=pytz.timezone(lover_tz["timezone"]))
