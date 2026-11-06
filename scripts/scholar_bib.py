#!/usr/bin/env python3
"""Mechanical half of the scholar-bibtex skill: queue, apply and verify.

The browser half (searching Google Scholar, clicking Cite -> BibTeX, or
OpenReview "Show Bibtex") is done by the agent through Playwright. Each fetched
record is saved as a receipt, one JSON file per citation key:

    {"key": "ainsworth2023gitrebasin",          # the manuscript's citation key
     "query": "Git re-basin: Merging models ...", # what was typed into Scholar
     "source": "scholar" | "openreview" | "origin",
     "source_url": "https://scholar.googleusercontent.com/scholar.bib?...",
     "scholar_result": "https://openreview.net/forum?id=...",  # link shown by Scholar
     "venue_note": "why this version was chosen",
     "http": {"url": ..., "status": 200, "headers": {...},
              "body_base64": "..."},             # the server response itself
     "bibtex": "@inproceedings{...}"}            # decoded from that response

For Scholar, `bibtex` is the scholar.bib response body decoded as UTF-8; for
OpenReview, the note's `_bibtex` string in the notes API JSON. `ingest` refuses
a record whose `bibtex` cannot be re-derived from `http`, and `verify` repeats
that check, so no receipt holds text that was retyped or read off a page.

`apply` puts the receipt's BibTeX into the .bib with only the entry key changed
to the manuscript key. `verify` re-reads the .bib and checks every entry against
its receipt. Only the standard library is used.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

ENTRY_START = re.compile(r"@([A-Za-z]+)\s*([{(])")


def split_entries(text: str) -> list[dict]:
    """Return entries as dicts with type, key, span and raw text (brace-matched)."""
    out = []
    pos = 0
    while True:
        m = ENTRY_START.search(text, pos)
        if not m:
            break
        typ = m.group(1)
        open_ch = m.group(2)
        close_ch = "}" if open_ch == "{" else ")"
        depth = 0
        i = m.end() - 1
        while i < len(text):
            c = text[i]
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if depth != 0:
            raise ValueError(f"unbalanced entry starting at offset {m.start()}")
        raw = text[m.start(): i + 1]
        entry = {"type": typ.lower(), "start": m.start(), "end": i + 1, "raw": raw, "key": None}
        if typ.lower() not in ("comment", "preamble", "string"):
            km = re.match(r"@[A-Za-z]+\s*[{(]\s*([^,\s]+)\s*,", raw)
            if not km:
                raise ValueError(f"no key in entry at offset {m.start()}: {raw[:60]!r}")
            entry["key"] = km.group(1)
        out.append(entry)
        pos = i + 1
    return out


def fields(raw: str) -> dict[str, str]:
    """Top-level field name -> value text (braces/quotes stripped once)."""
    body = raw[raw.index(",") + 1: -1]
    res: dict[str, str] = {}
    i = 0
    n = len(body)
    while i < n:
        m = re.compile(r"\s*,?\s*([A-Za-z][\w\-:]*)\s*=\s*").match(body, i)
        if not m:
            break
        name = m.group(1).lower()
        j = m.end()
        if j < n and body[j] == "{":
            depth, k = 0, j
            while k < n:
                if body[k] == "{":
                    depth += 1
                elif body[k] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            val = body[j + 1: k]
            i = k + 1
        elif j < n and body[j] == '"':
            k = j + 1
            while k < n and not (body[k] == '"' and body[k - 1] != "\\"):
                k += 1
            val = body[j + 1: k]
            i = k + 1
        else:
            k = j
            while k < n and body[k] not in ",\n":
                k += 1
            val = body[j:k].strip()
            i = k
        res[name] = val
    return res


def rekey(bibtex: str, key: str) -> str:
    """Replace the entry key; everything else stays byte for byte."""
    m = re.match(r"(\s*@[A-Za-z]+\s*[{(]\s*)([^,\s]+)(\s*,)", bibtex)
    if not m:
        raise ValueError("receipt bibtex has no entry key")
    return bibtex[: m.start(2)] + key + bibtex[m.end(2):]


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"\\[a-zA-Z]+|[{}]", "", t.lower())).strip()


def first_surname(authors: str) -> str:
    first = re.split(r"\s+and\s+", authors.strip(), maxsplit=1)[0]
    first = first.split(",")[0] if "," in first else (first.split()[-1] if first.split() else "")
    return norm_title(first)


def cited_keys(aux_paths: list[Path]) -> list[str]:
    keys: set[str] = set()
    for p in aux_paths:
        for line in p.read_text(errors="replace").splitlines():
            for m in re.finditer(r"\\(?:citation|abx@aux@cite\{[^}]*\})\{([^}]*)\}", line):
                keys.update(k.strip() for k in m.group(1).split(",") if k.strip())
    keys.discard("*")
    return sorted(keys)


def receipt_name(key: str) -> str:
    """File name for a key's receipt; keys may hold '/' or ':' (e.g. a DOI URL).
    The exact key lives inside the JSON."""
    return re.sub(r"[^\w.\-]", "_", key) + ".json"


def load_receipts(d: Path) -> dict[str, dict]:
    rec = {}
    for p in sorted(d.glob("*.json")):
        r = json.loads(p.read_text())
        rec[r["key"]] = r
    return rec


def cmd_queue(a) -> int:
    text = Path(a.bib).read_text()
    entries = {e["key"]: e for e in split_entries(text) if e["key"]}
    keys = cited_keys([Path(p) for p in a.aux]) if a.aux else sorted(entries)
    done = load_receipts(Path(a.receipts)) if a.receipts and Path(a.receipts).exists() else {}
    q = []
    for k in keys:
        e = entries.get(k)
        f = fields(e["raw"]) if e else {}
        q.append({"key": k, "in_bib": e is not None, "has_receipt": k in done,
                  "title": f.get("title", ""), "author": f.get("author", "")[:80],
                  "year": f.get("year", "")})
    json.dump(q, sys.stdout if a.out == "-" else open(a.out, "w"), indent=1, ensure_ascii=False)
    todo = sum(1 for x in q if not x["has_receipt"])
    print(f"\n{len(q)} keys, {todo} without receipt, {sum(not x['in_bib'] for x in q)} missing from bib",
          file=sys.stderr)
    return 0


TEX_TEXT = [(r"\\textquotesingle\s*(\{\})?\s*", "'"), (r"``|''", '"'), (r"\\&", "&"), (r"\\%", "%"),
            (r"\$\\alpha\$", "α"), (r"\$\\beta\$", "β"), (r"\$\\gamma\$", "γ"), (r"\$\\sigma\$", "σ"),
            (r'\\"\{?([aouAOU])\}?', lambda m: "äöüÄÖÜ"["aouAOU".index(m.group(1))]),
            (r"\\'\{?([aeiouAEIOU])\}?", lambda m: "áéíóúÁÉÍÓÚ"["aeiouAEIOU".index(m.group(1))])]


def query_text(title: str) -> str:
    """A bib title as text for the Scholar search box: accents, quotes and
    common math letters become characters; other commands and braces drop."""
    t = title
    for pat, sub in TEX_TEXT:
        t = re.sub(pat, sub, t)
    t = re.sub(r"\\[a-zA-Z]+\s*", "", t)
    return re.sub(r"\s+", " ", re.sub(r"[{}\\$]", "", t)).strip()


def cmd_batch(a) -> int:
    """Write scholar_fetch.js copies with QUERIES filled, `size` keys each."""
    q = [e for e in json.loads(Path(a.queue).read_text()) if a.all or not e["has_receipt"]]
    if a.keys:
        q = [e for e in q if e["key"] in set(a.keys)]
    src = (Path(__file__).parent / "scholar_fetch.js").read_text()
    marker = 'const QUERIES = [/* {key: "...", query: "exact title"} */];'
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(q), a.size):
        items = [{"key": e["key"], "query": query_text(e["title"])} for e in q[i: i + a.size]]
        (out / f"batch{i:03d}.js").write_text(src.replace(marker, "const QUERIES = " + json.dumps(items, ensure_ascii=False) + ";", 1))
    print(f"{len(q)} keys -> {-(-len(q) // a.size)} batches in {out}")
    return 0


FETCHED = ("venue", "venue_openreview", "venue_version", "preprint", "origin")


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def defect(bibtex: str) -> str:
    """Why a served record is unusable as it stands, or ''. Such a record is
    never repaired by the agent: it goes to the owner (see `owner`)."""
    why = []
    if "$$" in bibtex:
        why.append("display math $$ in a field")
    depth = 0
    for c in bibtex:
        depth += (c == "{") - (c == "}")
        if depth < 0:
            break
    if depth:
        why.append("unbalanced braces")
    if re.search(r"\\\$|\\textbackslash", bibtex):
        why.append("escaped LaTeX (prints its own source)")
    t = fields(bibtex).get("title", "") if bibtex.lstrip().startswith("@") else ""
    if re.search(rf",\s*(?:{MONTHS})\s+\d{{4}}\s*$", t):
        why.append("date appended to the title")
    return "; ".join(why)


def effective(r: dict) -> str:
    """The record that goes into the .bib: the owner's fix; for a key still
    awaiting the owner, the entry as it was before any replacement; otherwise
    the served record."""
    if r.get("owner_bibtex"):
        return r["owner_bibtex"]
    if r["status"] == "needs_owner":
        return r.get("original") or ""
    return r["bibtex"]


def from_response(r: dict) -> str | None:
    """Re-derive the record from the kept server response, or None if absent."""
    h = r.get("http") or {}
    if not h.get("body_base64"):
        return None
    body = base64.b64decode(h["body_base64"]).decode("utf-8")
    if r.get("source") == "openreview":
        for n in json.loads(body).get("notes", []):
            if n.get("id") == h.get("note_id") and "_bibtex" in n.get("content", {}):
                b = n["content"]["_bibtex"]
                return b if isinstance(b, str) else b.get("value")
        return None
    if r.get("source") == "origin":  # a citation block inside a README/CITATION file
        return r.get("bibtex") if r.get("bibtex") and r["bibtex"] in body else None
    return body


def cmd_ingest(a) -> int:
    """Turn scholar_fetch.js dumps into per-key receipts; later dumps win."""
    out = Path(a.receipts)
    out.mkdir(parents=True, exist_ok=True)
    originals = {e["key"]: e["raw"] for e in split_entries(Path(a.bib).read_text()) if e["key"]} if a.bib else {}
    for dump in a.dumps:
        data = json.loads(Path(dump).read_text())
        if isinstance(data, str):  # browser_evaluate(JSON.stringify(...)) double-encodes
            data = json.loads(data)
        for r in data:
            if r["status"] not in FETCHED + ("defective",):
                print(f"skip {r['key']}: {r['status']} {r.get('error', '')[:80]}")
                continue
            if not r.get("bibtex", "").lstrip().startswith("@"):
                print(f"skip {r['key']}: response is not BibTeX")
                continue
            if from_response(r) != r["bibtex"]:
                print(f"skip {r['key']}: bibtex does not match the kept server response")
                continue
            bad = defect(r["bibtex"])
            rec = {k: r.get(k) for k in ("key", "query", "status", "source", "source_url",
                                         "scholar_result", "scholar_meta", "origin_reason")}
            rec["venue_note"] = {"venue": "Scholar record is a venue",
                                 "venue_openreview": "Scholar record is a preprint; accepted OpenReview copy listed in All versions",
                                 "venue_version": "Scholar primary is a preprint; Scholar BibTeX of a venue copy listed in All versions",
                                 "preprint": "no venue record among Scholar results or the first All-versions pages",
                                 "origin": "no Scholar record; BibTeX block from the resource's own page",
                                 "defective": "every copy found is unusable as served"}[r["status"]]
            if bad:  # the served record stays as evidence; the owner writes the entry
                rec["fetched_status"], rec["status"], rec["defects"] = rec["status"], "needs_owner", bad
            if r.get("openreview_header"):
                rec["openreview_header"] = r["openreview_header"]
            if r.get("skipped"):
                rec["skipped"] = r["skipped"]  # defective copies passed over, kept as evidence
            rec["candidates"] = r.get("candidates", [])
            rec["versions"] = r.get("versions", [])
            rec["dump"] = str(dump)
            if r["key"] in originals:
                rec["original"] = originals[r["key"]]  # entry being replaced, for the author/year cross-check
            rec["http"] = r.get("http")
            rec["bibtex"] = r["bibtex"]
            (out / receipt_name(r["key"])).write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
            print(f"receipt {r['key']}: {rec['status']}" + (f" ({bad})" if bad else ""))
    return 0


def cmd_apply(a) -> int:
    bib = Path(a.bib)
    text = bib.read_text()
    rec = load_receipts(Path(a.receipts))
    entries = split_entries(text)
    pieces, last, replaced = [], 0, []
    seen = set()
    waiting = sorted(k for k, r in rec.items() if r["status"] == "needs_owner" and not r.get("owner_bibtex"))
    # entries awaiting the owner go back to what they were before any replacement
    rec = {k: r for k, r in rec.items() if effective(r)}
    for e in entries:
        if e["key"] in rec:
            r = rec[e["key"]]
            pieces.append(text[last: e["start"]])
            pieces.append(rekey(effective(r).strip(), e["key"]))
            last = e["end"]
            replaced.append(e["key"])
            seen.add(e["key"])
    pieces.append(text[last:])
    new = "".join(pieces)
    missing = sorted(set(rec) - seen)
    if missing:
        add = "\n".join("\n" + rekey(effective(rec[k]).strip(), k) for k in missing)
        new = new.rstrip("\n") + "\n" + add + "\n"
    if waiting:
        print(f"kept as before, awaiting the owner's fix ({len(waiting)}): {' '.join(waiting)}")
    if a.dry_run:
        print(f"would replace {len(replaced)}, append {len(missing)}")
        return 0
    bib.write_text(new)
    print(f"replaced {len(replaced)} entries, appended {len(missing)}: {bib}")
    return 0


def cmd_verify(a) -> int:
    text = Path(a.bib).read_text()
    entries = split_entries(text)
    rec = load_receipts(Path(a.receipts))
    keys = cited_keys([Path(p) for p in a.aux]) if a.aux else None
    by_key: dict[str, list] = {}
    for e in entries:
        if e["key"]:
            by_key.setdefault(e["key"], []).append(e)
    problems, notes = [], []
    for k, es in by_key.items():
        if len(es) > 1:
            problems.append(f"{k}: duplicate key ({len(es)} entries)")
    targets = keys if keys is not None else sorted(rec)
    for k in targets:
        if k not in rec:
            problems.append(f"{k}: no receipt")
            continue
        r = rec[k]
        if k not in by_key:
            problems.append(f"{k}: not in bib")
            continue
        raw = by_key[k][0]["raw"]
        if r["status"] == "needs_owner" and not r.get("owner_bibtex"):
            notes.append(f"{k}: awaiting the owner's fix ({r.get('defects')}); entry kept as before")
            if r.get("original") and raw != rekey(r["original"].strip(), k):
                problems.append(f"{k}: awaiting the owner, but the bib entry is not the one it replaced")
            continue
        want = rekey(effective(r).strip(), k)
        if raw != want:
            problems.append(f"{k}: bib entry differs from receipt " + ("owner_bibtex" if r.get("owner_bibtex") else "bibtex"))
        if r.get("owner_bibtex"):
            notes.append(f"{k}: owner-written entry by {r.get('owner_by')} on {r.get('owner_at')} (served record kept as evidence: {r.get('defects')})")
            if hashlib.sha256(r["owner_bibtex"].encode()).hexdigest() != r.get("owner_sha256"):
                problems.append(f"{k}: owner_bibtex changed after it was accepted")
        rf, bf = fields(want), fields(raw)
        extra = sorted(set(bf) - set(rf))
        if extra:
            problems.append(f"{k}: fields not in the fetched record: {extra}")
        if r.get("source") not in ("scholar", "openreview", "origin"):
            problems.append(f"{k}: source {r.get('source')!r} not allowed")
        if r.get("source") == "origin" and not (r.get("source_url") and r.get("origin_reason")):
            problems.append(f"{k}: origin record needs source_url and origin_reason (why Scholar had none)")
        if r.get("source") == "openreview" and "openreview.net" not in (r.get("scholar_result") or ""):
            problems.append(f"{k}: openreview record without a Scholar-listed openreview link")
        if r.get("bibtex") is None and r.get("owner_bibtex"):
            continue  # no source served a record; the owner's entry stands on the recorded evidence
        if defect(r["bibtex"]) and not r.get("owner_bibtex"):
            problems.append(f"{k}: unusable served record in the bib ({defect(r['bibtex'])})")
        if from_response(r) != r.get("bibtex"):
            problems.append(f"{k}: receipt bibtex is not the kept server response (missing or mismatched http body)")
        if r.get("source") == "scholar" and "scholar.googleusercontent.com/scholar.bib" not in r.get("source_url", ""):
            problems.append(f"{k}: scholar record without a scholar.bib source_url")
        q, t = norm_title(r.get("query", "")), norm_title(rf.get("title", ""))
        ratio = difflib.SequenceMatcher(None, q, t).ratio()
        if ratio < a.title_threshold:
            notes.append(f"{k}: title check {ratio:.2f} | query={r.get('query')!r} | fetched={rf.get('title')!r}")
        if r.get("original"):
            of = fields(r["original"])
            oa, fa = first_surname(of.get("author", "")), first_surname(rf.get("author", ""))
            if oa and fa and oa != fa:
                notes.append(f"{k}: first author differs: was {oa!r}, fetched {fa!r}")
            if of.get("year", "").isdigit() and rf.get("year", "").isdigit() and abs(int(of["year"]) - int(rf["year"])) > 1:
                notes.append(f"{k}: year differs: was {of['year']}, fetched {rf['year']}")
        if "year" not in rf:
            notes.append(f"{k}: fetched record has no year field (kept as fetched)")
        if "arxiv" in (rf.get("journal", "") + rf.get("booktitle", "")).lower():
            notes.append(f"{k}: arXiv record ({r.get('venue_note', 'no venue note')})")
    for p in problems:
        print("PROBLEM", p)
    for n in notes:
        print("CHECK  ", n)
    print(f"{len(targets)} keys checked, {len(problems)} problems, {len(notes)} items to eyeball")
    return 1 if problems else 0


def cmd_owner(a) -> int:
    """Show each record that needs the owner: why, where it came from, and
    exactly what the servers sent. The agent shows this; it does not write fixes."""
    rec = load_receipts(Path(a.receipts))
    todo = [r for k, r in sorted(rec.items()) if r["status"] == "needs_owner" and (a.all or not r.get("owner_bibtex"))
            and (not a.keys or k in a.keys)]
    fix_dir = Path(a.receipts).parent / "owner-fixes"
    for r in todo:
        print("=" * 78)
        print(f"{r['key']}: {r['defects']}")
        print(f"  served by : {r.get('source_url')}")
        if r.get("scholar_result"):
            print(f"  listed at : {r['scholar_result']}")
        print("  served record (verbatim):")
        print("    " + r["bibtex"].strip().replace("\n", "\n    "))
        for x in r.get("skipped", []):
            print(f"  also served, passed over ({x['reason']}): {x['source_url']}")
            print("    " + x["bibtex"].strip().replace("\n", "\n    "))
        if r.get("original"):
            print("  current entry in the .bib:")
            print("    " + r["original"].strip().replace("\n", "\n    "))
        print(f"  -> owner writes the entry to {fix_dir / receipt_name(r['key']).replace('.json', '.bib')}")
        print(f"     then: scholar_bib.py owner-accept {a.receipts} {r['key']} <that file> --by <name>")
    print(f"{len(todo)} records need the owner")
    return 0


def cmd_owner_accept(a) -> int:
    """Record the owner's own entry for a needs_owner key."""
    d = Path(a.receipts)
    rec = load_receipts(d)
    r = rec.get(a.key)
    if not r and a.source_url and a.origin_reason:
        # No source served a record (Scholar empty, the resource's pages carry
        # no BibTeX): the receipt holds the evidence and the owner's entry only.
        r = {"key": a.key, "query": a.query or a.key, "status": "needs_owner", "fetched_status": "not_found",
             "source": "origin", "source_url": a.source_url, "origin_reason": a.origin_reason,
             "evidence": a.evidence or [], "bibtex": None, "http": None,
             "defects": "no source provides BibTeX", "venue_note": "no Scholar record and no citation block on the resource's own pages"}
    if not r or r["status"] != "needs_owner":
        print(f"{a.key}: no needs_owner receipt")
        return 1
    entries = [e for e in split_entries(Path(a.file).read_text()) if e["key"]]
    if len(entries) != 1:
        print(f"{a.file}: expected exactly one entry, found {len(entries)}")
        return 1
    fix = entries[0]["raw"]
    r["owner_bibtex"], r["owner_by"] = fix, a.by
    r["owner_at"] = datetime.date.today().isoformat()
    r["owner_sha256"] = hashlib.sha256(fix.encode()).hexdigest()
    r["owner_file"] = str(a.file)
    (d / receipt_name(a.key)).write_text(json.dumps(r, indent=1, ensure_ascii=False) + "\n")
    print(f"{a.key}: owner entry recorded ({a.by})" + (f"; still flagged: {defect(fix)}" if defect(fix) else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queue", help="list keys to fetch with their current title")
    q.add_argument("bib")
    q.add_argument("--aux", nargs="*", help="restrict to keys cited in these .aux files")
    q.add_argument("--receipts")
    q.add_argument("-o", "--out", default="-")
    q.set_defaults(fn=cmd_queue)
    b = sub.add_parser("batch", help="write scholar_fetch.js batches from a queue")
    b.add_argument("queue")
    b.add_argument("out_dir")
    b.add_argument("--size", type=int, default=5)
    b.add_argument("--keys", nargs="*", help="only these keys")
    b.add_argument("--all", action="store_true", help="include keys that already have a receipt")
    b.set_defaults(fn=cmd_batch)
    g = sub.add_parser("ingest", help="write receipts from scholar_fetch.js dumps")
    g.add_argument("receipts")
    g.add_argument("dumps", nargs="+")
    g.add_argument("--bib", help="current .bib; its entries are kept in the receipts for cross-checks")
    g.set_defaults(fn=cmd_ingest)
    o = sub.add_parser("owner", help="show records the owner must write, with source URLs and served text")
    o.add_argument("receipts")
    o.add_argument("--keys", nargs="*")
    o.add_argument("--all", action="store_true", help="include ones already fixed")
    o.set_defaults(fn=cmd_owner)
    oa = sub.add_parser("owner-accept", help="record the owner's own entry for a needs_owner key")
    oa.add_argument("receipts")
    oa.add_argument("key")
    oa.add_argument("file", help="a .bib file with the one entry the owner wrote")
    oa.add_argument("--by", required=True, help="who wrote it")
    oa.add_argument("--source-url", help="with --origin-reason: create the receipt for a key no source served")
    oa.add_argument("--origin-reason", help="why Scholar and the resource's own pages provided no BibTeX")
    oa.add_argument("--query", help="the title searched")
    oa.add_argument("--evidence", nargs="*", help="dump files that record the empty searches")
    oa.set_defaults(fn=cmd_owner_accept)
    p = sub.add_parser("apply", help="replace bib entries with receipt bibtex (key kept)")
    p.add_argument("bib")
    p.add_argument("receipts")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_apply)
    v = sub.add_parser("verify", help="check bib entries against receipts")
    v.add_argument("bib")
    v.add_argument("receipts")
    v.add_argument("--aux", nargs="*")
    v.add_argument("--title-threshold", type=float, default=0.85)
    v.set_defaults(fn=cmd_verify)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
