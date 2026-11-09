DBLP RSS
===

Build [RSS feeds](https://en.wikipedia.org/wiki/RSS) from the daily DBLP XML dump, without calling the search API.

This is a quick and dirty solution. Use at your own risks.

# How the code works

- Downloads the official gzip snapshot on first startup, then checks every 24 hours using conditional HTTP requests. Network failures retry after one hour; storage failures retry after five minutes.
- Interrupted transfers retry up to four times, resuming via Range/If-Range when supported. MD5 and gzip/XML integrity are checked before publication.
- Streams XML into SQLite for requested journal/conference subscriptions only. Each stream retains its latest 300 records by publication order plus every record modified in the last 30 days; the feed returns all retained records.
- Journals sort descending by publication year, natural volume, natural issue, available publication month/day, DBLP modification date, then key. Conferences use year, available publication month/day, modification date, and key, ignoring proceedings-series volumes. Missing volume/issue fields skip that level; examples include `9 < 10`, `3 < 3-4 < 4`, and `S2 < S10`. Numeric volumes/issues precede textual supplements and missing values.
- A single shared key selects each stream's top 300 from the entire compressed dump before live publication, then unions all records modified within 30 days. Bounded candidates may spill to a temporary staging database; the complete historical dump is never imported into the live database just to be truncated. SQLite pruning and RSS rendering use that same key.
- Publication dates come from local XML `year/month` fields, with explicit precision and source. No publication date is inferred from a DOI, issue number, or DBLP modification date. An upgrade adds the `month` column and marks old subscriptions for one batched rebuild from the local dump (ordering version 2); cached RSS remains readable. Keep the existing cache mount; an upgrade itself does not require a new download. Scheduled daily checks and missing-dump recovery still apply.
- Keeps only the current compressed snapshot. A validated new snapshot is installed independently of optional paper-cache publication; failed caches retry against the newest local snapshot. No uncompressed XML is stored.
- Tracks RSS access times and evicts sources unused for 30 days. Under storage pressure, removes expired caches, can discard the old snapshot, then evicts whole sources in least-recently-used order. Search-directory entries remain available, and requested sources are rebuilt on demand. Evictions reclaim SQLite file space.
- Serves HTTP on `0.0.0.0:8080`. Existing IP/domain/reverse-proxy URLs continue to work.
- Example: `http://localhost:8080/dblp/stream%3Astreams%2Fjournals%2Ftdsc%3A`.
- `/search` lists all journal/conference streams identified in the snapshot, independently of the subscribed paper cache. Search names, abbreviations, and stream IDs, filter/paginate results, and copy RSS URLs using the browser's current origin. `/api/venues?q=TDSC&kind=all&limit=30&offset=0` provides the same directory as JSON.

So, to run it, you need to :
- Get the code
- Build the docker image
    ```bash
    cd dblp-rss
    docker build -t dblp-rss .
    ```
- Run the image on a server, this spawns the http server
    ```bash
    docker run -d -p 8080:8080 -v $(pwd)/cache:/app/cache --restart always --name dblp-rss dblp-rss
    ```
- Subscribe at `http://ip:8080/dblp/stream%3Astreams%2Fjournals%2Ftdsc%3A`, or use `http://your-domain:8080/dblp/stream%3Astreams%2Fjournals%2Ftdsc%3A`. Domain access also retains port `8080`. Use `conf` instead of `journals` for conferences. The search page is available at `http://your-domain:8080/search`; copied URLs preserve the browser's current origin, including its port. HTTPS requires a separately configured TLS proxy.

# Search for journals and conferences

Open `http://ip:8080/search` or `http://your-domain:8080/search`. The root path `/` redirects to this page. Search by an abbreviation, English name, or DBLP stream ID, such as `TDSC`, `Computer Vision and Pattern Recognition`, or `conf/eurocrypt`. Filter journals/conferences, browse pages, then select **Copy link** to add the RSS URL to your reader or **Open RSS** to inspect it. Copied URLs preserve the current protocol, hostname, and port, including `:8080`.

The page displays the directory size, update time, and cache availability. A source without a cache, including an evicted source, starts a background import on its first RSS request; retry the same URL if it returns `503`. An initial directory build refreshes the page automatically. Searching does not register subscriptions or download the dump.

Share a prefilled search with `http://your-domain:8080/search?q=TDSC&kind=journals`. The JSON directory endpoint is `/api/venues?q=TDSC&kind=all&limit=30&offset=0`; `kind` accepts `all`, `journals`, and `conf`, with at most 100 results per page.

# Health, synchronization, and storage status

Open `http://ip:8080/health` or `http://your-domain:8080/health` for JSON status, or run:

```bash
curl http://127.0.0.1:8080/health
```

| Fields | Meaning |
| --- | --- |
| `ready` | Whether the installed snapshot file referenced by metadata exists; does not mean every feed is current |
| `catalog_ready`, `venue_count`, `catalog_updated_at` | Directory initialization state, venue count, and last publication time |
| `subscriptions`, `pending_subscriptions` | Registered source count and IDs awaiting initial import or refresh to the newest snapshot |
| `last_sync_at`, `last_feed_sync_at` | Last successful snapshot installation and paper-cache publication, recorded separately |
| `last_checked_at`, `next_sync_at`, `last_error` | Last successful upstream check, next scheduled check/retry, and last recorded error; a cleared error is an empty string |
| `cache_bytes`, `cache_budget_bytes` | Total cache-directory usage and budget in bytes; default budget is 9 GB |
| `database_bytes`, `database_budget_bytes` | Main SQLite file size and per-database budget in bytes; default budget is approximately 1.5 GB |
| `cold_days` | Inactivity threshold for expiry, default 30 days |
| `evicted_sources_total`, `last_evicted_stream`, `last_eviction_at` | Cumulative eviction events and the last source/time; repeated evictions of a rebuilt source count again |
| `last_snapshot_discarded_at` | Last time an old snapshot was removed early to make room for an update |

Divide byte values by `1,000,000,000` for decimal GB. `next_sync_at` is a Unix-seconds timestamp and may be serialized as a string; other time fields normally use UTC ISO 8601 strings. Unset values may be `null`.

During initialization, `ready` or `catalog_ready` may be false. Nonempty `pending_subscriptions` indicates imports or refreshes remain; already cached feeds can still be read. Increasing eviction counts are expected during expiry or storage cleanup. For nonempty `last_error`, compare snapshot/cache timestamps and inspect `docker logs -f dblp-rss` on the server; a successful new snapshot can coexist with a cache-import error.

This endpoint is a **liveness** check: a normal status response uses HTTP 200 even during initialization. Readiness and synchronization monitoring must inspect the JSON fields as well.

# Configuration
First downloads and new subscription imports run in the background. Pending feeds return HTTP 503 with `Retry-After: 60`; existing feeds remain available. New subscriptions are parsed from the current local dump without downloading it again. Arbitrary DBLP search expressions are not supported.

New subscriptions are deduplicated by stream and processed in batches. Each scan freezes its source list at the start. Once it publishes, the worker immediately collects all pending sources for the next scan. For example, if A starts a 60-second scan and ten different sources arrive during it, scan 2 imports those ten together: two scans in total. Duplicate requests for A or those ten do not add scans. Sources first requested during scan 2 form scan 3; ready feeds do not trigger scans. Requests arriving during a daily snapshot scan join the following import batch too. The 60-second duration is an example, not a performance guarantee.

Each batch still streams the entire compressed dump, so CPU use may be high during scanning. An idle worker with no pending imports, directory rebuild, or scheduled daily sync does not scan. Import failures retain retry backoff even when new requests arrive; sources evicted under pressure are not repeatedly reimported within the same worker tick. Logs show batch numbers/counts, source IDs (up to 20), whether the directory is being built, record counts, and elapsed scan time. `pending_subscriptions` includes both active and waiting sources.

The venue directory covers all identified streams, while publication records are cached on demand. An upgrade builds a missing directory from the existing compressed snapshot without another download. Searching does not register subscriptions. The initial three verification feeds are examples, not a whitelist.

The existing `/app/cache` mount persists the database, access times, and synchronization schedule across restarts. The default data budget is 9 GB, with approximately 1.5 GB per database file. Compressed snapshots share that total budget and must leave room for the directory and import; the old fixed 2 GB snapshot limit has been removed. An 8 MB emergency reserve and bounded deletion transactions support low-disk cleanup; legacy databases migrate to incremental vacuum when space permits. Staging databases, rollback journals, and downloads share the storage budget. Old API cache files are preserved but no longer used. Docker images and host Docker logs are outside this budget; duplicate access-log output is disabled. Once the directory is ready, unknown stream IDs return empty RSS without registering subscriptions or triggering full scans. See `README.md` for full deployment details.

When space is tight, the old snapshot may be deleted before a replacement is verified. Cached RSS remains readable if that download fails; new sources wait for an automatic retry. A disk unable to hold even the newest snapshot and minimum directory, unavailable upstream networking, or a single source exceeding the database budget remains an explicit recoverable error rather than a promise of immediate success. Sources are evicted as a whole instead of silently truncating the 30-day record set.

Run locally without Docker using `python -m pip install -r requirements.txt` and `python server.py`. Run offline integration tests using `python -m pip install -r requirements-dev.txt` and `python -m unittest discover -s tests -v`.

Verification on 2026-11-09 passed all 66 automated tests in about 44 seconds, covering full-dump selection before publication, natural volume/issue ordering, date precision, legacy schema migration and a single local rebuild without downloads, synchronization, and storage cleanup. Earlier Chromium checks covered search, links, and responsive layout. Docker was neither installed nor run locally; server deployment retains the existing Dockerfile, port 8080, and `/app/cache` mount.

RSS `dc:date` preserves known date precision (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`). `pubDate` uses the known period's first day when only a year/month is available, explicitly described as approximate. Items expose `rssmeta:datePrecision`, `rssmeta:dateSource`, and `rssmeta:sortRank` under `https://github.com/HPShark/dblp-rss/ns/1.0/`. Unknown years omit `pubDate`. Month ranges preserve their original text and use their starting month; invalid/conflicting months fall back to the year. Readers that override XML order using equal `pubDate` values may need their source-order setting; unknown dates cannot establish the precise publication order within a year.

For an optional full-dump batching check, run `python tests/full_snapshot_batching.py cache/dblp-<md5>.xml.gz` against an existing snapshot with `dblp.dtd` beside it. The script writes only to a temporary database and performs no network synchronization. After scan 1 reaches 250,000 records, it requests ten more sources twice each. It verifies exactly two full scans with batch sizes `[1, 10]`, nonempty RSS for all eleven sources, and no extra idle scans, then removes the test database. This takes several minutes.

The full-dump check passed on 2026-11-09: each scan read 13,074,484 records, with batch sizes `[1, 10]` and durations of approximately 188 and 130 seconds (the first also built the 8,651-stream directory). All eleven feeds were nonempty, no subscriptions remained pending, and both network synchronizations and extra idle scans were zero. An independent check confirmed descending TDSC year/volume/issue order; its first three items were from 2026, volume 23, issue 4. Timings and item counts describe this local snapshot and machine only.

# Content

- `Dockerfile` : A docker image to deploy the RSS server
- `dblp.py` : normalizes stream queries and renders RSS
- `local_dblp.py` : daily synchronization, streaming XML parsing, SQLite retention and cleanup
- `server.py` : deploys a minimalist HTTP server to serve the result of the `dblp.py` file, and make it accessible to remote RSS aggregators
