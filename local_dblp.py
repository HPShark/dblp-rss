"""Bounded DBLP storage, streaming imports, and daily snapshot synchronization."""

from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import gzip
import errno
import hashlib
import heapq
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
from urllib.parse import urlsplit
from urllib.request import url2pathname

from filelock import FileLock, Timeout
from lxml import etree
import requests

from dblp import generate_rss_from_papers
from urllib.parse import quote


logger = logging.getLogger(__name__)
RECORD_TYPES = (
    "article", "inproceedings", "incollection", "book", "proceedings",
    "phdthesis", "mastersthesis", "www", "person", "data",
)
PAPER_TYPES = {"article", "inproceedings", "incollection", "book", "data"}
COLUMNS = "stream, key, mdate, title, authors, year, venue, volume, number, ee"
INSERT_PAPER = f"INSERT OR REPLACE INTO papers ({COLUMNS}) VALUES ({','.join('?' * 10)})"
PAPER_ORDER_VERSION = 1
PAPER_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    stream TEXT NOT NULL, key TEXT NOT NULL, mdate TEXT NOT NULL,
    title TEXT NOT NULL, authors TEXT NOT NULL, year TEXT NOT NULL,
    venue TEXT NOT NULL, volume TEXT NOT NULL, number TEXT NOT NULL, ee TEXT NOT NULL,
    PRIMARY KEY (stream, key)
);
CREATE INDEX IF NOT EXISTS papers_order ON papers(stream, mdate DESC, key DESC);
"""
CATALOG_VERSION = "1"
CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
    stream TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT NOT NULL,
    search_text TEXT NOT NULL
);
"""


def paper_order(stream):
    """Use bibliographic chronology first, then DBLP's update order."""
    year = "CAST(year AS INTEGER) DESC"
    if stream.startswith("journals/"):
        return year + ", CAST(volume AS INTEGER) DESC, mdate DESC, key DESC"
    return year + ", mdate DESC, key DESC"


def paper_rank(stream, paper):
    try:
        year_match = re.match(r"\s*([+-]?\d+)", str(paper[4]))
        year = int(year_match.group(1)) if year_match else 0
    except (TypeError, ValueError):
        year = 0
    try:
        volume_match = re.match(r"\s*([+-]?\d+)", str(paper[6]))
        volume = int(volume_match.group(1)) if stream.startswith("journals/") and volume_match else 0
    except (TypeError, ValueError):
        volume = 0
    return year, volume, paper[1], paper[0]


def search_words(value):
    """Normalize punctuation, accents, and common bibliographic abbreviations."""
    value = "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                    if not unicodedata.combining(c))
    equivalents = {
        "transactions": "trans", "transaction": "trans",
        "computers": "comput", "computer": "comput", "computing": "comput",
        "computation": "comput", "computational": "comput",
        "security": "secur", "secure": "secur",
        "international": "int", "conference": "conf", "conferences": "conf",
        "proceedings": "proc", "journal": "j", "systems": "syst", "system": "syst",
        "engineering": "eng", "information": "inf", "science": "sci", "sciences": "sci",
        "technology": "technol", "technologies": "technol",
    }
    stopwords = {"a", "an", "the", "on", "of", "and", "in", "for", "to", "with"}
    return [equivalents.get(t, t) for t in re.findall(r"[^\W_]+", value)
            if t not in stopwords]


def search_terms(value):
    return sorted(set(search_words(value)))


def rss_path(stream):
    return "/dblp/" + quote("stream:streams/" + stream + ":", safe="")


class VenueCatalog:
    """Small directory of all stream names, independent of the paper cache."""

    def __init__(self):
        self.entries = {}

    def add(self, element, streams):
        def text(tag):
            node = element.find(tag)
            return "".join(node.itertext()).strip() if node is not None else ""

        journal, booktitle = text("journal"), text("booktitle")
        proceedings_title = text("title") if element.tag == "proceedings" else ""
        for stream in streams:
            entry = self.entries.setdefault(stream, {"name": stream.split("/")[-1].upper(),
                                                       "rank": (-1, "", ""), "aliases": set()})
            label = (journal if stream.startswith("journals/") else booktitle) or proceedings_title
            if label:
                rank = (int(bool(journal or booktitle)), element.get("mdate", ""), label)
                if rank > entry["rank"]:
                    entry["name"], entry["rank"] = label, rank
                entry["aliases"].add(label)
            if proceedings_title:
                entry["aliases"].add(proceedings_title)

    def rows(self):
        for stream, entry in self.entries.items():
            aliases = sorted(entry["aliases"])
            terms = search_terms(stream + " " + " ".join(aliases))
            yield (stream, entry["name"], json.dumps(aliases, ensure_ascii=False),
                   " " + " ".join(terms) + " ")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def cutoff_date(today=None):
    return ((today or datetime.now(timezone.utc).date()) - timedelta(days=30)).isoformat()


@dataclass
class Settings:
    cache_dir: Path = field(default_factory=lambda: Path(os.environ.get("DBLP_CACHE_DIR", "cache")))
    dump_url: str = field(default_factory=lambda: os.environ.get(
        "DBLP_DUMP_URL", "https://dblp.uni-trier.de/xml/dblp.xml.gz"))
    interval: float = 86400
    retry_interval: float = 3600
    max_dump_bytes: int = 9_000_000_000
    max_database_bytes: int = 1_500_000_000
    max_cache_bytes: int = 9_000_000_000
    minimum_free_bytes: int = 100_000_000
    cold_days: float = 30
    maintenance_interval: float = 3600
    pressure_retry_interval: float = 300
    reserve_bytes: int = 8_000_000
    heap_record_limit: int = 20_000

    def __post_init__(self):
        self.cache_dir = Path(self.cache_dir)


class FeedPending(Exception):
    pass


class SyncStopped(Exception):
    pass


class CapacityError(OSError):
    """The minimum working set cannot fit, even after cache eviction."""


def storage_error(exc):
    return (isinstance(exc, sqlite3.Error) and
            getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL) or (
        isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT})


class Store:
    def __init__(self, settings):
        self.settings = settings
        # Separate from dblp_cache.db: do not overwrite legacy user data on upgrade.
        self.path = settings.cache_dir / "dblp_local.db"

    def connect(self, path=None):
        conn = sqlite3.connect(path or self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA temp_store = MEMORY")
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        conn.execute(f"PRAGMA max_page_count = {max(1, self.settings.max_database_bytes // page_size)}")
        return conn

    def initialize(self):
        self.settings.cache_dir.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn, conn:
            conn.executescript(PAPER_SCHEMA + CATALOG_SCHEMA + f"""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    stream TEXT PRIMARY KEY, refreshed_at TEXT, snapshot TEXT,
                    last_access REAL NOT NULL DEFAULT 0,
                    paper_order_version INTEGER NOT NULL DEFAULT {PAPER_ORDER_VERSION}
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(subscriptions)")}
            if "last_access" not in columns:
                conn.execute("ALTER TABLE subscriptions ADD COLUMN last_access REAL NOT NULL DEFAULT 0")
                # Existing subscriptions get a grace period after upgrading.
                conn.execute("UPDATE subscriptions SET last_access=?", (time.time(),))
            if "paper_order_version" not in columns:
                # Keep cached feeds readable, while scheduling one local-snapshot refresh.
                conn.execute("ALTER TABLE subscriptions ADD COLUMN paper_order_version INTEGER NOT NULL DEFAULT 0")
            conn.execute("CREATE INDEX IF NOT EXISTS subscriptions_lru ON subscriptions(last_access, stream)")

    def metadata(self):
        with closing(self.connect()) as conn:
            return dict(conn.execute("SELECT key, value FROM metadata"))

    def set_metadata(self, values):
        with closing(self.connect()) as conn, conn:
            conn.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                             [(k, str(v)) for k, v in values.items()])

    def register(self, stream):
        with closing(self.connect()) as conn, conn:
            conn.execute("""INSERT INTO subscriptions(stream, last_access) VALUES (?, ?)
                ON CONFLICT(stream) DO UPDATE SET last_access=excluded.last_access""",
                         (stream, time.time()))

    def subscriptions(self, pending_only=False, snapshot=None):
        with closing(self.connect()) as conn:
            query = "SELECT stream FROM subscriptions"
            parameters = []
            if pending_only:
                conditions = ["refreshed_at IS NULL", "paper_order_version != ?"]
                parameters.append(PAPER_ORDER_VERSION)
                if snapshot:
                    conditions.append("(snapshot IS NULL OR snapshot != ?)")
                    parameters.append(snapshot)
                query += " WHERE " + " OR ".join(conditions)
            return [row[0] for row in conn.execute(query, parameters)]

    def venue_exists(self, stream):
        with closing(self.connect()) as conn:
            conn.execute("BEGIN")
            state = dict(conn.execute("""SELECT key,value FROM metadata
                WHERE key IN ('catalog_version', 'catalog_snapshot', 'snapshot')"""))
            if (state.get('catalog_version') != CATALOG_VERSION or
                    state.get('catalog_snapshot') != state.get('snapshot')):
                return None  # The first complete directory has not arrived yet.
            return conn.execute("SELECT 1 FROM venues WHERE stream=?", (stream,)).fetchone() is not None

    @staticmethod
    def reclaim(conn):
        """Incrementally truncate free pages without a second full database copy."""
        if conn.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
            return
        while True:
            before = conn.execute("PRAGMA freelist_count").fetchone()[0]
            if not before:
                return
            conn.execute("PRAGMA incremental_vacuum(256)").fetchall()
            if conn.execute("PRAGMA freelist_count").fetchone()[0] >= before:
                return

    @staticmethod
    def delete_papers(conn, stream):
        # Bounded transactions leave room for the rollback journal at low disk space.
        while True:
            with conn:
                changed = conn.execute("""DELETE FROM papers WHERE rowid IN
                    (SELECT rowid FROM papers WHERE stream=? LIMIT 128)""", (stream,)).rowcount
            if not changed:
                break
        Store.reclaim(conn)

    def evict_oldest(self, older_than=None, exclude=()):
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            where, args = (" WHERE last_access < ?", (older_than,)) if older_than is not None else ("", ())
            rows = conn.execute("SELECT stream FROM subscriptions" + where +
                                " ORDER BY last_access, stream", args)
            row = next((row for row in rows if row[0] not in exclude), None)
            rows.close()
            if row is None:
                conn.rollback()
                return None
            stream = row[0]
            # Remove readiness first. A concurrent reader re-registers a pending feed,
            # and can never read a partly deleted set of papers.
            conn.execute("DELETE FROM subscriptions WHERE stream=?", (stream,))
            conn.execute("""INSERT INTO metadata VALUES ('evicted_sources_total', '1')
                ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1""")
            conn.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                             [("last_evicted_stream", stream), ("last_eviction_at", utcnow())])
            conn.commit()
            self.delete_papers(conn, stream)
            logger.warning("Evicted paper cache for %s; directory entry retained", stream)
            return stream

    def migrate_vacuum(self):
        """Run once for old databases, only when a complete safe VACUUM fits."""
        with closing(self.connect()) as conn:
            if conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2:
                return
            size = (conn.execute("PRAGMA page_count").fetchone()[0] -
                    conn.execute("PRAGMA freelist_count").fetchone()[0]) * conn.execute("PRAGMA page_size").fetchone()[0]
            used = sum(p.stat().st_size for p in self.settings.cache_dir.rglob("*") if p.is_file())
            if (used + size * 2 > self.settings.max_cache_bytes or
                    shutil.disk_usage(self.settings.cache_dir).free < size * 2 + self.settings.minimum_free_bytes):
                return
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            conn.execute("VACUUM")

    def compact_legacy(self):
        # A legacy non-auto-vacuum database needs one migration before truncation.
        self.migrate_vacuum()
        with closing(self.connect()) as conn:
            self.reclaim(conn)

    def search_venues(self, query="", kind="all", limit=30, offset=0):
        tokens = search_terms(query)[:12]
        phrase = " " + " ".join(search_words(query)[:12]) + " "

        def relevance(name, aliases):
            # Terms scattered across unrelated historical titles should not outrank
            # a venue whose single name contains the complete query.
            if not tokens:
                return 0

            def matches(label):
                words = search_words(label)
                count = sum(any(word.startswith(token) for word in words) for token in tokens)
                # Prefer the requested name in order over titles sharing its words.
                phrase_match = phrase in " " + " ".join(words) + " "
                return count * 100 + int(phrase_match) * 50

            primary = matches(name)
            best = max([primary] + [matches(label) for label in json.loads(aliases)])
            return best * 100 + primary

        conditions, parameters = [], []
        if kind in {"journals", "conf"}:
            conditions.append("v.stream LIKE ?")
            parameters.append(kind + "/%")
        for token in tokens:
            conditions.append("v.search_text LIKE ?")
            parameters.append("% " + token + "%")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        # Prefer an exact stream ID/name match before partial name matches.
        exact = query.strip().casefold()
        order = " ORDER BY CASE WHEN lower(v.stream)=? OR lower(v.stream)=? OR lower(v.stream)=? OR lower(v.name)=? THEN 0 ELSE 1 END, venue_relevance(v.name, v.aliases) DESC, v.name COLLATE NOCASE, v.stream"
        with closing(self.connect()) as conn, conn:
            conn.create_function("venue_relevance", 2, relevance, deterministic=True)
            conn.execute("BEGIN")
            state = dict(conn.execute("SELECT key, value FROM metadata"))
            count = conn.execute("SELECT COUNT(*) FROM venues v" + where, parameters).fetchone()[0]
            total_venues = conn.execute("SELECT COUNT(*) FROM venues").fetchone()[0]
            rows = conn.execute("""
                SELECT v.stream, v.name, s.refreshed_at FROM venues v
                LEFT JOIN subscriptions s ON s.stream=v.stream
            """ + where + order + " LIMIT ? OFFSET ?", parameters + [
                exact, "journals/" + exact, "conf/" + exact, exact, limit, offset,
            ]).fetchall()
        return {
            "ready": state.get("catalog_version") == CATALOG_VERSION,
            "updated_at": state.get("catalog_updated_at"),
            "query": query, "kind": kind, "total": count, "total_venues": total_venues,
            "offset": offset, "limit": limit,
            "results": [{"stream": row["stream"], "name": row["name"],
                         "abbreviation": row["stream"].split("/")[-1].upper(),
                         "kind": "journals" if row["stream"].startswith("journals/") else "conf",
                         "rss_path": rss_path(row["stream"]),
                         "feed_ready": bool(row["refreshed_at"])} for row in rows],
        }

    def feed_data(self, stream):
        with closing(self.connect()) as conn, conn:
            # Both queries see the same generation during an atomic publication.
            conn.execute("BEGIN")
            subscription = conn.execute(
                "SELECT refreshed_at FROM subscriptions WHERE stream = ?", (stream,)
            ).fetchone()
            if not subscription or not subscription[0]:
                raise FeedPending("正在下载总库或解析新订阅，请稍后重试。")
            order = paper_order(stream)
            papers = conn.execute(f"""
                SELECT * FROM papers WHERE stream = ? AND (
                    mdate >= ? OR key IN (
                        SELECT key FROM papers WHERE stream = ?
                        ORDER BY {order} LIMIT 300
                    )
                ) ORDER BY {order}
            """, (stream, cutoff_date(), stream)).fetchall()
            return papers, subscription[0]

    def publish(self, stage_path, streams, metadata=None):
        """Atomic publication: readers retain the previous feed on any failure."""
        refreshed = utcnow()
        with closing(self.connect()) as conn:
            conn.execute("ATTACH DATABASE ? AS incoming", (str(stage_path),))
            with conn:
                if conn.execute("SELECT value FROM incoming.stage_metadata WHERE key='catalog_version'").fetchone():
                    conn.execute("DELETE FROM venues")
                    conn.execute("INSERT INTO venues SELECT * FROM incoming.venues")
                    conn.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", [
                        ("catalog_version", CATALOG_VERSION), ("catalog_updated_at", refreshed),
                        ("catalog_snapshot", (metadata or {}).get("snapshot", "")),
                    ])
                for stream in streams:
                    if conn.execute("SELECT 1 FROM subscriptions WHERE stream=?", (stream,)).fetchone() is None:
                        continue  # Do not resurrect an evicted subscription.
                    conn.execute("DELETE FROM papers WHERE stream=?", (stream,))
                    conn.execute(f"""
                        INSERT INTO papers ({COLUMNS})
                        SELECT {COLUMNS} FROM incoming.papers WHERE stream = ?
                        ON CONFLICT(stream, key) DO UPDATE SET
                            mdate=excluded.mdate, title=excluded.title, authors=excluded.authors,
                            year=excluded.year, venue=excluded.venue, volume=excluded.volume,
                            number=excluded.number, ee=excluded.ee
                    """, (stream,))
                    conn.execute("""
                        DELETE FROM papers WHERE stream = ? AND key NOT IN (
                            SELECT key FROM incoming.papers WHERE stream = ?
                        )
                    """, (stream, stream))
                    conn.execute("""UPDATE subscriptions SET refreshed_at=?, snapshot=?,
                        paper_order_version=? WHERE stream=?""",
                        (refreshed, (metadata or {}).get("snapshot", ""), PAPER_ORDER_VERSION, stream))
                if metadata:
                    conn.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                                     [(k, str(v)) for k, v in metadata.items()])

    def prune(self):
        """Age out the 30-day overflow even if upstream has no new snapshot."""
        with closing(self.connect()) as conn, conn:
            for stream in self.subscriptions():
                order = paper_order(stream)
                conn.execute(f"""
                    DELETE FROM papers WHERE stream=? AND mdate < ? AND key NOT IN (
                        SELECT key FROM papers WHERE stream=?
                        ORDER BY {order} LIMIT 300
                    )
                """, (stream, cutoff_date(), stream))


class LocalDTDResolver(etree.Resolver):
    def __init__(self, path):
        super().__init__()
        self.path = path

    def resolve(self, system_url, public_id, context):
        if system_url == "dblp.dtd":
            return self.resolve_filename(str(self.path), context)
        location = urlsplit(system_url)
        if location.scheme == "file" and location.netloc in {"", "localhost"}:
            requested = Path(url2pathname(location.path)).resolve()
        elif not location.scheme:
            requested = Path(system_url).resolve()
        else:
            raise ValueError("Refusing an external XML entity: " + system_url)
        if requested in {self.path, self.path.with_name("dblp.dtd")}:
            return self.resolve_filename(str(self.path), context)
        raise ValueError("Refusing an external XML entity: " + system_url)


def record_streams(element):
    """DBLP key namespaces, with explicit/crossref stream membership as well."""
    streams = set()
    for key in [element.get("key", "")] + [node.text or "" for node in element.findall("crossref")]:
        parts = key.removeprefix("streams/").split("/")
        if len(parts) >= 2 and parts[0] in {"journals", "conf"}:
            streams.add("/".join(parts[:2]))
    for node in element.findall("stream"):
        value = "".join(node.itertext()).strip().removeprefix("streams/")
        if re.fullmatch(r"(?:journals|conf)/[A-Za-z0-9_.-]+", value):
            streams.add(value)
    return streams


def paper_values(element):
    def text(name):
        node = element.find(name)
        return "".join(node.itertext()).strip() if node is not None else ""

    modified = element.get("mdate", "")
    if modified:
        modified = date.fromisoformat(modified).isoformat()
    return (
        element.get("key", ""), modified, text("title"),
        json.dumps(["".join(node.itertext()) for node in element.findall("author")], ensure_ascii=False),
        text("year"), text("journal") or text("booktitle"), text("volume"), text("number"),
        json.dumps(["".join(node.itertext()) for node in element.findall("ee")], ensure_ascii=False),
    )


class LocalDBLP:
    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.store = Store(self.settings)
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None
        self._pending_retry_at = 0.0
        self._evicted_streams = set()
        self._reading_snapshot = None
        self._protected_snapshot = None
        self._last_maintenance_at = 0
        self._request_queue = set()
        self._request_lock = threading.Lock()

    def start(self):
        self._initialize_storage()
        self.thread = threading.Thread(target=self._worker, name="dblp-sync", daemon=True)
        self.thread.start()

    def _initialize_storage(self):
        try:
            self.store.initialize()
        except (sqlite3.Error, OSError) as exc:
            if not storage_error(exc):
                raise
            self._release_reserve()
            # Old installations may not yet have an emergency reserve. Under the
            # sync lock, free a snapshot so schema migration can still start.
            with FileLock(str(self.settings.cache_dir / "dblp-sync.lock"), timeout=30):
                self._cleanup()
                self._discard_old_snapshot()
                self.store.initialize()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _check_stopped(self):
        if self.stop_event.is_set():
            raise SyncStopped()

    def cache_bytes(self):
        total = 0
        for path in self.settings.cache_dir.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                pass  # A concurrent rotating request log may have moved.
        return total

    def _space_available(self, additional):
        return (self.cache_bytes() + additional <= self.settings.max_cache_bytes and
                shutil.disk_usage(self.settings.cache_dir).free >= additional + self.settings.minimum_free_bytes)

    def _release_reserve(self):
        try:
            (self.settings.cache_dir / "dblp-emergency.reserve").unlink(missing_ok=True)
        except PermissionError:
            # On Windows another thread may still be closing a reserve write.
            logger.debug("Emergency reserve is briefly busy; next cleanup will retry")

    def _reserve_space(self):
        reserve = self.settings.cache_dir / "dblp-emergency.reserve"
        if not reserve.exists() and self._space_available(self.settings.reserve_bytes * 2):
            try:
                with reserve.open("wb") as output:
                    remaining = self.settings.reserve_bytes
                    while remaining:
                        chunk = min(remaining, 1_000_000)
                        output.write(b"\0" * chunk)
                        remaining -= chunk
            except OSError:
                reserve.unlink(missing_ok=True)

    def _evict_one(self, stage_conn=None, older_than=None):
        self._release_reserve()
        stream = self.store.evict_oldest(older_than, exclude=self._evicted_streams)
        if stream:
            self._evicted_streams.add(stream)
            if stage_conn is not None:
                self.store.delete_papers(stage_conn, stream)
            self.store.compact_legacy()
        return stream

    def _discard_old_snapshot(self):
        current = self.snapshot_path()
        if current is None or current in {self._reading_snapshot, self._protected_snapshot}:
            return False
        current.unlink(missing_ok=True)
        # Missing local data also disables conditional requests after a restart.
        self.store.set_metadata({"snapshot_file": "", "next_sync_at": 0,
                                 "last_snapshot_discarded_at": utcnow()})
        logger.warning("Released old compressed snapshot to prioritize the next download")
        self.store.compact_legacy()
        return True

    def _check_space(self, additional=0, stage_conn=None):
        self._check_stopped()
        if additional > self.settings.max_cache_bytes:
            raise CapacityError("Minimum operation exceeds the DBLP cache disk budget")
        if self._space_available(additional):
            return
        self._release_reserve()
        while not self._space_available(additional):
            self._check_stopped()
            if self._evict_one(stage_conn, older_than=time.time() - self.settings.cold_days * 86400):
                continue
            if self._discard_old_snapshot():
                continue
            if self._evict_one(stage_conn):
                continue
            raise CapacityError("Insufficient disk space/budget even after evicting all paper caches and old snapshot")

    def _maintenance(self):
        if time.time() - self._last_maintenance_at < self.settings.maintenance_interval:
            return
        self.store.migrate_vacuum()
        before = time.time() - self.settings.cold_days * 86400
        while self._evict_one(older_than=before):
            self._check_stopped()
        # Recover a crash between removing a subscription and deleting its papers.
        with closing(self.store.connect()) as conn:
            orphans = [r[0] for r in conn.execute("""SELECT DISTINCT stream FROM papers
                WHERE stream NOT IN (SELECT stream FROM subscriptions)""")]
            for stream in orphans:
                self.store.delete_papers(conn, stream)
            self.store.reclaim(conn)
        self._check_space()
        self._reserve_space()
        self._last_maintenance_at = time.time()

    def snapshot_path(self):
        filename = self.store.metadata().get("snapshot_file", "")
        if not re.fullmatch(r"dblp-[0-9a-f]{32}\.xml\.gz", filename):
            return None
        path = self.settings.cache_dir / filename
        return path if path.is_file() else None

    def _cleanup(self):
        current = self.snapshot_path()
        for path in self.settings.cache_dir.iterdir():
            owned = bool(re.fullmatch(r"dblp-[0-9a-f]{32}\.xml\.gz", path.name))
            owned |= path.name in {"dblp.xml.gz.part", "dblp.dtd.part", "dblp-stage.db",
                                   "dblp-stage.db-journal"}
            if owned and path != current:
                path.unlink(missing_ok=True)

    def _download_small(self, session, url, limit):
        with session.get(url, stream=True, timeout=(15, 60)) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(16384):
                self._check_stopped()
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("Unexpectedly large metadata response: " + url)
            return bytes(data)

    def _download_snapshot(self, session, conditional_headers):
        """Resume interrupted transfers only when the server confirms the same version."""
        path = self.settings.cache_dir / "dblp.xml.gz.part"
        digest = hashlib.md5()
        size = 0
        validator = ""
        total = 0
        remote = {}
        last_progress = time.monotonic()
        for attempt in range(5):
            self._check_stopped()
            headers = dict(conditional_headers)
            if size and validator:
                headers = {"Range": f"bytes={size}-", "If-Range": validator}
            try:
                with session.get(self.settings.dump_url, headers=headers, stream=True,
                                 timeout=(15, 60)) as response:
                    if response.status_code == 304 and not size:
                        return None
                    response.raise_for_status()
                    if response.status_code == 206:
                        content_range = re.fullmatch(
                            r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                        if not content_range or int(content_range[1]) != size:
                            raise ValueError("Invalid range response while resuming DBLP download")
                        current_validator = response.headers.get(
                            "ETag" if validator.startswith('"') else "Last-Modified", "")
                        if not validator or current_validator != validator:
                            raise ValueError("DBLP snapshot version changed during range download")
                        range_total = int(content_range[3])
                        if total and range_total != total:
                            raise ValueError("DBLP snapshot size changed during range download")
                        total = range_total
                    else:
                        # A server may ignore Range or reject If-Range after a new release.
                        digest, size = hashlib.md5(), 0
                        total = int(response.headers.get("Content-Length", 0))
                        etag = response.headers.get("ETag", "")
                        validator = etag if etag and not etag.startswith("W/") else response.headers.get("Last-Modified", "")
                        remote = {"etag": etag, "last_modified": response.headers.get("Last-Modified", "")}
                    if total > self.settings.max_dump_bytes:
                        raise ValueError("DBLP snapshot exceeds the compressed file size limit")
                    self._check_space(max(0, total - size))
                    logger.info("Downloading DBLP snapshot: offset=%d total=%s", size, total or "unknown")
                    with path.open("ab" if size else "wb") as output:
                        for chunk in response.iter_content(1024 * 1024):
                            self._check_stopped()
                            if size + len(chunk) > self.settings.max_dump_bytes:
                                raise ValueError("DBLP snapshot exceeds the compressed file size limit")
                            self._check_space(len(chunk))
                            output.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                            if time.monotonic() - last_progress >= 30:
                                logger.info("Downloaded %.1f MB", size / 1_000_000)
                                last_progress = time.monotonic()
                    if total and total != size:
                        raise requests.ConnectionError("Incomplete DBLP snapshot download")
                    with path.open("rb") as source:
                        if source.read(2) != b"\x1f\x8b":
                            raise ValueError("DBLP returned something other than gzip (possibly an HTML challenge)")
                    return path, digest.hexdigest(), remote
            except requests.HTTPError:
                raise
            except requests.RequestException:
                if attempt == 4:
                    raise
                logger.warning("Transfer interrupted at %d bytes; retry %d/4", size, attempt + 1)
                if self.stop_event.wait(min(2 ** attempt, 10)):
                    raise SyncStopped()
            except OSError as exc:
                if not storage_error(exc) or attempt == 4:
                    raise
                self._release_reserve()
                if not self._evict_one() and not self._discard_old_snapshot():
                    raise
                # A short disk write may have persisted only part of the chunk.
                size = min(size, path.stat().st_size) if path.exists() else 0
                digest = hashlib.md5()
                if path.exists():
                    with path.open("r+b") as output:
                        output.truncate(size)
                        for chunk in iter(lambda: output.read(1024 * 1024), b""):
                            digest.update(chunk)
                logger.warning("Recovered a disk-full write; resuming download at %d bytes", size)

    def _download(self, session):
        state = self.store.metadata()
        headers = {}
        if self.snapshot_path() and (self.settings.cache_dir / "dblp.dtd").is_file():
            if state.get("etag"):
                headers["If-None-Match"] = state["etag"]
            if state.get("last_modified"):
                headers["If-Modified-Since"] = state["last_modified"]
        downloaded = self._download_snapshot(session, headers)
        if downloaded is None:
            return None
        path, digest, remote = downloaded
        checksum = self._download_small(session, self.settings.dump_url + ".md5", 16384)
        expected_md5 = checksum.decode("ascii").split()[0].lower()
        if not re.fullmatch(r"[0-9a-f]{32}", expected_md5) or expected_md5 != digest:
            raise ValueError("DBLP checksum mismatch; upstream may have changed, retry later")
        dtd_url = self.settings.dump_url.rsplit("/", 1)[0] + "/dblp.dtd"
        dtd = self._download_small(session, dtd_url, 1_000_000)
        if b"<!ELEMENT dblp" not in dtd:
            raise ValueError("Invalid DBLP DTD response")
        self._check_space(len(dtd) + 65536)
        (self.settings.cache_dir / "dblp.dtd.part").write_bytes(dtd)
        logger.info("Download complete and MD5 verified (%.1f MB)", path.stat().st_size / 1_000_000)
        return path, dict(remote, snapshot=digest)

    def build_stage(self, snapshot, streams, today=None, dtd_path=None, build_catalog=True):
        """Keep recent records on disk and at most 300 older candidates per stream in RAM."""
        started = time.monotonic()
        stage = self.settings.cache_dir / "dblp-stage.db"
        stage.unlink(missing_ok=True)
        cutoff = cutoff_date(today)
        selected = set(streams) - self._evicted_streams
        heaps = {stream: [] for stream in selected}
        heap_count = 0
        batch = []
        scanned = 0
        catalog = VenueCatalog() if build_catalog else None
        names = sorted(selected)
        logger.info("Parsing snapshot for %d subscriptions (catalog=%s): %s%s",
                    len(selected), build_catalog, ", ".join(names[:20]) or "(none)",
                    f", ... ({len(names) - 20} more)" if len(names) > 20 else "")
        with closing(self.store.connect(stage)) as conn:
            conn.executescript(PAPER_SCHEMA + CATALOG_SCHEMA +
                               "CREATE TABLE stage_metadata (key TEXT PRIMARY KEY, value TEXT);")

            def filter_evicted():
                nonlocal heap_count
                selected.difference_update(self._evicted_streams)
                for stream in list(heaps):
                    if stream not in selected:
                        heap_count -= len(heaps.pop(stream))
                batch[:] = [row for row in batch if row[0] in selected]

            def flush():
                while batch:
                    filter_evicted()
                    if not batch:
                        return
                    estimate = sum(len(str(v).encode("utf-8")) for row in batch for v in row) * 4 + 65536
                    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
                    occupied = (conn.execute("PRAGMA page_count").fetchone()[0] -
                                conn.execute("PRAGMA freelist_count").fetchone()[0]) * page_size
                    if occupied + estimate > self.settings.max_database_bytes:
                        if self._evict_one(conn):
                            continue
                        raise CapacityError("Directory and minimum import batch exceed the database budget")
                    self._check_space(estimate, conn)
                    filter_evicted()
                    try:
                        with conn:
                            conn.executemany(INSERT_PAPER, batch)
                            for stream in {row[0] for row in batch}:
                                order = paper_order(stream)
                                conn.execute(f"""DELETE FROM papers WHERE stream=? AND mdate < ? AND key NOT IN
                                    (SELECT key FROM papers WHERE stream=? ORDER BY {order} LIMIT 300)
                                """, (stream, cutoff, stream))
                        batch.clear()
                    except sqlite3.Error as exc:
                        if not storage_error(exc) or not self._evict_one(conn):
                            raise

            def spill_heaps():
                nonlocal heap_count
                for stream in list(heaps):
                    heap = heaps.get(stream, [])
                    heaps[stream] = []
                    heap_count -= len(heap)
                    for _, row in heap:
                        batch.append(row)
                        if len(batch) >= 500:
                            flush()
                flush()

            self._reading_snapshot = snapshot
            with gzip.open(snapshot, "rb") as source:
                context = etree.iterparse(
                    source, events=("end",), tag=RECORD_TYPES,
                    load_dtd=True, resolve_entities=True, no_network=True, huge_tree=True,
                )
                context.resolvers.add(LocalDTDResolver(
                    (dtd_path or self.settings.cache_dir / "dblp.dtd").resolve()))
                for _, element in context:
                    self._check_stopped()
                    parent = element.getparent()
                    if parent is None or parent.tag != "dblp":
                        continue
                    scanned += 1
                    memberships = set()
                    if element.tag in PAPER_TYPES or (catalog is not None and element.tag == "proceedings"):
                        memberships = record_streams(element)
                        if catalog is not None and memberships:
                            catalog.add(element, memberships)
                    if selected and element.tag in PAPER_TYPES:
                        matching = selected.intersection(memberships) - self._evicted_streams
                        if matching:
                            paper = paper_values(element)
                            if not paper[0] or not paper[2]:
                                raise ValueError("A matching DBLP paper has no key or title")
                            for stream in matching:
                                row = (stream,) + paper
                                if paper[1] >= cutoff:
                                    batch.append(row)
                                else:
                                    candidate = (paper_rank(stream, paper), row)
                                    heap = heaps[stream]
                                    if len(heap) < 300:
                                        heapq.heappush(heap, candidate)
                                        heap_count += 1
                                    elif candidate[:2] > heap[0][:2]:
                                        heapq.heapreplace(heap, candidate)
                            if len(batch) >= 500:
                                flush()
                            if heap_count >= self.settings.heap_record_limit:
                                spill_heaps()
                    element.clear()
                    while element.getprevious() is not None:
                        del parent[0]
                    if scanned % 250_000 == 0:
                        logger.info("Scanned %d DBLP records", scanned)
                if context.root.tag != "dblp" or not scanned:
                    raise ValueError("Not a nonempty DBLP XML snapshot")
                while source.read(1024 * 1024):
                    self._check_stopped()
            self._reading_snapshot = None
            spill_heaps()
            if catalog is not None:
                # Batch the small directory too; never commit a partial catalog to live data.
                rows = []
                for row in catalog.rows():
                    rows.append(row)
                    if len(rows) >= 200:
                        self._check_space(sum(len(str(v).encode("utf-8")) for r in rows for v in r) * 4, conn)
                        conn.executemany("INSERT INTO venues VALUES (?, ?, ?, ?)", rows)
                        conn.commit()
                        rows.clear()
                with conn:
                    conn.executemany("INSERT INTO venues VALUES (?, ?, ?, ?)", rows)
                    conn.execute("INSERT INTO stage_metadata VALUES ('catalog_version', ?)", (CATALOG_VERSION,))
                logger.info("Indexed %d journal/conference streams", len(catalog.entries))
            with conn:
                for stream in streams:
                    order = paper_order(stream)
                    conn.execute(f"""
                        DELETE FROM papers WHERE stream=? AND mdate < ? AND key NOT IN (
                            SELECT key FROM papers WHERE stream=?
                            ORDER BY {order} LIMIT 300
                        )
                        """, (stream, cutoff, stream))
        logger.info("Completed snapshot scan: %d records in %.1fs for %d subscriptions",
                    scanned, time.monotonic() - started, len(streams))
        return stage

    def _publish(self, stage, streams, metadata):
        with closing(self.store.connect(stage)) as conn:
            while True:
                # Reserve a complete rollback journal plus potential main-db growth.
                additional = self.store.path.stat().st_size + stage.stat().st_size + 65536
                self._check_space(additional, conn)
                chosen = [stream for stream in streams if stream not in self._evicted_streams]
                try:
                    self.store.publish(stage, chosen, metadata)
                    return
                except (sqlite3.Error, OSError) as exc:
                    if not storage_error(exc) or not self._evict_one(conn):
                        raise

    def synchronize(self):
        """One remote check. Caller holds the cross-process lock."""
        with requests.Session() as session:
            session.headers.update({"User-Agent": "dblp-rss/2.0 (daily XML snapshot sync)",
                                    "Accept-Encoding": "identity"})
            downloaded = self._download(session)
        metadata = {"last_checked_at": utcnow(),
                    "next_sync_at": time.time() + self.settings.interval, "last_error": ""}
        if downloaded is None:
            self.store.prune()
            self.store.set_metadata(metadata)
            logger.info("Snapshot unchanged; next remote check in 24 hours")
            return
        temporary, remote = downloaded
        streams = self.store.subscriptions()
        stage = self.build_stage(temporary, streams, dtd_path=self.settings.cache_dir / "dblp.dtd.part")
        installed = self.settings.cache_dir / f"dblp-{remote['snapshot']}.xml.gz"
        temporary.replace(installed)
        # A DBLP DTD is small and backwards compatible; the resolver permits no other files.
        (self.settings.cache_dir / "dblp.dtd.part").replace(self.settings.cache_dir / "dblp.dtd")
        metadata.update(remote)
        metadata.update({"snapshot_file": installed.name, "last_sync_at": utcnow()})
        # A fully validated new snapshot is independent of optional paper caches.
        # A cache publication failure must not pin future imports to yesterday's XML.
        while True:
            try:
                self.store.set_metadata(metadata)
                break
            except (sqlite3.Error, OSError) as exc:
                if not storage_error(exc) or not self._evict_one():
                    raise
        self._protected_snapshot = installed
        for old in self.settings.cache_dir.glob("dblp-*.xml.gz"):
            if old != installed:
                old.unlink(missing_ok=True)
        logger.info("Latest validated snapshot installed: %s", installed.name)
        try:
            self._publish(stage, streams, {"snapshot": remote["snapshot"], "last_error": "",
                                          "last_feed_sync_at": utcnow()})
        except (sqlite3.Error, OSError) as exc:
            self.store.set_metadata({"last_error": "Snapshot updated; paper cache import pending: " + str(exc)})
            self._pending_retry_at = time.time() + self.settings.pressure_retry_interval
            logger.exception("Latest snapshot retained; paper caches will retry independently")

    def _register_queued_requests(self):
        """Persist requests deferred by disk pressure, including mid-scan arrivals."""
        with self._request_lock:
            pending = sorted(self._request_queue)
            self._request_queue.clear()
        for index, stream in enumerate(pending):
            try:
                while True:
                    try:
                        self.store.register(stream)
                        break
                    except (sqlite3.Error, OSError) as exc:
                        if not storage_error(exc) or not self._evict_one():
                            raise
            except (sqlite3.Error, OSError):
                with self._request_lock:
                    self._request_queue.update(pending[index:])
                raise

    def _import_pending_subscriptions(self):
        """Freeze each scan's membership, then drain all new arrivals as one batch."""
        attempted = set()
        batch_number = 0
        while not self.stop_event.is_set():
            self._register_queued_requests()
            current = self.store.metadata()
            snapshot = self.snapshot_path()
            if snapshot is None or time.time() < self._pending_retry_at:
                return
            # Registration is keyed by stream, not HTTP request. Never repeat a
            # failed/evicted source within this tick if readers register it again.
            pending = tuple(sorted(set(self.store.subscriptions(
                pending_only=True, snapshot=current.get("snapshot")))
                - attempted - self._evicted_streams))
            needs_catalog = (current.get("catalog_version") != CATALOG_VERSION or
                             current.get("catalog_snapshot") != current.get("snapshot"))
            if not pending and not needs_catalog:
                return
            attempted.update(pending)
            batch_number += 1
            logger.info("Starting subscription batch %d: %d sources", batch_number, len(pending))
            try:
                stage = self.build_stage(snapshot, pending, build_catalog=needs_catalog)
                self._publish(stage, pending, {"snapshot": current["snapshot"],
                    "last_error": "", "last_feed_sync_at": utcnow()})
            except SyncStopped:
                raise
            except Exception as exc:
                logger.exception("Subscription batch %d failed; automatic retry deferred", batch_number)
                self.store.set_metadata({"last_error": str(exc)})
                self._pending_retry_at = time.time() + self.settings.retry_interval
                return
            logger.info("Completed subscription batch %d; collecting next pending batch", batch_number)

    def run_once(self):
        """One worker tick; synchronization state persists across server restarts."""
        self._initialize_storage()
        try:
            with FileLock(str(self.settings.cache_dir / "dblp-sync.lock"), timeout=0):
                self._evicted_streams = set()
                self._protected_snapshot = None
                self._cleanup()
                try:
                    self._maintenance()
                    self._register_queued_requests()
                    state = self.store.metadata()
                    missing_local = bool(state.get("snapshot_file")) and (
                        self.snapshot_path() is None or not (self.settings.cache_dir / "dblp.dtd").is_file())
                    if missing_local or time.time() >= float(state.get("next_sync_at", 0)):
                        try:
                            self.synchronize()
                        except SyncStopped:
                            raise
                        except Exception as exc:
                            logger.exception("Snapshot sync failed; retaining existing feeds")
                            delay = self.settings.pressure_retry_interval if isinstance(exc, CapacityError) or storage_error(exc) else self.settings.retry_interval
                            self.store.set_metadata({"last_error": str(exc), "next_sync_at": time.time() + delay})
                    self._import_pending_subscriptions()
                finally:
                    self._reading_snapshot = None
                    self._protected_snapshot = None
                    self._cleanup()
                    self._reserve_space()
        except Timeout:
            pass  # Another uvicorn process owns the synchronization task.
        except (sqlite3.Error, OSError) as exc:
            self._release_reserve()
            logger.exception("Storage maintenance failed; retrying without stopping the HTTP service")
            try:
                self.store.set_metadata({"last_error": str(exc),
                    "next_sync_at": time.time() + self.settings.pressure_retry_interval})
            except (sqlite3.Error, OSError):
                logger.error("Disk is too full to persist the retry status")

    def _worker(self):
        while not self.stop_event.is_set():
            self.wake_event.clear()
            try:
                self.run_once()
            except SyncStopped:
                return
            except Exception:
                logger.exception("DBLP background worker failed; retrying in 60 seconds")
                self.stop_event.wait(60)
            self.wake_event.wait(10)

    def feed(self, stream):
        if self.store.venue_exists(stream) is False:
            # Preserve empty RSS for unknown IDs, without unbounded registrations
            # or repeatedly scanning the entire dump for typo/bot requests.
            return generate_rss_from_papers([], stream)
        try:
            self.store.register(stream)
        except (sqlite3.Error, OSError) as exc:
            if not storage_error(exc):
                raise
            # Do not turn a full-disk registration into an unhandled HTTP 500.
            self._release_reserve()
            with self._request_lock:
                if len(self._request_queue) < 256:
                    self._request_queue.add(stream)
            self.wake_event.set()
            try:
                papers, refreshed = self.store.feed_data(stream)
                return generate_rss_from_papers(papers, stream, refreshed)
            except FeedPending:
                raise FeedPending("正在自动清理缓存并登记订阅，请稍后重试。") from exc
        self.wake_event.set()
        papers, refreshed = self.store.feed_data(stream)
        return generate_rss_from_papers(papers, stream, refreshed)

    def status(self):
        state = self.store.metadata()
        with closing(self.store.connect()) as conn:
            venue_count = conn.execute("SELECT COUNT(*) FROM venues").fetchone()[0]
        return {
            "ready": self.snapshot_path() is not None,
            "catalog_ready": state.get("catalog_version") == CATALOG_VERSION,
            "venue_count": venue_count,
            "catalog_updated_at": state.get("catalog_updated_at"),
            "subscriptions": len(self.store.subscriptions()),
            "pending_subscriptions": self.store.subscriptions(pending_only=True, snapshot=state.get("snapshot")),
            "last_sync_at": state.get("last_sync_at"),
            "last_checked_at": state.get("last_checked_at"),
            "next_sync_at": state.get("next_sync_at"),
            "last_error": state.get("last_error", ""),
            "last_feed_sync_at": state.get("last_feed_sync_at"),
            "cache_bytes": self.cache_bytes(),
            "cache_budget_bytes": self.settings.max_cache_bytes,
            "database_bytes": self.store.path.stat().st_size,
            "database_budget_bytes": self.settings.max_database_bytes,
            "cold_days": self.settings.cold_days,
            "evicted_sources_total": int(state.get("evicted_sources_total", 0)),
            "last_evicted_stream": state.get("last_evicted_stream"),
            "last_eviction_at": state.get("last_eviction_at"),
            "last_snapshot_discarded_at": state.get("last_snapshot_discarded_at"),
        }
