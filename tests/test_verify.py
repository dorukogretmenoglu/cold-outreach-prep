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
Fiyatlar From EUR99 /month
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
    ok = claim(type="price", statement="Fiyat 99 EUR/ay'dan başlıyor", quote="From EUR99 /month")
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
    c = claim(type="price", statement="99 EUR'den başlıyor", quote="From EUR99 /month")
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


@pytest.mark.parametrize("status,text,challenged", [
    (202, "Unfortunately, bots use DuckDuckGo too. Please complete the following challenge to confirm this search was made by a human.", True),
    (403, "Forbidden", True),
    (429, "Too many requests", True),
    (200, "Checking your browser before accessing the site", True),
    (200, "Pricing From EUR99 /month", False),
    (200, "Bu yazıda captcha nedir anlatıyoruz. " + "uzun makale metni " * 400, False),
])
def test_bot_challenge_detection(status, text, challenged):
    from outreach.fetch import bot_challenge
    assert bot_challenge(status, text) is challenged


@pytest.mark.parametrize("status,text,allowed", [
    (None, "", False),                                   # unreachable -> assume disallow (RFC 9309)
    (503, "", False),                                    # server error -> disallow
    (404, "", True),                                     # no robots.txt -> no rules
    (200, "User-agent: *\nDisallow: /maps/", False),
    (200, "User-agent: *\nDisallow: /admin/", True),
])
def test_robots_decision(status, text, allowed):
    from outreach.fetch import robots_decision
    assert robots_decision(status, text, "https://example.com/maps/place/x")[0] is allowed


def test_retracted_claim_is_not_reverified_and_cannot_be_cited(tmp_path, monkeypatch):
    import outreach.__main__ as cli
    from outreach import config
    monkeypatch.setattr(config, "RUNS", tmp_path)
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    config.write_json(tmp_path / "r1" / "run.json", {"product": "demo-bakeplan", "created_at": "x"})
    config.write_jsonl(tmp_path / "r1" / "leads.jsonl", [{"id": "acme", "name": "Acme", "domain": "acme.com", "status": "selected"}])
    config.write_jsonl(tmp_path / "r1" / "claims.jsonl", [
        {"id": "c001", "lead": "acme", "type": "review", "file": "README.md", "statement": "x",
         "quote": "evidence-first B2B prospecting", "status": "verified"}])
    cli.main(["retract-claim", "c001", "--run", "r1", "--reason", "iddia alıntıdan fazlasını söylüyor"])
    cli.main(["verify", "--run", "r1"])
    row = config.read_jsonl(tmp_path / "r1" / "claims.jsonl")[0]
    assert row["status"] == "retracted"
    errors = cli._check({"lead": "acme", "channel": "linkedin", "body": "x [c001]"},
                        config.read_jsonl(tmp_path / "r1" / "claims.jsonl"), [], {})
    assert any("doğrulanmış" in e for e in errors)


def test_quote_only_in_hidden_layer_passes_but_is_flagged(tmp_path):
    make_snapshot(tmp_path, text="Pricing\nFrom EUR99 /month")
    full = "Pricing\nFrom EUR99 /month\nReal results\nÖrnek Otel Bodrum 26% Food waste reduction"
    meta = json.loads((tmp_path / "s001.json").read_text(encoding="utf-8"))
    meta["full_sha256"] = hashlib.sha256(full.encode()).hexdigest()
    (tmp_path / "s001.json").write_text(json.dumps(meta), encoding="utf-8")
    (tmp_path / "s001.full.txt").write_text(full, encoding="utf-8")
    c = claim(type="company_fact", statement="Örnek Otel Bodrum'da israf %26 azaldı",
              quote="Örnek Otel Bodrum 26% Food waste reduction")
    assert check(tmp_path, c) == [] and c["layer"] == "hidden"
    visible = claim(type="price", statement="99 EUR'den", quote="From EUR99 /month")
    assert check(tmp_path, visible) == [] and "layer" not in visible
    (tmp_path / "s001.full.txt").write_text(full + "\n90% reduction", encoding="utf-8")
    assert any("gizli katmanı" in e for e in check(tmp_path, dict(c)))


def test_market_catalog_exclusions_always_apply(tmp_path, monkeypatch):
    from outreach import config
    (tmp_path / "markets").mkdir()
    (tmp_path / "markets" / "tr.toml").write_text(
        'country = "TR"\n[sources]\nreview_sites = ["sikayetvar.com"]\n'
        'excluded = [{ domain = "kariyer.net", reason = "şartlar" }]\n', encoding="utf-8")
    pack = tmp_path / "products" / "p"
    pack.mkdir(parents=True)
    (pack / "profile.toml").write_text(
        '[market]\ncountry = "TR"\n[sources]\nexcluded = [{ domain = "ornek.com", reason = "paket" }]\n',
        encoding="utf-8")
    monkeypatch.setattr(config, "MARKETS", tmp_path / "markets")
    monkeypatch.setattr(config, "PRODUCTS", tmp_path / "products")
    profile = config.load_profile("p")
    assert {e["domain"] for e in profile["sources"]["excluded"]} == {"ornek.com", "kariyer.net"}
    assert profile["sources"]["review_sites"] == ["sikayetvar.com"]


def test_unknown_market_fails_loudly(tmp_path, monkeypatch):
    from outreach import config
    monkeypatch.setattr(config, "MARKETS", tmp_path)
    with pytest.raises(SystemExit):
        config.load_market("DE")


@pytest.mark.parametrize("url,expected", [
    ("https://x.com.tr/hakkimizda", "about"),
    ("https://x.com.tr/en/about-us", "about"),
    ("https://x.com.tr/blog/galataport-kurumsal-ziyafetler", "press"),
    ("https://x.com.tr/subelerimiz", "branches"),
    ("https://x.com.tr/subelerimiz/ornek-marka/kadikoy", "branches"),
    ("https://x.com.tr/organizasyon-yapisi", "team"),
    ("https://x.com.tr/organizasyon-ve-davet-yemekleri", None),
    ("https://x.com.tr/Info/cerez-uyari-yonetim-paneli", None),
    ("https://x.com.tr/kvkk-aydinlatma-metni", None),
    ("https://x.com.tr/kariyer", "careers"),
    ("https://x.com.tr/iletisim", "contact"),
    ("https://x.com.tr/menu/pizza", None),
])
def test_crawl_classify(url, expected):
    from outreach.crawl import classify
    assert classify(url) == expected


def test_pick_urls_prefers_index_pages_newest_press_and_own_domain():
    from outreach.crawl import pick_urls
    entries = [
        ("https://x.com/subelerimiz/a/b", ""), ("https://x.com/subelerimiz", ""),
        ("https://x.com/blog/eski", "2023-01-01"), ("https://x.com/blog/yeni", "2026-09-01"),
        ("https://x.com/blog", "2020-01-01"), ("https://baska.com/hakkimizda", ""),
    ]
    picked = pick_urls(entries, "x.com", 10)
    urls = [u for u, _ in picked]
    assert urls.index("https://x.com/subelerimiz") < urls.index("https://x.com/subelerimiz/a/b")
    press = [u for u, t in picked if t == "press"]
    assert press == ["https://x.com/blog", "https://x.com/blog/yeni", "https://x.com/blog/eski"]
    assert "https://baska.com/hakkimizda" not in urls


def test_parse_sitemap_index_and_urlset():
    from outreach.crawl import parse_sitemap
    idx = "<sitemapindex><sitemap><loc> https://x.com/s1.xml </loc></sitemap></sitemapindex>"
    assert parse_sitemap(idx) == ([], ["https://x.com/s1.xml"])
    urlset = "<urlset><url><loc>https://x.com/a</loc><lastmod>2026-09-01</lastmod></url><url><loc>https://x.com/b</loc></url></urlset>"
    assert parse_sitemap(urlset)[0] == [("https://x.com/a", "2026-09-01"), ("https://x.com/b", "")]


def test_fetch_chain_has_no_stealth_step():
    from outreach.fetch import _methods
    assert [name for name, _ in _methods(None)] == ["http", "browser"]


def test_spiders_never_retry_blocked_requests_and_obey_robots():
    from outreach.crawl import _base_spider
    base = _base_spider()
    assert base.max_blocked_retries == 0 and base.robots_txt_obey is True


def test_windows_line_endings_do_not_break_snapshot_hash(tmp_path):
    from outreach.fetch import save_manual
    meta = save_manual("https://x.com/a", "Şube listesi\r\nTürkiye genelinde 14 şubemizle\r\nhizmet", tmp_path, "manual")
    c = claim(type="review", snapshot=meta["id"], statement="x", quote="Türkiye genelinde 14 şubemizle",
              content_date="2026-09-27", date_evidence="bugün")
    errors = check(tmp_path, c)
    assert not any("değiştirilmiş" in e for e in errors)


@pytest.mark.parametrize("text,expected", [
    ("30 bin kişi kapasiteli", "30000"), ("2,5 milyon öğün", "2500000"), ("12.000 yemek", "12000"),
])
def test_numbers_in_reads_turkish_scale_words(text, expected):
    from outreach.textnorm import numbers_in
    assert expected in numbers_in(text)


def test_stdin_payload_is_read_as_utf8(monkeypatch):
    import io
    import types
    import outreach.__main__ as cli
    raw = '{"statement": "Işık şube ğüşiöç"}'.encode("utf-8")
    monkeypatch.setattr(cli.sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(raw)))
    assert cli._payload(types.SimpleNamespace(stdin=True, json=None))["statement"] == "Işık şube ğüşiöç"


def test_joined_quote_from_separate_parts_is_rejected(tmp_path):
    make_snapshot(tmp_path)
    joined = claim(type="company_fact", statement="x", quote="Acme Lokanta Kurumsal Genel Müdür: Ayşe Yılmaz")
    assert any("birebir geçmiyor" in e for e in check(tmp_path, joined))


def test_directory_entry_from_blocks_table_and_profile():
    from scrapling.parser import Selector
    from outreach.crawl import directory_entry
    block = Selector('<div><h4>Örnek Yemek A</h4><p>Tesisimiz 12.000 yemek/gün kapasiteye sahiptir. '
                     '<strong>Adres:</strong> Bursa www.ornekyemek-a.com</p></div>')
    lst = {"id": "b", "unit": "günlük öğün", "name_css": ["h4", "p strong"],
           "count_regex": r"([\d.]+)\s*(?:yemek|öğün)\s*/\s*gün",
           "website_regex": r"((?:https?://|www\.)[\w.-]+\.[a-z]{2,})"}
    e = directory_entry(block, lst, "u")
    assert (e["entity"], e["count"], e["domain"]) == ("Örnek Yemek A", 12000, "ornekyemek-a.com")
    no_name = Selector('<div><p><strong>Adres:</strong> x</p></div>')
    assert directory_entry(no_name, lst, "u") is None                     # a label is not a name
    row = Selector('<table><tr><td>Örnek Muhallebici [ 3 ]</td><td>x</td><td>21 [ 3 ]</td></tr></table>')
    e = directory_entry(row.css("tr")[0], {"id": "w", "name_css": "td:nth-child(1)", "count_css": "td:nth-child(3)"}, "u")
    assert (e["entity"], e["count"]) == ("Örnek Muhallebici", 21)
    profile = Selector('<body>Marka Adı: ÖRNEK DÖNER Adres: İstanbul Yurtiçi Şube Sayısı: 11 İnternet Sitesi http://www.ornekdoner.com</body>')
    e = directory_entry(profile, {"id": "u", "name_regex": r"Marka Adı:\s*(.+?)\s+Adres:",
                                  "count_regex": r"Yurtiçi Şube Sayısı\s*:\s*([\d.]+)",
                                  "website_regex": r"İnternet Sitesi\s+((?:https?://)?[\w.-]+\.[a-z]{2,})"}, "u")
    assert (e["entity"], e["count"], e["domain"]) == ("ÖRNEK DÖNER", 11, "ornekdoner.com")


@pytest.mark.parametrize("raw,ok", [("21", True), ("12.000", True), ("2024", False), ("1998", False), ("06", False)])
def test_screen_size_hint_filter(raw, ok):
    from outreach.__main__ import _plausible_size
    assert _plausible_size(raw) is ok


def test_size_in_range_prescreen():
    from outreach.score import size_in_range
    profile = {"scoring": {"size": [{"units": ["şube", "restoran"], "min": 5, "max": 60},
                                    {"unit": "günlük öğün", "min": 2000, "max": 200000}]}}
    assert size_in_range(21, "restoran", profile) is True
    assert size_in_range(300, "restoran", profile) is False
    assert size_in_range(12000, "günlük öğün", profile) is True
    assert size_in_range(None, "şube", profile) is None
    assert size_in_range(10, "firma", profile) is None


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


def test_csv_neutralizes_formula_injection():
    from outreach.export import COLUMNS, to_csv
    import csv as _csv, io as _io
    row = {c: "" for c in COLUMNS}
    row.update({"Şirket": '=HYPERLINK("http://evil","x")', "Notlar": "-1+2", "Mesaj": "normal"})
    parsed = next(_csv.DictReader(_io.StringIO(to_csv([row]))))
    assert parsed["Şirket"].startswith("'=") and parsed["Notlar"] == "'-1+2" and parsed["Mesaj"] == "normal"


def test_carry_over_keeps_user_status_and_earlier_leads_but_not_auto_status():
    from outreach.export import build
    leads = [{"id": "a", "name": "A", "domain": "a.com", "status": "selected"}]
    scores = {"a": {"tier": "C", "score": 30, "breakdown": {}, "notes": []}}
    carry = {
        "a.com": {"Site": "a.com", "Durum": "Taslak yazılmadı (öncelik B)", "Notlar": "benim notum"},
        "old.com": {"Site": "old.com", "Şirket": "Old", "Öncelik": "B", "Skor": "53", "Durum": "Gönderildi 28.09"},
    }
    rows, _, _ = build(leads, [], [], [], scores, [], carry)
    by_site = {r["Site"]: r for r in rows}
    assert by_site["a.com"]["Durum"] == "Taslak yazılmadı (öncelik C)"   # auto status recomputed
    assert by_site["a.com"]["Notlar"] == "benim notum"                    # user's note kept
    assert by_site["old.com"]["Durum"] == "Gönderildi 28.09"              # earlier run's lead kept
    assert [r["Site"] for r in rows] == ["old.com", "a.com"]              # B before C


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
    kb = [{"id": "k001", "statement": "Örnek Otel Bodrum israfı azalttı", "quote": "reduced food waste by 26%"},
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


def test_relevant_title_signal_uses_verified_quote_not_paraphrase():
    profile = {"scoring": {"relevant_titles": ["Maliyet Kontrol"],
                           "signal_weights": {"job_post": 15, "title": 15}}}
    real = {"id": "c1", "type": "person_title", "statement": "Sinan Bey", "quote": "Deniz Aksoy\nMALİYET KONTROL Müdürü"}
    only_in_statement = {"id": "c2", "type": "person_title", "statement": "Ali, maliyet kontrol müdürü",
                         "quote": "Ali Veli\nGenel Müdür"}
    s = score_lead([real], profile, TODAY)
    assert s["breakdown"]["sinyal"] == 15 and s["relevant_people"] == ["c1"]
    assert score_lead([only_in_statement], profile, TODAY)["breakdown"]["sinyal"] == 0


def test_title_signal_off_when_weight_missing():
    profile = {"scoring": {"relevant_titles": ["maliyet kontrol"], "signal_weights": {"job_post": 15}}}
    c = {"id": "c1", "type": "person_title", "statement": "x", "quote": "Maliyet Kontrol Müdürü"}
    assert score_lead([c], profile, TODAY)["breakdown"]["sinyal"] == 0


def test_size_unit_synonyms_and_out_of_range_cap():
    profile = {"scoring": {"size": [{"units": ["şube", "mağaza"], "min": 5, "max": 60}],
                           "signal_weights": {"review": 10}}}
    big_chain = [{"id": "c1", "type": "branch_count", "unit": "mağaza", "value": 300, "statement": "x"},
                 {"id": "c2", "type": "company_fact", "signal": "segment", "statement": "zincir"},
                 {"id": "c3", "type": "review", "signal": "review", "statement": "stok yok", "content_date": "2026-09-26"}]
    s = score_lead(big_chain, profile, TODAY)
    assert s["score"] >= 45 and s["tier"] == "C"            # points say B, size says no
    assert any("hedef aralığın" in n for n in s["notes"])
    in_range = [dict(big_chain[0], value=12)] + big_chain[1:]
    assert score_lead(in_range, profile, TODAY)["tier"] == "B"
    odd_unit = [dict(big_chain[0], unit="nokta")]
    assert any("eşleşmedi" in n for n in score_lead(odd_unit, profile, TODAY)["notes"])


def test_disqualifier_eliminates_lead():
    s = score_lead([{"id": "c1", "type": "disqualifier", "statement": "zaten bir rakip ürün kullanıyor"}], {}, TODAY)
    assert s["tier"] == "X"
