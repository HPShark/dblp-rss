"""Optional one-shot command. Importing this module never performs network I/O."""

import argparse
from pathlib import Path

from dblp import dblp_rss, normalize_stream
from local_dblp import LocalDBLP


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync if due and export one local RSS feed.")
    parser.add_argument("keyword", nargs="?", default="stream:streams/journals/tdsc:")
    parser.add_argument("--output", default="test/sample.xml")
    args = parser.parse_args()
    service = LocalDBLP()
    service.store.initialize()
    service.store.register(normalize_stream(args.keyword))
    service.run_once()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dblp_rss(args.keyword, service), encoding="utf-8")
    print(f"RSS saved to {output}")
