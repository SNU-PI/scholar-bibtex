---
name: scholar-bibtex
description: "Replace a manuscript's .bib entries with the BibTeX that Google Scholar (or the OpenReview page Scholar links to) provides, fetched by driving the browser with Playwright, then verify every entry against its saved receipt. Use for bibliography cleanup, venue-over-arXiv upgrades, or auditing a .bib for invented fields."
---

# Scholar BibTeX

Each bibliography entry becomes a record someone else produced: the body of
the server's response to Google Scholar's `Cite -> BibTeX`, or the `_bibtex`
of OpenReview's notes API when Scholar lists an OpenReview copy. The value of
the exercise is provenance, so the record is copied, not improved.

## Rules

- **Server response, not page text.** The record is taken from the HTTP
  response the browser received (`scholar.bib` body; notes API JSON), and the
  receipt keeps that response (`http`: url, status, headers, base64 body).
  Never read the record off the rendered page (`innerText`, a screenshot, a
  modal) and never type it: rendering rewrites whitespace and entities, and a
  typed record is no longer the server's. `ingest` and `verify` re-derive
  `bibtex` from `http` and reject a mismatch.
- **Verbatim.** Keep the fetched text byte for byte: field set, field order,
  capitalization, spacing, and any typo or odd value. Never add, remove or edit a
  field (no `doi`, `url`, `pages`, venue names or braces from memory). The only
  change is the entry key, which stays the manuscript's citation key so `\cite`
  commands keep working. `apply` trims the record's outer whitespace; field
  values are untouched.
- **Broken records go to the owner.** A served record that would break or
  garble the manuscript (`$$` in a field, unbalanced braces, escaped LaTeX that
  prints its own source such as OpenReview's `PixArt-\${\textbackslash}alpha\$`,
  or a date glued to a stub's title) is passed over for the next copy in the
  order below. If no usable copy exists, `ingest` marks the key `needs_owner`
  and `apply` leaves its entry as it was. The agent never writes the fix: it
  runs `owner`, shows the owner every source URL and the verbatim served
  records, and the owner writes the entry, which `owner-accept` records next to
  the served evidence.
- **Accepted venue first, then arXiv.** In order:
  1. a matching Scholar result whose BibTeX is a venue (`booktitle`, or a
     `journal` that is not arXiv/CoRR/preprint);
  2. when the primary record is a preprint, the cluster's *All versions*: an
     accepted OpenReview copy's own BibTeX (the note's `_bibtex`), else Scholar's BibTeX for another venue
     copy (NeurIPS/PMLR/ACL/journal page). Only the primary row has a `인용`
     button; the same cite dialog for a version opens at
     `scholar?q=info:<data-did>:scholar.google.com/&output=cite`. Such records
     can lack `year`; keep them as fetched and report it;
  3. otherwise Scholar's arXiv record.
  Look through two pages of versions at most. A very famous arXiv-only paper
  (LayerNorm, GPT-2) has hundreds of copies and no venue to find.
- **Direct browser operation.** Drive the visible browser with Playwright:
  navigate to the search, click `인용`/`Cite`, click `BibTeX`, and take the
  `scholar.bib` response that click produces.
  Scholar's `scholar.bib` links answer the request API with 429 and a CAPTCHA;
  browser navigation with the session's cookies works. Pace with jitter
  (6–14 s between actions, a 25–60 s break every 6–10 actions, small mouse
  moves). On a CAPTCHA, which can also be an inline "로봇이 아니라는 확인"
  page with no results, stop the batch and ask the user to solve it in the
  browser window. Never solve it yourself. Resume from the first unfinished key,
  and re-run any key recorded as `not_found` shortly before the block.
- **Outside Scholar only when Scholar has nothing.** If no Scholar result
  matches (datasets, model cards, blog posts, repositories, old books missing
  from the index), take the BibTeX that the resource's own page provides, for
  example a Hugging Face dataset card's citation block, a repository's
  `CITATION.cff`/README BibTeX, or the publisher's export. Fetch the raw file
  or export (e.g. the card's `raw/main/README.md`) in the browser, keep that
  response in `http`, and copy the BibTeX block out of it unchanged (`verify`
  checks the block occurs verbatim in the body). Record it as
  `source: "origin"` with `source_url` and `origin_reason`. If no page provides
  BibTeX, leave the existing entry untouched and list it for the user. Never
  compose a record by hand.

## Workflow

Helpers live in `scripts/` next to this file.

1. **Queue.** List the keys to fetch with their current titles. Restrict to
   cited keys by passing the compiled `.aux`; without it, every entry is queued.

   ```bash
   python3 <skill-dir>/scripts/scholar_bib.py queue references.bib --aux main.aux --receipts <receipts-dir> -o queue.json
   ```

2. **Fetch.** `scripts/scholar_fetch.js` is a `browser_run_code_unsafe` routine.
   `batch` writes copies of it with `QUERIES` filled, about five keys each
   (query = the entry's title as text: accents, quotes and math letters such as
   `\"{u}`, `\textquotesingle`, `$\alpha$` become characters, other commands
   and braces drop), into a Playwright allowed root:

   ```bash
   python3 <skill-dir>/scripts/scholar_bib.py batch queue.json <playwright-root>/scholar [--keys K1 K2 ...]
   ```

   Run each batch with `filename`. For each key it searches Scholar, keeps the
   citable results whose title matches, tries non-arXiv results first, clicks
   through to BibTeX, and falls back to the OpenReview copy or the arXiv record
   as above. It returns a one-line-per-key summary and leaves the records on
   `about:blank` as `window.__scholarBib`. The run_code sandbox has no `fs`, so
   save them with `browser_evaluate(() => JSON.stringify(window.__scholarBib),
   filename=<dump>.json)` before the next navigation; never retype a record.
   Each record has `status` (`venue`, `venue_openreview`, `venue_version`,
   `preprint`, `defective` (every copy unusable), `not_found`, `captcha`, `error`), the source URL, the Scholar
   link and meta line, the server response `http`, and the `bibtex` decoded
   from it. A batch runs several minutes,
   and the call moves to the background.

   Read the returned candidates before saving: the title filter is a token
   overlap, so a same-titled different paper, a thesis, or a `[인용]` stub can
   match. When a result is wrong or missing, redo that key by hand in the
   browser (search a more specific query, open the right cluster).

3. **Receipts.** Turn dumps into one JSON per key in a receipts directory kept
   with the manuscript; later dumps win for the same key:

   ```bash
   python3 <skill-dir>/scripts/scholar_bib.py ingest <receipts-dir> dump1.json dump2.json ... --bib references.bib
   ```

   The receipt is the evidence: `http` is the server's response and `bibtex`
   is decoded from it; `ingest` skips a record where the two disagree. `--bib` keeps the entry being replaced, and `verify` uses it to flag a
   changed first author or year. Generic titles ("Pattern Classification") can
   land on a different work. For an `origin` record, save the raw response the
   same way, through `browser_evaluate` with `filename`.

4. **Apply and verify.**

   ```bash
   python3 <skill-dir>/scripts/scholar_bib.py apply references.bib <receipts-dir> --dry-run
   python3 <skill-dir>/scripts/scholar_bib.py apply references.bib <receipts-dir>
   python3 <skill-dir>/scripts/scholar_bib.py verify references.bib <receipts-dir> --aux main.aux
   ```

   Then list what the owner has to write, and show it to the owner as printed:

   ```bash
   python3 <skill-dir>/scripts/scholar_bib.py owner <receipts-dir>
   # owner writes <receipts-dir>/../owner-fixes/<key>.bib, then:
   python3 <skill-dir>/scripts/scholar_bib.py owner-accept <receipts-dir> <key> <file> --by <owner>
   python3 <skill-dir>/scripts/scholar_bib.py apply references.bib <receipts-dir>
   ```

   `verify` fails on a missing receipt, a receipt whose `bibtex` is not its kept
   server response, a bib entry that differs from its
   receipt, fields absent from the fetched record, a disallowed source, or
   duplicate keys. It also lists items to eyeball: titles that differ from the
   query and entries that remain arXiv. Then compile the manuscript and read the
   BibTeX log for new warnings, and check that the reference list still renders
   (entry types change, e.g. `@article` with an arXiv `journal`).

5. **Report.** Give the user the counts by status, the keys left unchanged with
   the reason, and the `CHECK` lines from `verify`. Call out verbatim records
   that change what the reader sees: Scholar's author order (`Huben` before
   `Cunningham`), corporate authors split as `Team, Qwen` (cited as "Team"),
   title typos (`Qwen3. 6`), and reprint years (Hebb 2005 for 1949). Keep the
   original entry when the fetched record names a different edition of a cited
   classic and no source has the original. The receipt's `original` field
   restores any single entry. Pushing the updated `.bib`
   to Overleaf or a shared remote needs the user's authorization.
