// Playwright routine for the scholar-bibtex skill. Fill QUERIES, save the file
// under a Playwright allowed root and run it with browser_run_code_unsafe
// (filename). It drives the visible page (the request API gets 429/CAPTCHA
// from scholar.bib), returns a key/status/title summary, and leaves the full
// records in window.__scholarBib on about:blank. Each record's bibtex is the
// server's HTTP response as sent (the scholar.bib body, or the _bibtex string
// of OpenReview's notes API JSON), never text read back from the rendered page;
// `http` keeps the response itself (url, status, headers, base64 body).
async (page) => {
  const QUERIES = [/* {key: "...", query: "exact title"} */];
  const VERSION_PAGES = 2;                  // venue copies of big clusters sit within ~20 versions
  const VENUE_HOSTS = /openreview\.net|proceedings\.neurips\.cc|papers\.nips\.cc|proceedings\.mlr\.press|aclanthology\.org|jmlr\.org|ieeexplore\.ieee\.org|dl\.acm\.org|link\.springer\.com|sciencedirect\.com|nature\.com|pnas\.org|aps\.org|iopscience|academic\.oup\.com|tandfonline\.com|wiley\.com|thecvf\.com|aaai\.org|ijcai\.org|jstor\.org|science\.org|cell\.com|direct\.mit\.edu|siam\.org|cambridge\.org|psycnet\.apa\.org|degruyter|europepmc\.org|pubmed\.ncbi\.nlm\.nih\.gov|annualreviews\.org|elifesciences\.org|journals\.plos\.org|frontiersin\.org|projecteuclid\.org|ams\.org/;

  // Human-scale, jittered pacing: mostly 6-14 s, now and then a 25-60 s break.
  let actions = 0, nextBreak = 6 + Math.floor(Math.random() * 5);
  const nap = async () => {
    actions += 1;
    let ms = 6000 + Math.random() * 8000;
    if (actions >= nextBreak || Math.random() < 0.08) { ms += 25000 + Math.random() * 35000; nextBreak = actions + 6 + Math.floor(Math.random() * 5); }
    await page.mouse.move(100 + Math.random() * 700, 100 + Math.random() * 400, { steps: 5 + Math.floor(Math.random() * 10) }).catch(() => null);
    await page.waitForTimeout(ms);
  };
  const norm = s => (s || '').toLowerCase().replace(/\\[a-z]+|[{}]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
  const sim = (a, b) => {
    const A = new Set(norm(a).split(' ')), B = new Set(norm(b).split(' '));
    const inter = [...A].filter(x => B.has(x)).length;
    // Same letters with different word breaks ("Common Canvas" vs "CommonCanvas") match too.
    if (norm(a).replace(/ /g, '') === norm(b).replace(/ /g, '')) return 1;
    return inter / Math.max(A.size, B.size, 1);
  };
  // Scholar blocks as a /sorry/ redirect, a reCAPTCHA form, or an inline
  // "로봇이 아니라는 확인" / "not a robot" page with no results.
  const blocked = async () => {
    if (/\/sorry\/|google\.com\/sorry/.test(page.url())) return true;
    if (await page.locator('#gs_captcha_ccl, #captcha-form, iframe[src*="recaptcha"]').count()) return true;
    const text = await page.evaluate(() => document.body?.innerText?.slice(0, 3000) || '');
    return /로봇이 아니라는|비정상적인 트래픽|not a robot|unusual traffic/i.test(text);
  };
  // Books, chapters and theses are published records even without journal/booktitle.
  const isPreprint = bib => !/^@(book|incollection|inbook|phdthesis|mastersthesis)\b/i.test(bib.trim()) && (/(journal|booktitle)\s*=\s*\{[^}]*(arxiv|preprint|corr|biorxiv|e-prints)/i.test(bib) || /^@misc/i.test(bib.trim()) || !/(journal|booktitle)\s*=/i.test(bib));
  // A served record is unusable as it stands when it would break or garble the
  // manuscript: display math `$$` (Scholar's "PixArt-$$\backslash$alpha $"),
  // unbalanced braces, escaped LaTeX that prints its own source
  // (OpenReview's "PixArt-\${\textbackslash}alpha\$"), or a date glued to the
  // title (a [인용] stub's "..., September 2025"). Such a copy is passed over for
  // the next one; if none is usable the key goes to the owner (scholar_bib.py owner).
  // Keep in step with defect() in scholar_bib.py.
  const defect = bib => {
    const why = [];
    if (/\$\$/.test(bib)) why.push('display math $$ in a field');
    let d = 0;
    for (const c of bib) { if (c === '{') d++; else if (c === '}' && --d < 0) break; }
    if (d) why.push('unbalanced braces');
    if (/\\\$|\\textbackslash/.test(bib)) why.push('escaped LaTeX (prints its own source)');
    const t = (bib.match(/title\s*=\s*\{(.*)\},?\s*$/m) || [])[1] || '';
    if (/,\s*(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\s*$/.test(t)) why.push('date appended to the title');
    return why.join('; ');
  };
  const rows = () => page.$$eval('.gs_r.gs_or', rs => rs.map(r => ({
    stub: /^\[(인용|CITATION)\]/.test(r.querySelector('.gs_rt')?.innerText || ''),
    cid: r.dataset.cid, did: r.dataset.did,
    title: (r.querySelector('.gs_rt')?.innerText || '').replace(/^\[[^\]]+\]\s*/g, ''),
    href: r.querySelector('.gs_rt a')?.href || '', meta: r.querySelector('.gs_a')?.innerText || '',
    versions: [...r.querySelectorAll('.gs_fl a')].find(a => /cluster=/.test(a.href))?.href || '',
    citable: !!r.querySelector('.gs_or_cit'),
  })));
  // Start listening before the click that navigates to scholar.bib, then take
  // that response's body. A CAPTCHA page is an HTML body, not BibTeX.
  const bibResponse = () => page.waitForResponse(r => /\/scholar\.bib\?/.test(r.url()), { timeout: 20000 });
  const readBib = async (pending) => {
    const resp = await pending;
    const body = await resp.body();
    await page.waitForLoadState('domcontentloaded');
    if (await blocked()) throw new Error('CAPTCHA');
    return { url: resp.url(), bibtex: body.toString('utf8'),
      http: { url: resp.url(), status: resp.status(), headers: resp.headers(), body_base64: body.toString('base64') } };
  };
  // Primary result: click 인용 in the result list, then BibTeX.
  const scholarBib = async (cid) => {
    await page.keyboard.press('Escape').catch(() => null);
    const btn = page.locator(`.gs_r.gs_or[data-cid="${cid}"] .gs_or_cit`).first();
    await btn.scrollIntoViewIfNeeded();
    await btn.click({ timeout: 10000 }).catch(async () => { await page.keyboard.press('Escape'); await page.waitForTimeout(1000); await btn.click({ timeout: 10000 }); });
    await page.waitForSelector('#gs_citi a', { timeout: 15000 });
    await page.waitForTimeout(800 + Math.random() * 1200);
    const pending = bibResponse();
    await page.locator('#gs_citi a', { hasText: 'BibTeX' }).first().click();
    return readBib(pending);
  };
  // Non-primary version: only the primary row has a 인용 button, so open the
  // cite dialog Scholar serves for that version's id, then click BibTeX.
  const versionBib = async (did) => {
    await page.goto(`https://scholar.google.com/scholar?q=info:${did}:scholar.google.com/&output=cite&scirp=0&hl=ko`);
    if (await blocked()) throw new Error('CAPTCHA');
    const link = page.locator('a', { hasText: 'BibTeX' }).first();
    if (!(await link.count())) return null;
    await page.waitForTimeout(800 + Math.random() * 1200);
    const pending = bibResponse();
    await link.click();
    return readBib(pending);
  };
  // OpenReview's notes API JSON carries the note's _bibtex (API v1: a string,
  // v2: {value}). Keep the response the forum page loads; if the page was
  // server-rendered and made no such call, request it from the page (same
  // origin cookies) and keep that response.
  const openreviewBib = async (forum) => {
    const id = (forum.match(/[?&]id=([^&#]+)/) || [])[1];  // no URL global in the run_code sandbox
    const isNotes = r => /api2?\.openreview\.net\/notes\?/.test(r.url());
    const seen = [];
    const onResp = r => { if (isNotes(r)) seen.push(r); };
    page.on('response', onResp);
    await page.goto(forum); await page.waitForLoadState('domcontentloaded');
    await page.getByText(/Published:|Submitted:|Keywords:/).first().waitFor({ timeout: 15000 }).catch(() => null);
    const header = await page.evaluate(() => document.body.innerText.slice(0, 1500));
    const pick = async r => {
      const body = await r.body().catch(() => null);
      if (!body) return null;
      let notes;
      try { notes = JSON.parse(body.toString('utf8')).notes || []; } catch (e) { return null; }
      const n = notes.find(x => x.id === id && x.content && x.content._bibtex);
      if (!n) return null;
      const b = n.content._bibtex;
      return { url: forum, header, bibtex: typeof b === 'string' ? b : b.value,
        http: { url: r.url(), status: r.status(), headers: r.headers(), body_base64: body.toString('base64'), note_id: id } };
    };
    try {
      for (const r of seen) { const got = await pick(r); if (got) return got; }
      for (const api of ['https://api2.openreview.net', 'https://api.openreview.net']) {
        const url = `${api}/notes?id=${encodeURIComponent(id)}`;
        const pending = page.waitForResponse(r => r.url() === url, { timeout: 15000 }).catch(() => null);
        await page.evaluate(u => fetch(u, { credentials: 'include' }).then(r => r.text()).catch(() => ''), url);
        const r = await pending;
        const got = r && await pick(r);
        if (got) return got;
      }
    } finally { page.off('response', onResp); }
    return { url: forum, header, bibtex: '', http: null };
  };

  const out = [];
  for (const { key, query } of QUERIES) {
    const rec = { key, query, candidates: [], status: 'not_found' };
    try {
      const resultsUrl = 'https://scholar.google.com/scholar?hl=ko&q=' + encodeURIComponent(query);
      await page.goto(resultsUrl);
      if (await blocked()) throw new Error('CAPTCHA');
      // [인용]/[CITATION] stubs are used only when nothing else matches.
      // 0.85: one extra word in a short title ("Qwen2.5 Technical Report" vs
      // "Qwen2.5-Coder technical report") scores 0.8 and must not pass.
      const matched = (await rows()).filter(r => r.citable && sim(r.title, query) >= 0.85);
      const rs = matched.some(r => !r.stub) ? matched.filter(r => !r.stub) : matched;
      rec.candidates = rs.map(r => ({ stub: r.stub, cid: r.cid, title: r.title, meta: r.meta, href: r.href }));
      if (!rs.length) { out.push(rec); await nap(); continue; }
      // 1. Matching primary results, non-arXiv metas first; stop at a venue record.
      // Exact normalized title first, then non-arXiv metas; stable within groups.
      const exact = r => norm(r.title) === norm(query) ? 0 : 1;
      rs.sort((a, b) => exact(a) - exact(b) || /arxiv/i.test(a.meta) - /arxiv/i.test(b.meta));
      let fallback = null;
      for (const r of rs.slice(0, 3)) {
        await nap();
        if (!page.url().startsWith(resultsUrl)) { await page.goto(resultsUrl); if (await blocked()) throw new Error('CAPTCHA'); }
        const b = await scholarBib(r.cid);
        const got = { source: 'scholar', source_url: b.url, scholar_result: r.href, scholar_meta: r.meta, bibtex: b.bibtex, http: b.http };
        const bad = defect(b.bibtex);
        if (bad) { (rec.skipped = rec.skipped || []).push({ source_url: b.url, reason: bad, bibtex: b.bibtex }); }
        if (!bad && !isPreprint(b.bibtex)) { Object.assign(rec, got, { status: 'venue' }); break; }
        // Fallback ranking: usable over defective, then a record with a venue field
        // (arXiv journal) over a bare one, then a full result over a [인용] stub.
        const rank = x => (x.defect ? 4 : 0) + (/(journal|booktitle)\s*=/i.test(x.bibtex) ? 0 : 2) + (x.stub ? 1 : 0);
        const cand = { ...got, versions_url: r.versions, primary_did: r.did, stub: r.stub, defect: bad };
        if (!fallback || rank(cand) < rank(fallback)) fallback = cand;
      }
      if (rec.status === 'venue' || !fallback) { out.push(rec); await nap(); continue; }
      // 2. Preprint (or defective) primary: walk All versions (up to VERSION_PAGES) for a usable venue copy.
      let vs = [];
      if (fallback.versions_url) {
        for (let p = 0; p < VERSION_PAGES; p++) {
          await nap(); await page.goto(fallback.versions_url + (p ? `&start=${p * 10}` : ''));
          if (await blocked()) throw new Error('CAPTCHA');
          const pr = await rows();
          vs = vs.concat(pr);
          if (pr.length < 10 || pr.some(v => VENUE_HOSTS.test(v.href))) break;
        }
      }
      rec.versions = vs.map(v => ({ did: v.did, title: v.title, href: v.href, meta: v.meta }));
      // 2a. Accepted OpenReview copy: its own BibTeX (carries year and url).
      const o = vs.find(v => /openreview\.net\/(forum|pdf)\?id=/.test(v.href));
      if (o) {
        const ob = await openreviewBib(o.href.replace('/pdf?', '/forum?'));
        if (ob.bibtex && !defect(ob.bibtex) && !/^@misc/i.test(ob.bibtex.trim()) && !/Submitted to|withdrawn|rejected/i.test(ob.bibtex + ob.header.slice(0, 400))) {
          Object.assign(rec, { source: 'openreview', source_url: ob.url, scholar_result: o.href, scholar_meta: o.meta, bibtex: ob.bibtex, http: ob.http, openreview_header: ob.header.slice(0, 400), status: 'venue_openreview' });
          out.push(rec); await nap(); continue;
        }
      }
      // 2b. Other venue copy: Scholar's BibTeX for that version id.
      const seen = new Set([fallback.primary_did]);
      const venueVs = vs.filter(v => VENUE_HOSTS.test(v.href) && !/openreview/.test(v.href) && !seen.has(v.did) && seen.add(v.did));
      for (const v of venueVs.slice(0, 2)) {
        await nap();
        const b = await versionBib(v.did);
        if (b && b.bibtex.trim().startsWith('@') && !defect(b.bibtex) && !isPreprint(b.bibtex)) {
          Object.assign(rec, { source: 'scholar', source_url: b.url, scholar_result: v.href, scholar_meta: v.meta, bibtex: b.bibtex, http: b.http, status: 'venue_version' });
          break;
        }
      }
      // 3. Otherwise the best remaining record: arXiv, or a defective one flagged
      // as such (ingest refuses it and the entry stays as it was).
      if (rec.status === 'not_found') Object.assign(rec, fallback, { status: fallback.defect ? 'defective' : 'preprint' });
    } catch (e) {
      rec.status = /CAPTCHA/.test(String(e)) ? 'captcha' : 'error'; rec.error = String(e).slice(0, 300);
      out.push(rec); if (rec.status === 'captcha') break; continue;
    }
    out.push(rec);
    await nap();
  }
  // The run_code sandbox has no fs. Park the verbatim records on a blank page;
  // the caller saves them with browser_evaluate(() => JSON.stringify(window.__scholarBib), filename).
  await page.goto('about:blank');
  await page.evaluate(o => { window.__scholarBib = o; }, out);
  return out.map(r => `${r.key}\t${r.status}\t${(r.bibtex || '').match(/title\s*=\s*\{(.*)\},?\s*$/m)?.[1] || ''}`).join('\n');
}
