"""Lead scoring computed only from verified claims, so unverified findings cannot raise a score."""
from datetime import date

from .dates import parse_iso

DEFAULT_SIGNAL_WEIGHTS = {"job_post": 15, "news": 10, "review": 10, "public_post": 8}


def score_lead(claims: list[dict], profile: dict, today: date) -> dict:
    cfg = profile.get("scoring", {})
    weights = cfg.get("signal_weights", DEFAULT_SIGNAL_WEIGHTS)
    notes = []

    if any(c["type"] == "disqualifier" for c in claims):
        reason = next(c["statement"] for c in claims if c["type"] == "disqualifier")
        return {"score": 0, "tier": "X", "breakdown": {}, "why_now": "", "notes": [f"Elendi: {reason}"]}

    fit = 0
    if any(c.get("signal") == "segment" for c in claims):
        fit += 20
    else:
        notes.append("segment uyumu kanıtlanmadı")
    size_rules = cfg.get("size", [])
    size_rules = [size_rules] if isinstance(size_rules, dict) else size_rules
    matched = None
    for rule in size_rules:
        unit = rule.get("unit", "şube")
        for c in claims:
            if c["type"] == rule.get("claim_type", "branch_count") and "value" in c \
                    and c.get("unit", "şube") == unit:
                matched = (rule, c, unit)
                break
        if matched:
            break
    if matched:
        rule, c, unit = matched
        value = float(str(c["value"]).replace(".", "").replace(",", "."))
        if rule.get("min", 0) <= value <= rule.get("max", float("inf")):
            fit += 15
        else:
            notes.append(f"büyüklük ({value:g} {unit}) hedef aralığın dışında")
    else:
        notes.append("büyüklük bilinmiyor (tahmin edilmedi)")

    signal_claims = [c for c in claims if c.get("signal") in weights]
    by_signal = {}
    for c in signal_claims:
        by_signal.setdefault(c["signal"], c)
    signal = min(35, sum(weights[s] for s in by_signal))

    timing, why_now = 0, ""
    dated = [(parse_iso(c.get("content_date")), c) for c in signal_claims]
    dated = [(d, c) for d, c in dated if d]
    if dated:
        newest, claim = max(dated, key=lambda x: x[0])
        age = (today - newest).days
        timing = 20 if age <= 30 else 12 if age <= 90 else 6 if age <= 180 else 0
        why_now = f"{claim['statement']} ({newest.isoformat()}) [{claim['id']}]"

    reach = 0
    if any(c["type"] == "person_title" for c in claims):
        reach += 7
    else:
        notes.append("karar verici bulunamadı")
    if any(c["type"] == "contact" for c in claims):
        reach += 3

    total = fit + signal + timing + reach
    tiers = cfg.get("tiers", {"A": 70, "B": 45})
    tier = "A" if total >= tiers["A"] else "B" if total >= tiers["B"] else "C"
    return {"score": total, "tier": tier, "why_now": why_now, "notes": notes,
            "breakdown": {"uyum": fit, "sinyal": signal, "zamanlama": timing, "ulaşılabilirlik": reach}}
