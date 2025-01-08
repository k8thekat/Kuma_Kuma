import pathlib
from pathlib import Path

import asqlite
from asqlite import Pool


class Kuma_DB:
    _DBFILE: Path = pathlib.Path("kuma.db")
    _DBExists: bool = False
    _db_pool: Pool

    def __init__(self) -> None:
        if self._DBFILE.exists():
            self._DBExists = True

    async def _dev_return(self) -> Pool:
        """Easy return of _db_pool"""
        if self._DBExists:
            self._db_pool = await asqlite.create_pool(self._DBFILE.as_posix())
            return self._db_pool
        else:
            raise ValueError("Database does not exist")

    async def _init_database(self) -> None:
        print()
        # Call schema/.execute() table here..
