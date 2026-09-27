"""Fetch a page through an escalating chain and save an auditable snapshot.

A 200 status is not treated as success: JS-rendered pages often return an empty shell.
A page only counts as fetched when it has enough visible text (and the expected terms,
if any). Otherwise the next method is tried; if every method fails the snapshot is kept
with ok=false so that nothing downstream can cite it.
"""
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from .config import find_chrome, locked, now_iso, read_json, write_json
from .textnorm import normalize

USER_AGENT = "*"
MIN_DELAY_SECONDS = 2.0
_DATE_KEYS = ("datePublished", "dateModified", "datePosted", "dateCreated", "uploadDate", "validThrough")
_META_DATE_PROPS = (
    "article:published_time", "article:modified_time", "og:updated_time",
    "date", "pubdate", "publish-date", "last-modified",
)


def _silence_scrapling_logs() -> None:
    import logging

    import scrapling.core.utils  # noqa: F401  (sets the logger to INFO on import, so silence afterwards)
    logging.getLogger("scrapling").setLevel(logging.WARNING)


def _domain(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _get_robots(robots_url: str) -> tuple[int, str]:
    """Return (status, text). Falls back to a real browser when the HTTP client fails (e.g. TLS quirks)."""
    from scrapling.fetchers import DynamicFetcher, Fetcher

    try:
        resp = Fetcher.get(robots_url, timeout=15)
        return resp.status, resp.body.decode("utf-8", errors="replace")
    except Exception:
        kw = {"real_chrome": True} if find_chrome() else {}
        page = DynamicFetcher.fetch(robots_url, timeout=30000, **kw)
        return page.status, str(page.get_all_text(ignore_tags=("script", "style")))


def robots_decision(status: int | None, text: str, url: str) -> tuple[bool, str]:
    """RFC 9309: 4xx means no rules (allowed); 5xx or unreachable means assume full disallow."""
    if status is None:
        return False, "robots.txt okunamadı: RFC 9309 gereği tamamen yasak kabul edildi"
    if 400 <= status < 500:
        return True, f"robots.txt yok ({status})"
    if status >= 500:
        return False, f"robots.txt sunucu hatası ({status}): yasak kabul edildi"
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    return parser.can_fetch(USER_AGENT, url), "robots.txt kontrol edildi"


def _robots_allows(url: str) -> tuple[bool, str]:
    try:
        status, text = _get_robots(urljoin(url, "/robots.txt"))
    except Exception:
        status, text = None, ""
    return robots_decision(status, text, url)


def _wait_for_domain(clock_file: Path, domain: str) -> None:
    with locked(clock_file):
        clock = read_json(clock_file) if clock_file.exists() else {}
        wait = clock.get(domain, 0) + MIN_DELAY_SECONDS - time.time()
        if wait > 0:
            time.sleep(wait)
        clock[domain] = time.time()
        write_json(clock_file, clock)


def _visible_text(page) -> str:
    from scrapling.core.shell import Convertor

    # main_content_only strips hidden elements, comments and zero-width characters
    # (prompt-injection vectors) exactly like the CLI's --ai-targeted flag.
    return "".join(Convertor._extract_content(page, "text", main_content_only=True)).strip()


def _full_text(page) -> str:
    """Text including CSS-hidden elements: tabs, accordions (FAQ) and carousels are hidden until clicked.

    Kept separately so claims can cite it, but flagged, because hidden text is also where
    SEO stuffing and prompt injection live.
    """
    from scrapling.core.shell import Convertor

    body = page.css("body").first or page
    return "".join(Convertor._extract_content(Convertor._strip_noise_tags(body), "text")).strip()


def _walk_json(node, found: dict) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _DATE_KEYS and isinstance(value, str):
                found.setdefault(key, []).append(value)
            else:
                _walk_json(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, found)


def extract_html_dates(page) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for raw in page.css('script[type="application/ld+json"]::text').getall():
        try:
            _walk_json(json.loads(raw), found)
        except (ValueError, TypeError):
            continue
    for prop in _META_DATE_PROPS:
        for attr in ("property", "name", "itemprop"):
            for value in page.css(f'meta[{attr}="{prop}"]::attr(content)').getall():
                found.setdefault(f"meta:{prop}", []).append(value)
    times = page.css("time::attr(datetime)").getall()
    if times:
        found["time"] = list(dict.fromkeys(times))[:30]
    return {k: list(dict.fromkeys(v)) for k, v in found.items()}


def _methods(chrome: str | None):
    """Plain HTTP, then a real browser for JS-rendered pages. Deliberately no stealth/anti-detection
    step: a site that challenges or blocks automated clients is recorded as unreachable, not evaded."""
    from scrapling.fetchers import DynamicFetcher, Fetcher

    browser_kw = {"real_chrome": True} if chrome else {}
    yield "http", lambda url: Fetcher.get(url, timeout=30)
    yield "browser", lambda url: DynamicFetcher.fetch(
        url, network_idle=True, block_ads=True, timeout=45000, **browser_kw)


_CHALLENGE_MARKERS = (
    "captcha", "are you a robot", "are you human", "made by a human", "verify you are human",
    "checking your browser", "cf-challenge", "robot olmadığınızı", "insan olduğunuzu doğrula",
)


def bot_challenge(status: int, text: str) -> bool:
    """True when the site is telling automated clients to go away (challenge page or block status)."""
    if status in (403, 429):
        return True
    t = text.lower()
    return len(t) < 3000 and any(m in t for m in _CHALLENGE_MARKERS)


def _next_id(snap_dir: Path) -> str:
    """Reserve the next snapshot id by creating its metadata file exclusively (safe under parallel agents)."""
    n = len(list(snap_dir.glob("s*.json"))) + 1
    while True:
        try:
            with (snap_dir / f"s{n:03d}.json").open("x", encoding="utf-8") as f:
                f.write("{}")
            return f"s{n:03d}"
        except FileExistsError:
            n += 1


USABLE_CHARS = 150
MANUAL_METHODS = {"manual": "kullanıcı yapıştırdı", "chrome": "kullanıcının Chrome'unda okundu"}


def excluded_reason(url: str, excluded: list[dict] | None) -> str | None:
    domain = _domain(url)
    for rule in excluded or []:
        d = rule["domain"].lower().removeprefix("www.")
        if domain == d or domain.endswith("." + d):
            return rule.get("reason", "profilde hariç tutulan kaynak")
    return None


def save_manual(url: str, text: str, snap_dir: Path, method: str, excluded: list[dict] | None = None) -> dict:
    """Store text the user supplied (or read in the user's own browser) as an auditable snapshot."""
    if method not in MANUAL_METHODS:
        raise ValueError(f"method {sorted(MANUAL_METHODS)} olmalı")
    if not urlparse(url).hostname:
        raise ValueError("geçerli bir URL gerekli: kaynak her zaman bir sayfaya bağlı olmalı")
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_id = _next_id(snap_dir)
    text = text.strip()
    meta = {"id": snap_id, "url": url, "domain": _domain(url), "final_url": url, "final_domain": _domain(url),
            "fetched_at": now_iso(), "method": method, "captured_by": MANUAL_METHODS[method],
            "chars": len(text), "html_dates": {}, "ok": False}
    if reason := excluded_reason(url, excluded):
        meta["error"] = f"hariç tutulan kaynak: {reason}"
    elif len(text) < 20:
        meta["error"] = "metin çok kısa"
    else:
        meta["ok"] = True
        meta["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        (snap_dir / f"{snap_id}.txt").write_text(text, encoding="utf-8")
    write_json(snap_dir / f"{snap_id}.json", meta)
    return meta


def fetch(url: str, snap_dir: Path, expect: list[str] | None = None, min_chars: int = 400,
          excluded: list[dict] | None = None) -> dict:
    """Stop escalating once a method returns >= min_chars with every expected term present.

    Missing expected terms trigger escalation but do not make the page unusable on their own:
    the best attempt is kept and ok=True as long as it returned 2xx with USABLE_CHARS of text.
    """
    _silence_scrapling_logs()
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_id = _next_id(snap_dir)
    meta = {"id": snap_id, "url": url, "domain": _domain(url), "fetched_at": now_iso(),
            "ok": False, "method": None, "attempts": []}

    if reason := excluded_reason(url, excluded):
        meta["error"] = f"hariç tutulan kaynak: {reason}"
        write_json(snap_dir / f"{snap_id}.json", meta)
        return meta

    allowed, robots_note = _robots_allows(url)
    meta["robots"] = robots_note
    if not allowed:
        meta["error"] = "robots.txt bu sayfayı yasaklıyor, çekilmedi"
        write_json(snap_dir / f"{snap_id}.json", meta)
        return meta

    expect_norm = [normalize(e) for e in (expect or []) if e.strip()]
    best = None
    for name, call in _methods(find_chrome()):
        _wait_for_domain(snap_dir.parent / ".domain_clock.json", meta["domain"])
        attempt = {"method": name}
        try:
            page = call(url)
        except Exception as e:
            attempt["error"] = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
            meta["attempts"].append(attempt)
            continue
        text = _visible_text(page)
        # A term sitting in a tab/accordion is still on the page: escalating the fetcher won't reveal it.
        searchable = normalize(text + "\n" + _full_text(page)) if expect_norm else ""
        missing = [e for e in expect_norm if e not in searchable]
        attempt.update(status=page.status, chars=len(text), missing_expected=missing)
        meta["attempts"].append(attempt)
        good_status = 200 <= page.status < 300
        rank = (good_status, len(expect_norm) - len(missing), len(text))
        if best is None or rank > best[0]:
            best = (rank, page, text, name, missing)
        if page.status in (404, 410):
            meta["error"] = f"sayfa yok ({page.status})"
            break
        if bot_challenge(page.status, text):
            meta["error"] = "site otomatik erişimi engelliyor (bot kontrolü); atlatılmaz, erişilemedi sayılır"
            best = None
            break
        if good_status and len(text) >= min_chars and not missing:
            break

    if best is not None:
        (good_status, _, _), page, text, name, missing = best
        meta["ok"] = good_status and len(text) >= USABLE_CHARS
        meta["method"] = name
        meta["missing_expected"] = missing
        store_page(meta, page, text, snap_dir)
    if not meta["ok"] and "error" not in meta:
        meta["error"] = "yeterli içerik alınamadı"
    write_json(snap_dir / f"{snap_id}.json", meta)
    return meta


def store_page(meta: dict, page, text: str, snap_dir: Path) -> dict:
    """Write the visible layer, the raw HTML and (if different) the hidden layer; fill integrity fields."""
    snap_id = meta["id"]
    meta.update(final_url=page.url, final_domain=_domain(page.url), status=page.status, chars=len(text),
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), html_dates=extract_html_dates(page))
    (snap_dir / f"{snap_id}.txt").write_text(text, encoding="utf-8")
    (snap_dir / f"{snap_id}.html").write_text(str(page.html_content), encoding="utf-8")
    full = _full_text(page)
    if len(full) > len(text):
        meta["full_chars"] = len(full)
        meta["full_sha256"] = hashlib.sha256(full.encode("utf-8")).hexdigest()
        (snap_dir / f"{snap_id}.full.txt").write_text(full, encoding="utf-8")
    return meta
