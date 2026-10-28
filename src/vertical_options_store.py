"""Vertical Options-only complete result store and indexed page queries.

Only normalized market data and computed spreads are written to a private,
temporary scan directory. No credentials, accounts or order state are stored.
The canonical scanner streams each result here; no result-count cutoff exists.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import threading
import weakref
from typing import Any, Callable, Iterable


_CANONICAL_ORDER = (
    "grade ASC, distance ASC, cost ASC, ratio DESC, expiry ASC, side ASC, "
    "buy ASC, sell ASC, buy_osi ASC, sell_osi ASC"
)
_SORTS = {
    "FILL + TARGET": (("grade", False), ("distance", False), ("cost", False), ("ratio", True)),
    "R:R": (("ratio", False),), "COST": (("cost", False),),
    "BREAKEVEN": (("breakeven", False),), "TARGET DISTANCE": (("distance", False),),
    "MAX PROFIT": (("profit", False),), "EXPIRATION": (("expiry", False),),
    "BUY STRIKE": (("buy", False),), "SELL STRIKE": (("sell", False),),
    "BUY LAST": (("buy_last", False),), "SELL LAST": (("sell_last", False),),
    "SPREAD WIDTH": (("width", False),), "FILL QUALITY": (("grade", False),),
}
_DETAILS = (
    "buy_bid", "buy_ask", "sell_bid", "sell_ask", "debit", "basis", "orderable",
    "spread_bid", "spread_mid", "spread_ask", "last_debit", "liquidity_signal",
)


def _rank(row: dict[str, Any], target: float) -> tuple:
    return (row["grade"], abs(row["breakeven"] - target), row["cost"],
            -row["reward_risk"], row["expiry"], row["call_put"],
            row["buy_strike"], row["sell_strike"], row["buy_osi"], row["sell_osi"])


class VerticalResultStore:
    """Bound Python memory by the input chain, write batch and visible page.

    SQLite's page cache is 4 MiB and sort scratch stays on disk. A separate
    connection is opened per operation, including deferred download threads.
    TemporaryDirectory removes scan files when the last cache/export reference
    is released; expired snapshots and evicted policies cannot leak databases.
    """

    def __init__(self, symbol: str, spot: float, target: float, max_cost: float | None, mode: str):
        self.symbol, self.spot, self.target = symbol, spot, target
        self.max_cost, self.mode = max_cost, mode
        self._directory = tempfile.TemporaryDirectory(prefix="terminal_vertical_")
        self._cleanup = weakref.finalize(self, self._directory.cleanup)
        self.path = Path(self._directory.name) / "spreads.sqlite3"
        self._lock = threading.RLock()
        self._count = 0
        self._completed: dict[date, int] = {}
        self._annual: dict[int, dict[str, Any]] = {}
        self._indexes = {}
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE spreads (
                id TEXT PRIMARY KEY, expiry TEXT NOT NULL, side TEXT NOT NULL,
                grade INTEGER NOT NULL, distance REAL NOT NULL, cost REAL NOT NULL,
                ratio REAL NOT NULL, breakeven REAL NOT NULL, profit REAL NOT NULL,
                width REAL NOT NULL, buy REAL NOT NULL, sell REAL NOT NULL,
                buy_last REAL, sell_last REAL, buy_osi TEXT NOT NULL, sell_osi TEXT NOT NULL,
                details TEXT NOT NULL
            )""")
            db.execute(f"CREATE INDEX canonical ON spreads ({_CANONICAL_ORDER})")
            db.execute("CREATE INDEX expiry_side ON spreads (expiry, side)")
            db.execute("CREATE INDEX side_expiry ON spreads (side, expiry)")
            db.execute("CREATE INDEX target_distance ON spreads (distance, id)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA cache_size=-4096")
        db.execute("PRAGMA temp_store=FILE")
        try:
            with db:
                yield db
        finally:
            db.close()

    def __len__(self):
        return self._count

    @property
    def ranked_expirations(self) -> int:
        return len(self._completed)

    @property
    def annual_ids(self) -> set[str]:
        return {row["id"] for row in self._annual.values()}

    @property
    def annual_rows(self) -> list[dict[str, Any]]:
        return sorted(self._annual.values(), key=lambda row: _rank(row, self.target))

    @property
    def expirations(self) -> list[date]:
        with self._connect() as db:
            return [date.fromisoformat(row[0]) for row in db.execute("SELECT DISTINCT expiry FROM spreads ORDER BY expiry")]

    def best_by_year(self, *, max_distance=None) -> list[dict[str, Any]]:
        if max_distance is None:
            return self.annual_rows
        where, args = self._where(max_distance=max_distance)
        result = []
        with self._connect() as db:
            for year in sorted({expiry.year for expiry in self._completed}):
                row = db.execute("SELECT * FROM spreads" + where
                    + " AND expiry>=? AND expiry<? ORDER BY " + _CANONICAL_ORDER + " LIMIT 1",
                    args + [f"{year:04d}-01-01", f"{year + 1:04d}-01-01"]).fetchone()
                if row is not None:
                    result.append(self._decode(row))
        return sorted(result, key=lambda row: _rank(row, self.target))

    @contextmanager
    def snapshot(self):
        """Keep a deferred CSV's rows and annual markers in the same revision."""
        with self._lock:
            yield self

    def add_expiration(self, expiry: date, rows: list[dict[str, Any]], calculator: Callable,
                       progress: Callable[[int], None] | None = None) -> bool:
        """Commit a whole expiration atomically; never refetch or reprice old ones."""
        with self._lock:
            if self._completed.get(expiry) == len(rows):
                return False
            annual = dict(self._annual)
            batch = []
            with self._connect() as db:
                if expiry in self._completed:
                    raise ValueError("An already ranked expiration changed; start a fresh scan.")

                def flush():
                    db.executemany("INSERT INTO spreads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                    batch.clear()

                def retain(row):
                    old = annual.get(row["year"])
                    if old is None or _rank(row, self.target) < _rank(old, self.target):
                        annual[row["year"]] = row
                    batch.append((
                        row["id"], row["expiry"].isoformat(), row["call_put"], row["grade"],
                        abs(row["breakeven"] - self.target), row["cost"], row["reward_risk"],
                        row["breakeven"], row["max_profit"], row["width"],
                        row["buy_strike"], row["sell_strike"], row["buy_last"], row["sell_last"],
                        row["buy_osi"], row["sell_osi"],
                        json.dumps([row.get(key) for key in _DETAILS], separators=(",", ":"), allow_nan=False),
                    ))
                    if len(batch) >= 512:
                        flush()

                calculator(symbol=self.symbol, spot_last=self.spot, option_rows=rows,
                           target_breakeven=self.target, max_cost=self.max_cost,
                           pricing_mode=self.mode, max_per_expiration=None,
                           candidate_sink=retain, progress=progress)
                flush()
                added = db.total_changes
            # Publish metadata only after successful transaction commit.
            self._count += added
            self._annual = annual
            self._completed[expiry] = len(rows)
            return True

    @staticmethod
    def _where(expiry="ALL", side="CALL + PUT", max_distance=None):
        clauses, args = [], []
        if expiry != "ALL":
            if not isinstance(expiry, date):
                raise ValueError("Invalid expiration filter.")
            clauses.append("expiry=?")
            args.append(expiry.isoformat())
        if side not in {"CALL + PUT", "CALL", "PUT"}:
            raise ValueError("Invalid option type filter.")
        if side != "CALL + PUT":
            clauses.append("side=?")
            args.append(side)
        if max_distance is not None:
            if not math.isfinite(max_distance) or max_distance < 0:
                raise ValueError("Invalid breakeven distance filter.")
            clauses.append("distance<=?")
            args.append(max_distance + 1e-8)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), args

    def count(self, *, expiry="ALL", side="CALL + PUT", max_distance=None) -> int:
        if expiry == "ALL" and side == "CALL + PUT" and max_distance is None:
            return len(self)
        where, args = self._where(expiry, side, max_distance)
        with self._connect() as db:
            return db.execute("SELECT count(*) FROM spreads" + where, args).fetchone()[0]

    @staticmethod
    def _order(sort_by, direction):
        if sort_by not in _SORTS or direction not in {"ASC", "DESC"}:
            raise ValueError("Invalid qualified-spread sorting controls.")
        terms = []
        for column, reverse in _SORTS[sort_by]:
            if column in {"buy_last", "sell_last"}:
                terms.append(f"({column} IS NULL) {direction}")
            terms.append(f"{column} {'DESC' if reverse != (direction == 'DESC') else 'ASC'}")
        # Match the existing stable identity tie-break, including DESC ties.
        return ", ".join(terms + ["id ASC"])

    def _ensure_index(self, sort_by, direction):
        if sort_by == "TARGET DISTANCE" and direction == "ASC":
            return  # Covered by the permanent distance index; do not duplicate it.
        key = (sort_by, direction)
        if key not in self._indexes:
            order = self._order(sort_by, direction)
            with self._lock, self._connect() as db:
                name = f"sort_{list(_SORTS).index(sort_by)}_{direction}"
                db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON spreads ({order})")
                self._indexes[key] = name
                while len(self._indexes) > 3:
                    old = next(iter(self._indexes))
                    db.execute(f"DROP INDEX {self._indexes.pop(old)}")

    def _decode(self, row):
        (identity, expiry, side, grade, _, cost, ratio, breakeven, profit, width,
         buy, sell, buy_last, sell_last, buy_osi, sell_osi, details) = row
        expiry = date.fromisoformat(expiry)
        return {
            "id": identity, "symbol": self.symbol, "spot_last": self.spot,
            "expiry": expiry, "year": expiry.year, "call_put": side,
            "buy_strike": buy, "sell_strike": sell, "buy_osi": buy_osi, "sell_osi": sell_osi,
            "buy_last": buy_last, "sell_last": sell_last, "pricing_mode": self.mode,
            **dict(zip(_DETAILS, json.loads(details))), "grade": grade, "cost": cost,
            "width": width, "max_profit": profit, "max_loss": cost,
            "breakeven": breakeven, "reward_risk": ratio,
        }

    def page(self, *, expiry="ALL", side="CALL + PUT", sort_by="FILL + TARGET",
             direction="ASC", offset=0, limit=200, max_distance=None) -> list[dict[str, Any]]:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Invalid spread page.")
        self._ensure_index(sort_by, direction)
        where, args = self._where(expiry, side, max_distance)
        # Locate page IDs through the index before reading their detail payloads.
        sql = ("SELECT * FROM spreads WHERE rowid IN (SELECT rowid FROM spreads" + where
               + " ORDER BY " + self._order(sort_by, direction) + " LIMIT ? OFFSET ?) ORDER BY "
               + self._order(sort_by, direction))
        with self._connect() as db:
            return [self._decode(row) for row in db.execute(sql, args + [limit, offset])]

    def get_many(self, identities: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = sorted({item for item in identities if isinstance(item, str)})
        found = {}
        with self._connect() as db:
            for offset in range(0, len(ids), 400):
                part = ids[offset:offset + 400]
                sql = "SELECT * FROM spreads WHERE id IN (" + ",".join("?" for _ in part) + ")"
                for values in db.execute(sql, part):
                    row = self._decode(values)
                    found[row["id"]] = row
        return dict(sorted(found.items(), key=lambda item: _rank(item[1], self.target)))

    def iter_rows(self, *, expiry="ALL", side="CALL + PUT", sort_by="FILL + TARGET",
                  direction="ASC", max_distance=None):
        """Stream the entire filtered universe, never just the displayed page."""
        self._ensure_index(sort_by, direction)
        where, args = self._where(expiry, side, max_distance)
        yield from self._iterate(where, args, self._order(sort_by, direction))

    def iter_ranked(self):
        yield from self._iterate("", [], _CANONICAL_ORDER)

    def _iterate(self, where, args, order):
        with self._connect() as db:
            cursor = db.execute("SELECT * FROM spreads" + where + " ORDER BY " + order, args)
            while batch := cursor.fetchmany(256):
                for row in batch:
                    yield self._decode(row)
