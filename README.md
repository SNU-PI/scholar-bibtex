# scholar-bibtex

An agent skill that replaces a manuscript's `.bib` entries with the BibTeX that
Google Scholar (or the OpenReview page Scholar links to) actually serves, then
verifies every entry against a saved receipt.

The point is provenance: the agent never writes bibliographic fields itself, so
it cannot invent an author name or a venue. Each entry is the server's response,
copied byte for byte, with only the citation key kept.

## What it does

1. `queue`: lists the keys cited in your manuscript (from `.aux`) with their titles.
2. Fetch: drives a visible browser (Playwright) through Scholar, Cite, BibTeX and
   keeps the HTTP response itself as the source of truth.
3. `ingest`: writes one receipt per key (URL, status, headers, base64 body, decoded BibTeX).
4. `apply --dry-run`, then `apply`: puts the records into your `.bib`.
5. `verify`: re-derives each record from its receipt and flags any mismatch.

Records that look broken, or CAPTCHAs, are never "fixed" or bypassed by the agent;
they are handed to a human. See [SKILL.md](SKILL.md) for the full rules.

## Install

Clone into your agent's skills directory.

```bash
# Claude Code
git clone https://github.com/SNU-PI/scholar-bibtex.git ~/.claude/skills/scholar-bibtex

# Codex
git clone https://github.com/SNU-PI/scholar-bibtex.git ~/.codex/skills/scholar-bibtex
```

Then ask your agent, for example: "Use scholar-bibtex to update references.bib."

## Requirements

- Python 3 (standard library only) for `scripts/scholar_bib.py`
- An agent session with a Playwright browser tool for the fetch step
  (`scripts/scholar_fetch.js` is written as a `browser_run_code_unsafe` routine)

## Notes on use

- Meant for small, personal bibliography cleanup, driven at a human pace.
- Google Scholar rate-limits automated traffic and shows a CAPTCHA. The skill stops
  and asks you to solve it; it does not try to get around it.
- You are responsible for complying with Google Scholar's and OpenReview's terms of use.
- Verbatim records can contain Scholar's own oddities (author order, reprint years,
  title typos). The skill reports them instead of silently editing them.

## Origin

Extracted from the SNU-PI research toolkit.
