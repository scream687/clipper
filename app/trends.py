"""What India is watching and searching: Google Trends India, the user's upcoming events (GTA 6 ...), and
uploads from big Indian creators that are beating their channel's usual views. All free, no keys."""

import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from statistics import median
from typing import Any

from app import DATA_DIR, SETTINGS_FILE

GOOGLE_TRENDS = "https://trends.google.com/trending/rss?geo=IN"
FEED = "https://www.youtube.com/feeds/videos.xml?playlist_id=UULF{}"  # UULF = long uploads only, no Shorts
CREATORS = [  # India, all categories; editable in Settings
    "@rajshamani", "@BeerBiceps", "@RanveerAllahbadia", "@nikhil.kamath", "@ShubhankarMishraOfficial", "@HindiRush",
    "@dhruvrathee", "@thedeshbhakt", "@thelallantop", "@warikoo", "@SandeepSeminars", "@TechnicalGuruji",
    "@CarryMinati", "@TriggeredInsaan", "@SamayRainaOfficial", "@BBKiVines", "@FukraInsaan", "@ElvishYadavVlogs",
    "@MostlySane", "@ZakirKhan", "@KunalKamra", "@AbhiNiyu", "@TotalGaming093", "@MortaLyt", "@Mythpat", "@Dostcast",
]
UPCOMING = [{"date": "2026-11-19", "terms": ["GTA 6", "GTA VI", "Grand Theft Auto"]}]  # default; editable in Settings
UPCOMING_BEFORE_D, UPCOMING_AFTER_D = 60, 14   # an event is hot from 60 days before to 14 days after
PER_CHANNEL = 2                                # news channels post many a day; keep the list varied
MIN_RATIO = 1.2                                # "beating the usual" means at least 20% above it
FRESH_D = 7                                    # trending uploads: published in the last week
CACHE_S = 30 * 60
MAX_CREATORS = 60
TIMEOUT_S = 15
IDS_FILE = DATA_DIR / "channel_ids.json"
NS = {"a": "http://www.w3.org/2005/Atom", "m": "http://search.yahoo.com/mrss/", "yt": "http://www.youtube.com/xml/schemas/2015",
      "ht": "https://trends.google.com/trending/rss"}
_cache: dict[str, tuple[float, Any]] = {}  # ponytail: in-memory, refetched after a restart


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
        return response.read()


def _cached(key: str, fetch):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    value = fetch()
    _cache[key] = (time.time(), value)
    return value


def parse_google(xml: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    out = []
    for item in root.iter("item"):
        term = (item.findtext("title") or "").strip()
        if term:
            out.append({"term": term, "traffic": item.findtext("ht:approx_traffic", "", NS),
                        "news": [n.findtext("ht:news_item_title", "", NS) for n in item.findall("ht:news_item", NS)][:2]})
    return out


def searches() -> list[dict[str, Any]]:
    """Google Trends India, most searched first. Empty when unreachable."""
    try:
        found = _cached("google", lambda: parse_google(_get(GOOGLE_TRENDS)))
    except Exception:  # trends are a hint; never block clipping on them
        return []
    return sorted(found, key=lambda s: -traffic(s["traffic"]))


def traffic(text: str) -> int:
    """'20,000+' -> 20000."""
    digits = re.sub(r"[^0-9]", "", text or "")
    return int(digits) if digits else 0



def parse_upcoming(lines: list[str]) -> list[dict[str, Any]]:
    """'2026-11-19 GTA 6 / GTA VI / Grand Theft Auto' -> {date, terms}. Lines without a valid date are dropped."""
    out = []
    for line in lines:
        head, _, rest = line.strip().partition(" ")
        try:
            date.fromisoformat(head)
        except ValueError:
            continue
        terms = [t.strip() for t in rest.split("/") if t.strip()][:6]
        if terms:
            out.append({"date": head, "terms": terms})
    return out[:20]


def upcoming_now(events: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    """Events inside their hot window, with days left (negative = days since)."""
    today = today or date.today()
    out = []
    for e in events:
        days = (date.fromisoformat(e["date"]) - today).days
        if -UPCOMING_AFTER_D <= days <= UPCOMING_BEFORE_D:
            out.append({**e, "days": days})
    return sorted(out, key=lambda e: abs(e["days"]))


def _when(days: int) -> str:
    if days > 1:
        return f"in {days} days"
    return {1: "tomorrow", 0: "today", -1: "yesterday"}.get(days, f"{-days} days ago")


def _channel_ids() -> dict[str, str]:
    try:
        return json.loads(IDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def channel_id(handle: str) -> str | None:
    """@handle -> UC… id, cached on disk forever (ids never change)."""
    ids = _channel_ids()
    if handle.lower() in ids:
        return ids[handle.lower()]
    found = re.search(rb'"externalId":"(UC[\w-]{22})"', _get(f"https://www.youtube.com/{handle}"))
    if not found:
        return None
    ids = {**_channel_ids(), handle.lower(): found.group(1).decode()}
    IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    IDS_FILE.write_text(json.dumps(ids, indent=1), encoding="utf-8")
    return ids[handle.lower()]


def parse_feed(xml: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    out = []
    for e in root.findall("a:entry", NS):
        stats = e.find("m:group/m:community/m:statistics", NS)
        vid = e.findtext("yt:videoId", "", NS)
        out.append({"id": vid, "title": e.findtext("a:title", "", NS),
                    "channel": e.findtext("a:author/a:name", "", NS),
                    "published": e.findtext("a:published", "", NS), "url": f"https://www.youtube.com/watch?v={vid}",
                    "views": int(stats.get("views", 0)) if stats is not None else 0})
    return out


def outliers(uploads: list[dict[str, Any]], now: datetime, fresh_d: int = FRESH_D) -> list[dict[str, Any]]:
    """Fresh uploads scored against the channel's usual: views / median views of its recent uploads.
    ponytail: older uploads have had longer to collect views, so a 1-day-old video is under-scored; fine for
    ranking hits, add an age curve if fresh uploads keep ranking too low."""
    usual = median([u["views"] for u in uploads]) if uploads else 0
    out = []
    for u in uploads:
        age_h = (now - datetime.fromisoformat(u["published"])).total_seconds() / 3600
        if 0 < age_h <= fresh_d * 24 and usual and u["views"] > usual * MIN_RATIO:
            out.append({**u, "age_h": round(age_h), "ratio": round(u["views"] / usual, 2)})
    return out


def _creator(handle: str) -> list[dict[str, Any]]:
    try:
        cid = channel_id(handle)
        if not cid:
            return []
        return outliers(parse_feed(_get(FEED.format(cid[2:]))), datetime.now(timezone.utc))
    except Exception:  # one broken channel must not hide the others
        return []


def videos(handles: list[str], limit: int = 8) -> list[dict[str, Any]]:
    """Fresh uploads across the creators, the biggest hits for their channel first."""
    def fetch():
        with ThreadPoolExecutor(max_workers=8) as pool:
            found = [v for batch in pool.map(_creator, handles)
                     for v in sorted(batch, key=lambda v: -v["ratio"])[:PER_CHANNEL]]
        return sorted(found, key=lambda v: -v["ratio"])
    return _cached("videos:" + ",".join(sorted(h.lower() for h in handles)), fetch)[:limit]


def note(events: list[dict[str, Any]], handles: list[str]) -> str:
    """Text for the clip-picking prompt; empty when nothing is reachable."""
    parts = []
    hot = upcoming_now(events)
    if hot:
        parts.append("Big upcoming events people in India are waiting for:")
        parts += [f"- {' / '.join(e['terms'])}: {_when(e['days'])} ({e['date']})" for e in hot]
    found = searches()[:20]
    if found:
        parts.append("Trending Google searches in India right now (with a headline):")
        parts += [f"- {s['term']} ({s['traffic']} searches){': ' + s['news'][0] if s['news'] else ''}" for s in found]
    hits = videos(handles, limit=10) if handles else []
    if hits:
        parts.append("Uploads beating their channel's usual views this week (topics people are clicking):")
        parts += [f"- {v['title']} ({v['channel']}, {v['ratio']}x usual)" for v in hits]
    return "\n".join(parts)


def settings() -> tuple[list[dict[str, Any]], list[str]]:
    """(upcoming events, trend creators) from settings.json, with the defaults when unset."""
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return saved.get("upcoming", UPCOMING), saved.get("trend_creators", CREATORS)


def current_note() -> str:
    try:
        return note(*settings())
    except Exception:  # never block clipping on trends
        return ""
