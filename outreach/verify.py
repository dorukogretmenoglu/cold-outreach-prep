"""Deterministic evidence checks. No model judgement happens here — only string, date and
domain rules — so a claim either provably matches its saved source or it is rejected."""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import ROOT, read_json
from .dates import parse_date_quote, parse_iso
from .textnorm import contains_quote, normalize, numbers_in

# source: "official" = must come from the lead's own domain; "own" = the seller's own docs/site.
# max_fetch_age: snapshot must be this fresh (days) — used for "current state" facts like prices.
# max_content_age: the content itself must carry a verified date this recent.
DEFAULT_POLICY: dict[str, dict] = {
    "company_fact": {"source": "official", "max_fetch_age": 7},
    "price": {"source": "official", "max_fetch_age": 7},
    "branch_count": {"source": "official", "max_fetch_age": 7},
    "contact": {"source": "official", "max_fetch_age": 7},
    "job_post": {"max_content_age": 60},
    "news": {"max_content_age": 365},
    "review": {"max_content_age": 180},
    "public_post": {"max_content_age": 180},
    "person_title": {"max_content_age": 365, "undated_official_ok": True, "max_fetch_age": 7},
    "disqualifier": {"max_fetch_age": 30},
    "vendor_reference": {"max_fetch_age": 30},  # "X uses our product" on a vendor's own site; informational
    "product_fact": {"source": "own"},
    "case_study": {"max_fetch_age": 180},
    "competitor_fact": {"max_fetch_age": 30},
}
LEAD_TYPES = {k for k in DEFAULT_POLICY if k not in ("product_fact", "case_study", "competitor_fact")}
KB_TYPES = {"product_fact", "case_study", "competitor_fact"}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MARKER = re.compile(r"\[([ck]\d{3,})\]")


def build_policy(overrides: dict | None) -> dict[str, dict]:
    policy = {k: dict(v) for k, v in DEFAULT_POLICY.items()}
    for type_, days in (overrides or {}).items():
        key = "max_content_age" if "max_content_age" in policy.get(type_, {}) else "max_fetch_age"
        policy.setdefault(type_, {})[key] = int(days)
    return policy


@dataclass
class Source:
    text: str
    kind: str                      # "snapshot" | "file"
    meta: dict = field(default_factory=dict)
    full_text: str = ""            # includes CSS-hidden elements (tabs, accordions); may be empty

    def locate(self, quote: str) -> str | None:
        """'visible', 'hidden' (only in tabs/accordions/carousels) or None."""
        if contains_quote(self.text, quote):
            return "visible"
        if self.full_text and contains_quote(self.full_text, quote):
            return "hidden"
        return None

    @property
    def domain(self) -> str:
        return self.meta.get("final_domain") or self.meta.get("domain", "")

    @property
    def fetched_on(self) -> date | None:
        return parse_iso(self.meta.get("fetched_at"))


def _same_site(domain: str, expected: str) -> bool:
    domain, expected = domain.lower(), expected.lower().removeprefix("www.")
    return bool(expected) and (domain == expected or domain.endswith("." + expected))


def load_source(claim: dict, snap_dir: Path) -> tuple[Source | None, str | None]:
    if claim.get("snapshot"):
        sid = claim["snapshot"]
        meta_path, text_path = snap_dir / f"{sid}.json", snap_dir / f"{sid}.txt"
        if not meta_path.exists():
            return None, f"snapshot {sid} yok"
        meta = read_json(meta_path)
        if not meta.get("ok"):
            return None, f"snapshot {sid} başarısız çekim ({meta.get('error', 'bilinmiyor')}), kaynak olamaz"
        if not text_path.exists():
            return None, f"snapshot {sid} metni yok"
        text = text_path.read_text(encoding="utf-8")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != meta.get("sha256"):
            return None, f"snapshot {sid} çekimden sonra değiştirilmiş"
        full = ""
        full_path = snap_dir / f"{sid}.full.txt"
        if meta.get("full_sha256") and full_path.exists():
            full = full_path.read_text(encoding="utf-8")
            if hashlib.sha256(full.encode("utf-8")).hexdigest() != meta["full_sha256"]:
                return None, f"snapshot {sid} gizli katmanı çekimden sonra değiştirilmiş"
        return Source(text, "snapshot", meta, full), None
    if claim.get("file"):
        path = (ROOT / claim["file"]).resolve()
        if ROOT not in path.parents:
            return None, "dosya proje klasörünün dışında"
        if not path.exists():
            return None, f"dosya yok: {claim['file']}"
        return Source(path.read_text(encoding="utf-8"), "file"), None
    return None, "kaynak yok (snapshot ya da file gerekli)"


def _check_date(claim: dict, src: Source, rule: dict, today: date) -> list[str]:
    errors = []
    claimed = parse_iso(claim.get("content_date"))
    if claimed is None:
        if rule.get("undated_official_ok") and src.meta.get("_official"):
            return []
        return ["içerik tarihi yok ya da geçersiz; tarihsiz bilgi bu tür için kullanılamaz"]
    evidence = (claim.get("date_evidence") or "").strip()
    tolerance = 0
    if not evidence:
        return ["date_evidence yok: tarih sayfadan kanıtlanmalı"]
    if evidence.startswith("meta:") or evidence in src.meta.get("html_dates", {}):
        key = evidence if evidence in src.meta.get("html_dates", {}) else evidence.removeprefix("meta:")
        values = src.meta.get("html_dates", {}).get(key, [])
        if claimed not in {parse_iso(v) for v in values}:
            errors.append(f"sayfanın {key} tarihleri {values[:3]} içinde {claimed} yok")
    else:
        if src.locate(evidence) is None:
            errors.append("tarih alıntısı sayfada birebir geçmiyor")
        reference = src.fetched_on or today
        parsed = parse_date_quote(evidence, reference)
        if parsed is None:
            errors.append(f"tarih alıntısından tarih okunamadı: {evidence!r}")
        else:
            stated, tolerance = parsed
            if abs((stated - claimed).days) > tolerance:
                errors.append(f"tarih alıntısı {stated} diyor, iddia {claimed} diyor")
    if claimed > today:
        errors.append(f"içerik tarihi gelecekte: {claimed}")
    oldest_plausible = date.fromordinal(claimed.toordinal() - tolerance)
    age = (today - oldest_plausible).days
    if age > rule["max_content_age"]:
        errors.append(f"bilgi {age} günlük, bu tür için sınır {rule['max_content_age']} gün")
    return errors


def verify_claim(claim: dict, snap_dir: Path, policy: dict, today: date,
                 lead: dict | None = None, own_domain: str | None = None,
                 excluded: list[dict] | None = None) -> list[str]:
    type_ = claim.get("type")
    if type_ not in policy:
        return [f"bilinmeyen tür: {type_}"]
    rule = policy[type_]
    errors = [f"{k} alanı boş" for k in ("statement", "quote") if not str(claim.get(k, "")).strip()]
    if errors:
        return errors
    quote = claim["quote"]
    if len(normalize(quote)) < 8:
        return ["alıntı çok kısa (en az 8 karakter), tek başına kanıt olamaz"]
    if len(quote) > 500:
        return ["alıntı çok uzun (en fazla 500 karakter), iddiayı taşıyan kısmı alıntıla"]

    src, err = load_source(claim, snap_dir)
    if err:
        return [err]
    layer = src.locate(quote)
    if layer is None:
        errors.append("alıntı kaynakta birebir geçmiyor")
    elif layer == "hidden":
        claim["layer"] = "hidden"
    else:
        claim.pop("layer", None)
    for rule_ in excluded or []:
        d = rule_["domain"].lower().removeprefix("www.")
        if src.kind == "snapshot" and _same_site(src.domain, d):
            errors.append(f"hariç tutulan kaynak ({d}): {rule_.get('reason', '')}")

    if src.kind == "snapshot" and lead and _same_site(src.domain, lead.get("domain", "")):
        src.meta["_official"] = True
    if rule.get("source") == "official" and src.meta.get("method") in ("manual", "chrome"):
        errors.append("resmi bilgiler (fiyat, şube, iletişim vb.) elle eklenen kaynaktan gelemez; şirketin sitesinden fetch ile çek")
    elif rule.get("source") == "official" and not src.meta.get("_official"):
        errors.append(f"bu tür sadece şirketin kendi sitesinden ({lead.get('domain') if lead else '?'}) gelebilir, kaynak: {src.domain}")
    if rule.get("source") == "own" and src.kind == "snapshot" and not _same_site(src.domain, own_domain or ""):
        errors.append("ürün bilgisi ya senin dokümanından ya da ürün sitenden gelmeli")

    if "max_fetch_age" in rule and src.kind == "snapshot":
        fetched = src.fetched_on
        if fetched is None or (today - fetched).days > rule["max_fetch_age"]:
            errors.append(f"sayfa {rule['max_fetch_age']} günden eski çekilmiş; güncel bilgi için yeniden çek")
    if "max_content_age" in rule:
        errors += _check_date(claim, src, rule, today)

    supported = numbers_in(quote) | numbers_in(claim.get("date_evidence") or "")
    extra = numbers_in(claim["statement"]) - supported
    if extra:
        errors.append(f"iddiadaki sayılar alıntıda yok: {sorted(extra)}")
    if "value" in claim and str(claim["value"]).replace(".", "").replace(",", "") not in numbers_in(quote):
        errors.append(f"value={claim['value']} alıntıda geçmiyor")
    if type_ == "contact":
        emails = set(_EMAIL.findall(quote))
        if not emails:
            errors.append("contact türü alıntıda yayınlanmış bir e-posta içermeli")
        elif claim.get("email") and claim["email"].lower() not in {e.lower() for e in emails}:
            errors.append("email alanı alıntıdaki adresle aynı değil")
    return errors


def check_draft(draft: dict, verified: dict[str, dict], profile: dict) -> list[str]:
    """verified: claim id -> verified claim (lead claims for this lead + KB claims)."""
    errors = []
    text = f"{draft.get('subject', '')}\n{draft.get('body', '')}"
    cited = _MARKER.findall(text)
    if not cited:
        errors.append("mesaj hiçbir kanıta dayanmıyor (en az bir [cNNN] işareti olmalı)")
    for cid in cited:
        claim = verified.get(cid)
        if claim is None:
            errors.append(f"[{cid}] doğrulanmış bir iddia değil")
        elif claim.get("lead") not in (None, draft.get("lead")):
            errors.append(f"[{cid}] başka bir şirkete ait")

    msg_cfg = profile.get("message", {})
    allowed = {str(n) for n in msg_cfg.get("allowed_numbers", [])}
    supported = set(allowed)
    for cid in cited:
        if cid in verified:
            c = verified[cid]
            supported |= numbers_in(c["quote"]) | numbers_in(c.get("statement", "")) \
                | numbers_in(c.get("date_evidence") or "")
    plain = _MARKER.sub("", text)
    extra = numbers_in(plain) - supported
    if extra:
        errors.append(f"mesajdaki sayılar hiçbir kanıtta yok: {sorted(extra)}")

    to = (draft.get("to") or "").strip().lower()
    if draft.get("channel", "email") == "email" and not to:
        errors.append("e-posta kanalı için doğrulanmış alıcı yok; kanalı linkedin ya da form yap")
    if to:
        published = {c.get("email", "").lower() for c in verified.values()
                     if c.get("type") == "contact" and c.get("lead") == draft.get("lead")}
        published |= {e.lower() for c in verified.values() if c.get("type") == "contact"
                      and c.get("lead") == draft.get("lead") for e in _EMAIL.findall(c["quote"])}
        if to not in published:
            errors.append("alıcı e-postası doğrulanmış bir contact kaydından gelmiyor (tahmin edilmiş adres kullanılamaz)")

    channel = draft.get("channel", "email")
    limits = msg_cfg.get("limits", {}).get(channel, {})
    body = _MARKER.sub("", draft.get("body", "")).strip()
    if (mw := limits.get("max_words")) and len(body.split()) > mw:
        errors.append(f"{channel} mesajı {len(body.split())} kelime, sınır {mw}")
    if (mc := limits.get("max_chars")) and len(body) > mc:
        errors.append(f"{channel} mesajı {len(body)} karakter, sınır {mc}")
    return errors


def strip_markers(text: str) -> str:
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", re.sub(r"[ \t]*\[[ck]\d{3,}\]", "", text)).strip()


def cited_ids(text: str) -> list[str]:
    return list(dict.fromkeys(_MARKER.findall(text)))
