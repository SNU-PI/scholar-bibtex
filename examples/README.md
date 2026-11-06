# Hands-on exercise: five famous papers

A tiny manuscript that cites five well-known papers. Each `.bib` entry has only a
key and a title, so every author, venue, volume and page must come from Google Scholar.

| File | Role |
|---|---|
| `main.tex` | the manuscript; cites five keys |
| `references.bib` | one entry per key (title only), plus one entry nobody cites |
| `main.aux` | the list of cited keys that LaTeX would generate (`\citation{...}`) |
| `expected/references.bib` | what the `.bib` looked like after one real run |

The five papers: ResNet (He 2016), Transformer (Vaswani 2017), BERT (Devlin 2019),
Adam (Kingma 2015), GAN (Goodfellow 2014). `krizhevsky2012imagenet` is in the `.bib`
but not cited, so the skill should leave it alone.

## Steps

1. Copy `main.tex`, `references.bib` and `main.aux` into a working folder.
2. Open an agent session in that folder that has the skill installed and a
   Playwright browser tool (see the top-level README).
3. Ask:

   ```
   scholar-bibtex로 references.bib 갱신해줘
   ```

4. If Google Scholar shows a CAPTCHA, the agent stops. Solve it yourself in the
   browser window, then tell the agent to continue.

## What you should see

- `queue`: `5 keys, 5 without receipt, 0 missing from bib`
- `ingest`: one receipt per key (statuses such as `venue`, `venue_version`)
- `apply --dry-run`: `would replace 5, append 0`, then `apply`: `replaced 5 entries`
- `verify`: `5 keys checked, 0 problems`
- `krizhevsky2012imagenet` is unchanged

## Things that can differ from `expected/`

- Scholar results change over time. A title may be capitalized differently, a page
  range may appear or not, and a paper may come back as arXiv instead of a venue.
- Some papers are returned under another venue name or with initials only
  (Adam's authors come back as `Kingma, DP and Ba, JL`). The skill reports such
  oddities; it does not edit the record.
- Keep batches small and pace requests. Scholar rate-limits automated traffic.
- Anything the skill flags (`defective`, `needs_owner`, CHECK notes) is for you to
  decide. It will not fill the gap itself.
