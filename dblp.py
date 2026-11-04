"""Render RSS from the local DBLP database; never call the search API."""

from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
import json
import re
from urllib.parse import unquote
from xml.etree import ElementTree as ET


NAMESPACES = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "prism": "http://prismstandard.org/namespaces/basic/2.0/",
}
for prefix, namespace in NAMESPACES.items():
    ET.register_namespace(prefix, namespace)


def normalize_stream(keyword):
    """Accept the existing encoded or decoded journal/conference stream URL."""
    if len(keyword) > 512:
        raise ValueError("DBLP stream 查询过长。")
    match = re.fullmatch(
        r"stream:streams/((?:journals|conf)/[A-Za-z0-9_.-]+):?",
        unquote(keyword).strip(),
    )
    if not match:
        raise ValueError(
            "仅支持期刊或会议 stream 查询，例如 stream:streams/journals/tdsc: "
            "或 stream:streams/conf/eurocrypt:"
        )
    return match.group(1)


def generate_rss_from_papers(papers_data, stream="", updated_at=None):
    """Render every retained record; pubDate means DBLP modification date."""
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"DBLP {stream}".strip()
    ET.SubElement(channel, "link").text = (
        f"https://dblp.org/db/{stream}/" if stream else "https://dblp.org/"
    )
    ET.SubElement(channel, "description").text = (
        "DBLP updates: latest 300 records plus all records modified in the last 30 days. "
        "pubDate is the DBLP record modification date, not the paper publication date."
    )
    ET.SubElement(channel, "language").text = "en"
    built = datetime.fromisoformat(updated_at) if updated_at else datetime.now(timezone.utc)
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(built, usegmt=True)

    def metadata(item, prefix, name, value):
        if value:
            ET.SubElement(item, f"{{{NAMESPACES[prefix]}}}{name}").text = str(value)

    for paper in papers_data:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = paper["title"]
        url = "https://dblp.org/rec/" + paper["key"]
        ET.SubElement(item, "link").text = url
        ET.SubElement(item, "guid", isPermaLink="true").text = url
        if paper["mdate"]:
            modified = datetime.fromisoformat(paper["mdate"]).replace(tzinfo=timezone.utc)
            ET.SubElement(item, "pubDate").text = format_datetime(modified, usegmt=True)
            metadata(item, "dc", "date", paper["mdate"])
        authors = json.loads(paper["authors"])
        for author in authors:
            metadata(item, "dc", "creator", author)
        metadata(item, "dc", "title", paper["title"])
        metadata(item, "dc", "source", paper["venue"])
        metadata(item, "dc", "type", "text")
        metadata(item, "prism", "publicationName", paper["venue"])
        metadata(item, "prism", "volume", paper["volume"])
        metadata(item, "prism", "number", paper["number"])
        for link in json.loads(paper["ee"]):
            metadata(item, "dc", "relation", link)
            if "doi.org/" in link:
                metadata(item, "dc", "identifier", "doi:" + link.split("doi.org/", 1)[1])
        # Do not mislabel mdate or a fabricated January 1 as the publication date.
        details = {
            "Year": paper["year"], "Venue": paper["venue"],
            "Authors": ", ".join(authors), "DBLP updated": paper["mdate"],
        }
        ET.SubElement(item, "description").text = "".join(
            f"<p>{label}: {escape(str(value))}</p>"
            for label, value in details.items() if value
        )
    ET.indent(rss)
    return ET.tostring(rss, encoding="unicode", xml_declaration=True)


def init_cache_db():
    """Compatibility entry point for Python callers."""
    from local_dblp import LocalDBLP
    LocalDBLP().store.initialize()


def dblp_rss(keyword, service=None):
    """Read a prepared local feed; the server lifespan owns synchronization."""
    if service is None:
        from local_dblp import LocalDBLP
        service = LocalDBLP()
        service.store.initialize()
    return service.feed(normalize_stream(keyword))
