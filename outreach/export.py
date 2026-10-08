import csv
import io
from pathlib import Path

from .config import read_json
from .verify import cited_ids, strip_markers

COLUMNS = ["Öncelik", "Skor", "Skor kırılımı", "Şirket", "Site", "Neden şimdi", "Bulunan sinyaller",
           "Karar verici", "E-posta", "Kanal", "Konu", "Mesaj", "Kaynaklar", "Doğrulama",
           "Durum", "Sonraki adım", "Notlar"]
USER_OWNED = ("Durum", "Sonraki adım", "Notlar")
# Statuses the tool writes itself; only anything else in "Durum" is the user's own and carried over.
AUTO_STATUS_PREFIXES = ("Taslak - onayını bekliyor", "Kontrol gerekli", "Taslak yazılmadı", "Elendi")
TIER_ORDER = {"A": 0, "B": 1, "C": 2, "X": 3}


def _source_line(n: int, claim: dict, snap_dirs: list[Path]) -> str:
    where, fetched = claim.get("file", ""), ""
    if sid := claim.get("snapshot"):
        for d in snap_dirs:
            if (d / f"{sid}.json").exists():
                meta = read_json(d / f"{sid}.json")
                where, fetched = meta.get("final_url") or meta["url"], meta["fetched_at"][:10]
                if meta.get("captured_by"):
                    where += f" [{meta['captured_by']}]"
                break
    if claim.get("layer") == "hidden":
        where += " [sayfanın gizli bölümünden: sekme/akordeon/kaydırmalı alan]"
    dates = f"çekildi {fetched}" if fetched else "kendi dokümanın"
    if claim.get("content_date"):
        dates += f", içerik tarihi {claim['content_date']}"
    return f"[{n}] {where} ({dates}): \"{claim['quote']}\""


def draft_hash(draft: dict) -> str:
    import hashlib
    payload = "\x1f".join(draft.get(k, "") or "" for k in ("to", "channel", "subject", "body"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _review_status(draft: dict) -> tuple[bool, str]:
    code_ok = "errors" in draft and not draft["errors"]
    review = draft.get("review") or {}
    if review and review.get("draft_sha") != draft_hash(draft):
        review = {"verdict": "fail", "issues": ["onaydan sonra taslak değişti, yeniden doğrulanmalı"]}
    review_ok = review.get("verdict") == "pass"
    parts = ["Kod: ✓" if code_ok else f"Kod: ✗ ({'; '.join(draft.get('errors', []))})",
             "Bağımsız: ✓" if review_ok else
             ("Bağımsız: bekliyor" if not review else f"Bağımsız: ✗ ({'; '.join(review.get('issues', []))})")]
    return code_ok and review_ok, " | ".join(parts)


def build(leads: list[dict], claims: list[dict], kb_claims: list[dict], drafts: list[dict],
          scores: dict[str, dict], snap_dirs: list[Path], carry_over: dict[str, dict] | None = None):
    """Return (sheet_rows, review_markdown_by_lead, gmail_drafts)."""
    by_id = {c["id"]: c for c in claims + kb_claims}
    drafts_by_lead = {}
    for d in drafts:
        drafts_by_lead.setdefault(d["lead"], d)
    rows, reviews, gmail = [], {}, []

    for lead in leads:
        if lead.get("status") != "selected":
            continue
        lid = lead["id"]
        lead_claims = [c for c in claims if c.get("lead") == lid]
        s = scores.get(lid, {})
        draft = drafts_by_lead.get(lid, {})
        ready, check = _review_status(draft) if draft else (False, "Taslak yok")
        if ready:
            status = "Taslak - onayını bekliyor"
        elif draft:
            status = "Kontrol gerekli"
        else:
            status = "Elendi" if s.get("tier") == "X" else f"Taslak yazılmadı (öncelik {s.get('tier', '?')})"

        order = cited_ids(f"{draft.get('subject', '')}\n{draft.get('body', '')}")
        numbering = {cid: i for i, cid in enumerate(order, 1)}
        sources = [_source_line(numbering[cid], by_id[cid], snap_dirs) for cid in order if cid in by_id]

        signals = [f"• {c['statement']}" for c in lead_claims if c.get("signal") and c.get("signal") != "segment"]
        relevant = set(s.get("relevant_people", []))
        people = sorted((c for c in lead_claims if c["type"] == "person_title"), key=lambda c: c["id"] not in relevant)
        people = [("★ İlgili ünvan: " if c["id"] in relevant else "") + c["statement"] for c in people]
        row = {
            "Öncelik": s.get("tier", ""),
            "Skor": s.get("score", ""),
            "Skor kırılımı": ", ".join(f"{k} {v}" for k, v in s.get("breakdown", {}).items())
                             + ("; " + "; ".join(s["notes"]) if s.get("notes") else ""),
            "Şirket": lead["name"],
            "Site": lead.get("domain", ""),
            "Neden şimdi": s.get("why_now", ""),
            "Bulunan sinyaller": "\n".join(signals),
            "Karar verici": "\n".join(people),
            "E-posta": draft.get("to", ""),
            "Kanal": draft.get("channel", ""),
            "Konu": strip_markers(draft.get("subject", "")),
            "Mesaj": strip_markers(draft.get("body", "")),
            "Kaynaklar": "\n".join(sources),
            "Doğrulama": check,
            "Durum": status,
            "Sonraki adım": "",
            "Notlar": "",
        }
        previous = (carry_over or {}).get(row["Site"])
        if previous:
            for col in USER_OWNED:
                value = previous.get(col, "")
                if value and not (col == "Durum" and value.startswith(AUTO_STATUS_PREFIXES)):
                    row[col] = value
        rows.append(row)

        marked = f"{draft.get('subject', '')}\n\n{draft.get('body', '')}"
        for cid, n in numbering.items():
            marked = marked.replace(f"[{cid}]", f"[{n}]")
        reviews[lid] = "\n".join([
            f"# {lead['name']} ({lead.get('domain', '')})", "",
            f"**Öncelik:** {row['Öncelik']} ({row['Skor']}) · {row['Skor kırılımı']}",
            f"**Neden şimdi:** {row['Neden şimdi'] or '-'}", f"**Doğrulama:** {check}", "",
            "## Taslak (kaynak işaretli)", "", marked.strip() or "_taslak yok_", "",
            "## Kaynaklar", "", *sources, "",
        ])
        if ready and draft.get("channel") == "email" and draft.get("to"):
            gmail.append({"lead": lid, "to": draft["to"], "subject": row["Konu"], "body": row["Mesaj"]})

    # The sheet is a tracker across runs: leads from earlier runs that this run didn't touch stay as they were.
    current = {r["Site"] for r in rows}
    for site, previous in (carry_over or {}).items():
        if site not in current:
            rows.append({col: previous.get(col, "") for col in COLUMNS})

    def _score(r):
        try:
            return -float(r["Skor"])
        except (TypeError, ValueError):
            return 0.0
    rows.sort(key=lambda r: (TIER_ORDER.get(r["Öncelik"], 9), _score(r)))
    return rows, reviews, gmail


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _neutralize(value):
    # Scraped text starting with a formula character would execute when imported into Sheets/Excel.
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows({k: _neutralize(v) for k, v in row.items()} for row in rows)
    return buf.getvalue()


def read_carry_over(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {row.get("Site", ""): row for row in csv.DictReader(f) if row.get("Site")}
