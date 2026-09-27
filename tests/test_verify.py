import hashlib
import json
from datetime import date

import pytest

from outreach.dates import parse_date_quote
from outreach.export import draft_hash, _review_status
from outreach.kb import search
from outreach.score import score_lead
from outreach.verify import build_policy, check_draft, strip_markers, verify_claim

TODAY = date(2026, 9, 27)
POLICY = build_policy(None)
LEAD = {"id": "acme", "domain": "acme-lokanta.com.tr"}

PAGE = """Acme Lokanta Kurumsal
Türkiye genelinde 14 şubemizle hizmet veriyoruz.
Kariyer: Maliyet Kontrol Uzmanı arıyoruz. İlan tarihi: 12 Eylül 2026
Genel Müdür: Ayşe Yılmaz
İletişim: info@acme-lokanta.com.tr
Fiyatlar From CHF349 /month
Yorum: yemekler çok çabuk tükendi, akşam gittik yok dediler. 3 hafta önce"""


def make_snapshot(tmp_path, sid="s001", text=PAGE, domain="acme-lokanta.com.tr",
                  fetched_at="2026-09-27T10:00:00+00:00", ok=True, html_dates=None):
    meta = {"id": sid, "url": f"https://{domain}/x", "domain": domain, "final_domain": domain,
            "fetched_at": fetched_at, "ok": ok, "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "html_dates": html_dates or {}}
    (tmp_path / f"{sid}.json").write_text(json.dumps(meta), encoding="utf-8")
    (tmp_path / f"{sid}.txt").write_text(text, encoding="utf-8")
    return sid


def claim(**kw):
    base = {"id": "c001", "lead": "acme", "snapshot": "s001"}
    base.update(kw)
    return base


def check(tmp_path, c, lead=LEAD, own=None):
    return verify_claim(c, tmp_path, POLICY, TODAY, lead=lead, own_domain=own)


# ---------- quotes ----------

def test_exact_quote_on_official_site_passes(tmp_path):
    make_snapshot(tmp_path)
    c = claim(type="branch_count", statement="14 şubesi var", quote="Türkiye genelinde 14 şubemizle", value=14)
    assert check(tmp_path, c) == []


def test_quote_matching_ignores_case_whitespace_and_turkish_i(tmp_path):
    make_snapshot(tmp_path)
    c = claim(type="company_fact", statement="Acme bir lokanta", quote="ACME   LOKANTA  KURUMSAL")
    assert check(tmp_path, c) == []


def test_invented_quote_is_rejected(tmp_path):
    make_snapshot(tmp_path)
    c = claim(type="branch_count", statement="20 şubesi var", quote="Türkiye genelinde 20 şubemizle", value=20)
    errors = check(tmp_path, c)
    assert any("birebir geçmiyor" in e for e in errors)


def test_statement_cannot_add_numbers_missing_from_quote(tmp_path):
    make_snapshot(tmp_path)
    c = claim(type="company_fact", statement="Acme 30 şubeye çıkacak", quote="Acme Lokanta Kurumsal")
    assert any("sayılar alıntıda yok" in e for e in check(tmp_path, c))


def test_price_keeps_from_nuance_via_quote(tmp_path):
    make_snapshot(tmp_path)
    ok = claim(type="price", statement="Fiyat 349 CHF/ay'dan başlıyor", quote="From CHF349 /month")
    assert check(tmp_path, ok) == []


def test_tampered_snapshot_is_rejected(tmp_path):
    sid = make_snapshot(tmp_path)
    (tmp_path / f"{sid}.txt").write_text(PAGE + "\n50 şubemizle", encoding="utf-8")
    c = claim(type="company_fact", statement="x", quote="Acme Lokanta Kurumsal")
    assert any("değiştirilmiş" in e for e in check(tmp_path, c))


def test_failed_fetch_cannot_be_cited(tmp_path):
    make_snapshot(tmp_path, ok=False)
    c = claim(type="company_fact", statement="x", quote="Acme Lokanta Kurumsal")
    assert any("başarısız" in e for e in check(tmp_path, c))


def test_too_short_quote_rejected(tmp_path):
    make_snapshot(tmp_path)
    assert any("çok kısa" in e for e in check(tmp_path, claim(type="company_fact", statement="x", quote="14")))


# ---------- sources & freshness ----------

def test_official_fact_from_third_party_site_rejected(tmp_path):
    make_snapshot(tmp_path, domain="haberler.com")
    c = claim(type="branch_count", statement="14 şube", quote="Türkiye genelinde 14 şubemizle", value=14)
    assert any("kendi sitesinden" in e for e in check(tmp_path, c))


def test_subdomain_counts_as_official(tmp_path):
    make_snapshot(tmp_path, domain="kariyer.acme-lokanta.com.tr")
    c = claim(type="company_fact", statement="x", quote="Acme Lokanta Kurumsal")
    assert check(tmp_path, c) == []


def test_lookalike_domain_is_not_official(tmp_path):
    make_snapshot(tmp_path, domain="notacme-lokanta.com.tr")
    c = claim(type="company_fact", statement="x", quote="Acme Lokanta Kurumsal")
    assert any("kendi sitesinden" in e for e in check(tmp_path, c))


def test_old_snapshot_rejected_for_current_price(tmp_path):
    make_snapshot(tmp_path, fetched_at="2026-08-01T10:00:00+00:00")
    c = claim(type="price", statement="349 CHF'den başlıyor", quote="From CHF349 /month")
    assert any("yeniden çek" in e for e in check(tmp_path, c))


def test_job_post_with_verified_turkish_date_passes(tmp_path):
    make_snapshot(tmp_path, domain="kariyer.net")
    c = claim(type="job_post", statement="Maliyet kontrol uzmanı arıyor", quote="Maliyet Kontrol Uzmanı arıyoruz",
              content_date="2026-09-12", date_evidence="İlan tarihi: 12 Eylül 2026")
    assert check(tmp_path, c) == []


def test_claimed_date_must_match_date_quote(tmp_path):
    make_snapshot(tmp_path, domain="kariyer.net")
    c = claim(type="job_post", statement="Maliyet kontrol uzmanı arıyor", quote="Maliyet Kontrol Uzmanı arıyoruz",
              content_date="2026-09-25", date_evidence="İlan tarihi: 12 Eylül 2026")
    assert any("tarih alıntısı" in e for e in check(tmp_path, c))


def test_signal_without_date_rejected(tmp_path):
    make_snapshot(tmp_path, domain="kariyer.net")
    c = claim(type="job_post", statement="Maliyet kontrol uzmanı arıyor", quote="Maliyet Kontrol Uzmanı arıyoruz")
    assert any("tarih" in e for e in check(tmp_path, c))


def test_three_year_old_news_rejected(tmp_path):
    make_snapshot(tmp_path, domain="haber.com", text=PAGE + "\nYayın: 10.03.2023")
    c = claim(type="news", statement="Acme büyüyor", quote="Türkiye genelinde 14 şubemizle",
              content_date="2023-03-10", date_evidence="Yayın: 10.03.2023")
    assert any("günlük, bu tür için sınır" in e for e in check(tmp_path, c))


def test_relative_date_uses_fetch_date_and_tolerance(tmp_path):
    make_snapshot(tmp_path, domain="sikayet.com")
    c = claim(type="review", statement="Müşteri yemeklerin tükendiğini söylüyor",
              quote="yemekler çok çabuk tükendi", content_date="2026-09-06", date_evidence="3 hafta önce")
    assert check(tmp_path, c) == []


def test_meta_date_evidence(tmp_path):
    make_snapshot(tmp_path, domain="kariyer.net", html_dates={"datePosted": ["2026-09-12T08:00:00Z"]})
    good = claim(type="job_post", statement="İlan var", quote="Maliyet Kontrol Uzmanı arıyoruz",
                 content_date="2026-09-12", date_evidence="meta:datePosted")
    bad = dict(good, content_date="2026-09-20")
    assert check(tmp_path, good) == []
    assert check(tmp_path, bad)


def test_undated_team_page_ok_only_on_official_site(tmp_path):
    make_snapshot(tmp_path)
    c = claim(type="person_title", statement="Ayşe Yılmaz Genel Müdür", quote="Genel Müdür: Ayşe Yılmaz")
    assert check(tmp_path, c) == []
    make_snapshot(tmp_path, sid="s002", domain="rehber.com")
    assert check(tmp_path, dict(c, snapshot="s002"))


def test_contact_requires_published_email(tmp_path):
    make_snapshot(tmp_path)
    ok = claim(type="contact", statement="Genel iletişim adresi", quote="İletişim: info@acme-lokanta.com.tr",
               email="info@acme-lokanta.com.tr")
    guessed = dict(ok, email="ayse.yilmaz@acme-lokanta.com.tr")
    assert check(tmp_path, ok) == []
    assert any("aynı değil" in e for e in check(tmp_path, guessed))


def test_manual_snapshot_backs_review_but_not_official_facts(tmp_path):
    from outreach.fetch import save_manual
    meta = save_manual("https://www.google.com/maps/place/acme", "Ayşe K. · 2 gün önce\nAkşam gittik, tatlılar tükenmişti.",
                       tmp_path, "chrome")
    assert meta["ok"] and meta["captured_by"]
    review = claim(type="review", snapshot=meta["id"], statement="Müşteri akşam tatlıların tükendiğini yazmış",
                   quote="Akşam gittik, tatlılar tükenmişti.", content_date="2026-09-25", date_evidence="2 gün önce")
    assert check(tmp_path, review) == []
    meta2 = save_manual("https://acme-lokanta.com.tr/subeler", "Türkiye genelinde 14 şubemizle hizmet veriyoruz.",
                        tmp_path, "manual")
    official = claim(type="branch_count", snapshot=meta2["id"], statement="14 şube",
                     quote="Türkiye genelinde 14 şubemizle", value=14)
    assert any("elle eklenen" in e for e in check(tmp_path, official))


def test_excluded_source_rejected_everywhere(tmp_path):
    from outreach.fetch import save_manual
    rules = [{"domain": "yemeksepeti.com", "reason": "şartlar"}]
    meta = save_manual("https://www.yemeksepeti.com/restaurant/x", "uzun bir yorum metni burada duruyor", tmp_path,
                       "manual", excluded=rules)
    assert not meta["ok"] and "hariç" in meta["error"]
    make_snapshot(tmp_path, sid="s009", domain="yemeksepeti.com")
    c = claim(type="review", snapshot="s009", statement="x", quote="Acme Lokanta Kurumsal",
              content_date="2026-09-06", date_evidence="3 hafta önce")
    errors = verify_claim(c, tmp_path, POLICY, TODAY, lead=LEAD, excluded=rules)
    assert any("hariç" in e for e in errors)


def test_manual_snapshot_requires_url(tmp_path):
    from outreach.fetch import save_manual
    with pytest.raises(ValueError):
        save_manual("bir yerden kopyaladım", "metin metin metin metin metin", tmp_path, "manual")


# ---------- drafts ----------

VERIFIED = {
    "c001": {"id": "c001", "lead": "acme", "type": "branch_count", "statement": "14 şube",
             "quote": "Türkiye genelinde 14 şubemizle"},
    "c002": {"id": "c002", "lead": "acme", "type": "contact", "statement": "iletişim",
             "quote": "İletişim: info@acme-lokanta.com.tr", "email": "info@acme-lokanta.com.tr"},
    "c009": {"id": "c009", "lead": "other", "type": "company_fact", "statement": "x", "quote": "başka şirket metni"},
}
PROFILE = {"message": {"allowed_numbers": [15], "limits": {"email": {"max_words": 60}}}}


def draft(**kw):
    d = {"lead": "acme", "channel": "email", "to": "info@acme-lokanta.com.tr", "subject": "Şube sayısı",
         "body": "14 şubeniz var [c001]. 15 dakikalık bir görüşme?"}
    d.update(kw)
    return d


def test_good_draft_passes():
    assert check_draft(draft(), VERIFIED, PROFILE) == []


def test_draft_with_unsupported_number_rejected():
    errors = check_draft(draft(body="14 şubeniz [c001] israfı %30 azaltabilir."), VERIFIED, PROFILE)
    assert any("30" in e for e in errors)


def test_draft_citing_unverified_or_foreign_claim_rejected():
    assert any("doğrulanmış" in e for e in check_draft(draft(body="x [c404]"), VERIFIED, PROFILE))
    assert any("başka bir şirkete" in e for e in check_draft(draft(body="x [c009]"), VERIFIED, PROFILE))


def test_draft_without_evidence_rejected():
    assert check_draft(draft(body="Merhaba, ürünümüz harika."), VERIFIED, PROFILE)


def test_guessed_recipient_rejected():
    errors = check_draft(draft(to="ayse.yilmaz@acme-lokanta.com.tr"), VERIFIED, PROFILE)
    assert any("tahmin" in e for e in errors)


def test_email_channel_needs_recipient():
    assert any("alıcı yok" in e for e in check_draft(draft(to=""), VERIFIED, PROFILE))
    assert check_draft(draft(to="", channel="linkedin"), VERIFIED, PROFILE) == []


def test_length_limit():
    long_body = "14 şube [c001] " + "kelime " * 80
    assert any("sınır" in e for e in check_draft(draft(body=long_body), VERIFIED, PROFILE))


def test_strip_markers():
    assert strip_markers("14 şubeniz var [c001]. Vaka [k002], sonuç.") == "14 şubeniz var. Vaka, sonuç."


def test_review_invalidated_when_draft_changes():
    d = draft(errors=[])
    d["review"] = {"verdict": "pass", "draft_sha": draft_hash(d)}
    assert _review_status(d)[0] is True
    d["body"] += " Ek cümle."
    assert _review_status(d)[0] is False


# ---------- dates, kb, score ----------

@pytest.mark.parametrize("quote,expected", [
    ("12 Eylül 2026", date(2026, 9, 12)),
    ("EYLÜL 12, 2026", date(2026, 9, 12)),
    ("September 12, 2026", date(2026, 9, 12)),
    ("APRIL 3, 2026", date(2026, 4, 3)),
    ("12.09.2026", date(2026, 9, 12)),
    ("2026-09-12T08:00", date(2026, 9, 12)),
    ("dün", date(2026, 9, 26)),
    ("2 gün önce", date(2026, 9, 25)),
    ("tarih yok", None),
])
def test_parse_date_quote(quote, expected):
    parsed = parse_date_quote(quote, TODAY)
    assert (parsed[0] if parsed else None) == expected


def test_kb_search_handles_turkish_suffixes():
    kb = [{"id": "k001", "statement": "Marriott Zürih israfı azalttı", "quote": "reduced food waste by 26%"},
          {"id": "k002", "statement": "Fiyatlandırma tasarrufun yüzdesi", "quote": "..."}]
    hits = search(kb, "israfın azaltılması")
    assert hits and hits[0][1]["id"] == "k001"


def test_score_uses_only_given_claims_and_never_guesses_size():
    claims = [{"id": "c1", "type": "job_post", "signal": "job_post", "statement": "ilan", "content_date": "2026-09-20"},
              {"id": "c2", "type": "company_fact", "signal": "segment", "statement": "zincir"}]
    s = score_lead(claims, {"scoring": {"size": {"min": 5, "max": 40}}}, TODAY)
    assert s["breakdown"] == {"uyum": 20, "sinyal": 15, "zamanlama": 20, "ulaşılabilirlik": 0}
    assert "büyüklük bilinmiyor (tahmin edilmedi)" in s["notes"]
    assert "[c1]" in s["why_now"]


def test_size_rules_match_by_unit():
    profile = {"scoring": {"size": [{"unit": "şube", "min": 5, "max": 60},
                                    {"unit": "günlük öğün", "min": 2000, "max": 200000}]}}
    catering = [{"id": "c1", "type": "branch_count", "unit": "günlük öğün", "value": "45.000", "statement": "x"}]
    chain_too_big = [{"id": "c1", "type": "branch_count", "value": 120, "statement": "x"}]
    assert score_lead(catering, profile, TODAY)["breakdown"]["uyum"] == 15
    s = score_lead(chain_too_big, profile, TODAY)
    assert s["breakdown"]["uyum"] == 0 and any("aralığın dışında" in n for n in s["notes"])


def test_disqualifier_eliminates_lead():
    s = score_lead([{"id": "c1", "type": "disqualifier", "statement": "robotPOS kullanıyor"}], {}, TODAY)
    assert s["tier"] == "X"
