# Setting up a new product pack

Goal: `products/<slug>/profile.toml`, the seller's own docs in `products/<slug>/docs/`, and a
verified knowledge base in `products/<slug>/knowledge/claims.jsonl`. Use
`products/demo-bakeplan/` as the template; copy its structure.

## 1. Interview (keep it short)

Ask only what you cannot find yourself. If the user gives a product website, fetch it first and
draft answers from it for them to confirm.

0. Which country (or countries) do you sell in? Set `[market] country = "<ISO code>"`. If `markets/<code>.toml` does not exist, build it before anything else (see "Market catalog" below).
1. What does the product do, in one sentence? What problem does it remove?
2. Stage: idea / pilot / selling? (A pilot has no results to claim.)
3. Who buys: segments, rough size range, geography. Who is explicitly *not* a fit?
4. Which job titles decide or feel the pain? Split them into decision makers (`[[icp.decision_makers]]`) and titles whose mere existence at a company shows it already owns the problem (`[scoring].relevant_titles`, e.g. "maliyet kontrol"; scored via `signal_weights.title`).
5. What would show a company has the problem *right now*? (job posts, growth news, complaints, public statements). Turn these into 2-4 search patterns per signal type.
6. Sender name, company, signature, and any honest context that may be mentioned.
7. Channels (email, LinkedIn) and language.

If the user keeps notes somewhere (a folder, an Obsidian vault, docs), read them for these answers instead of asking.

## 2. Own docs → product knowledge

Write what the user confirmed about their *own* product into `docs/<slug>.md`, one fact per
sentence. Then record each as a `product_fact` with `add-claim --product <slug>`. Only facts the
user states about their own product go here, and never results the product has not achieved yet.

## 3. Third-party knowledge → verify at the source

Case studies, competitor facts and market figures (even from the user's own notes) must be
fetched from the original page and recorded as `case_study` / `competitor_fact` with a quote. List
anything you could not verify in `docs/dogrulanacak.md`; it may not be used in messages.

```bash
python -m outreach fetch "<url>" --product <slug> --expect "<key term>"
python -m outreach add-claim --product <slug> --stdin
python -m outreach verify --product <slug>
```

## Market catalog (once per country)

Copy `markets/tr.toml` as the template. For every candidate source (review sites, job boards,
news sites, registries, business directories):

1. Check robots.txt for the exact page types you would fetch (`_robots_allows` in `outreach/fetch.py`).
2. Fetch the site's terms of use and read the clauses on automated access, copying and commercial use.
   Record the decision and a one-line reason with the date. A site whose terms forbid commercial
   copying goes to `excluded` (enforced for every method, including user pastes).
3. List local news domains under `[search].news_domains` so WebSearch can be restricted to them,
   and write `query_tips` for the local language (morphology, date formats, typical phrasing).
4. Put anything you could not check under `to_check`. Never add a source because it "is probably fine".

Tell the user plainly which sources were ruled out and why; if they ask for a blocked source
(e.g. LinkedIn, Instagram), explain the rule and offer the user-supplied snapshot route.

## 4. Privacy

Real packs are git-ignored (`products/*` except the demo). Tell the user this, and never move a
real pack into the demo folder.
