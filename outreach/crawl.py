"""Scrapling spiders used by the research workflow.

- crawl_site: maps a lead's own website (sitemap first, then internal links) and stores the pages that
  tend to carry evidence (about, branches, team, careers, press, contact, sustainability) as snapshots.
- discover: walks listing pages declared in the market catalog (e.g. complaint-site categories) and
  returns candidate companies with the dated items that mention them.

Both obey robots.txt, throttle per domain, and never retry or evade a block: a blocked or challenged
page is reported as unreachable.
"""
import logging
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .config import find_chrome, now_iso, write_json
from .fetch import (USABLE_CHARS, _domain, _next_id, _silence_scrapling_logs, _visible_text, bot_challenge,
                    excluded_reason, store_page)

PAGE_TYPES = {  # type -> (url pattern, how many pages of this type to keep)
    "about": (r"hakkimizda|hakkinda|about|kurumsal|biz-kimiz|hikayemiz|our-story", 3),
    "branches": (r"sube|subeler|lokasyon|location|restoranlar|restaurants|magaza|bayi|adres", 3),
    "team": (r"yonetim|ekip|team|organizasyon-yapisi|organizasyon-semasi|organization-chart|kadro|leadership|management", 3),
    "careers": (r"kariyer|career|jobs|insan-kaynaklari|is-ilan|acik-pozisyon|join-us", 4),
    "press": (r"basin|press|haber|news|blog|duyuru|medya|media", 8),
    "sustainability": (r"surdurulebilir|sustainab|israf|atik|sifir-atik|cevre|esg", 3),
    "contact": (r"iletisim|contact|bize-ulasin", 2),
}
_BLOCK_STATUSES = {401, 403, 407, 429, 444}
_LOC = re.compile(r"<url>(.*?)</url>", re.S)
_TAG = lambda t: re.compile(rf"<{t}>\s*(.*?)\s*</{t}>", re.S)  # noqa: E731
_CHILD_SITEMAP = re.compile(r"<sitemap>\s*<loc>\s*(.*?)\s*</loc>", re.S)


_LANG_PREFIX = re.compile(r"^(tr|en|de|ar|ru|fr)$")


def classify(url: str) -> str | None:
    """The section (first real path segment) decides first: /blog/kurumsal-ziyafetler is press, not about."""
    segments = [s for s in urlparse(url).path.lower().split("/") if s]
    if segments and _LANG_PREFIX.match(segments[0]):
        segments = segments[1:]
    if not segments:
        return None
    for part in (segments[0], "/".join(segments)):
        for type_, (pattern, _) in PAGE_TYPES.items():
            if re.search(pattern, part):
                return type_
    return None


def _depth_key(url: str) -> tuple[int, int]:
    path = urlparse(url).path.strip("/")
    return (path.count("/"), len(path))


def pick_urls(entries: list[tuple[str, str]], domain: str, max_pages: int) -> list[tuple[str, str]]:
    """entries: (url, lastmod). Keep evidence-bearing pages on the lead's domain, newest press first."""
    buckets: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for url, lastmod in entries:
        host = _domain(url)
        if not (host == domain or host.endswith("." + domain)):
            continue
        if (t := classify(url)) and url not in {u for u, _ in buckets[t]}:
            buckets[t].append((url, lastmod or ""))
    chosen = []
    for t, (_, cap) in PAGE_TYPES.items():
        if t == "press":  # the section index first, then the newest posts
            items = sorted(buckets[t], key=lambda x: _depth_key(x[0]))[:1]
            items += sorted((e for e in buckets[t] if e not in items), key=lambda x: x[1], reverse=True)
        else:             # index pages (e.g. /subelerimiz) before detail pages
            items = sorted(buckets[t], key=lambda x: _depth_key(x[0]))
        chosen += [(u, t) for u, _ in items[:cap]]
    return chosen[:max_pages]


def parse_sitemap(body: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Return ((url, lastmod) entries, child sitemap URLs)."""
    children = _CHILD_SITEMAP.findall(body) if "<sitemapindex" in body else []
    entries = []
    for block in _LOC.findall(body):
        loc = _TAG("loc").search(block)
        lastmod = _TAG("lastmod").search(block)
        if loc:
            entries.append((loc.group(1).strip(), lastmod.group(1).strip() if lastmod else ""))
    return entries, children


def _base_spider():
    from scrapling.spiders import Spider

    class _Polite(Spider):
        robots_txt_obey = True
        concurrent_requests = 2
        concurrent_requests_per_domain = 2
        download_delay = 1.0
        autothrottle_enabled = True
        autothrottle_start_delay = 1.5
        autothrottle_max_delay = 20.0
        max_blocked_retries = 0            # a block is an answer, not an obstacle
        logging_level = logging.WARNING

        def __init__(self, *a, **kw):
            self.blocked: list[str] = []
            super().__init__(*a, **kw)

        async def is_blocked(self, response) -> bool:
            text = "" if response.status in _BLOCK_STATUSES else _visible_text(response)
            if response.status in _BLOCK_STATUSES or bot_challenge(response.status, text):
                self.blocked.append(response.url)
                return True
            return False

    return _Polite


def crawl_site(domain: str, snap_dir: Path, max_pages: int = 25, excluded: list[dict] | None = None) -> dict:
    """Map a company's site and store evidence-bearing pages as snapshots."""
    _silence_scrapling_logs()
    from scrapling.fetchers import AsyncDynamicSession, FetcherSession
    from scrapling.spiders import LinkExtractor, Request

    domain = domain.lower().removeprefix("www.")
    home = f"https://{domain}/"
    if reason := excluded_reason(home, excluded):
        return {"domain": domain, "error": f"hariç tutulan kaynak: {reason}", "pages": []}
    snap_dir.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()

    class SiteSpider(_base_spider()):
        name = "site"
        start_urls = [home, urljoin(home, "/robots.txt")]
        allowed_domains = {domain}

        def __init__(self):
            self.saved: list[dict] = []
            self.failed: list[dict] = []
            self.queued: set[str] = set()
            self.sitemap_entries: list[tuple[str, str]] = []
            self.link_entries: list[tuple[str, str]] = []
            super().__init__()

        def configure_sessions(self, manager):
            manager.add("http", FetcherSession(impersonate="chrome"))
            manager.add("browser", AsyncDynamicSession(network_idle=True, block_ads=True, timeout=45000,
                                                         **({"real_chrome": True} if chrome else {})), lazy=True)

        async def parse(self, response):
            url = response.url
            body = response.body.decode("utf-8", errors="replace") if isinstance(response.body, bytes) else str(response.body)
            if url.endswith("robots.txt"):
                maps = [l.split(":", 1)[1].strip() for l in body.splitlines() if l.lower().startswith("sitemap:")]
                for m in maps or [urljoin(home, "/sitemap.xml")]:
                    yield Request(m, sid="http", callback=self.parse_sitemap)
                return
            # homepage (or any HTML reached through parse): save it and harvest internal links
            async for item in self._save(response, "home"):
                yield item
            links = LinkExtractor(allow_domains=domain).extract(response)
            self.link_entries += [(u, "") for u in links]
            for req in self._schedule(self.link_entries):
                yield req

        async def parse_sitemap(self, response):
            body = response.body.decode("utf-8", errors="replace") if isinstance(response.body, bytes) else str(response.body)
            entries, children = parse_sitemap(body)
            for child in children[:10]:
                yield Request(child, sid="http", callback=self.parse_sitemap)
            self.sitemap_entries += entries
            for req in self._schedule(self.sitemap_entries + self.link_entries):
                yield req

        def _schedule(self, entries):
            budget = max_pages - len(self.queued)
            for url, type_ in pick_urls(entries, domain, max_pages):
                if budget <= 0:
                    break
                if url not in self.queued:
                    self.queued.add(url)
                    budget -= 1
                    yield Request(url, sid="http", callback=self.parse_page, meta={"type": type_})

        async def parse_page(self, response):
            async for item in self._save(response, response.meta.get("type")):
                yield item

        async def _save(self, response, type_):
            if not 200 <= response.status < 300:
                self.failed.append({"url": response.url, "status": response.status, "type": type_})
                return
            text = _visible_text(response)
            on_http = response.meta.get("_via") != "browser"
            if len(text) < USABLE_CHARS and on_http:  # JS-rendered page: render it, don't give up
                yield Request(response.url, sid="browser", callback=self.parse_page, dont_filter=True,
                              meta={"type": type_, "_via": "browser"})
                return
            meta = {"id": _next_id(snap_dir), "url": response.url, "domain": _domain(response.url),
                    "fetched_at": now_iso(), "method": "spider" if on_http else "spider-browser",
                    "page_type": type_, "robots": "spider robots_txt_obey"}
            store_page(meta, response, text, snap_dir)
            meta["ok"] = 200 <= response.status < 300 and len(text) >= USABLE_CHARS
            if not meta["ok"]:
                meta["error"] = "yeterli içerik alınamadı"
            write_json(snap_dir / f"{meta['id']}.json", meta)
            row = {"snapshot": meta["id"], "type": type_, "url": response.url, "ok": meta["ok"], "chars": len(text),
                   "has_hidden_layer": "full_sha256" in meta}
            self.saved.append(row)
            yield row

    spider = SiteSpider()
    result = spider.start()
    return {"domain": domain, "sitemap_urls_seen": len(spider.sitemap_entries),
            "pages": sorted(spider.saved, key=lambda r: list(PAGE_TYPES).index(r["type"]) if r["type"] in PAGE_TYPES else -1),
            "failed": spider.failed, "blocked": spider.blocked,
            "robots_disallowed": result.stats.robots_disallowed_count}


def discover(listings: list[dict], excluded: list[dict] | None = None) -> dict:
    """Walk catalog listing pages; group items by the entity (company) they mention."""
    _silence_scrapling_logs()
    from scrapling.fetchers import FetcherSession

    starts, skipped = [], []
    for lst in listings:
        if reason := excluded_reason(lst["url"], excluded):
            skipped.append({"listing": lst["id"], "reason": reason})
            continue
        param = lst.get("page_param", "page")
        sep = "&" if "?" in lst["url"] else "?"
        for n in range(1, int(lst.get("pages", 1)) + 1):
            starts.append((lst["url"] if n == 1 else f"{lst['url']}{sep}{param}={n}", lst))

    by_id = {lst["id"]: lst for _, lst in starts}
    found: dict[str, dict] = {}

    class ListingSpider(_base_spider()):
        name = "discover"
        start_urls = [u for u, _ in starts]

        def configure_sessions(self, manager):
            manager.add("http", FetcherSession(impersonate="chrome"))

        async def start_requests(self):
            from scrapling.spiders import Request
            # The listing id travels with the request, so redirects (/catering -> /sikayetler?k=catering) don't lose it.
            for url, lst in starts:
                yield Request(url, sid="http", meta={"listing": lst["id"]})

        async def parse(self, response):
            lst = by_id.get(response.meta.get("listing"))
            if lst is None:
                return
            entity_re = re.compile(lst["entity_href"])
            detail_re = re.compile(lst["detail_href"])
            date_re = re.compile(lst["date_regex"]) if lst.get("date_regex") else None
            skip = tuple(lst.get("exclude_href_prefixes", []))
            for node in response.css(lst["item_css"]):
                hrefs = [h for h in node.css("a::attr(href)").getall() if h and not h.startswith(skip)]
                entity = next((m.group(1) for h in hrefs if (m := entity_re.match(h))), None)
                if not entity:
                    continue
                detail = next((h for h in hrefs if detail_re.match(h)), None)
                title = ""
                if detail:
                    title = " ".join(node.css(f'a[href="{detail}"]')[0].get_all_text(strip=True).split()) \
                        if node.css(f'a[href="{detail}"]') else ""
                text = " ".join(node.get_all_text(strip=True).split())
                date = date_re.search(text).group(0) if date_re and date_re.search(text) else ""
                rec = found.setdefault(entity, {"entity": entity, "entity_url": urljoin(response.url, f"/{entity}"),
                                                "sources": set(), "items": []})
                rec["sources"].add(lst["id"])
                if detail and all(i["url"] != urljoin(response.url, detail) for i in rec["items"]):
                    rec["items"].append({"title": title, "url": urljoin(response.url, detail), "date_text": date})
            if False:  # results are collected in `found`; this keeps parse an async generator
                yield {}

    spider = ListingSpider()
    spider.start()
    candidates = sorted(({**v, "sources": sorted(v["sources"]), "count": len(v["items"])} for v in found.values()),
                        key=lambda c: -c["count"])
    return {"candidates": candidates, "blocked": spider.blocked, "skipped_listings": skipped}
