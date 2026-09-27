# Setting up a new product pack

Goal: `products/<slug>/profile.toml`, the seller's own docs in `products/<slug>/docs/`, and a
verified knowledge base in `products/<slug>/knowledge/claims.jsonl`. Use
`products/demo-bakeplan/` as the template; copy its structure.

## 1. Interview (keep it short)

Ask only what you cannot find yourself. If the user gives a product website, fetch it first and
draft answers from it for them to confirm.

1. What does the product do, in one sentence? What problem does it remove?
2. Stage: idea / pilot / selling? (A pilot has no results to claim.)
3. Who buys: segments, rough size range, geography. Who is explicitly *not* a fit?
4. Which job titles decide or feel the pain?
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

## 4. Privacy

Real packs are git-ignored (`products/*` except the demo). Tell the user this, and never move a
real pack into the demo folder.
