# Evidence rules

A claim is one fact plus the verbatim span of a saved source that proves it. The code in
`outreach/verify.py` checks it deterministically. A claim is either `verified` or `rejected`;
there is no "probably".

## Claim fields

| Field | Required | Meaning |
|---|---|---|
| `lead` | lead claims | Lead id from `leads` (omit for product knowledge claims) |
| `type` | yes | See the types table |
| `snapshot` or `file` | yes | `s00N` from `fetch`, or a path inside the project (the seller's own docs) |
| `statement` | yes | Faithful paraphrase of the quote. It must not add information. Every number in it must appear in the quote. |
| `quote` | yes | Smallest exact span from the snapshot `.txt` that proves the statement (8-500 chars). Copy it; never retype from memory or from a search snippet. |
| `content_date` | dated types | `YYYY-MM-DD`, the date the content itself carries |
| `date_evidence` | dated types | A verbatim date span from the page (`"İlan tarihi: 12 Eylül 2026"`, `"3 hafta önce"`) or `meta:<key>` for a date found in the page HTML (`fetch` lists them under `html_dates`, e.g. `meta:datePosted`) |
| `signal` | optional | Scoring tag: `segment`, `job_post`, `news`, `review`, `public_post` |
| `value`, `unit` | `branch_count` | Number (must appear in the quote) and unit (`şube` by default, e.g. `günlük öğün`) |
| `email` | `contact` | The published address; must appear in the quote |
| `tags` | optional | Keywords that help `kb-search` find product claims |

## Types and freshness

| Type | Source rule | Freshness |
|---|---|---|
| `company_fact`, `price`, `branch_count`, `contact` | Lead's own domain (subdomains OK) | Snapshot ≤ 7 days old |
| `person_title` | Any | Content ≤ 365 days; an undated page is OK only on the official site |
| `job_post` | Any | Content ≤ 60 days |
| `news` | Any | Content ≤ 365 days |
| `review`, `public_post` | Any | Content ≤ 180 days |
| `disqualifier` | Any | Snapshot ≤ 30 days |
| `product_fact` | Seller's own doc or product website | None |
| `case_study` | Any | Snapshot ≤ 180 days |
| `competitor_fact` | Any | Snapshot ≤ 30 days |

A pack can override day limits under `[freshness]`. Relative dates ("3 ay önce") are read from
the fetch date with a one-unit tolerance, and the oldest plausible date is used for the age check.

## Sources

- `fetch` snapshots: automated, robots.txt enforced. The only source allowed for official facts.
- `add-snapshot --method manual|chrome`: text the user pasted or that was read in the user's own browser, always tied to a URL. Allowed for reviews, posts, news and people; rejected for official facts.
- `file`: the seller's own docs (product facts only).
- Domains in the pack's `[sources].excluded` are rejected for every method, including claims recorded before the domain was excluded.

## Keep the nuance

The quote protects nuance, so the statement must keep it too:
- `From CHF349 /month` → "349 CHF/ay'**dan başlıyor**", not "349 CHF".
- A price for one plan, country or currency stays tied to that plan, country or currency.
- "yaklaşık", "üzeri", "hedefliyor", "planlıyor" stay in the statement. A plan is not a fact.
- Same-name companies: check the domain, city and sector before recording. If unsure, record nothing.

## Common rejections and the right fix

| Rejection | Right fix | Wrong fix |
|---|---|---|
| `alıntı kaynakta birebir geçmiyor` | Open the `.txt`, copy the exact span | Paraphrase inside `quote` |
| `iddiadaki sayılar alıntıda yok` | Extend the quote to include the number, or remove the number from the statement | Round or compute a number |
| `bu tür sadece şirketin kendi sitesinden` | Find it on the official site, or leave it empty | Relabel the type |
| `bilgi N günlük, sınır M` | Look for a newer source, or leave it empty | Change the date |
| `snapshot başarısız çekim` | Re-fetch with `--expect`, or try another page | Quote from the search snippet |
| `içerik tarihi yok` | Find the date on the page (`html_dates` or visible text) | Assume today's date |

## Empty is a valid answer

"Bulunamadı" is information. A lead with no decision maker, no size or no email is exported with
those cells empty and the score reflects it. That is always better than a plausible guess.
