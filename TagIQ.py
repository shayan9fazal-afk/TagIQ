import os
import re
import time
import math
import html
import json
from urllib.parse import parse_qs, urlparse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from flask import Flask, jsonify, request, make_response


APP_TITLE = "YouTube Channel Trending Tags"
DEFAULT_PORT = 8000

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
DEFAULT_REGION_CODE = "US"

# Minimal category name -> ID mapping (YouTube videoCategories.list IDs)
# Users often type names like "animation" instead of numeric IDs.
CATEGORY_NAME_TO_ID = {
    "film": "1",
    "animation": "1",
    "film & animation": "1",
    "autos": "2",
    "vehicles": "2",
    "autos & vehicles": "2",
    "music": "10",
    "pets": "15",
    "animals": "15",
    "pets & animals": "15",
    "sports": "17",
    "travel": "19",
    "events": "19",
    "travel & events": "19",
    "gaming": "20",
    "people": "22",
    "blogs": "22",
    "people & blogs": "22",
    "comedy": "23",
    "entertainment": "24",
    "news": "25",
    "politics": "25",
    "news & politics": "25",
    "howto": "26",
    "style": "26",
    "howto & style": "26",
    "education": "27",
    "science": "28",
    "technology": "28",
    "science & technology": "28",
}


def normalize_category_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        return s
    key = re.sub(r"\s+", " ", s.lower()).strip()
    return CATEGORY_NAME_TO_ID.get(key)

# Lightweight stopword list (good enough for titles/descriptions)
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "let",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "so",
    "than",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "they",
    "this",
    "to",
    "too",
    "up",
    "us",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
    "you",
    "your",
}


@dataclass(frozen=True)
class VideoRow:
    video_id: str
    title: str
    description: str
    published_at: datetime
    view_count: int
    duration_seconds: Optional[int] = None

    @property
    def age_days(self) -> float:
        now = datetime.now(timezone.utc)
        delta = now - self.published_at
        return max(delta.total_seconds() / 86400.0, 0.001)

    @property
    def views_per_day(self) -> float:
        return self.view_count / self.age_days

    @property
    def is_short(self) -> bool:
        if self.duration_seconds is not None:
            if self.duration_seconds <= 61:
                return True
        txt = f"{self.title}\n{self.description}".lower()
        if "#shorts" in txt or "shorts" in txt:
            return True
        return False


class SimpleTTLCache:
    def __init__(self, ttl_seconds: int = 600):
        self.ttl_seconds = ttl_seconds
        self._store: Dict[str, Tuple[float, object]] = {}

    def get(self, key: str):
        hit = self._store.get(key)
        if not hit:
            return None
        ts, value = hit
        if time.time() - ts > self.ttl_seconds:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object):
        self._store[key] = (time.time(), value)


cache = SimpleTTLCache(ttl_seconds=15 * 60)
app = Flask(__name__)


def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def _api_key() -> str:
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "Missing YOUTUBE_API_KEY. Set it as an environment variable and restart."
        )
    return key AIzaSyDuewgGmcN8J8bgDOuhYg4G4cgmWfdqHLk


def _yt_get(path: str, params: Dict[str, str]) -> dict:
    params = dict(params)
    params["key"] = _api_key()
    url = f"{YOUTUBE_API_BASE}/{path}"
    r = requests.get(url, params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"YouTube API error {r.status_code}: {r.text}")
    return r.json()


def parse_channel_input(channel_input: str) -> Dict[str, str]:
    """
    Returns one of:
    - {"channelId": "..."}
    - {"handle": "somehandle"} (without @)
    - {"query": "..."} (fallback search)
    """
    s = channel_input.strip()
    if not s:
        raise ValueError("channel input is empty")

    # Handle YouTube "results" URL pasted by mistake (extract search_query)
    # Example: https://www.youtube.com/results?search_query=cryingpanda
    if "youtube.com/results" in s and "search_query=" in s:
        try:
            u = urlparse(s)
            qs = parse_qs(u.query)
            q = (qs.get("search_query") or [""])[0].strip()
            if q:
                return {"query": q}
        except Exception:
            pass

    # Direct UC channelId
    if re.fullmatch(r"UC[a-zA-Z0-9_-]{20,}", s):
        return {"channelId": s}

    # URL patterns
    m = re.search(r"/channel/(UC[a-zA-Z0-9_-]{20,})", s)
    if m:
        return {"channelId": m.group(1)}

    m = re.search(r"youtube\.com/@([a-zA-Z0-9._-]+)", s)
    if m:
        return {"handle": m.group(1)}

    # Common legacy URL patterns: /c/<name> or /user/<name> (best-effort search)
    m = re.search(r"youtube\.com/(?:c|user)/([a-zA-Z0-9._-]+)", s)
    if m:
        return {"query": m.group(1)}

    # If user typed @handle
    m = re.fullmatch(r"@([a-zA-Z0-9._-]+)", s)
    if m:
        return {"handle": m.group(1)}

    # Fallback: treat as search query (channel name)
    return {"query": s}


def resolve_channel_id(channel_input: str) -> str:
    cache_key = f"channel_resolve::{channel_input.strip()}"
    cached = cache.get(cache_key)
    if isinstance(cached, str) and cached:
        return cached

    parsed = parse_channel_input(channel_input)

    if "channelId" in parsed:
        cache.set(cache_key, parsed["channelId"])
        return parsed["channelId"]

    if "handle" in parsed:
        # Newer API supports forHandle
        data = _yt_get(
            "channels",
            {
                "part": "id",
                "forHandle": parsed["handle"],
                "maxResults": "1",
            },
        )
        items = data.get("items", [])
        if items:
            cid = items[0]["id"]
            cache.set(cache_key, cid)
            return cid

        # Fallback search if forHandle didn’t work
        parsed = {"query": parsed["handle"]}

    # Search by query (best effort)
    data = _yt_get(
        "search",
        {
            "part": "snippet",
            "type": "channel",
            "q": parsed["query"],
            "maxResults": "1",
        },
    )
    items = data.get("items", [])
    if not items:
        raise RuntimeError("Could not find channel. Try a full channel URL or UC... ID.")
    cid = items[0]["snippet"]["channelId"]
    cache.set(cache_key, cid)
    return cid


def list_channel_videos(channel_id: str, max_videos: int = 100) -> List[str]:
    # Use "search" for latest videos, then fetch stats via "videos"
    video_ids: List[str] = []
    page_token: Optional[str] = None
    while len(video_ids) < max_videos:
        data = _yt_get(
            "search",
            {
                "part": "id",
                "channelId": channel_id,
                "type": "video",
                "order": "date",
                "maxResults": str(min(50, max_videos - len(video_ids))),
                **({"pageToken": page_token} if page_token else {}),
            },
        )
        for item in data.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if vid:
                video_ids.append(vid)
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return video_ids


def list_most_popular_videos(
    region_code: str = DEFAULT_REGION_CODE,
    video_category_id: Optional[str] = None,
    max_videos: int = 50,
) -> List[VideoRow]:
    """
    Pulls currently trending "mostPopular" videos for a region (and optional category).
    This is the closest available YouTube Data API signal for "what's trending on YouTube right now".
    """
    region_code = (region_code or DEFAULT_REGION_CODE).strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", region_code):
        region_code = DEFAULT_REGION_CODE

    out: List[VideoRow] = []
    page_token: Optional[str] = None
    while len(out) < max_videos:
        params: Dict[str, str] = {
            "part": "snippet,statistics,contentDetails",
            "chart": "mostPopular",
            "regionCode": region_code,
            "maxResults": str(min(50, max_videos - len(out))),
        }
        if video_category_id:
            params["videoCategoryId"] = str(video_category_id).strip()
        if page_token:
            params["pageToken"] = page_token

        data = _yt_get("videos", params)
        for item in data.get("items", []):
            snippet = item.get("snippet") or {}
            stats = item.get("statistics") or {}
            details = item.get("contentDetails") or {}
            vid = item.get("id", "")
            title = snippet.get("title") or ""
            desc = snippet.get("description") or ""
            published_at = _parse_yt_datetime(snippet.get("publishedAt"))
            view_count = int(stats.get("viewCount") or 0)
            duration_seconds = parse_iso8601_duration_seconds(details.get("duration"))
            out.append(
                VideoRow(
                    video_id=vid,
                    title=title,
                    description=desc,
                    published_at=published_at,
                    view_count=view_count,
                    duration_seconds=duration_seconds,
                )
            )
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return out


def _parse_yt_datetime(s: str) -> datetime:
    # Example: 2025-01-30T12:34:56Z
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fetch_video_rows(video_ids: Sequence[str]) -> List[VideoRow]:
    rows: List[VideoRow] = []
    # videos.list supports up to 50 IDs per call
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        data = _yt_get(
            "videos",
            {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(batch),
                "maxResults": "50",
            },
        )
        for item in data.get("items", []):
            snippet = item.get("snippet") or {}
            stats = item.get("statistics") or {}
            details = item.get("contentDetails") or {}
            vid = item.get("id", "")
            title = snippet.get("title") or ""
            desc = snippet.get("description") or ""
            published_at = _parse_yt_datetime(snippet.get("publishedAt"))
            view_count = int(stats.get("viewCount") or 0)
            duration_seconds = parse_iso8601_duration_seconds(details.get("duration"))
            rows.append(
                VideoRow(
                    video_id=vid,
                    title=title,
                    description=desc,
                    published_at=published_at,
                    view_count=view_count,
                    duration_seconds=duration_seconds,
                )
            )
    return rows


def parse_iso8601_duration_seconds(duration: Optional[str]) -> Optional[int]:
    """
    Parses YouTube ISO 8601 durations like:
    - PT15S
    - PT1M2S
    - PT2H3M4S
    Returns seconds or None if missing/unparseable.
    """
    if not duration or not isinstance(duration, str):
        return None
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not m:
        return None
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return (h * 3600) + (mi * 60) + s


TOKEN_RE = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)?", re.IGNORECASE)


def tokenize(text: str) -> List[str]:
    toks = [t.lower() for t in TOKEN_RE.findall(text or "")]
    return [t for t in toks if t not in STOPWORDS and len(t) >= 2]


def extract_hashtags(text: str) -> List[str]:
    tags = re.findall(r"(?<!\w)#([A-Za-z0-9_]{2,})", text or "")
    out: List[str] = []
    seen = set()
    for t in tags:
        t = t.strip()
        if not t:
            continue
        low = t.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(t)
    return out


def ngrams(tokens: Sequence[str], n: int) -> Iterable[Tuple[str, ...]]:
    if len(tokens) < n:
        return []
    return (tuple(tokens[i : i + n]) for i in range(0, len(tokens) - n + 1))


def phrase_candidates(title: str, description: str) -> List[str]:
    combined = f"{title}\n{description}"
    toks = tokenize(combined)
    cands: List[str] = []
    for n in (1, 2, 3):
        for g in ngrams(toks, n):
            phrase = " ".join(g).strip()
            if not phrase:
                continue
            if len(phrase) < 3:
                continue
            cands.append(phrase)
    # Add explicit hashtags (without #) as candidates
    for h in extract_hashtags(description):
        cands.append(h.lower())
    return cands


def soft_dedupe_keep_order(items: Sequence[str], limit: int) -> List[str]:
    seen = set()
    out: List[str] = []
    for it in items:
        key = re.sub(r"\s+", " ", it.strip().lower())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it.strip())
        if len(out) >= limit:
            break
    return out


def compute_trending_phrases(
    rows: Sequence[VideoRow],
    top_fraction: float = 0.2,
    max_phrases: int = 50,
) -> List[Tuple[str, float]]:
    if not rows:
        return []

    sorted_rows = sorted(rows, key=lambda r: r.views_per_day, reverse=True)
    k = max(1, int(math.ceil(len(sorted_rows) * top_fraction)))
    winners = sorted_rows[:k]

    # document frequency on winners vs all
    def docfreq(vs: Sequence[VideoRow]) -> Dict[str, int]:
        df: Dict[str, int] = {}
        for r in vs:
            cands = set(phrase_candidates(r.title, r.description))
            for c in cands:
                df[c] = df.get(c, 0) + 1
        return df

    df_all = docfreq(rows)
    df_win = docfreq(winners)

    # Recency boost: phrases found in recent winner videos weigh more
    recent_hits: Dict[str, float] = {}
    for r in winners:
        boost = 1.0 / (1.0 + (r.age_days / 30.0))  # 30-day half-ish decay
        for c in set(phrase_candidates(r.title, r.description)):
            recent_hits[c] = recent_hits.get(c, 0.0) + boost

    scored: List[Tuple[str, float]] = []
    n_all = max(1, len(rows))
    n_win = max(1, len(winners))
    for phrase, win_ct in df_win.items():
        base_ct = df_all.get(phrase, 0)
        win_rate = win_ct / n_win
        base_rate = base_ct / n_all
        lift = max(0.0, win_rate - base_rate)
        rec = recent_hits.get(phrase, 0.0)
        score = (lift * 10.0) + rec
        if score <= 0:
            continue
        scored.append((phrase, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:max_phrases]


def compute_trending_hashtags(
    rows: Sequence[VideoRow],
    top_fraction: float = 0.2,
    max_tags: int = 40,
) -> List[Tuple[str, float]]:
    if not rows:
        return []

    sorted_rows = sorted(rows, key=lambda r: r.views_per_day, reverse=True)
    k = max(1, int(math.ceil(len(sorted_rows) * top_fraction)))
    winners = sorted_rows[:k]

    def df(vs: Sequence[VideoRow]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in vs:
            hs = set(extract_hashtags(r.description) + extract_hashtags(r.title))
            for h in hs:
                key = h.lower()
                out[key] = out.get(key, 0) + 1
        return out

    df_all = df(rows)
    df_win = df(winners)

    recent_hits: Dict[str, float] = {}
    for r in winners:
        boost = 1.0 / (1.0 + (r.age_days / 21.0))  # faster decay for "current"
        hs = set(extract_hashtags(r.description) + extract_hashtags(r.title))
        for h in hs:
            key = h.lower()
            recent_hits[key] = recent_hits.get(key, 0.0) + boost

    scored: List[Tuple[str, float]] = []
    n_all = max(1, len(rows))
    n_win = max(1, len(winners))
    for tag, win_ct in df_win.items():
        base_ct = df_all.get(tag, 0)
        win_rate = win_ct / n_win
        base_rate = base_ct / n_all
        lift = max(0.0, win_rate - base_rate)
        rec = recent_hits.get(tag, 0.0)
        score = (lift * 10.0) + rec
        if score <= 0:
            continue
        scored.append((tag, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:max_tags]


def generate_tags_for_keywords(
    keywords: str,
    trending: Sequence[Tuple[str, float]],
    max_tags: int = 25,
    max_hashtags: int = 8,
) -> Dict[str, List[str]]:
    kw = keywords.strip()
    kw_tokens = set(tokenize(kw))

    # Base tags: from keywords themselves (unigrams + bigrams)
    base: List[str] = []
    toks = tokenize(kw)
    base.extend(toks)
    base.extend(" ".join(g) for g in ngrams(toks, 2))
    base.extend(" ".join(g) for g in ngrams(toks, 3))

    # Add matching trending phrases
    matched: List[str] = []
    for phrase, _score in trending:
        ptoks = set(tokenize(phrase))
        if not ptoks:
            continue
        if kw_tokens and (len(ptoks & kw_tokens) >= 1):
            matched.append(phrase)

    # Also add top trending even if no match (helps when keywords empty)
    top_trending = [p for p, _s in trending[: max(10, max_tags)]]

    tags = soft_dedupe_keep_order(
        base + matched + top_trending,
        limit=max_tags,
    )

    # Hashtags: keep short + no spaces (YouTube hashtags are typically single token)
    hashtags_src: List[str] = []
    for t in tags:
        if " " in t:
            continue
        if len(t) < 3:
            continue
        hashtags_src.append(t)

    hashtags = soft_dedupe_keep_order(
        [f"#{h.replace('#', '')}" for h in hashtags_src],
        limit=max_hashtags,
    )

    return {"tags": tags, "hashtags": hashtags}


def generate_shorts_hashtags(
    keywords: str,
    trending_shorts_hashtags: Sequence[Tuple[str, float]],
    max_hashtags: int = 8,
) -> List[str]:
    toks = tokenize(keywords or "")
    base = soft_dedupe_keep_order([t for t in toks if len(t) >= 3], limit=10)
    trend = [f"#{t}" for t, _s in trending_shorts_hashtags]
    merged = soft_dedupe_keep_order(
        [f"#{t}" for t in base] + trend + ["#shorts"],
        limit=max_hashtags,
    )
    return merged


def generate_global_hashtags(
    keywords: str,
    global_trending_hashtags: Sequence[Tuple[str, float]],
    max_hashtags: int = 8,
) -> List[str]:
    """
    Global hashtags should stay relevant. We only promote global hashtags that
    overlap with the user's keywords; otherwise we include a small "evergreen" set.
    """
    kw_tokens = set(tokenize(keywords or ""))
    picked: List[str] = []

    for tag, _s in global_trending_hashtags:
        tag_tokens = set(tokenize(tag))
        if not tag_tokens:
            continue
        if kw_tokens and (len(tag_tokens & kw_tokens) >= 1):
            picked.append(f"#{tag}")

    evergreen = ["#youtube", "#youtubeshorts", "#shorts", "#viral", "#trending"]
    merged = soft_dedupe_keep_order(picked + evergreen, limit=max_hashtags)
    return merged


def analyze_channel(channel_input: str, max_videos: int = 100) -> dict:
    cache_key = f"analysis::{channel_input.strip()}::{max_videos}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict) and cached:
        return cached

    rows = get_channel_rows(channel_input, max_videos=max_videos)
    channel_id = resolve_channel_id(channel_input)

    trending_overall = compute_trending_phrases(rows, top_fraction=0.2, max_phrases=60)
    shorts_rows = [r for r in rows if r.is_short]
    trending_shorts_phrases = compute_trending_phrases(
        shorts_rows, top_fraction=0.2, max_phrases=60
    )
    trending_shorts_hashtags = compute_trending_hashtags(
        shorts_rows, top_fraction=0.25, max_tags=50
    )

    top_videos = [
        {
            "videoId": r.video_id,
            "title": r.title,
            "views": r.view_count,
            "viewsPerDay": round(r.views_per_day, 2),
            "publishedAt": r.published_at.isoformat(),
            "url": f"https://www.youtube.com/watch?v={r.video_id}",
        }
        for r in rows[:10]
    ]

    out = {
        "channelId": channel_id,
        "videoCount": len(rows),
        "shortsCount": len(shorts_rows),
        "topVideos": top_videos,
        "trendingPhrases": [
            {"phrase": p, "score": round(s, 3)} for p, s in trending_overall
        ],
        "trendingShortsPhrases": [
            {"phrase": p, "score": round(s, 3)} for p, s in trending_shorts_phrases
        ],
        "trendingShortsHashtags": [
            {"tag": f"#{t}", "score": round(s, 3)} for t, s in trending_shorts_hashtags
        ],
    }
    cache.set(cache_key, out)
    return out


def get_channel_rows(channel_input: str, max_videos: int = 100) -> List[VideoRow]:
    """
    Cached fetch of channel videos + stats. Kept internal (not returned to clients).
    """
    cache_key = f"rows::{channel_input.strip()}::{max_videos}"
    cached = cache.get(cache_key)
    if isinstance(cached, list) and cached and isinstance(cached[0], VideoRow):
        return cached

    channel_id = resolve_channel_id(channel_input)
    video_ids = list_channel_videos(channel_id, max_videos=max_videos)
    rows = fetch_video_rows(video_ids)
    rows.sort(key=lambda r: r.views_per_day, reverse=True)
    cache.set(cache_key, rows)
    return rows


def _row_token_set(r: VideoRow) -> set:
    toks = set(tokenize(f"{r.title}\n{r.description}"))
    for h in extract_hashtags(r.title) + extract_hashtags(r.description):
        toks.add(h.lower())
    return toks


def _weighted_top_terms(
    rows: Sequence[VideoRow],
    weights: Sequence[float],
    max_tags: int,
    include_phrases: bool,
    include_hashtags: bool,
    prefer_shorts: bool = False,
) -> List[str]:
    """
    Builds a ranked list of tags from selected rows.
    """
    counts: Dict[str, float] = {}
    for r, w in zip(rows, weights):
        if prefer_shorts and not r.is_short:
            w *= 0.65
        if include_phrases:
            for p in set(phrase_candidates(r.title, r.description)):
                key = re.sub(r"\s+", " ", p.strip().lower())
                if len(key) < 3:
                    continue
                counts[key] = counts.get(key, 0.0) + w
        if include_hashtags:
            for h in set(extract_hashtags(r.title) + extract_hashtags(r.description)):
                key = h.strip().lower()
                if len(key) < 2:
                    continue
                counts[key] = counts.get(key, 0.0) + (w * 1.25)

    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return soft_dedupe_keep_order([k for k, _v in ranked], limit=max_tags)


def audience_targeted_suggestions(
    keywords: str,
    rows: Sequence[VideoRow],
    max_tags: int = 25,
    max_hashtags: int = 8,
    neighbors: int = 18,
) -> Dict[str, List[str]]:
    """
    Target the channel's *actual audience* by pulling terms from the most similar
    high-performing videos on THIS channel, rather than generic global trends.

    Similarity is based on token overlap between input keywords and each video's
    public metadata (title/description/hashtags), weighted by views/day.
    """
    if not rows:
        return {"audience_tags": [], "audience_hashtags": [], "audience_shorts_hashtags": []}

    kw_tokens = set(tokenize(keywords or ""))
    scored: List[Tuple[float, VideoRow]] = []
    for r in rows:
        toks = _row_token_set(r)
        if kw_tokens:
            overlap = len(kw_tokens & toks)
            denom = max(1, len(kw_tokens))
            sim = overlap / denom
        else:
            sim = 0.0
        perf = math.log1p(max(0.0, r.views_per_day))
        score = (sim * 3.0) + perf
        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    chosen = [r for _s, r in scored[: max(5, neighbors)]]

    # Weights: favor the top few strongly
    weights: List[float] = []
    for idx, (_s, r) in enumerate(scored[: max(5, neighbors)]):
        rank_boost = 1.0 / (1.0 + (idx / 4.0))
        recency = 1.0 / (1.0 + (r.age_days / 45.0))
        weights.append(rank_boost + recency)

    tags = _weighted_top_terms(
        chosen,
        weights,
        max_tags=max_tags,
        include_phrases=True,
        include_hashtags=False,
    )
    hashtags_raw = _weighted_top_terms(
        chosen,
        weights,
        max_tags=20,
        include_phrases=False,
        include_hashtags=True,
    )
    # Make hashtags single-token and prefixed
    hashtags = soft_dedupe_keep_order(
        [f"#{h.lstrip('#')}" for h in hashtags_raw if " " not in h],
        limit=max_hashtags,
    )

    shorts_only = [r for r in chosen if r.is_short]
    shorts_weights = [w for r, w in zip(chosen, weights) if r.is_short]
    shorts_hashtags_raw = _weighted_top_terms(
        shorts_only,
        shorts_weights,
        max_tags=20,
        include_phrases=False,
        include_hashtags=True,
        prefer_shorts=True,
    )
    shorts_hashtags = soft_dedupe_keep_order(
        [f"#{h.lstrip('#')}" for h in shorts_hashtags_raw if " " not in h] + ["#shorts"],
        limit=max_hashtags,
    )

    return {
        "audience_tags": tags,
        "audience_hashtags": hashtags,
        "audience_shorts_hashtags": shorts_hashtags,
    }


def analyze_global_trending(
    region_code: str = DEFAULT_REGION_CODE,
    video_category_id: Optional[str] = None,
    max_videos: int = 50,
) -> dict:
    video_category_id = normalize_category_id(video_category_id)
    cache_key = f"global::{region_code}::{video_category_id or ''}::{max_videos}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict) and cached:
        return cached

    rows = list_most_popular_videos(
        region_code=region_code, video_category_id=video_category_id, max_videos=max_videos
    )
    rows.sort(key=lambda r: r.views_per_day, reverse=True)

    phrases = compute_trending_phrases(rows, top_fraction=0.25, max_phrases=80)
    hashtags = compute_trending_hashtags(rows, top_fraction=0.25, max_tags=60)

    top_videos = [
        {
            "videoId": r.video_id,
            "title": r.title,
            "views": r.view_count,
            "viewsPerDay": round(r.views_per_day, 2),
            "publishedAt": r.published_at.isoformat(),
            "url": f"https://www.youtube.com/watch?v={r.video_id}",
        }
        for r in rows[:10]
    ]

    out = {
        "regionCode": region_code,
        "videoCategoryId": video_category_id,
        "videoCount": len(rows),
        "topVideos": top_videos,
        "trendingPhrases": [{"phrase": p, "score": round(s, 3)} for p, s in phrases],
        "trendingHashtags": [{"tag": f"#{t}", "score": round(s, 3)} for t, s in hashtags],
    }
    cache.set(cache_key, out)
    return out


def render_page(result: Optional[dict] = None, error: Optional[str] = None) -> str:
    def esc(x: str) -> str:
        return html.escape(x or "", quote=True)

    result_json = json.dumps(result, indent=2) if result else ""

    body = f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{esc(APP_TITLE)}</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; color: #111; }}
      .card {{ max-width: 980px; margin: 0 auto; }}
      h1 {{ font-size: 22px; margin: 0 0 8px; }}
      p {{ margin: 8px 0 16px; color: #333; }}
      form {{ display: grid; gap: 10px; padding: 14px; border: 1px solid #ddd; border-radius: 10px; background: #fafafa; }}
      label {{ font-size: 12px; color: #444; display: grid; gap: 6px; }}
      input, textarea {{ padding: 10px; border: 1px solid #ccc; border-radius: 8px; font-size: 14px; }}
      textarea {{ min-height: 80px; resize: vertical; }}
      button {{ padding: 10px 14px; border: 0; border-radius: 10px; background: #111; color: white; font-size: 14px; cursor: pointer; }}
      .error {{ padding: 10px 12px; border: 1px solid #f2b8b5; background: #fff3f2; border-radius: 10px; color: #7a1210; margin: 12px 0; }}
      .grid {{ display: grid; gap: 14px; grid-template-columns: 1fr; margin-top: 14px; }}
      .panel {{ border: 1px solid #eee; border-radius: 10px; padding: 12px; }}
      .pill {{ display: inline-block; padding: 6px 10px; margin: 6px 6px 0 0; border-radius: 999px; background: #f1f5f9; border: 1px solid #e2e8f0; font-size: 13px; }}
      .muted {{ color: #555; font-size: 12px; }}
      pre {{ background: #0b1020; color: #d6e7ff; padding: 12px; border-radius: 10px; overflow: auto; }}
      a {{ color: #0b57d0; }}
      .row {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
      .btn {{ padding: 8px 10px; border: 1px solid #ddd; border-radius: 10px; background: white; color: #111; font-size: 13px; cursor: pointer; }}
      .btn.primary {{ background:#111; color:#fff; border-color:#111; }}
      .toast {{ position: fixed; right: 18px; bottom: 18px; padding: 10px 12px; background: #111; color:#fff; border-radius: 10px; font-size: 13px; opacity: 0; transform: translateY(6px); transition: all .18s ease; }}
      .toast.show {{ opacity: 1; transform: translateY(0); }}
    </style>
    <script>
      async function copyText(text) {{
        try {{
          await navigator.clipboard.writeText(text);
          const t = document.getElementById('toast');
          if (t) {{
            t.textContent = "Copied to clipboard";
            t.classList.add('show');
            setTimeout(() => t.classList.remove('show'), 1200);
          }}
        }} catch (e) {{
          alert("Copy failed. Your browser may block clipboard access.");
        }}
      }}
    </script>
  </head>
  <body>
    <div class="card">
      <h1>{esc(APP_TITLE)}</h1>
      <p class="muted">Analyzes recent videos and extracts trending phrases from titles/descriptions based on views/day. Then generates suggested tags and hashtags for your keywords.</p>
      {"<div class='error'>" + esc(error) + "</div>" if error else ""}
      <form method="post" action="/analyze">
        <label>
          Channel URL / @handle / UC channel ID / channel name
          <input name="channel" placeholder="e.g. https://www.youtube.com/@MrBeast or UC..." required />
        </label>
        <label>
          Keywords or draft title (optional)
          <textarea name="keywords" placeholder="e.g. best productivity app 2026, ai automation, ..."></textarea>
        </label>
        <label>
          How many recent videos to analyze (default 100)
          <input name="max_videos" value="100" />
        </label>
        <div class="row">
          <label style="flex:1; min-width: 200px;">
            Global trending region (optional, default US)
            <input name="region" value="{esc(DEFAULT_REGION_CODE)}" placeholder="e.g. US, GB, PK" />
          </label>
          <label style="flex:1; min-width: 200px;">
            Global trending categoryId (optional)
            <input name="category_id" placeholder="e.g. 10 (Music) or animation" />
          </label>
        </div>
        <button type="submit">Analyze</button>
        <div class="muted">API key required: set <code>YOUTUBE_API_KEY</code> in your environment.</div>
      </form>
      {render_result_panels(result) if result else ""}
      {f"<div class='panel' style='margin-top:14px;'><div class='muted'>Raw JSON</div><pre>{esc(result_json)}</pre></div>" if result else ""}
    </div>
    <div id="toast" class="toast"></div>
  </body>
</html>
"""
    return body


def render_result_panels(result: dict) -> str:
    if not result:
        return ""

    trending = result.get("trendingPhrases") or []
    trending_shorts = result.get("trendingShortsPhrases") or []
    trending_shorts_hashtags = result.get("trendingShortsHashtags") or []
    global_trending = result.get("global") or {}
    global_hashtags = global_trending.get("trendingHashtags") or []
    tags = result.get("suggested", {}).get("tags") or []
    hashtags = result.get("suggested", {}).get("hashtags") or []
    shorts_hashtags = result.get("suggested", {}).get("shorts_hashtags") or []
    global_hashtags_suggested = result.get("suggested", {}).get("global_hashtags") or []
    top_videos = result.get("topVideos") or []

    def pill_list(items: Sequence[str]) -> str:
        return "".join(f"<span class='pill'>{html.escape(x)}</span>" for x in items)

    def trending_list(items: Sequence[dict]) -> str:
        out = []
        for it in items[:30]:
            p = html.escape(it.get("phrase", ""))
            s = html.escape(str(it.get("score", "")))
            out.append(f"<div><span class='pill'>{p}</span> <span class='muted'>score {s}</span></div>")
        return "".join(out)

    def hashtag_list(items: Sequence[dict]) -> str:
        out = []
        for it in items[:30]:
            p = html.escape(it.get("tag", ""))
            s = html.escape(str(it.get("score", "")))
            out.append(f"<div><span class='pill'>{p}</span> <span class='muted'>score {s}</span></div>")
        return "".join(out)

    def videos_list(items: Sequence[dict]) -> str:
        out = []
        for it in items[:10]:
            title = html.escape(it.get("title", ""))
            url = html.escape(it.get("url", ""))
            vpd = html.escape(str(it.get("viewsPerDay", "")))
            views = html.escape(str(it.get("views", "")))
            out.append(
                f"<div style='margin-top:8px;'><a href='{url}' target='_blank' rel='noreferrer'>{title}</a>"
                f"<div class='muted'>{views} views • {vpd} views/day</div></div>"
            )
        return "".join(out)

    tags_comma = ", ".join(tags)
    hashtags_space = " ".join(hashtags)
    shorts_hashtags_space = " ".join(shorts_hashtags)
    global_hashtags_space = " ".join(global_hashtags_suggested)
    copy_all = "\n".join(
        [
            "TAGS:",
            tags_comma,
            "",
            "HASHTAGS:",
            hashtags_space,
            "",
            "SHORTS HASHTAGS:",
            shorts_hashtags_space,
            "",
            "GLOBAL TRENDING HASHTAGS:",
            global_hashtags_space,
        ]
    ).strip()

    return f"""
      <div class="grid">
        <div class="panel">
          <div class="muted">Suggested tags</div>
          <div>{pill_list(tags) if tags else "<span class='muted'>Enter keywords to get more targeted suggestions.</span>"}</div>
          <div style="margin-top:10px;" class="row">
            <button class="btn" onclick="copyText({json.dumps(tags_comma)})">Copy tags (comma)</button>
            <button class="btn" onclick="copyText({json.dumps(copy_all)})">Copy all</button>
          </div>
          <div style="margin-top:10px;" class="muted">Suggested hashtags</div>
          <div>{pill_list(hashtags) if hashtags else "<span class='muted'>No hashtags generated.</span>"}</div>
          <div style="margin-top:10px;" class="row">
            <button class="btn" onclick="copyText({json.dumps(hashtags_space)})">Copy hashtags</button>
          </div>
          <div style="margin-top:10px;" class="muted">Shorts hashtags (channel-niche)</div>
          <div>{pill_list(shorts_hashtags) if shorts_hashtags else "<span class='muted'>Not enough Shorts data found on this channel yet.</span>"}</div>
          <div style="margin-top:10px;" class="row">
            <button class="btn primary" onclick="copyText({json.dumps(shorts_hashtags_space)})">Copy Shorts hashtags</button>
          </div>
          <div style="margin-top:10px;" class="muted">Global trending hashtags (YouTube-wide)</div>
          <div>{pill_list(global_hashtags_suggested) if global_hashtags_suggested else "<span class='muted'>No global hashtag suggestions yet (try setting region).</span>"}</div>
          <div style="margin-top:10px;" class="row">
            <button class="btn" onclick="copyText({json.dumps(global_hashtags_space)})">Copy global hashtags</button>
          </div>
        </div>
        <div class="panel">
          <div class="muted">Trending phrases (top 30)</div>
          <div>{trending_list(trending)}</div>
          <div style="margin-top:12px;" class="muted">Trending Shorts phrases (top 30)</div>
          <div>{trending_list(trending_shorts) if trending_shorts else "<span class='muted'>No Shorts detected in recent videos.</span>"}</div>
          <div style="margin-top:12px;" class="muted">Trending Shorts hashtags (top 30)</div>
          <div>{hashtag_list(trending_shorts_hashtags) if trending_shorts_hashtags else "<span class='muted'>No Shorts hashtags found.</span>"}</div>
          <div style="margin-top:12px;" class="muted">Trending global hashtags (top 30)</div>
          <div>{hashtag_list(global_hashtags) if global_hashtags else "<span class='muted'>Global trending hashtags not available.</span>"}</div>
        </div>
        <div class="panel">
          <div class="muted">Top recent performers (views/day)</div>
          <div>{videos_list(top_videos)}</div>
        </div>
      </div>
    """


@app.get("/")
def home():
    return render_page()


@app.post("/analyze")
def analyze_form():
    channel = (request.form.get("channel") or "").strip()
    keywords = (request.form.get("keywords") or "").strip()
    max_videos_raw = (request.form.get("max_videos") or "100").strip()
    region = (request.form.get("region") or DEFAULT_REGION_CODE).strip()
    category_id = (request.form.get("category_id") or "").strip() or None
    try:
        max_videos = int(max_videos_raw)
        max_videos = max(10, min(300, max_videos))
    except Exception:
        max_videos = 100

    try:
        base = analyze_channel(channel, max_videos=max_videos)
        rows = get_channel_rows(channel, max_videos=max_videos)
        trending_pairs = [
            (x["phrase"], float(x["score"])) for x in base.get("trendingPhrases", [])
        ]
        trending_shorts_hashtags_pairs = [
            (x["tag"].lstrip("#"), float(x["score"]))
            for x in base.get("trendingShortsHashtags", [])
        ]
        global_data = analyze_global_trending(
            region_code=region, video_category_id=category_id, max_videos=50
        )
        global_hashtag_pairs = [
            (x["tag"].lstrip("#"), float(x["score"]))
            for x in global_data.get("trendingHashtags", [])
        ]
        suggested = generate_tags_for_keywords(keywords, trending_pairs)
        suggested["shorts_hashtags"] = generate_shorts_hashtags(
            keywords, trending_shorts_hashtags_pairs
        )
        suggested["global_hashtags"] = generate_global_hashtags(
            keywords, global_hashtag_pairs
        )
        suggested.update(audience_targeted_suggestions(keywords, rows))
        base["suggested"] = suggested
        base["global"] = global_data
        base["input"] = {"channel": channel, "keywords": keywords, "maxVideos": max_videos}
        return render_page(result=base)
    except Exception as e:
        return render_page(error=str(e)), 400


@app.post("/api/analyze")
def analyze_api():
    try:
        payload = request.get_json(silent=True) or {}
        channel = str(payload.get("channel") or "").strip()
        keywords = str(payload.get("keywords") or "").strip()
        max_videos = payload.get("max_videos", 100)
        region = str(payload.get("region") or DEFAULT_REGION_CODE).strip()
        category_id = payload.get("category_id", None)
        category_id = str(category_id).strip() if category_id is not None else None
        if category_id == "":
            category_id = None
        try:
            max_videos = int(max_videos)
            max_videos = max(10, min(300, max_videos))
        except Exception:
            max_videos = 100

        if not channel:
            return jsonify({"error": "Missing required field: channel"}), 400

        base = analyze_channel(channel, max_videos=max_videos)
        rows = get_channel_rows(channel, max_videos=max_videos)
        trending_pairs = [
            (x["phrase"], float(x["score"])) for x in base.get("trendingPhrases", [])
        ]
        trending_shorts_hashtags_pairs = [
            (x["tag"].lstrip("#"), float(x["score"]))
            for x in base.get("trendingShortsHashtags", [])
        ]
        global_data = analyze_global_trending(
            region_code=region, video_category_id=category_id, max_videos=50
        )
        global_hashtag_pairs = [
            (x["tag"].lstrip("#"), float(x["score"]))
            for x in global_data.get("trendingHashtags", [])
        ]
        suggested = generate_tags_for_keywords(keywords, trending_pairs)
        suggested["shorts_hashtags"] = generate_shorts_hashtags(
            keywords, trending_shorts_hashtags_pairs
        )
        suggested["global_hashtags"] = generate_global_hashtags(
            keywords, global_hashtag_pairs
        )
        suggested.update(audience_targeted_suggestions(keywords, rows))
        base["suggested"] = suggested
        base["input"] = {"channel": channel, "keywords": keywords, "maxVideos": max_videos}
        base["global"] = global_data

        tags_comma = ", ".join(base["suggested"].get("tags") or [])
        hashtags_space = " ".join(base["suggested"].get("hashtags") or [])
        shorts_hashtags_space = " ".join(base["suggested"].get("shorts_hashtags") or [])
        global_hashtags_space = " ".join(base["suggested"].get("global_hashtags") or [])
        audience_tags_comma = ", ".join(base["suggested"].get("audience_tags") or [])
        audience_hashtags_space = " ".join(base["suggested"].get("audience_hashtags") or [])
        audience_shorts_hashtags_space = " ".join(
            base["suggested"].get("audience_shorts_hashtags") or []
        )
        base["copy"] = {
            "tagsComma": tags_comma,
            "hashtags": hashtags_space,
            "shortsHashtags": shorts_hashtags_space,
            "globalHashtags": global_hashtags_space,
            "audienceTagsComma": audience_tags_comma,
            "audienceHashtags": audience_hashtags_space,
            "audienceShortsHashtags": audience_shorts_hashtags_space,
            "all": "\n".join(
                [
                    "TAGS:",
                    tags_comma,
                    "",
                    "HASHTAGS:",
                    hashtags_space,
                    "",
                    "SHORTS HASHTAGS:",
                    shorts_hashtags_space,
                    "",
                    "GLOBAL TRENDING HASHTAGS:",
                    global_hashtags_space,
                    "",
                    "AUDIENCE-TARGETED TAGS (CHANNEL):",
                    audience_tags_comma,
                    "",
                    "AUDIENCE-TARGETED HASHTAGS (CHANNEL):",
                    audience_hashtags_space,
                    "",
                    "AUDIENCE-TARGETED SHORTS HASHTAGS (CHANNEL):",
                    audience_shorts_hashtags_space,
                ]
            ).strip(),
        }
        return add_cors(make_response(jsonify(base), 200))
    except Exception as e:
        # Always return JSON so frontends can display the real reason.
        return add_cors(make_response(jsonify({"error": str(e)}), 400))


@app.options("/api/analyze")
def analyze_api_options():
    return add_cors(make_response("", 204))


def main():
    port = int(os.getenv("PORT", str(DEFAULT_PORT)))
    print(f"{APP_TITLE} running on http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()

