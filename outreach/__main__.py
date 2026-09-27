import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from . import export as exp
from .config import (PRODUCTS, RUNS, find_chrome, load_profile, now_iso, product_dir, read_json,
                     read_jsonl, run_dir, today, write_json, write_jsonl)
from .fetch import fetch, save_manual
from .kb import search
from .score import score_lead
from .verify import KB_TYPES, LEAD_TYPES, build_policy, check_draft, verify_claim


def _out(data) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2) if not isinstance(data, str) else data)


def _slug(text: str) -> str:
    text = text.translate(str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU"))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "lead"


def _domain(value: str) -> str:
    value = re.sub(r"^https?://", "", value.strip().lower()).split("/")[0]
    return value.removeprefix("www.")


def _payload(args) -> dict:
    # Read bytes: on Windows sys.stdin decodes with the ANSI code page and mangles UTF-8.
    raw = sys.stdin.buffer.read().decode("utf-8-sig") if args.stdin else args.json
    if not raw:
        raise SystemExit("--json ya da --stdin ile JSON ver")
    return json.loads(raw)


def _run_ctx(run_id: str):
    rdir = run_dir(run_id)
    run = read_json(rdir / "run.json")
    return rdir, run, load_profile(run["product"])


def _kb_paths(slug: str) -> tuple[Path, Path]:
    kdir = product_dir(slug) / "knowledge"
    return kdir / "claims.jsonl", kdir / "snapshots"


def _excluded(profile: dict) -> list[dict]:
    return profile.get("sources", {}).get("excluded", [])


def _snap_target(args) -> tuple[Path, dict]:
    if bool(args.run) == bool(args.product):
        raise SystemExit("--run ya da --product (bilgi tabanı için) ver, ikisinden biri")
    if args.run:
        rdir, _, profile = _run_ctx(args.run)
        return rdir / "snapshots", profile
    return _kb_paths(args.product)[1], load_profile(args.product)


def _verify_all(claims: list[dict], snap_dir: Path, profile: dict, leads: dict | None) -> list[dict]:
    policy = build_policy(profile.get("freshness"))
    own = _domain(profile.get("product", {}).get("website", ""))
    excluded = _excluded(profile)
    for c in claims:
        lead = leads.get(c.get("lead")) if leads is not None else None
        errors = verify_claim(c, snap_dir, policy, today(), lead=lead, own_domain=own, excluded=excluded)
        if leads is not None and c.get("lead") not in leads:
            errors.append("bilinmeyen lead")
        c["status"] = "rejected" if errors else "verified"
        c["errors"] = errors
        c["checked_at"] = now_iso()
    return claims


# ---------- commands ----------

def cmd_doctor(args):
    import platform
    info = {"python": platform.python_version(), "chrome": find_chrome() or "bulunamadı (paketli Chromium denenecek)"}
    try:
        import scrapling
        info["scrapling"] = scrapling.__version__
    except ImportError:
        info["scrapling"] = "YOK: pip install -e . && scrapling install"
    info["products"] = sorted(p.name for p in PRODUCTS.iterdir() if (p / "profile.toml").exists()) \
        if PRODUCTS.exists() else []
    _out(info)


def cmd_new_run(args):
    load_profile(args.product)
    base = f"{today().isoformat()}-{args.product}"
    run_id, n = base, 2
    while (RUNS / run_id).exists():
        run_id, n = f"{base}-{n}", n + 1
    write_json(RUNS / run_id / "run.json", {"product": args.product, "created_at": now_iso()})
    _out({"run": run_id})


def cmd_add_lead(args):
    rdir, _, _ = _run_ctx(args.run)
    leads = read_jsonl(rdir / "leads.jsonl")
    domain = _domain(args.domain)
    if any(l["domain"] == domain for l in leads):
        raise SystemExit(f"{domain} zaten listede")
    lid, n = _slug(args.name), 2
    while any(l["id"] == lid for l in leads):
        lid, n = f"{_slug(args.name)}-{n}", n + 1
    lead = {"id": lid, "name": args.name, "domain": domain, "status": "candidate",
            "discovery_note": args.note or "", "discovery_url": args.url or "", "added_at": now_iso()}
    leads.append(lead)
    write_jsonl(rdir / "leads.jsonl", leads)
    _out(lead)


def cmd_select(args):
    rdir, _, _ = _run_ctx(args.run)
    leads = read_jsonl(rdir / "leads.jsonl")
    ids = set(args.ids)
    unknown = ids - {l["id"] for l in leads}
    if unknown:
        raise SystemExit(f"bilinmeyen lead: {sorted(unknown)}")
    for l in leads:
        if l["id"] in ids:
            l["status"] = "dropped" if args.drop else "selected"
    write_jsonl(rdir / "leads.jsonl", leads)
    _out({l["id"]: l["status"] for l in leads})


def cmd_leads(args):
    rdir, _, _ = _run_ctx(args.run)
    _out([{k: l[k] for k in ("id", "name", "domain", "status", "discovery_note")}
          for l in read_jsonl(rdir / "leads.jsonl")])


def cmd_fetch(args):
    snap_dir, profile = _snap_target(args)
    meta = fetch(args.url, snap_dir, expect=args.expect, min_chars=args.min_chars, excluded=_excluded(profile))
    meta["text_file"] = str(snap_dir / f"{meta['id']}.txt") if meta.get("chars") else None
    meta["html_dates"] = {k: v[:5] for k, v in meta.get("html_dates", {}).items()}
    _out(meta)


def cmd_add_snapshot(args):
    snap_dir, profile = _snap_target(args)
    text = sys.stdin.buffer.read().decode("utf-8-sig")
    try:
        meta = save_manual(args.url, text, snap_dir, args.method, excluded=_excluded(profile))
    except ValueError as e:
        raise SystemExit(str(e))
    meta["text_file"] = str(snap_dir / f"{meta['id']}.txt") if meta["ok"] else None
    _out(meta)


def cmd_add_claim(args):
    claim = _payload(args)
    if args.run:
        rdir, _, profile = _run_ctx(args.run)
        path, snap_dir, prefix, allowed = rdir / "claims.jsonl", rdir / "snapshots", "c", LEAD_TYPES
        leads = {l["id"]: l for l in read_jsonl(rdir / "leads.jsonl")}
        if claim.get("lead") not in leads:
            raise SystemExit(f"lead alanı geçerli bir lead id olmalı: {sorted(leads)}")
    elif args.product:
        profile = load_profile(args.product)
        path, snap_dir = _kb_paths(args.product)
        prefix, allowed, leads = "k", KB_TYPES, None
        claim.pop("lead", None)
    else:
        raise SystemExit("--run ya da --product ver")
    if claim.get("type") not in allowed:
        raise SystemExit(f"bu yerde izin verilen türler: {sorted(allowed)}")
    claims = read_jsonl(path)
    claim["id"] = f"{prefix}{len(claims) + 1:03d}"
    claim["added_at"] = now_iso()
    _verify_all([claim], snap_dir, profile, leads)
    claims.append(claim)
    write_jsonl(path, claims)
    _out({"id": claim["id"], "status": claim["status"], "errors": claim["errors"]})


def cmd_verify(args):
    if args.product:
        profile = load_profile(args.product)
        path, snap_dir = _kb_paths(args.product)
        claims = _verify_all(read_jsonl(path), snap_dir, profile, None)
        write_jsonl(path, claims)
        _out(_summary(claims))
        return
    rdir, run, profile = _run_ctx(args.run)
    leads = {l["id"]: l for l in read_jsonl(rdir / "leads.jsonl")}
    claims = _verify_all(read_jsonl(rdir / "claims.jsonl"), rdir / "snapshots", profile, leads)
    write_jsonl(rdir / "claims.jsonl", claims)
    kb_claims = _verified_kb(run["product"])
    drafts = read_jsonl(rdir / "drafts.jsonl")
    for d in drafts:
        d["errors"] = _check(d, claims, kb_claims, profile)
    write_jsonl(rdir / "drafts.jsonl", drafts)
    _out({"claims": _summary(claims),
          "drafts": {d["lead"]: d["errors"] or "geçti" for d in drafts}})


def _summary(claims: list[dict]) -> dict:
    rejected = [{"id": c["id"], "statement": c.get("statement"), "errors": c["errors"]}
                for c in claims if c["status"] == "rejected"]
    return {"toplam": len(claims), "doğrulandı": len(claims) - len(rejected), "reddedildi": rejected}


def _verified_kb(slug: str) -> list[dict]:
    path, _ = _kb_paths(slug)
    return [c for c in read_jsonl(path) if c.get("status") == "verified"]


def _check(draft: dict, claims: list[dict], kb_claims: list[dict], profile: dict) -> list[str]:
    verified = {c["id"]: c for c in claims if c["status"] == "verified" and c.get("lead") == draft["lead"]}
    verified.update({c["id"]: c for c in kb_claims})
    return check_draft(draft, verified, profile)


def cmd_add_draft(args):
    rdir, run, profile = _run_ctx(args.run)
    draft = _payload(args)
    leads = {l["id"] for l in read_jsonl(rdir / "leads.jsonl")}
    if draft.get("lead") not in leads:
        raise SystemExit("lead alanı geçerli bir lead id olmalı")
    draft.setdefault("channel", "email")
    draft["updated_at"] = now_iso()
    claims = read_jsonl(rdir / "claims.jsonl")
    draft["errors"] = _check(draft, claims, _verified_kb(run["product"]), profile)
    drafts = [d for d in read_jsonl(rdir / "drafts.jsonl") if d["lead"] != draft["lead"]] + [draft]
    write_jsonl(rdir / "drafts.jsonl", drafts)
    _out({"lead": draft["lead"], "errors": draft["errors"] or "geçti"})


def cmd_set_review(args):
    rdir, _, _ = _run_ctx(args.run)
    review = _payload(args)
    if review.get("verdict") not in ("pass", "fail"):
        raise SystemExit('verdict "pass" ya da "fail" olmalı')
    drafts = read_jsonl(rdir / "drafts.jsonl")
    target = next((d for d in drafts if d["lead"] == args.lead), None)
    if target is None:
        raise SystemExit(f"{args.lead} için taslak yok")
    review.update(draft_sha=exp.draft_hash(target), reviewed_at=now_iso())
    target["review"] = review
    write_jsonl(rdir / "drafts.jsonl", drafts)
    _out({"lead": args.lead, "verdict": review["verdict"]})


def _scores(rdir: Path, profile: dict) -> dict[str, dict]:
    claims = [c for c in read_jsonl(rdir / "claims.jsonl") if c.get("status") == "verified"]
    result = {}
    for lead in read_jsonl(rdir / "leads.jsonl"):
        if lead["status"] == "selected":
            result[lead["id"]] = score_lead([c for c in claims if c["lead"] == lead["id"]], profile, today())
    write_json(rdir / "scores.json", result)
    return result


def cmd_score(args):
    rdir, _, profile = _run_ctx(args.run)
    _out(_scores(rdir, profile))


def cmd_kb_search(args):
    hits = search(_verified_kb(args.product), args.query, k=args.k)
    _out([{"id": c["id"], "score": round(s, 2), "type": c["type"], "statement": c["statement"],
           "quote": c["quote"]} for s, c in hits] or "sonuç yok (bilgi tabanında doğrulanmış karşılığı yok, mesajda bu konuda iddia kurma)")


def cmd_export(args):
    rdir, run, profile = _run_ctx(args.run)
    cmd_verify(argparse.Namespace(run=args.run, product=None))
    scores = _scores(rdir, profile)
    claims = [c for c in read_jsonl(rdir / "claims.jsonl") if c["status"] == "verified"]
    carry = exp.read_carry_over(Path(args.carry_over)) if args.carry_over else None
    rows, reviews, gmail = exp.build(
        read_jsonl(rdir / "leads.jsonl"), claims, _verified_kb(run["product"]),
        read_jsonl(rdir / "drafts.jsonl"), scores,
        [rdir / "snapshots", _kb_paths(run["product"])[1]], carry)
    out = rdir / "out"
    (out / "review").mkdir(parents=True, exist_ok=True)
    (out / "sheet.csv").write_text(exp.to_csv(rows), encoding="utf-8")
    for lid, md in reviews.items():
        (out / "review" / f"{lid}.md").write_text(md, encoding="utf-8")
    write_json(out / "gmail_drafts.json", {"drafts": gmail})
    _out({"sheet_csv": str(out / "sheet.csv"), "review_dir": str(out / "review"),
          "gmail_drafts": str(out / "gmail_drafts.json"), "satır": len(rows),
          "gönderime hazır e-posta": len(gmail),
          "öncelik": {t: sum(r["Öncelik"] == t for r in rows) for t in "ABCX"}})


def cmd_state(args):
    path = product_dir(args.product) / "state.json"
    state = read_json(path) if path.exists() else {}
    for item in args.set or []:
        key, _, value = item.partition("=")
        state[key] = value
    if args.set:
        write_json(path, state)
    _out(state)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m outreach")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("new-run"); s.add_argument("--product", required=True); s.set_defaults(fn=cmd_new_run)
    s = sub.add_parser("add-lead"); s.add_argument("--run", required=True); s.add_argument("--name", required=True)
    s.add_argument("--domain", required=True); s.add_argument("--note"); s.add_argument("--url"); s.set_defaults(fn=cmd_add_lead)
    s = sub.add_parser("select"); s.add_argument("--run", required=True); s.add_argument("ids", nargs="+")
    s.add_argument("--drop", action="store_true"); s.set_defaults(fn=cmd_select)
    s = sub.add_parser("leads"); s.add_argument("--run", required=True); s.set_defaults(fn=cmd_leads)
    s = sub.add_parser("fetch"); s.add_argument("url"); s.add_argument("--run"); s.add_argument("--product")
    s.add_argument("--expect", nargs="*"); s.add_argument("--min-chars", type=int, default=400); s.set_defaults(fn=cmd_fetch)
    s = sub.add_parser("add-snapshot", help="kullanıcının verdiği ya da Chrome'unda okunan metni kaynak olarak kaydet (stdin)")
    s.add_argument("--url", required=True); s.add_argument("--method", choices=["manual", "chrome"], required=True)
    s.add_argument("--run"); s.add_argument("--product"); s.set_defaults(fn=cmd_add_snapshot)
    for name, fn in (("add-claim", cmd_add_claim), ("add-draft", cmd_add_draft)):
        s = sub.add_parser(name); s.add_argument("--run"); s.add_argument("--product")
        s.add_argument("--json"); s.add_argument("--stdin", action="store_true"); s.set_defaults(fn=fn)
    s = sub.add_parser("set-review"); s.add_argument("--run", required=True); s.add_argument("--lead", required=True)
    s.add_argument("--json"); s.add_argument("--stdin", action="store_true"); s.set_defaults(fn=cmd_set_review)
    s = sub.add_parser("verify"); s.add_argument("--run"); s.add_argument("--product"); s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("score"); s.add_argument("--run", required=True); s.set_defaults(fn=cmd_score)
    s = sub.add_parser("kb-search"); s.add_argument("--product", required=True); s.add_argument("query")
    s.add_argument("-k", type=int, default=5); s.set_defaults(fn=cmd_kb_search)
    s = sub.add_parser("export"); s.add_argument("--run", required=True); s.add_argument("--carry-over"); s.set_defaults(fn=cmd_export)
    s = sub.add_parser("state"); s.add_argument("--product", required=True); s.add_argument("--set", nargs="*")
    s.set_defaults(fn=cmd_state)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
