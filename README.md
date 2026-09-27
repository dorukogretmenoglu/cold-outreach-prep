# cold-outreach-prep

A Claude Code skill for evidence-first B2B prospecting. It finds companies that show a buying
signal for your product, researches them and the right decision maker, scores them, and drafts a
personalised first message. **Every fact in a message is backed by a verbatim quote from a saved
source, checked by code and then by an independent agent. It never sends anything.**

## Why evidence-first

One wrong fact in a cold message (an old price, the wrong company, an invented statistic) ends
the conversation. LLM research fails in predictable ways: JS-rendered pages return an empty shell
with `200 OK` and the model fills the gap from memory; search snippets are stale; nuance like
"from CHF 349" becomes "CHF 349". This project treats those as engineering problems:

| Failure | Guard |
|---|---|
| Empty JS page treated as success | Fetch chain `http → real browser → stealth browser`, escalates on thin text or missing expected terms |
| Fact filled from memory/snippet | A claim needs a verbatim quote found in a saved snapshot (checked by code, whitespace/case/Turkish-İ aware) |
| Snapshot edited after the fact | SHA-256 of every snapshot is checked |
| Stale information | Per-type age limits; dates must be proven by a date quote or page metadata |
| Invented numbers | Every number in a claim or message must appear in a cited quote |
| Guessed email addresses | Recipients must come from an address the company publishes |
| Wrong citation / misleading use | Blind verifier agent re-reads the sources and passes or fails each draft |
| Review then edit | A review is bound to a hash of the exact draft; any change invalidates it |

Unknown stays empty. A lead with no verified decision maker is exported with that cell blank.

## How it works

```
product pack ─┐
              ├─ 1. discovery: web search on signal patterns → 15-20 candidates → you pick
              ├─ 2. research: fetch pages → quote-backed claims (verified on write)
              ├─ 3. score: fit · signal · timing · reach, from verified claims only
              ├─ 4. draft: every fact cites [cNNN] lead evidence or [kNNN] product knowledge
              ├─ 5. verify: code checks + blind verifier agent
              └─ 6. export: Google Sheet (or CSV) + review files with sources + Gmail drafts on request
```

A **product pack** (`products/<slug>/`) holds everything product-specific: `profile.toml`
(segments, decision-maker titles, signal search patterns, exclusions, scoring, message rules), your
own docs, and a verified knowledge base that drafts retrieve from (BM25, no external services).
`products/demo-bakeplan/` is a fictional example. Real packs and all research runs are git-ignored.

## Setup

```bash
pip install -e ".[dev]"
scrapling install        # browser dependencies
python -m outreach doctor
python -m pytest
```

Having Google Chrome installed is recommended: the browser steps use it, which is the most reliable
option against bot protection. Google Drive and Gmail connectors in Claude are optional; without
them you get `sheet.csv` and plain-text drafts.

## Usage

Open the folder in Claude Code and ask, for example:

- "Yeni ürün paketi kuralım" / "set up a product pack for my product"
- "demo-bakeplan için müşteri adayı bul"
- "cevap gelen var mı?" (read-only reply check)

The skill lives in `.claude/skills/cold-outreach-prep/`, the verifier in
`.claude/agents/outreach-verifier.md`, the tooling in `outreach/` (`python -m outreach --help`).

## Limits and responsibilities

- It respects `robots.txt` and rate-limits per domain; disallowed pages (e.g. Google Maps) are
  skipped, not worked around. For a few shortlisted leads you can paste such text yourself (or have
  Claude read one page in your own browser); it is stored as a labelled user-supplied source and can
  never back official facts like prices or contacts. Domains whose terms forbid commercial use are
  listed under `[sources].excluded` in the pack and rejected for every method. It does not scrape
  LinkedIn or log in anywhere.
- It collects only professional information (name, title, public work statements, published
  business contact). You are responsible for how you contact people (KVKK, GDPR, anti-spam rules).
- The Google Drive connector cannot edit an existing sheet, so each export is a new sheet version;
  your status/notes columns are carried over from the latest version.
- Code checks prove a quote exists; they cannot prove it is interpreted correctly. That is what the
  verifier agent and your own final read are for.
