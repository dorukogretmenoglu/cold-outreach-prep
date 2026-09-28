"""Code-only evidence brief: candidate quotes pulled from a lead's saved pages.

Agents read this short brief instead of whole pages. Every quote is an exact, contiguous span of a
snapshot's visible text (one line, or two adjacent lines), so the normal verifier still decides.
The net is deliberately wide: code finds candidates, a model (or person) decides meaning.
"""
import re
from pathlib import Path

from .config import read_json
from .textnorm import normalize

_UNIT = r"(şube|restoran|mağaza|lokasyon|nokta|buluşma noktası|öğün|yemek|kişi)"
SIZE = re.compile(rf"(\d[\d.,]*)\s*(bin|milyon)?\s*(?:['’]?[a-zçğıöşü]*\s*)?{_UNIT}", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
TITLES = ["kurucu", "genel müdür", "ceo", "coo", "cfo", "yönetim kurulu", "başkan", "direktör", "müdür",
          "founder", "managing director", "president", "sahibi", "ortağı", "şef"]
SEGMENT = ["restoran", "zincir", "şube", "catering", "toplu yemek", "yemek hizmet", "lokanta", "kafe", "cafe",
           "franchise", "endüstriyel yemek", "yemekhane"]
# Stems, not words: "yetme" catches yetmedi / yetmemiş; "kalma" catches kalmadı / kalmamıştı.
# Strong stems rank first; weak ones (porsiyon, stok) only widen the net.
PROBLEM_STRONG = ["yetme", "yetmi", "yeters", "yetiş", "kalma", "bitti", "bitmi", "tüken", "yoktu", "yok dedi",
                  "bayat", "israf", "çöpe", "atıl", "fazla üret", "artan yemek", "stokta yok", "stokta olma"]
PROBLEM_WEAK = ["porsiyon", "stok", "kalmamış", "az geldi", "eksik geldi"]
PROBLEM_STEMS = PROBLEM_STRONG + PROBLEM_WEAK
SEGMENT_STRONG = ["zincir", "şube", "catering", "toplu yemek", "endüstriyel yemek", "franchise"]
PARENT = ["holding", "grubu", "grup şirket", "group", "bünyesinde", "çatısı altında", "iştirak", "markalarından",
          "şirketler"]
MAX_PER_KIND = 12
MAX_PER_PAGE = 3


def _lines(text: str) -> list[str]:
    return [l.strip() for l in text.split("\n") if l.strip()]


def _has(line: str, words: list[str]) -> bool:
    n = normalize(line)
    return any(w in n for w in words)


def _cand(kind: str, sid: str, page_type: str, quote: str, rank: int = 1) -> dict:
    # Never truncate: a cut quote is no longer an exact span of the page.
    return {"kind": kind, "snapshot": sid, "page_type": page_type, "quote": quote, "rank": rank}


def _plausible(num: str, scale: str) -> bool:
    if num.startswith("0"):
        return False
    try:
        n = float(num.replace(".", "").replace(",", "."))
    except ValueError:
        return False
    return scale != "" or not 1900 <= n <= 2099


def page_candidates(text: str, sid: str, page_type: str, domain: str, titles: list[str]) -> list[dict]:
    lines = _lines(text)
    out = []
    for i, line in enumerate(lines):
        if len(line) > 250:  # long paragraphs: work on sentences so each quote stays a short exact span
            parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", line) if p.strip()]
        else:
            parts = [line]
        for part in parts:
            for m in SIZE.finditer(part):
                if _plausible(m.group(1), m.group(2) or ""):
                    out.append(_cand("size", sid, page_type, part))
                    break
            for email in EMAIL.findall(part):
                host = email.split("@")[1].lower()
                if host == domain or host.endswith("." + domain) or domain.split(".")[0] in host:
                    out.append(_cand("contact", sid, page_type, part))
            if _has(part, titles):
                # a name is often on the line before or after the title
                window = " ".join(lines[max(0, i - 1): i + 2]) if len(part) < 80 else part
                out.append(_cand("person", sid, page_type, window))
            if _has(part, SEGMENT) and 30 <= len(part) <= 300:
                out.append(_cand("segment", sid, page_type, part, rank=0 if _has(part, SEGMENT_STRONG) else 1))
            if _has(part, PROBLEM_STEMS):
                out.append(_cand("problem", sid, page_type, part, rank=0 if _has(part, PROBLEM_STRONG) else 1))
            if _has(part, PARENT) and len(part) <= 300:
                # group listings are often a few short lines ("X Grup Şirketleri / X Et / X Lokantacılık")
                window = "\n".join(lines[i: i + 6]) if len(part) < 40 and part == line else part
                out.append(_cand("parent", sid, page_type, window))
    return out


def build_brief(run_dir: Path, lead: dict, profile: dict, extra_snapshots: list[str] = ()) -> dict:
    snap_dir = run_dir / "snapshots"
    crawl_path = run_dir / "crawls" / f"{lead['id']}.json"
    pages = read_json(crawl_path).get("pages", []) if crawl_path.exists() else []
    targets = [(p["snapshot"], p.get("type") or "") for p in pages if p.get("ok")]
    targets += [(s, "extra") for s in extra_snapshots]
    titles = list(TITLES)
    for dm in profile.get("icp", {}).get("decision_makers", []):
        titles += [t.lower() for t in dm.get("titles", [])]
    titles += [t.lower() for t in profile.get("scoring", {}).get("relevant_titles", [])]
    titles = [normalize(t) for t in dict.fromkeys(titles)]

    cands, dates = [], {}
    for sid, ptype in targets:
        txt = snap_dir / f"{sid}.txt"
        if not txt.exists():
            continue
        cands += page_candidates(txt.read_text(encoding="utf-8"), sid, ptype, lead["domain"], titles)
        meta = read_json(snap_dir / f"{sid}.json")
        if meta.get("html_dates"):
            dates[sid] = {k: v[:2] for k, v in meta["html_dates"].items()}

    seen, brief, per_page = set(), {}, {}
    for c in sorted(cands, key=lambda c: c["rank"]):  # strong matches fill the caps first
        key = (c["kind"], normalize(c["quote"]))
        if key in seen:
            continue
        seen.add(key)
        page_key = (c["kind"], c["snapshot"])
        if per_page.get(page_key, 0) >= MAX_PER_PAGE:  # one noisy page (a menu) can't crowd out the rest
            continue
        bucket = brief.setdefault(c["kind"], [])
        if len(bucket) < MAX_PER_KIND:
            per_page[page_key] = per_page.get(page_key, 0) + 1
            bucket.append({k: c[k] for k in ("snapshot", "page_type", "quote")})
    return {"lead": lead["id"], "domain": lead["domain"], "pages": len(targets), "candidates": brief,
            "html_dates": dates}


def recall(brief: dict, claims: list[dict]) -> dict:
    """How many verified claims of this lead would the brief have surfaced (quote overlap on the same page)."""
    kind_of = {"branch_count": "size", "contact": "contact", "person_title": "person", "review": "problem",
               "company_fact": "segment/parent"}
    found, missed = [], []
    all_cands = [c for group in brief["candidates"].values() for c in group]

    def overlap(a: str, b: str) -> float:
        ta, tb = set(re.findall(r"\w+", normalize(a))), set(re.findall(r"\w+", normalize(b)))
        return len(ta & tb) / max(1, min(len(ta), len(tb)))

    for cl in claims:
        # Same fact on another page of the same site is an equally valid source, so any snapshot counts.
        hit = any(overlap(cl["quote"], c["quote"]) >= 0.6 for c in all_cands)
        (found if hit else missed).append({"id": cl["id"], "type": cl["type"], "kind": kind_of.get(cl["type"]),
                                           "snapshot": cl.get("snapshot")})
    return {"found": found, "missed": missed}
