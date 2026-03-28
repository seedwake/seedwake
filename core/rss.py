"""Native RSS reader for Seedwake news actions."""

import html
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib import error, request
from xml.etree import ElementTree

from core.types import ActionResultEnvelope, JsonObject, NewsItem

FEED_TIMEOUT_SECONDS = 10
MAX_ITEMS_PER_FEED = 10
MAX_TOTAL_ITEMS = 20
TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")
RSS_REQUEST_HEADERS = {
    "User-Agent": "Seedwake/0.3.0 (+native-rss-reader)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
}
RSS_READ_EXCEPTIONS = (
    error.HTTPError,
    error.URLError,
    OSError,
    TimeoutError,
    ValueError,
    ElementTree.ParseError,
)


def read_news_result(feed_urls: list[str], timeout_seconds: int = 30) -> ActionResultEnvelope:
    normalized_urls = [item.strip() for item in feed_urls if item.strip()]
    if not normalized_urls:
        return _build_result(
            ok=False,
            summary="固定 RSS feed 列表未配置",
            data={},
            error_detail="news_feed_urls_not_configured",
        )

    items: list[tuple[NewsItem, float | None, int]] = []
    failures: list[str] = []
    sequence = 0
    deadline = time.monotonic() + max(1, timeout_seconds)
    for feed_url in normalized_urls:
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failures.append("rss_timeout_budget_exhausted")
                break
            xml_text = _fetch_feed_text(feed_url, timeout_seconds=min(FEED_TIMEOUT_SECONDS, max(1.0, remaining)))
            for item in _parse_feed_items(feed_url, xml_text):
                items.append((item, _sort_timestamp(item.get("published_at", "")), sequence))
                sequence += 1
        except RSS_READ_EXCEPTIONS as exc:
            failures.append(f"{feed_url}: {exc}")

    ordered_items = _order_items(items)
    data: JsonObject = {
        "items": ordered_items,
        "feeds": normalized_urls,
    }
    if failures:
        data["errors"] = failures

    if ordered_items:
        return _build_result(
            ok=True,
            summary=_summarize_items(ordered_items),
            data=data,
            error_detail=None,
        )
    if failures:
        return _build_result(
            ok=False,
            summary="RSS 读取失败",
            data=data,
            error_detail="; ".join(failures),
        )
    return _build_result(
        ok=True,
        summary="RSS 没有新的条目",
        data=data,
        error_detail=None,
    )


def _build_result(
    *,
    ok: bool,
    summary: str,
    data: JsonObject,
    error_detail,
) -> ActionResultEnvelope:
    return {
        "ok": ok,
        "summary": summary,
        "data": data,
        "error": error_detail,
        "run_id": None,
        "session_key": None,
        "transport": "native",
    }


def _fetch_feed_text(feed_url: str, *, timeout_seconds: float) -> str:
    req = request.Request(feed_url, headers=RSS_REQUEST_HEADERS, method="GET")
    with request.urlopen(req, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _parse_feed_items(feed_url: str, xml_text: str) -> list[NewsItem]:
    root = ElementTree.fromstring(xml_text)
    if _local_name(root.tag) == "feed":
        items = _parse_atom_entries(feed_url, root)
    else:
        items = _parse_rss_entries(feed_url, root)
    return items[:MAX_ITEMS_PER_FEED]


def _parse_rss_entries(feed_url: str, root: ElementTree.Element) -> list[NewsItem]:
    channel = _first_child(root, "channel")
    if channel is None:
        channel = root
    items: list[NewsItem] = []
    for item in _children(channel, "item"):
        items.append({
            "feed_url": feed_url,
            "guid": _child_text(item, "guid"),
            "link": _child_text(item, "link"),
            "title": _clean_text(_child_text(item, "title")),
            "published_at": _normalize_published_at(_child_text(item, "pubDate")),
            "summary": _extract_summary(item, "description", "encoded", "content"),
        })
    return items


def _parse_atom_entries(feed_url: str, root: ElementTree.Element) -> list[NewsItem]:
    items: list[NewsItem] = []
    for entry in _children(root, "entry"):
        items.append({
            "feed_url": feed_url,
            "guid": _child_text(entry, "id"),
            "link": _extract_atom_link(entry),
            "title": _clean_text(_child_text(entry, "title")),
            "published_at": _normalize_published_at(
                _child_text(entry, "updated") or _child_text(entry, "published")
            ),
            "summary": _extract_summary(entry, "summary", "content"),
        })
    return items


def _extract_atom_link(entry: ElementTree.Element) -> str:
    for child in entry:
        if _local_name(child.tag) != "link":
            continue
        rel = (child.attrib.get("rel") or "alternate").strip()
        href = (child.attrib.get("href") or "").strip()
        if href and rel == "alternate":
            return href
    for child in entry:
        if _local_name(child.tag) == "link":
            href = (child.attrib.get("href") or "").strip()
            if href:
                return href
    return ""


def _extract_summary(node: ElementTree.Element, *names: str) -> str:
    for name in names:
        value = _child_text(node, name)
        if value:
            return _clean_text(value)
    return ""


def _child_text(node: ElementTree.Element, name: str) -> str:
    child = _first_child(node, name)
    if child is None:
        return ""
    text = "".join(child.itertext()).strip()
    return text


def _first_child(node: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for child in node:
        if _local_name(child.tag) == name:
            return child
    return None


def _children(node: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in node if _local_name(child.tag) == name]


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _clean_text(value: str) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = TAG_PATTERN.sub(" ", text)
    text = WHITESPACE_PATTERN.sub(" ", text).strip()
    return text


def _normalize_published_at(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""
    parsed = _parse_datetime(normalized)
    if parsed is None:
        return normalized
    return parsed.astimezone(UTC).isoformat()


def _parse_datetime(value: str) -> datetime | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(stripped)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _sort_timestamp(value: str) -> float | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return parsed.timestamp()


def _order_items(items: list[tuple[NewsItem, float | None, int]]) -> list[JsonObject]:
    ranked = sorted(
        items,
        key=lambda item: (
            item[1] is not None,
            item[1] if item[1] is not None else float("-inf"),
            -item[2],
        ),
        reverse=True,
    )
    return [dict(item) for item, _, _ in ranked[:MAX_TOTAL_ITEMS]]


def _summarize_items(items: list[JsonObject]) -> str:
    if not items:
        return "RSS 没有新的条目"
    labels = []
    for item in items[:3]:
        title = str(item.get("title") or "").strip()
        feed_url = str(item.get("feed_url") or "").strip()
        if title and feed_url:
            labels.append(f"{title} ({feed_url})")
            continue
        if title:
            labels.append(title)
            continue
        summary = str(item.get("summary") or "").strip()
        if summary:
            labels.append(summary)
    if not labels:
        return f"RSS 新条目 {len(items)} 条"
    return f"RSS 新条目 {len(items)} 条：{'；'.join(labels)}"
