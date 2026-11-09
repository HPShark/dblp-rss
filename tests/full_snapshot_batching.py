"""Opt-in full-dump batching check; uses an isolated DB and never downloads data."""

import argparse
import json
import logging
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_dblp import FeedPending, LocalDBLP, Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Existing dblp-<md5>.xml.gz")
    args = parser.parse_args()
    snapshot = args.snapshot.absolute()
    dtd = snapshot.parent / "dblp.dtd"
    before = (snapshot.stat().st_size, snapshot.stat().st_mtime_ns)
    first = "journals/tdsc"
    arrivals = ["conf/acns", "conf/cvpr", "conf/eurocrypt", "journals/compsec",
                "journals/joc", "journals/tcc", "journals/tifs", "journals/tissec",
                "journals/tocs", "journals/toplas"]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Keep all writes in a temporary local directory; read the existing gzip
    # directly, without copying a 1+ GB file or changing the user's live cache.
    with tempfile.TemporaryDirectory(prefix="dblp-batch-") as temp:
        cache = Path(temp)
        shutil.copyfile(dtd, cache / "dblp.dtd")
        service = LocalDBLP(Settings(cache_dir=cache, minimum_free_bytes=0))
        service.store.initialize()
        service.store.set_metadata({"snapshot_file": snapshot.name,
            "snapshot": snapshot.name[5:-7], "next_sync_at": time.time() + 86400})
        service.store.register(first)
        original_build = service.build_stage
        batches, durations, failures = [], [], []
        scanning = threading.Event()

        class Progress(logging.Handler):
            def emit(self, record):
                if record.msg == "Scanned %d DBLP records" and len(batches) == 1:
                    scanning.set()

        def measured_build(local_snapshot, streams, **kwargs):
            batches.append(list(streams))
            started = time.monotonic()
            kwargs.setdefault("dtd_path", dtd)
            stage = original_build(local_snapshot, streams, **kwargs)
            durations.append(round(time.monotonic() - started, 2))
            return stage

        def run():
            try:
                service.run_once()
            except BaseException as exc:
                failures.append(repr(exc))

        progress = Progress()
        logging.getLogger("local_dblp").addHandler(progress)
        worker = threading.Thread(target=run, name="full-batch-check", daemon=True)
        try:
            with patch.object(service, "build_stage", side_effect=measured_build), \
                 patch.object(service, "snapshot_path", return_value=snapshot), \
                 patch.object(service, "_discard_old_snapshot", return_value=False), \
                 patch.object(service, "synchronize", side_effect=AssertionError("Unexpected network sync")) as sync:
                worker.start()
                try:
                    assert scanning.wait(120), "First scan did not reach 250,000 records"
                    for stream in arrivals * 2:
                        try:
                            service.feed(stream)
                        except FeedPending:
                            pass
                        else:
                            raise AssertionError(f"New source unexpectedly ready: {stream}")
                    assert len(batches) == 1, "Requests did not arrive during the first scan"
                    worker.join(900)
                    assert not worker.is_alive(), "Full scans timed out"
                    assert not failures, failures
                    assert batches == [[first], sorted(arrivals)], batches
                    counts = {}
                    tdsc_front = []
                    for stream in [first] + arrivals:
                        items = ET.fromstring(service.feed(stream)).findall("channel/item")
                        assert items, f"No records imported for {stream}"
                        counts[stream] = len(items)
                        if stream == first:
                            dc = "{http://purl.org/dc/elements/1.1/}"
                            prism = "{http://prismstandard.org/namespaces/basic/2.0/}"
                            chronology = [(int(item.findtext(dc + "date")[:4]),
                                int(item.findtext(prism + "volume") or 0),
                                int(item.findtext(prism + "number") or 0)) for item in items]
                            assert chronology == sorted(chronology, reverse=True), "TDSC issue order regressed"
                            tdsc_front = [{"title": item.findtext("title"), "year_volume_issue": key}
                                          for item, key in zip(items[:3], chronology[:3])]
                    assert service.status()["pending_subscriptions"] == []
                    assert service.status()["last_error"] == ""
                    for _ in range(3):
                        service.run_once()
                    assert len(batches) == 2, "Ready/idle sources triggered extra scans"
                    sync.assert_not_called()
                    print(json.dumps({"batch_sizes": list(map(len, batches)),
                        "scan_seconds": durations, "feed_item_counts": counts,
                        "venue_count": service.status()["venue_count"],
                        "tdsc_front": tdsc_front,
                        "network_syncs": sync.call_count, "extra_idle_scans": len(batches) - 2},
                        ensure_ascii=False), flush=True)
                finally:
                    service.stop()
                    worker.join(30)
                    if worker.is_alive():
                        raise RuntimeError("Parser did not stop; inspect the temporary test directory")
        finally:
            logging.getLogger("local_dblp").removeHandler(progress)
    assert before == (snapshot.stat().st_size, snapshot.stat().st_mtime_ns)


if __name__ == "__main__":
    main()
