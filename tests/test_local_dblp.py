from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import gzip
import errno
import hashlib
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import random
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import requests

from fastapi.testclient import TestClient
from filelock import FileLock

from dblp import normalize_stream
from local_dblp import CapacityError, FeedPending, LocalDBLP, Settings, cutoff_date
from server import create_app


DTD = b'<!ELEMENT dblp ANY>\n<!ENTITY ouml "&#246;">\n'


def record(key, mdate="2020-01-01", title="Paper", tag="article", extra="", year="2026"):
    return (
        f'<{tag} key="{key}" mdate="{mdate}"><author>J&ouml;rg &amp; Li</author>'
        f'<title>{title}</title><year>{year}</year><journal>Test Journal</journal>'
        f'<ee>https://doi.org/10.1234/example</ee>{extra}</{tag}>'
    )


def dump(records):
    xml = ('<?xml version="1.0" encoding="ISO-8859-1"?>'
           '<!DOCTYPE dblp SYSTEM "dblp.dtd"><dblp>' + ''.join(records) + '</dblp>')
    return gzip.compress(xml.encode("iso-8859-1"), mtime=0)


class Upstream:
    """Actual HTTP fixture with conditional requests and controlled failures."""

    def __init__(self):
        self.payload = dump([record("journals/tdsc/one")])
        self.requests = []
        self.mode = "ok"
        self.disconnected = False
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                upstream.requests.append((self.path, dict(self.headers)))
                etag = '"' + hashlib.md5(upstream.payload).hexdigest() + '"'
                if self.path == "/dblp.xml.gz":
                    if upstream.mode == "http_error":
                        self.send_error(503)
                        return
                    if self.headers.get("If-None-Match") == etag and upstream.mode == "ok":
                        self.send_response(304)
                        self.end_headers()
                        return
                    body = b'<html>bot challenge</html>' if upstream.mode == "html" else upstream.payload
                elif self.path == "/dblp.xml.gz.md5":
                    digest = "0" * 32 if upstream.mode == "bad_md5" else hashlib.md5(upstream.payload).hexdigest()
                    body = (digest + '  dblp.xml.gz\n').encode()
                elif self.path == "/dblp.dtd":
                    body = DTD
                else:
                    self.send_error(404)
                    return
                is_dump = self.path == "/dblp.xml.gz"
                offset = 0
                if is_dump and self.headers.get("Range") and upstream.mode != "ignore_range":
                    offset = int(self.headers["Range"].removeprefix("bytes=").removesuffix("-"))
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {offset}-{len(body)-1}/{len(body)}")
                else:
                    self.send_response(200)
                body = body[offset:]
                self.send_header("Content-Length", str(len(body)))
                self.send_header("ETag", etag)
                self.send_header("Last-Modified", "Tue, 03 Nov 2026 00:00:00 GMT")
                self.end_headers()
                if is_dump and upstream.mode in {"disconnect_once", "ignore_range"} and not upstream.disconnected:
                    upstream.disconnected = True
                    self.wfile.write(body[:len(body) // 2])
                    self.close_connection = True
                else:
                    self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/dblp.xml.gz"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def downloads(self):
        return sum(path == "/dblp.xml.gz" for path, _ in self.requests)


class LocalDBLPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = Upstream()

    @classmethod
    def tearDownClass(cls):
        cls.upstream.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp.name)
        self.upstream.payload = dump([record("journals/tdsc/one")])
        self.upstream.mode = "ok"
        self.upstream.disconnected = False
        self.upstream.requests.clear()
        self.service = LocalDBLP(Settings(cache_dir=self.cache, dump_url=self.upstream.url,
                                          minimum_free_bytes=0))
        self.service.store.initialize()
        self.service.store.register("journals/tdsc")

    def tearDown(self):
        self.service.stop()
        # Windows can release a terminated native server's file handles slightly
        # after wait() returns; retry only this test's own temporary directory.
        for attempt in range(20):
            try:
                self.temp.cleanup()
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.1)

    def force_sync(self):
        self.service.store.set_metadata({"next_sync_at": 0})
        self.service.run_once()

    def items(self, stream="journals/tdsc"):
        return ET.fromstring(self.service.feed(stream)).findall("channel/item")

    def test_encoded_stream_and_invalid_queries(self):
        self.assertEqual(normalize_stream("stream%3Astreams%2Fjournals%2Ftdsc%3A"), "journals/tdsc")
        self.assertEqual(normalize_stream("stream:streams/conf/eurocrypt:"), "conf/eurocrypt")
        for invalid in ["security", "stream:streams/journals/tdsc: OR x", "stream:streams/../secret:"]:
            with self.assertRaises(ValueError):
                normalize_stream(invalid)

    def test_first_sync_entities_rss_and_persistent_daily_schedule(self):
        self.service.run_once()
        items = self.items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].findtext("{http://purl.org/dc/elements/1.1/}creator"), "Jörg & Li")
        self.assertEqual(items[0].findtext("guid"), "https://dblp.org/rec/journals/tdsc/one")
        self.assertEqual(items[0].findtext("pubDate"), "Thu, 01 Jan 2026 00:00:00 GMT")
        self.assertEqual(items[0].findtext("{http://purl.org/dc/elements/1.1/}date"), "2026")
        self.assertEqual(self.upstream.downloads(), 1)
        state = self.service.store.metadata()
        self.assertAlmostEqual(float(state["next_sync_at"]) - time.time(), 86400, delta=10)
        LocalDBLP(self.service.settings).run_once()
        self.assertEqual(self.upstream.downloads(), 1)
        self.force_sync()
        self.assertEqual(self.upstream.downloads(), 2)
        self.assertEqual(len(self.upstream.requests), 4)
        self.assertIn("If-None-Match", self.upstream.requests[-1][1])
        self.assertEqual(len(list(self.cache.glob("dblp-*.xml.gz"))), 1)
        self.assertFalse((self.cache / "dblp-stage.db").exists())

    def test_retention_each_stream_latest_300_plus_all_recent(self):
        today = datetime.now(timezone.utc).date()
        records = [record(f"journals/tdsc/old{i:04}") for i in range(450)]
        records += [record(f"journals/tdsc/new{i:04}", today.isoformat(), year="2001") for i in range(1200)]
        records += [record("journals/tdsc/boundary", cutoff_date(today))]
        records += [record(f"conf/eurocrypt/old{i:04}", tag="inproceedings") for i in range(420)]
        records += [record("conf/eurocrypt/new", today.isoformat(), tag="inproceedings")]
        self.service.store.register("conf/eurocrypt")
        self.upstream.payload = dump(reversed(records))
        self.service.run_once()
        self.assertEqual(len(self.items()), 1500)
        self.assertEqual(len(self.items("conf/eurocrypt")), 300)
        with closing(self.service.store.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM papers WHERE stream='journals/tdsc'").fetchone()[0], 1500)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM papers WHERE stream='conf/eurocrypt'").fetchone()[0], 300)
        dates = [item.findtext("pubDate") for item in self.items()]
        self.assertEqual(dates[0], "Thu, 01 Jan 2026 00:00:00 GMT")
        self.assertEqual(dates[300], "Mon, 01 Jan 2001 00:00:00 GMT")
        self.assertNotEqual(dates[0], dates[-1])

    def test_publication_year_precedes_mdate_for_every_stream_and_volume_precedes_mdate_for_journals(self):
        today = datetime.now(timezone.utc).date().isoformat()
        records = [
            record("journals/tdsc/old-revised", today,
                   title="Trust-Based Service Management for Social Internet of Things Systems.",
                   year="2016", extra="<volume>9</volume>"),
            record("journals/tdsc/volume-9", "2020-01-01", title="2025 volume 9",
                   year="2025", extra="<volume>9</volume>"),
            record("journals/tdsc/volume-13", "2019-01-01", title="2025 volume 13",
                   year="2025", extra="<volume>13</volume>"),
            record("journals/tdsc/newer-year", today, title="2024 volume 20",
                   year="2024", extra="<volume>20</volume>"),
            record("conf/eurocrypt/older-update", "2020-01-01", title="2025 older update",
                   tag="inproceedings", year="2025", extra="<volume>99</volume>"),
            record("conf/eurocrypt/newer-update", today, title="2025 newer update",
                   tag="inproceedings", year="2025"),
            record("conf/eurocrypt/newer-year", "2019-01-01", title="2026 older DBLP update",
                   tag="inproceedings", year="2026"),
        ]
        self.service.store.register("conf/eurocrypt")
        self.upstream.payload = dump(records)
        self.service.run_once()

        journal_items = self.items()
        self.assertEqual([item.findtext("title") for item in journal_items], [
            "2025 volume 13", "2025 volume 9", "2024 volume 20",
            "Trust-Based Service Management for Social Internet of Things Systems.",
        ])
        self.assertEqual(journal_items[0].findtext("pubDate"), "Wed, 01 Jan 2025 00:00:00 GMT")
        self.assertEqual(journal_items[-1].findtext("pubDate"), "Fri, 01 Jan 2016 00:00:00 GMT")
        self.assertIn("DBLP updated: " + today, journal_items[-1].findtext("description"))

        conference_items = self.items("conf/eurocrypt")
        self.assertEqual([item.findtext("title") for item in conference_items], [
            "2026 older DBLP update", "2025 newer update", "2025 older update",
        ])
        self.assertEqual(conference_items[0].findtext("pubDate"), "Thu, 01 Jan 2026 00:00:00 GMT")

    def test_old_order_cache_is_refreshed_from_local_snapshot_without_blocking_reads_or_downloading(self):
        today = datetime.now(timezone.utc).date().isoformat()
        self.upstream.payload = dump([
            record("journals/tdsc/revised-old-paper", today, title="Published 2016",
                   year="2016", extra="<volume>13</volume>"),
            record("journals/tdsc/missing-from-old-cache", "2019-01-01",
                   title="Published 2025", year="2025", extra="<volume>22</volume>"),
        ])
        self.service.run_once()
        downloads = self.upstream.downloads()
        with closing(self.service.store.connect()) as conn, conn:
            conn.execute("DELETE FROM papers WHERE key='journals/tdsc/missing-from-old-cache'")
            conn.execute("UPDATE subscriptions SET paper_order_version=0 WHERE stream='journals/tdsc'")

        self.assertEqual(self.service.status()["pending_subscriptions"], ["journals/tdsc"])
        self.assertIn("Published 2016", self.service.feed("journals/tdsc"))
        with patch.object(self.service, "build_stage", wraps=self.service.build_stage) as build:
            self.service.run_once()
            self.assertEqual(build.call_count, 1)
        self.assertEqual(self.upstream.downloads(), downloads)
        self.assertEqual(self.service.status()["pending_subscriptions"], [])
        self.assertEqual(self.items()[0].findtext("title"), "Published 2025")

    def test_latest_300_cap_uses_publication_order_for_journals_and_conferences(self):
        old_date = "2020-01-01"
        records = [record(f"journals/tdsc/base{i:03}", old_date, year="2024",
                          extra="<volume>10</volume>") for i in range(301)]
        records += [record("journals/tdsc/new-year", "2019-01-01", year="2025",
                           title="New publication year", extra="<volume>1</volume>"),
                    record("journals/tdsc/new-volume", "2018-01-01", year="2024",
                           title="New publication volume", extra="<volume>99</volume>")]
        records += [record(f"conf/eurocrypt/base{i:03}", old_date,
                           tag="inproceedings", year="2024") for i in range(301)]
        records.append(record("conf/eurocrypt/new-year", "2019-01-01", tag="inproceedings",
                              year="2025", title="New conference publication year"))
        self.service.store.register("conf/eurocrypt")
        self.upstream.payload = dump(records)
        self.service.run_once()

        journal_items = self.items()
        self.assertEqual(len(journal_items), 300)
        journal_guids = {item.findtext("guid") for item in journal_items}
        self.assertNotIn("https://dblp.org/rec/journals/tdsc/base000", journal_guids)
        self.assertEqual(journal_items[0].findtext("title"), "New publication year")

        conference_items = self.items("conf/eurocrypt")
        self.assertEqual(len(conference_items), 300)
        self.assertEqual(conference_items[0].findtext("title"), "New conference publication year")
        conference_guids = {item.findtext("guid") for item in conference_items}
        self.assertNotIn("https://dblp.org/rec/conf/eurocrypt/base000", conference_guids)

    def test_less_than_300_and_mixed_recent_old(self):
        today = datetime.now(timezone.utc).date().isoformat()
        self.upstream.payload = dump(
            [record(f"journals/tdsc/{i:04}") for i in range(500)]
            + [record(f"journals/tdsc/recent{i:04}", today) for i in range(100)]
            + [record("conf/eurocrypt/only", tag="inproceedings")]
        )
        self.service.store.register("conf/eurocrypt")
        self.service.run_once()
        papers, _ = self.service.store.feed_data("journals/tdsc")
        self.assertEqual(len(papers), 300)
        self.assertEqual(sum(p["mdate"] == today for p in papers), 100)
        self.assertEqual(len(self.items("conf/eurocrypt")), 1)

    def test_aging_recent_records_back_to_300(self):
        today = datetime.now(timezone.utc).date()
        self.upstream.payload = dump([record(f"journals/tdsc/{i:04}", today.isoformat()) for i in range(1100)])
        self.service.run_once()
        self.assertEqual(len(self.items()), 1100)
        with patch("local_dblp.cutoff_date", return_value=(today + timedelta(days=1)).isoformat()):
            self.assertEqual(len(self.items()), 300)
            self.service.store.prune()
        with closing(self.service.store.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0], 300)

    def test_new_subscription_uses_local_snapshot_without_download(self):
        self.upstream.payload = dump([record("journals/tdsc/one"), record(
            "conf/eurocrypt/one", title="E = mc<sup>2</sup>", tag="inproceedings")])
        self.service.run_once()
        with self.assertRaises(FeedPending):
            self.service.feed("conf/eurocrypt")
        self.service.run_once()
        self.assertEqual(len(self.items("conf/eurocrypt")), 1)
        self.assertEqual(self.items("conf/eurocrypt")[0].findtext("title"), "E = mc2")
        self.assertEqual(self.upstream.downloads(), 1)
        self.assertEqual(len(ET.fromstring(self.service.feed("conf/nonexistent")).findall('channel/item')), 0)
        self.service.run_once()
        self.assertEqual(len(self.items("conf/nonexistent")), 0)

    def test_arrivals_during_scan_form_one_next_batch_and_duplicate_requests_do_not_rescan(self):
        first = "conf/first"
        arrivals = [f"conf/batch{i}" for i in range(10)]
        self.upstream.payload = dump([record(f"{s}/one", title=s) for s in
                                      ["journals/tdsc", first] + arrivals])
        self.service.run_once()  # Install a local snapshot and directory first.
        downloads = self.upstream.downloads()
        original_build = self.service.build_stage
        batches = []
        entered = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]

        def blocked_build(snapshot, streams, **kwargs):
            index = len(batches)
            batches.append(tuple(streams))
            if index < 2:
                entered[index].set()
                if not release[index].wait(15):
                    raise RuntimeError("Timed out waiting for concurrent test requests")
            return original_build(snapshot, streams, **kwargs)

        def path(stream):
            return "/dblp/stream%3Astreams%2F" + stream.replace("/", "%2F") + "%3A"

        with patch.object(self.service, "start"), patch.object(self.service, "stop"):
            with TestClient(create_app(self.service), base_url="http://papers.example.org:8080") as client:
                self.assertEqual(client.get(path(first)).status_code, 503)
                with patch.object(self.service, "build_stage", side_effect=blocked_build):
                    worker = threading.Thread(target=self.service.run_once, daemon=True)
                    worker.start()
                    try:
                        self.assertTrue(entered[0].wait(5))
                        # Twenty concurrent requests represent ten distinct sources.
                        with ThreadPoolExecutor(max_workers=10) as pool:
                            responses = list(pool.map(lambda s: client.get(path(s)), arrivals * 2))
                        self.assertTrue(all(r.status_code == 503 for r in responses))
                        self.assertEqual(client.get(path("journals/tdsc")).status_code, 200)
                        self.assertEqual(batches, [(first,)])
                        release[0].set()
                        self.assertTrue(entered[1].wait(5))  # Same tick starts the next batch immediately.
                        self.assertEqual(set(batches[1]), set(arrivals))
                        self.assertEqual(len(batches[1]), 10)
                        self.assertEqual(client.get(path(first)).status_code, 200)
                        # Retries of the active second batch cannot add more work.
                        for stream in arrivals:
                            self.assertEqual(client.get(path(stream)).status_code, 503)
                    finally:
                        for event in release:
                            event.set()
                        worker.join(15)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual([len(b) for b in batches], [1, 10])
                    for stream in [first] + arrivals:
                        response = client.get(path(stream))
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(ET.fromstring(response.content).findtext("channel/item/title"), stream)
                    for _ in range(3):
                        self.service.run_once()
                    self.assertEqual([len(b) for b in batches], [1, 10])
        self.assertEqual(self.service.status()["pending_subscriptions"], [])
        self.assertEqual(self.upstream.downloads(), downloads)

    def test_arrivals_in_each_batch_are_collected_only_by_the_following_scan(self):
        streams = ["conf/first", "conf/second", "conf/third", "conf/fourth"]
        self.upstream.payload = dump([record(f"{s}/one") for s in ["journals/tdsc"] + streams])
        self.service.run_once()
        with self.assertRaises(FeedPending):
            self.service.feed(streams[0])
        batches = []
        original_build = self.service.build_stage

        def arriving_build(snapshot, selected, **kwargs):
            batches.append(tuple(selected))
            arrivals = streams[1:3] if len(batches) == 1 else streams[3:] if len(batches) == 2 else []
            for stream in arrivals:
                with self.assertRaises(FeedPending):
                    self.service.feed(stream)
            return original_build(snapshot, selected, **kwargs)

        with patch.object(self.service, "build_stage", side_effect=arriving_build):
            self.service.run_once()
            self.service.run_once()
        self.assertEqual(batches, [(streams[0],), tuple(streams[1:3]), (streams[3],)])
        self.assertEqual(self.service.status()["pending_subscriptions"], [])

    def test_daily_snapshot_scan_collects_arrivals_in_one_following_import(self):
        arrivals = [f"conf/new{i}" for i in range(10)]
        self.upstream.payload = dump([record(f"{s}/one") for s in ["journals/tdsc"] + arrivals])
        original_build = self.service.build_stage
        batches = []

        def arriving_build(snapshot, streams, **kwargs):
            batches.append(tuple(streams))
            if len(batches) == 1:
                for stream in arrivals:
                    with self.assertRaises(FeedPending):
                        self.service.feed(stream)
            return original_build(snapshot, streams, **kwargs)

        with patch.object(self.service, "build_stage", side_effect=arriving_build):
            self.service.run_once()
            self.service.run_once()
        self.assertEqual(batches, [("journals/tdsc",), tuple(arrivals)])
        self.assertEqual(self.upstream.downloads(), 1)
        self.assertEqual(self.service.status()["pending_subscriptions"], [])

    def test_failed_batch_respects_retry_backoff_despite_new_requests(self):
        self.upstream.payload = dump([record(f"{s}/one") for s in
                                      ["journals/tdsc", "conf/first", "conf/next"]])
        self.service.run_once()
        with self.assertRaises(FeedPending):
            self.service.feed("conf/first")
        with patch.object(self.service, "build_stage", side_effect=ValueError("broken import")) as build:
            self.service.run_once()
            retry_at = self.service._pending_retry_at
            for stream in ["conf/next", "conf/first"] * 3:
                with self.assertRaises(FeedPending):
                    self.service.feed(stream)
                self.service.run_once()
            self.assertEqual(build.call_count, 1)
            self.assertEqual(self.service._pending_retry_at, retry_at)
            self.assertGreater(retry_at, time.time())
        self.service._pending_retry_at = 0
        original_build = self.service.build_stage
        with patch.object(self.service, "build_stage", wraps=original_build) as build:
            self.service.run_once()
            self.assertEqual(build.call_count, 1)
            self.assertEqual(set(build.call_args.args[1]), {"conf/first", "conf/next"})
        self.assertEqual(self.service.status()["pending_subscriptions"], [])

    def test_evicted_source_requested_again_does_not_spin_within_batch_drain(self):
        self.upstream.payload = dump([record("journals/tdsc/one"), record("conf/new/one")])
        self.service.run_once()
        with self.assertRaises(FeedPending):
            self.service.feed("conf/new")
        original_build = self.service.build_stage
        original_publish = self.service._publish

        def evict_and_request(stage, streams, metadata):
            self.assertEqual(self.service._evict_one(), "journals/tdsc")
            with self.assertRaises(FeedPending):
                self.service.feed("journals/tdsc")
            return original_publish(stage, streams, metadata)

        with patch.object(self.service, "build_stage", wraps=original_build) as build:
            with patch.object(self.service, "_publish", side_effect=evict_and_request):
                self.service.run_once()
            self.assertEqual(build.call_count, 1)
            self.assertEqual(self.service.status()["pending_subscriptions"], ["journals/tdsc"])
            self.service.run_once()  # Rebuild is allowed on a later tick.
            self.assertEqual(build.call_count, 2)
        self.assertEqual(self.service.status()["pending_subscriptions"], [])

    def test_disk_full_deferred_arrivals_join_next_batch_without_losing_requests(self):
        streams = ["conf/first", "conf/second", "conf/third"]
        self.upstream.payload = dump([record(f"{s}/one") for s in ["journals/tdsc"] + streams])
        self.service.run_once()
        self.service.store.register(streams[0])
        original_build = self.service.build_stage
        batches = []

        def arriving_build(snapshot, selected, **kwargs):
            batches.append(tuple(selected))
            if len(batches) == 1:
                full = sqlite3.OperationalError("database or disk is full")
                full.sqlite_errorcode = sqlite3.SQLITE_FULL
                with patch.object(self.service.store, "register", side_effect=full):
                    for stream in streams[1:]:
                        with self.assertRaises(FeedPending):
                            self.service.feed(stream)
            return original_build(snapshot, selected, **kwargs)

        with patch.object(self.service, "build_stage", side_effect=arriving_build):
            self.service.run_once()
        self.assertEqual(batches, [(streams[0],), tuple(streams[1:])])
        self.assertEqual(self.service._request_queue, set())
        self.assertEqual(self.service.status()["pending_subscriptions"], [])

    def test_failed_deferred_registration_preserves_all_unprocessed_requests(self):
        streams = {"conf/a", "conf/b", "conf/c"}
        self.service._request_queue.update(streams)
        with patch.object(self.service.store, "register", side_effect=OSError("write failed")):
            self.service.run_once()
        self.assertEqual(self.service._request_queue, streams)

    def test_restart_repairs_missing_snapshot_or_dtd_before_next_daily_check(self):
        self.service.run_once()
        for missing in [self.service.snapshot_path(), self.cache / "dblp.dtd"]:
            previous_downloads = self.upstream.downloads()
            missing.unlink()
            LocalDBLP(self.service.settings).run_once()
            self.assertEqual(self.upstream.downloads(), previous_downloads + 1)
            self.assertIsNotNone(self.service.snapshot_path())
            self.assertTrue((self.cache / "dblp.dtd").is_file())

    def test_update_delete_duplicate_titles_and_old_snapshot_cleanup(self):
        self.upstream.payload = dump([record("journals/tdsc/a"), record("journals/tdsc/b")])
        self.service.run_once()
        old_snapshot = self.service.snapshot_path()
        self.assertEqual(len(self.items()), 2)
        self.upstream.payload = dump([record("journals/tdsc/a", "2026-11-03", "Corrected title"),
                                      record("journals/tdsc/c")])
        self.force_sync()
        items = self.items()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].findtext("title"), "Corrected title")
        self.assertFalse(old_snapshot.exists())
        self.assertEqual(len(list(self.cache.glob("dblp-*.xml.gz"))), 1)
        self.assertFalse(any(self.cache.glob("*.part")))

    def test_failures_preserve_previous_feed_and_cleanup_temporary_files(self):
        self.service.run_once()
        original_snapshot = self.service.snapshot_path()
        original_feed = self.service.feed("journals/tdsc")
        for mode in ["bad_md5", "html", "http_error"]:
            with self.subTest(mode=mode):
                self.upstream.mode = mode
                self.force_sync()
                self.assertEqual(self.service.snapshot_path(), original_snapshot)
                self.assertEqual(self.service.feed("journals/tdsc"), original_feed)
                self.assertTrue(self.service.store.metadata()["last_error"])
                self.assertFalse(any(self.cache.glob("*.part")))
        self.upstream.mode = "ok"
        for invalid in [gzip.compress(b'<dblp><article key="journals/tdsc/broken">'),
                        self.upstream.payload[:-5]]:
            self.upstream.payload = invalid
            self.force_sync()
            self.assertEqual(self.service.feed("journals/tdsc"), original_feed)
            self.assertEqual(self.service.snapshot_path(), original_snapshot)
            self.assertFalse((self.cache / "dblp-stage.db").exists())

    def test_limits_keep_previous_data(self):
        self.service.run_once()
        original = self.service.snapshot_path()
        self.upstream.payload = dump([record("journals/tdsc/changed", title="Changed")])
        self.service.settings.max_dump_bytes = 1
        self.force_sync()
        self.assertEqual(self.service.snapshot_path(), original)
        self.assertIn("size limit", self.service.store.metadata()["last_error"])
        self.service.settings.max_dump_bytes = 2_000_000_000
        self.service.settings.max_cache_bytes = 1
        self.force_sync()
        self.assertEqual(self.service.snapshot_path(), original)
        self.assertIn("budget", self.service.store.metadata()["last_error"])

    def test_publish_failure_keeps_latest_snapshot_and_retries_cache_without_download(self):
        self.service.run_once()
        original = self.service.snapshot_path()
        original_feed = self.service.feed("journals/tdsc")
        self.upstream.payload = dump([record("journals/tdsc/new", title="New")])
        with patch.object(self.service.store, "publish", side_effect=OSError("disk write failed")):
            self.force_sync()
        self.assertNotEqual(self.service.snapshot_path(), original)
        self.assertFalse(original.exists())
        self.assertEqual(self.service.feed("journals/tdsc"), original_feed)
        self.assertEqual(len(list(self.cache.glob("dblp-*.xml.gz"))), 1)
        self.assertFalse(any(self.cache.glob("*.part")))
        downloads = self.upstream.downloads()
        self.service._pending_retry_at = 0
        self.service.run_once()
        self.assertEqual(self.upstream.downloads(), downloads)
        self.assertEqual(self.items()[0].findtext("title"), "New")
        self.assertEqual(self.service.status()["last_error"], "")

    def test_sql_transaction_failure_preserves_existing_feed(self):
        self.service.run_once()
        original = self.service.snapshot_path()
        self.upstream.payload = dump([record("journals/tdsc/new", title="New")])
        # Abort the insertion after the publication transaction has started.
        with closing(self.service.store.connect()) as conn, conn:
            conn.execute("""CREATE TRIGGER fail_new BEFORE INSERT ON papers
                BEGIN SELECT RAISE(ABORT, 'simulated publication failure'); END""")
        self.force_sync()
        self.assertNotEqual(self.service.snapshot_path(), original)
        self.assertEqual(self.items()[0].findtext("guid"), "https://dblp.org/rec/journals/tdsc/one")
        with closing(self.service.store.connect()) as conn, conn:
            conn.execute("DROP TRIGGER fail_new")
        self.service._pending_retry_at = 0
        self.service.run_once()
        self.assertEqual(self.items()[0].findtext("title"), "New")

    def test_explicit_stream_membership_and_crossref(self):
        self.service.store.register("conf/eurocrypt")
        self.upstream.payload = dump([
            record("series/other/one", extra='<stream>streams/journals/tdsc</stream>'),
            record("series/other/two", tag="inproceedings", extra='<crossref>conf/eurocrypt/2026</crossref>'),
        ])
        self.service.run_once()
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(len(self.items("conf/eurocrypt")), 1)

    def test_external_xml_entities_are_rejected(self):
        self.service.run_once()
        original = self.service.snapshot_path()
        secret = self.cache / "secret.txt"
        secret.write_text("must not appear in RSS", encoding="utf-8")
        xml = ('<!DOCTYPE dblp SYSTEM "dblp.dtd" ['
               f'<!ENTITY secret SYSTEM "{secret.as_uri()}">]><dblp>'
               + record("journals/tdsc/evil", title="&secret;") + '</dblp>')
        self.upstream.payload = gzip.compress(xml.encode())
        self.force_sync()
        self.assertEqual(self.service.snapshot_path(), original)
        self.assertNotIn("must not appear", self.service.feed("journals/tdsc"))
        self.assertTrue(self.service.store.metadata()["last_error"])

    def test_initial_failure_retries_and_recovers(self):
        self.upstream.mode = "html"
        self.service.run_once()
        self.assertFalse(self.service.status()["ready"])
        with self.assertRaises(FeedPending):
            self.service.feed("journals/tdsc")
        next_sync = float(self.service.store.metadata()["next_sync_at"])
        self.assertAlmostEqual(next_sync - time.time(), 3600, delta=10)
        self.upstream.mode = "ok"
        self.force_sync()
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.service.status()["last_error"], "")

    def test_interrupted_download_resumes_same_snapshot_and_verifies_checksum(self):
        noise = base64.b64encode(random.Random(42).randbytes(2_500_000))
        xml = gzip.decompress(self.upstream.payload).replace(b'</dblp>', b'<!--' + noise + b'--></dblp>')
        self.upstream.payload = gzip.compress(xml, mtime=0)
        self.upstream.mode = "disconnect_once"
        self.service.run_once()
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.upstream.downloads(), 2)
        second = [headers for path, headers in self.upstream.requests if path == "/dblp.xml.gz"][1]
        self.assertEqual(second["Range"], "bytes=1048576-")
        self.assertIn("If-Range", second)
        self.assertEqual(hashlib.md5(self.service.snapshot_path().read_bytes()).hexdigest(),
                         hashlib.md5(self.upstream.payload).hexdigest())

    def test_ignored_range_restarts_instead_of_appending_entire_file(self):
        noise = base64.b64encode(random.Random(42).randbytes(2_500_000))
        xml = gzip.decompress(self.upstream.payload).replace(b'</dblp>', b'<!--' + noise + b'--></dblp>')
        self.upstream.payload = gzip.compress(xml, mtime=0)
        self.upstream.mode = "ignore_range"
        self.service.run_once()
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.service.snapshot_path().stat().st_size, len(self.upstream.payload))

    def test_cross_process_lock_skips_overlapping_sync(self):
        with FileLock(str(self.cache / "dblp-sync.lock")):
            self.service.run_once()
        self.assertEqual(self.upstream.downloads(), 0)
        self.service.run_once()
        self.assertEqual(self.upstream.downloads(), 1)

    def test_crash_orphans_removed_but_current_and_legacy_data_preserved(self):
        self.service.run_once()
        current = self.service.snapshot_path()
        orphan = self.cache / ("dblp-" + "0" * 32 + ".xml.gz")
        orphan.write_bytes(b"orphan")
        legacy = self.cache / "dblp_cache.db"
        legacy.write_bytes(b"legacy")
        (self.cache / "dblp.xml.gz.part").write_bytes(b"incomplete")
        self.service.run_once()
        self.assertTrue(current.exists())
        self.assertFalse(orphan.exists())
        self.assertFalse(any(self.cache.glob("*.part")))
        self.assertEqual(legacy.read_bytes(), b"legacy")

    def test_domain_and_encoded_http_paths_and_pending_retry(self):
        with patch.object(self.service, "start"), patch.object(self.service, "stop"):
            with TestClient(create_app(self.service), base_url="http://papers.example.org:8080") as client:
                path = "/dblp/stream%3Astreams%2Fjournals%2Ftdsc%3A"
                response = client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["retry-after"], "60")
                self.service.run_once()
                for path in [path, "/dblp/stream:streams/journals/tdsc:"]:
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("application/rss+xml", response.headers["content-type"])
                    self.assertEqual(len(ET.fromstring(response.text).findall("channel/item")), 1)
                self.assertEqual(client.get("/dblp/security").status_code, 400)
                self.assertTrue(client.get("/health").json()["ready"])

    def test_real_background_lifespan_initializes_empty_cache(self):
        service = LocalDBLP(Settings(cache_dir=self.cache / "fresh", dump_url=self.upstream.url,
                                     minimum_free_bytes=0))
        try:
            with TestClient(create_app(service)) as client:
                path = "/dblp/stream%3Astreams%2Fjournals%2Ftdsc%3A"
                deadline = time.monotonic() + 10
                while True:
                    response = client.get(path)
                    if response.status_code == 200 or time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
                self.assertEqual(response.status_code, 200, response.text)
        finally:
            service.stop()
        self.assertFalse(service.thread.is_alive())
        self.assertTrue((service.settings.cache_dir / "request.log").exists())

    def test_native_uvicorn_process_and_http_host_header(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = dict(os.environ, DBLP_CACHE_DIR=str(self.cache / "native"),
                   DBLP_DUMP_URL=self.upstream.url)
        with (self.cache / "native-server.log").open("wb") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", str(port)],
                cwd=Path(__file__).resolve().parents[1], env=env,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                url = f"http://127.0.0.1:{port}/dblp/stream%3Astreams%2Fjournals%2Ftdsc%3A"
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    self.assertIsNone(process.poll(), "uvicorn exited unexpectedly")
                    try:
                        response = requests.get(url, headers={"Host": "papers.example.org:8080"}, timeout=1)
                        if response.status_code == 200:
                            break
                    except requests.ConnectionError:
                        pass
                    time.sleep(0.05)
                else:
                    self.fail("Native HTTP service did not become ready")
                self.assertIn("application/rss+xml", response.headers["Content-Type"])
                self.assertEqual(len(ET.fromstring(response.content).findall("channel/item")), 1)
                self.assertTrue(requests.get(f"http://127.0.0.1:{port}/health", timeout=2).json()["ready"])
            finally:
                process.terminate()
                process.wait(timeout=10)

    def test_catalog_includes_unsubscribed_venues_and_full_conference_names(self):
        self.upstream.payload = dump([
            record("journals/tdsc/a", extra="", title="Paper"),
            record("conf/newconf/a", tag="inproceedings", extra="<booktitle>NewConf</booktitle>"),
            record("conf/newconf/2026", tag="proceedings", title="International Conference on New Research, NewConf 2026"),
            record("journals/other/a"),
        ])
        self.service.run_once()
        result = self.service.store.search_venues("New Research")
        self.assertTrue(result["ready"])
        self.assertEqual(result["total_venues"], 3)
        self.assertEqual(result["total"], 1)
        venue = result["results"][0]
        self.assertEqual(venue["stream"], "conf/newconf")
        self.assertEqual(venue["name"], "NewConf")
        self.assertFalse(venue["feed_ready"])
        self.assertEqual(venue["rss_path"], "/dblp/stream%3Astreams%2Fconf%2Fnewconf%3A")
        self.assertEqual(self.service.store.subscriptions(), ["journals/tdsc"])

    def test_catalog_search_acronym_full_name_accents_filters_and_pagination(self):
        self.upstream.payload = dump([
            record("journals/tdsc/a").replace("Test Journal", "IEEE Trans. Dependable Secur. Comput."),
            record("journals/special/a").replace("Test Journal", "TDSC Special Journal"),
            record("conf/eurocrypt/a", tag="inproceedings", extra="<booktitle>EUROCRYPT</booktitle>"),
            record("journals/ecole/a").replace("Test Journal", "École Informatique"),
        ])
        self.service.run_once()
        self.assertEqual(self.service.store.search_venues("tdsc")["results"][0]["stream"], "journals/tdsc")
        self.assertEqual(self.service.store.search_venues("IEEE Transactions on Dependable and Secure Computing")["total"], 1)
        self.assertEqual(self.service.store.search_venues("ecole")["results"][0]["stream"], "journals/ecole")
        self.assertEqual(self.service.store.search_venues("", kind="conf")["total"], 1)
        self.assertEqual(self.service.store.search_venues("", kind="journals")["total"], 3)
        first = self.service.store.search_venues("", limit=2)
        second = self.service.store.search_venues("", limit=2, offset=2)
        self.assertEqual(first["total"], 4)
        self.assertEqual(len({v["stream"] for v in first["results"] + second["results"]}), 4)
        self.assertEqual(self.service.store.search_venues("%_' OR 1=1 --")["total"], 0)

    def test_catalog_prioritizes_complete_names_in_order(self):
        self.upstream.payload = dump([
            record("conf/acpr/a", tag="inproceedings", extra="<booktitle>Pattern Recognition and Computer Vision</booktitle>"),
            record("conf/cvippr/a", tag="inproceedings", extra="<booktitle>Computer Vision, Image Processing and Pattern Recognition</booktitle>"),
            record("conf/cvpr/a", tag="inproceedings", extra="<booktitle>CVPR</booktitle>"),
            record("conf/cvpr/2026", tag="proceedings", title="IEEE Conference on Computer Vision and Pattern Recognition, CVPR 2026"),
        ])
        self.service.run_once()
        result = self.service.store.search_venues("Computer Vision and Pattern Recognition")
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["results"][0]["stream"], "conf/cvpr")

    def test_catalog_migration_reuses_local_dump_and_keeps_feeds(self):
        self.service.run_once()
        old_feed = self.service.feed("journals/tdsc")
        with closing(self.service.store.connect()) as conn, conn:
            conn.execute("DELETE FROM venues")
            conn.execute("DELETE FROM metadata WHERE key LIKE 'catalog_%'")
        self.assertFalse(self.service.status()["catalog_ready"])
        self.service.run_once()
        self.assertEqual(self.upstream.downloads(), 1)
        self.assertTrue(self.service.status()["catalog_ready"])
        self.assertEqual(self.service.store.search_venues("tdsc")["total"], 1)
        self.assertEqual(self.service.feed("journals/tdsc"), old_feed)

    def test_catalog_updates_atomically_and_removes_disappeared_venues(self):
        self.upstream.payload = dump([record("journals/tdsc/a"), record("conf/old/a", tag="inproceedings")])
        self.service.run_once()
        self.upstream.payload = dump([record("journals/tdsc/a"), record("conf/new/a", tag="inproceedings")])
        with patch.object(self.service.store, "publish", side_effect=OSError("write failed")):
            self.force_sync()
        self.assertEqual(self.service.store.search_venues("old")["total"], 1)
        self.assertEqual(self.service.store.search_venues("new")["total"], 0)
        self.service._pending_retry_at = 0
        self.force_sync()
        self.assertEqual(self.service.store.search_venues("old")["total"], 0)
        self.assertEqual(self.service.store.search_venues("new")["total"], 1)

    def test_access_time_tracks_rss_requests_not_search_or_sync(self):
        self.service.run_once()
        with closing(self.service.store.connect()) as conn, conn:
            conn.execute("UPDATE subscriptions SET last_access=123 WHERE stream='journals/tdsc'")
        self.service.store.search_venues("tdsc")
        with closing(self.service.store.connect()) as conn:
            self.assertEqual(conn.execute("SELECT last_access FROM subscriptions").fetchone()[0], 123)
        before = time.time()
        self.service.feed("journals/tdsc")
        with closing(self.service.store.connect()) as conn:
            touched = conn.execute("SELECT last_access FROM subscriptions").fetchone()[0]
        self.assertGreaterEqual(touched, before)
        self.force_sync()
        with closing(self.service.store.connect()) as conn:
            self.assertEqual(conn.execute("SELECT last_access FROM subscriptions").fetchone()[0], touched)

    def test_cold_cache_expires_reclaims_disk_and_rebuilds_on_request(self):
        self.upstream.payload = dump([record('journals/tdsc/a')] + [
            record(f'conf/cold/{i:04}', title='x' * 3000, tag='inproceedings') for i in range(300)])
        self.service.store.register('conf/cold')
        self.service.run_once()
        self.service._release_reserve()
        before = self.service.store.path.stat().st_size
        with closing(self.service.store.connect()) as conn, conn:
            conn.execute("UPDATE subscriptions SET last_access=? WHERE stream='conf/cold'", (time.time()-31*86400,))
        self.service._last_maintenance_at = 0
        self.service.run_once()
        self.assertNotIn('conf/cold', self.service.store.subscriptions())
        self.assertLess(self.service.store.path.stat().st_size, before)
        self.assertEqual(self.service.store.search_venues('cold')['total'], 1)
        downloads = self.upstream.downloads()
        with self.assertRaises(FeedPending):
            self.service.feed('conf/cold')
        self.service.run_once()
        self.assertEqual(len(self.items('conf/cold')), 300)
        self.assertEqual(self.upstream.downloads(), downloads)

    def test_pressure_evicts_lru_and_preserves_newer_sources(self):
        streams = ['journals/tdsc', 'conf/middle', 'conf/warm']
        self.upstream.payload = dump([record(f'{stream}/{i:04}', title='x' * 3000)
                                      for stream in streams for i in range(300)])
        for stream in streams:
            self.service.store.register(stream)
        self.service.run_once()
        self.service._release_reserve()
        with closing(self.service.store.connect()) as conn, conn:
            for i, stream in enumerate(streams):
                conn.execute('UPDATE subscriptions SET last_access=? WHERE stream=?', (time.time()-1000+i, stream))
        self.service._protected_snapshot = self.service.snapshot_path()
        additional = 500_000
        self.service.settings.max_cache_bytes = self.service.cache_bytes() + additional - 100_000
        self.service._check_space(additional)
        self.assertEqual(set(self.service.store.subscriptions()), {'conf/middle', 'conf/warm'})
        self.assertLessEqual(self.service.cache_bytes()+additional, self.service.settings.max_cache_bytes)
        self.assertTrue(self.service.snapshot_path().exists())
        self.assertEqual(self.service.status()['last_evicted_stream'], 'journals/tdsc')

    def test_old_snapshot_can_be_removed_for_space_and_redownloaded_unconditionally(self):
        self.service.run_once()
        self.service._release_reserve()
        original = self.service.snapshot_path()
        before = self.service.feed('journals/tdsc')
        self.service.settings.max_cache_bytes = self.service.cache_bytes()+500-original.stat().st_size
        self.service._check_space(500)
        self.assertFalse(original.exists())
        self.assertIsNone(self.service.snapshot_path())
        self.assertIn('journals/tdsc', self.service.store.subscriptions())
        self.assertEqual(self.service.feed('journals/tdsc'), before)
        self.service.settings.max_cache_bytes = 9_000_000_000
        self.service.run_once()
        headers = [headers for path, headers in self.upstream.requests if path == '/dblp.xml.gz'][-1]
        self.assertNotIn('If-None-Match', headers)
        self.assertIsNotNone(self.service.snapshot_path())

    def test_database_pressure_drops_whole_source_and_updates_snapshot(self):
        self.service.settings.max_database_bytes = 600_000
        self.service.settings.reserve_bytes = 0
        self.service.store.register('conf/huge')
        self.service.store.register('conf/warm')
        self.upstream.payload = dump([
            record(f'conf/huge/{i:04}', datetime.now(timezone.utc).date().isoformat(), title='x'*3000)
            for i in range(800)
        ] + [record('conf/warm/a'), record('journals/tdsc/a')])
        self.service.run_once()
        self.assertTrue(self.service.status()['ready'])
        self.assertTrue(self.service.status()['catalog_ready'])
        self.assertEqual(self.service.store.search_venues('huge')['total'], 1)
        self.assertNotIn('conf/huge', self.service.store.subscriptions())
        with closing(self.service.store.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM papers WHERE stream='conf/huge'").fetchone()[0], 0)
        self.assertEqual(self.service.status()['last_error'], '')
        self.assertLessEqual(self.service.store.path.stat().st_size, 600_000)

    def test_real_disk_free_space_pressure_also_evicts_cache(self):
        self.upstream.payload = dump([record('journals/tdsc/a', title='x'*300_000), record('conf/warm/a')])
        self.service.store.register('conf/warm')
        self.service.run_once()
        self.service._release_reserve()
        self.service._protected_snapshot = self.service.snapshot_path()
        capacity = self.service.cache_bytes() + 100_000

        class Disk:
            @property
            def free(inner):
                return capacity - self.service.cache_bytes()

        with patch('local_dblp.shutil.disk_usage', return_value=Disk()):
            self.service._check_space(200_000)
        self.assertNotIn('journals/tdsc', self.service.store.subscriptions())
        self.assertIn('conf/warm', self.service.store.subscriptions())

    def test_minimum_unavoidable_space_failure_is_explicit_and_bounded(self):
        self.service.run_once()
        self.service._release_reserve()
        self.service.settings.max_cache_bytes = 1
        with self.assertRaisesRegex(CapacityError, 'budget'):
            self.service._check_space(2)
        self.assertIn('journals/tdsc', self.service.store.subscriptions())

    def test_eviction_during_reader_request_never_serves_partial_papers(self):
        self.upstream.payload = dump([record(f'journals/tdsc/{i:04}') for i in range(350)])
        self.service.run_once()
        original_delete = self.service.store.delete_papers
        seen = []

        def concurrent_request(conn, stream):
            try:
                self.service.feed(stream)
            except FeedPending:
                seen.append('pending')
            original_delete(conn, stream)

        with patch.object(self.service.store, 'delete_papers', side_effect=concurrent_request):
            self.service._evict_one()
        self.assertEqual(seen, ['pending'])
        with self.assertRaises(FeedPending):
            self.service.store.feed_data('journals/tdsc')
        self.assertIsNone(self.service._evict_one())
        self.service.run_once()
        self.assertEqual(len(self.items()), 300)

    def test_sqlite_full_at_publication_evicts_and_retries(self):
        self.service.store.register('conf/warm')
        self.upstream.payload = dump([record('journals/tdsc/a'), record('conf/warm/a')])
        self.service.run_once()
        original_publish = self.service.store.publish
        full = sqlite3.OperationalError('database or disk is full')
        full.sqlite_errorcode = sqlite3.SQLITE_FULL
        calls = []

        def once_full(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise full
            return original_publish(*args, **kwargs)

        self.upstream.payload = dump([record('journals/tdsc/b'), record('conf/warm/b', title='Fresh')])
        with patch.object(self.service.store, 'publish', side_effect=once_full):
            self.force_sync()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.service.status()['last_error'], '')
        self.assertEqual(self.items('conf/warm')[0].findtext('title'), 'Fresh')
        self.assertGreater(self.service.status()['evicted_sources_total'], 0)

    def test_evicted_source_is_not_resurrected_by_in_progress_stage(self):
        self.service.store.register('conf/warm')
        self.upstream.payload = dump([record('journals/tdsc/a'), record('conf/warm/a')])
        self.service.run_once()
        original_check = self.service._check_space
        evicted = []

        def pressure_once(additional=0, stage_conn=None):
            if stage_conn is not None and not evicted:
                evicted.append(self.service._evict_one(stage_conn))
            return original_check(additional, stage_conn)

        self.upstream.payload = dump([record('journals/tdsc/b'), record('conf/warm/b')])
        with patch.object(self.service, '_check_space', side_effect=pressure_once):
            self.force_sync()
        self.assertNotIn(evicted[0], self.service.store.subscriptions())
        with closing(self.service.store.connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM papers WHERE stream=?', (evicted[0],)).fetchone()[0], 0)
        self.assertEqual(self.service.store.search_venues(evicted[0].split('/')[-1])['total'], 1)

    def test_heap_spilling_preserves_latest_300_with_bounded_memory(self):
        self.service.settings.heap_record_limit = 20
        self.upstream.payload = dump([record(f'journals/tdsc/{i:04}') for i in range(950)])
        self.service.run_once()
        papers, _ = self.service.store.feed_data('journals/tdsc')
        self.assertEqual(len(papers), 300)
        self.assertEqual(papers[0]['key'], 'journals/tdsc/0949')
        self.assertEqual(papers[-1]['key'], 'journals/tdsc/0650')

    def test_crash_orphan_papers_are_reclaimed_without_recreating_subscription(self):
        self.service.run_once()
        with closing(self.service.store.connect()) as conn, conn:
            conn.execute("DELETE FROM subscriptions WHERE stream='journals/tdsc'")
        restarted = LocalDBLP(self.service.settings)
        restarted.run_once()
        self.assertEqual(restarted.store.subscriptions(), [])
        with closing(restarted.store.connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM papers').fetchone()[0], 0)
        self.assertEqual(restarted.store.search_venues('tdsc')['total'], 1)

    def test_legacy_database_migrates_access_times_and_incremental_vacuum(self):
        legacy = self.cache / 'legacy'
        legacy.mkdir()
        with closing(sqlite3.connect(legacy/'dblp_local.db')) as conn, conn:
            conn.executescript('''CREATE TABLE subscriptions(stream TEXT PRIMARY KEY, refreshed_at TEXT, snapshot TEXT);
                INSERT INTO subscriptions VALUES ('journals/tdsc', 'old', 'old');''')
        migrated = LocalDBLP(Settings(cache_dir=legacy, minimum_free_bytes=0))
        before = time.time()
        migrated.store.initialize()
        migrated.store.migrate_vacuum()
        with closing(migrated.store.connect()) as conn:
            row = conn.execute('SELECT last_access, paper_order_version FROM subscriptions').fetchone()
            self.assertGreaterEqual(row[0], before)
            self.assertEqual(row[1], 0)
            self.assertEqual(conn.execute('PRAGMA auto_vacuum').fetchone()[0], 2)

    def test_registration_when_full_queues_request_and_serves_existing_rss(self):
        self.upstream.payload = dump([record('journals/tdsc/a'), record('conf/new/a')])
        self.service.run_once()
        before = self.service.feed('journals/tdsc')
        full = sqlite3.OperationalError('database or disk is full')
        full.sqlite_errorcode = sqlite3.SQLITE_FULL
        with patch.object(self.service.store, 'register', side_effect=full):
            self.assertEqual(self.service.feed('journals/tdsc'), before)
            with self.assertRaises(FeedPending):
                self.service.feed('conf/new')
        self.service.run_once()
        self.assertEqual(len(self.items('conf/new')), 1)

    def test_disk_full_short_write_is_truncated_and_download_retries_cleanly(self):
        self.service.run_once()
        self.upstream.payload = dump([record('journals/tdsc/new', title='Newest snapshot')])
        original_open = Path.open
        failed = []

        class ShortWrite:
            def __init__(inner, file):
                inner.file = file

            def __enter__(inner):
                inner.file.__enter__()
                return inner

            def write(inner, data):
                inner.file.write(data[:13])
                inner.file.flush()
                raise OSError(errno.ENOSPC, 'simulated disk full after a partial write')

            def __exit__(inner, *args):
                return inner.file.__exit__(*args)

        def open_with_one_short_write(path, mode='r', *args, **kwargs):
            file = original_open(path, mode, *args, **kwargs)
            if path.name == 'dblp.xml.gz.part' and mode in {'wb', 'ab'} and not failed:
                failed.append(True)
                return ShortWrite(file)
            return file

        with patch.object(Path, 'open', new=open_with_one_short_write):
            self.force_sync()
        self.assertTrue(failed)
        self.assertEqual(self.service.status()['last_error'], '')
        self.assertEqual(hashlib.md5(self.service.snapshot_path().read_bytes()).hexdigest(),
                         hashlib.md5(self.upstream.payload).hexdigest())
        self.assertFalse(any(self.cache.glob('*.part')))

    def test_schema_initialization_disk_full_releases_space_and_recovers(self):
        self.service.run_once()
        initialize = self.service.store.initialize
        full = sqlite3.OperationalError('database or disk is full')
        full.sqlite_errorcode = sqlite3.SQLITE_FULL
        attempts = []

        def once_full():
            attempts.append(1)
            if len(attempts) == 1:
                raise full
            return initialize()

        with patch.object(self.service.store, 'initialize', side_effect=once_full):
            self.service.run_once()
        self.assertGreaterEqual(len(attempts), 2)
        self.assertTrue(self.service.status()['ready'])
        self.assertEqual(len(self.items()), 1)

    def test_restart_recovers_cache_failure_from_latest_local_snapshot(self):
        self.service.run_once()
        self.upstream.payload = dump([record('journals/tdsc/new', title='New after restart')])
        with patch.object(self.service.store, 'publish', side_effect=OSError('temporary I/O failure')):
            self.force_sync()
        downloads = self.upstream.downloads()
        restarted = LocalDBLP(self.service.settings)
        restarted.run_once()
        self.assertEqual(self.upstream.downloads(), downloads)
        self.assertEqual(ET.fromstring(restarted.feed('journals/tdsc')).findtext('channel/item/title'), 'New after restart')
        self.assertEqual(restarted.status()['last_error'], '')

    def test_unknown_streams_do_not_create_cache_or_trigger_scans(self):
        self.service.run_once()
        before = self.service.store.subscriptions()
        with patch.object(self.service, 'build_stage', side_effect=AssertionError('unknown stream must not scan')):
            for i in range(20):
                rss = self.service.feed(f'conf/unknown{i}')
                self.assertEqual(len(ET.fromstring(rss).findall('channel/item')), 0)
            self.service.run_once()
        self.assertEqual(self.service.store.subscriptions(), before)
        with self.assertRaises(ValueError):
            normalize_stream('stream:streams/conf/' + 'x'*513 + ':')

    def test_failure_after_releasing_old_snapshot_recovers_and_preserves_rss(self):
        self.service.run_once()
        before = self.service.feed('journals/tdsc')
        self.service._discard_old_snapshot()
        self.upstream.mode = 'bad_md5'
        self.force_sync()
        self.assertIsNone(self.service.snapshot_path())
        self.assertEqual(self.service.feed('journals/tdsc'), before)
        self.upstream.mode = 'ok'
        self.force_sync()
        self.assertIsNotNone(self.service.snapshot_path())
        self.assertEqual(self.service.status()['last_error'], '')

    def test_search_page_and_api_are_read_only_and_validate_parameters(self):
        with patch.object(self.service, "start"), patch.object(self.service, "stop"):
            with TestClient(create_app(self.service), base_url="http://papers.example.org:8080") as client:
                pending = client.get("/api/venues?q=tdsc").json()
                self.assertFalse(pending["ready"])
                self.service.run_once()
                response = client.get("/search?q=tdsc")
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertIn('id="search-form"', response.text)
                self.assertIn("navigator.clipboard", response.text)
                self.assertIn("new URL(item.rss_path, location.href)", response.text)
                self.assertEqual(client.get("/", follow_redirects=False).headers["location"], "/search")
                data = client.get("/api/venues?q=tdsc&kind=journals").json()
                self.assertEqual(data["total"], 1)
                self.assertTrue(data["results"][0]["feed_ready"])
                feed = client.get(data["results"][0]["rss_path"])
                self.assertEqual(feed.status_code, 200)
                self.assertEqual(str(feed.request.url), "http://papers.example.org:8080" + data["results"][0]["rss_path"])
                for args in ["limit=0", "limit=101", "offset=-1", "kind=bad", "q=" + "x"*201]:
                    self.assertEqual(client.get("/api/venues?" + args).status_code, 422)


if __name__ == "__main__":
    unittest.main()
