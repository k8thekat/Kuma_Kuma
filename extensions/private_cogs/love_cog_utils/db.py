import pathlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self, Union

import utils.asqlite as asqlite

script_loc: Path = Path(__file__).parent
DB_FILENAME = "lovers.sqlite"
DB_PATH: str = script_loc.joinpath(DB_FILENAME).as_posix()

LOVERS_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS lovers (
    name TEXT NOT NULL,
    discord_id BIGINT NOT NULL,
    role_switching INT NOT NULL DEFAULT 0,
    role INT NOT NULL,
    position_switching INT NOT NULL DEFAULT 0,
    position INT NOT NULL,
    PRIMARY KEY(discord_id)
)
"""

PARTNERS_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS partners (
    lovers_id INT NOT NULL,
    partner_id INT NOT NULL,
    role_switch INT NOT NULL,
    position_switching INT NOT NULL,
    s_time INT NOT NULL,
    FOREIGN KEY (lovers_id) references lovers(discord_id),
    FOREIGN KEY (partner_id) references lovers(discord_id)
    PRIMARY KEY(lovers_id, partner_id)
)
"""

KINKS_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS kinks (
    lovers_id INT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    FOREIGN KEY (lovers_id) references lovers(discord_id)
    PRIMARY KEY(lovers_id, name)
)
"""

TIMEZONE_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS user_settings (
    discord_id BIGINT NOT NULL,
    timezone TEXT NOT NULL,
    PRIMARY KEY(discord_id)
)"""


async def get_range_suggestion_time(value1: int, value2: int):
    """ Selects all Partner rows where s_time is between `value1` and `value2` *is inclusive*"""
    async with asqlite.connect(DB_FILENAME) as db:
        async with db.cursor() as cur:
            await cur.execute("""SELECT lovers_id, partner_id FROM partners where s_time BETWEEN ? and ?""", value1, value2)
            res = await cur.fetchall()
            return res if not None else None


@dataclass()
class LoverEntry:
    name: str
    discord_id: int

    role: int
    role_switching: bool

    position: int
    position_switching: bool

    @property
    def get_role(self) -> str:
        """Possible options see `LoverRoles`"""
        pos_roles = ["dominant", "submissive"]
        return pos_roles[self.role]

    @property
    def get_position(self) -> str:
        """Possible options see `LoverPositions`"""
        pos_position = ["top", "bottom"]
        return pos_position[self.position]

    # partners: list[dict[int, str]] #{id/owner_id : name}
    # kinks: list[dict[int, str]] #{id/owner_id: name}

    @classmethod
    async def get_or_none(cls, *, discord_id: int) -> Self | None:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""SELECT * FROM lovers WHERE discord_id = ?""", discord_id)
                res = await cur.fetchone()

                return cls(**res) if res is not None else None

    @classmethod
    async def add_lover(cls, *, name: str, discord_id: int, role: int, position: int, role_switching: bool = False, position_switching: bool = False,) -> Self | None:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""INSERT INTO lovers(name, discord_id, role, role_switching, position, position_switching) VALUES (?, ?, ?, ?, ?, ?)ON CONFLICT(discord_id) DO NOTHING RETURNING *""", name, discord_id, role, position, role_switching, position_switching)
                res = await cur.fetchone()
                await db.commit()

                return cls(**res) if res is not None else None

    async def delete_lover(self) -> int:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                # remove from partner tables
                await cur.execute(
                    """DELETE FROM partners WHERE lovers_id = ?""", self.discord_id
                )
                await cur.execute(
                    """DELETE FROM kinks where lovers_id = ?""", self.discord_id
                )
                await cur.execute(
                    """DELETE FROM lovers WHERE discord_id = ?""", self.discord_id)
                await db.commit()

                return cur.get_cursor().rowcount

    # async def update_lover(self, name: str, role: int, position: int, role_switching: bool = False, position_switching: bool = False) -> LoverEntry:
    async def update_lover(self, args: dict[str, int | bool]) -> Self:
        SQL = []
        VALUES = []
        for entry in args:
            SQL.append(entry + " = ?")
            VALUES.append(args[entry])

        SQL = ", ".join(SQL)
        VALUES.append(self.discord_id)
       # print(SQL)
        # print(VALUES)
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                # await cur.execute("""UPDATE lovers SET name = ? WHERE discord_id = ? RETURNING *""", name, self.discord_id)
                await cur.execute(
                    f"""UPDATE lovers SET {SQL} WHERE discord_id = ? RETURNING *""",
                    tuple(VALUES),
                )
                await db.commit()

                res = await cur.fetchone()
                return LoverEntry(**res)  # type:ignore

    async def add_partner(self, partner_id: int, role_switching: bool, position_switching: bool, s_time: int) -> Self | None | bool:
        """Partners TABLE SCHEMA  
        ----------------------------

            lovers_id `INT NOT NULL`

            partner_id `INT NOT NULL` 

            role_switch `INT NOT NULL`

            position_switching `INT NOT NULL`

            s_time `INT NOT NULL`


        RETURNS
        -------------------------
        `False` - partner_id does not exist in `LOVERS` table \n
        `None` - partner_id/lover_id is already in the table as `PRIMARY KEY`.
        """

        partner: LoverEntry | None = await self.get_or_none(discord_id=partner_id)

        # if lover == None:
        #     lover = await self.add_lover(
        #         name=partner_name,
        #         discord_id=partner_id,
        #         role=role,
        #         position=position,
        #         role_switching=role_switching,
        #         position_switching=position_switching,
        #     )

        if partner is not None:
            async with asqlite.connect(DB_FILENAME) as db:
                async with db.cursor() as cur:
                    # await cur.execute("""INSERT INTO partners(lovers_id, partner_id) VALUES (?, ?)
                    # ON CONFLICT(lovers_id, partner_id) DO NOTHING RETURNING *""", lover.discord_id, partner_id)
                    try:
                        await cur.execute("""INSERT INTO partners(lovers_id, partner_id, role_switch, position_switching, s_time) VALUES (?, ?, ?, ?, ?)""", self.discord_id, partner_id, role_switching, position_switching, s_time)
                        # await cur.execute("""INSERT INTO partners(partner_id) VALUES (?, ?))

                    except sqlite3.IntegrityError as err:
                        if (type(err.args[0]) == str and err.args[0].lower() == "unique constraint failed: partners.lovers_id, partners.partner_id"):
                            return None

                    # res = await cur.fetchone()
                    await db.commit()
                    return partner  # type:ignore
        else:
            return False
        # return lover

    async def remove_partner(self, partner_id: int) -> None | int:
        lover = await self.get_or_none(discord_id=partner_id)

        if lover == None:
            return lover

        else:
            async with asqlite.connect(DB_FILENAME) as db:
                async with db.cursor() as cur:
                    await cur.execute("""DELETE FROM partners WHERE lovers_id = ? and partner_id = ?""", self.discord_id, partner_id)
                    await db.commit()

                    return cur.get_cursor().rowcount

    async def list_partners(self) -> list:
        """
        Returns a list of Discord IDs for lookup.
        """
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""SELECT partner_id FROM partners WHERE lovers_id = ?""", self.discord_id)
                res = await cur.fetchall()
                # partners = []
                if res:
                    # for entry in res:
                    #     if entry["partner_id"] not in partners and entry["partner_id"] != self.discord_id:
                    #         partners.append(entry["partner_id"])
                    #     # if entry["lovers_id"] not in partners and entry["lovers_id"] != self.discord_id:
                    #     #     partners.append(entry["lovers_id"])
                    res = [entry["partner_id"] for entry in res]

                # return partners if len(partners) else None
                return res

    async def add_kink(self, name: str, description: Union[str, None] = None) -> str | None:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""INSERT INTO kinks(lovers_id, name, description) VALUES (?, ?, ?) ON CONFLICT(lovers_id, name) DO NOTHING RETURNING *""", self.discord_id, name, description)
                res = await cur.fetchone()
                await db.commit()

                return name if res is not None else None

    async def remove_kink(self, name: str) -> int:
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""DELETE FROM kinks WHERE name = ?""", name)
                res = await cur.fetchone()
                await db.commit()

                return cur.get_cursor().rowcount

    async def update_partner(self, args: dict[str, int | bool | None]):
        """ Last value inside args must be  `partner_id`.\n
            PARTNER table layout

            lovers_id `INT NOT NULL`

            partner_id `INT NOT NULL` 

            role_switch `INT NOT NULL`

            position_switching `INT NOT NULL`

            s_time `INT NOT NULL`
        """
        SQL = []
        VALUES = []
        partner_id = 0
        for entry in args:
            # We don't need to set the partner_id; just need it for the WHERE statement.
            if entry == "partner_id":
                partner_id: int | bool | None = args[entry]
                continue
            SQL.append(entry + " = ?")
            VALUES.append(args[entry])

        SQL = ", ".join(SQL)
        VALUES.append(partner_id)
        VALUES.append(self.discord_id)
        # print("SQL", SQL)
        # print("VALUES", VALUES)
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute(
                    f"""UPDATE partners SET {SQL} WHERE partner_id = ? and lovers_id = ? RETURNING *""",
                    tuple(VALUES),
                )
                await db.commit()
                return

    # TODO Possibly bring this back and make a slash command for it. Unsure..
    # async def update_kink(self, name: str, new_name: str | None = None, new_description: str | None = None) -> int | None:
    #     async with asqlite.connect(DB_FILENAME) as db:
    #         async with db.cursor() as cur:
    #             await cur.execute("""SELECT * FROM kinks WHERE name = ?""", name)
    #             res = await cur.fetchone()
    #             if res is not None:
    #                 name = name if new_name == None else new_name
    #                 description = res["description"] if new_description == None else new_description
    #                 await cur.execute("""UPDATE kinks SET name = ?, description = ? WHERE name = ?""", name, description)

    async def list_kinks(self) -> list[Any]:
        """`RETURNS` list[Row("name" | "description" | "lover_id"]"""
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute(
                    """SELECT * FROM kinks WHERE lovers_id = ?""", self.discord_id
                )
                res = await cur.fetchall()

                return res if not None else None

    async def get_kink(self, name):
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute(""" SELECT * FROM kinks WHERE lovers_id =? and name = ?""", self.discord_id, name)
                res = await cur.fetchone()

                return res if not None else None

    async def set_timezone(self, tz: str):
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute(""" INSERT INTO user_settings(discord_id, timezone) VALUES($1, $2) 
                ON CONFLICT(discord_id) DO UPDATE SET timezone = $2""", self.discord_id, tz)
                res = await cur.fetchone()
                await db.commit()

                return res if not None else None

    async def get_timezone(self):
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""SELECT timezone from user_settings WHERE discord_id =?""", self.discord_id)
                res = await cur.fetchone()
                return res if not None else None

    async def get_partner_suggestion_time(self, partner_id: int):
        async with asqlite.connect(DB_FILENAME) as db:
            async with db.cursor() as cur:
                await cur.execute("""SELECT s_time FROM partners WHERE lovers_id = ? and partner_id = ?""", self.discord_id, partner_id)
                res = await cur.fetchone()
                return res if not None else None
