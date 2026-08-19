# Hardware Set Extraction

Extracts door hardware sets from Division 08 (Openings) construction specification PDFs.

## Summary

Input is a folder of specbook PDFs; output is one record per hardware set — `set_number`,
`description`, `location` (page, bounding box, line range), and `components[]` with `qty`,
`description`, `catalog_number`, `mfr`, `finish`, `notes` and a per-field confidence. Six commands,
one per step, produce it.

**Manufacturer and finish codes are told apart by position, not by value.** A PDF gives every word an
exact left edge, so each stream induces the horizontal interval its columns occupy, and a short code
is accepted as `mfr` or `finish` only when it lands inside that column's span. Vocabulary — the
book's legend plus the column layout induced from that same book — sets confidence rather than
membership; semantics only splits fields within a column. A value outside its column is rejected, and
the rejection stays in the record (483 corpus-wide). So `CA` is delivered as a finish 126 times and a
manufacturer twice, the finish column carries `630`/`US32D`/`US26D` while the manufacturer column
carries `IVE`/`SCH`/`LCN`, and `PE` resolves to Pemko in all 318 places it appears here.

Across 20 specbooks (42 PDFs, 16,177 pages) it extracts **1,295 hardware sets and 11,358
components**; 23 of those sets are NOT USED headers that legitimately carry none. One book has no
schedule at all — its Division 08 section makes the schedule a contractor shop-drawing submittal — so
it returns an empty result plus an alarm naming every rejected region. That is the right answer.

How I check correctness: the same input run twice reproduces all 2,249 products byte for byte, and
747 assertions across seven `*_checks.py` suites hold the output against facts I confirmed by hand in
the PDFs. The largest suite:

```bash
python pipeline/step3c_checks.py data/out/step3
```

## Running it

Python 3.13 — what this was built and verified on; the pinned dependencies declare 3.10 as their floor.

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\Activate.ps1 (PowerShell)
pip install -r requirements.txt
```

Neither `pdfs/` nor `data/` is in git — the specbooks are not mine to redistribute, and everything
under `data/out/` is reproducible from them — so a fresh clone starts empty. Put a project's specbook
PDFs in `pdfs/<project>/`, then run the six steps. Step 1 takes one project folder at a time; the
rest walk the whole tree. Every `--out` below is that step's default and can be dropped.

```bash
python pipeline/step1_locate.py "pdfs/<project>" --out data/out/step1                           # region location
python pipeline/step1p5_roles.py data/out/step1 --out data/out/step1p5                          # line roles
python pipeline/step2_chunk.py data/out/step1p5 --out data/out/step2                            # chunking
python pipeline/step3_dossier.py data/out/step2 --out data/out/step3                            # per-book dossier
python pipeline/step3_rules.py data/out/step2 --dossiers data/out/step3 --out data/out/step3    # field rules
python pipeline/step3c_assemble.py data/out/step3 --blocks data/out/step2 --out data/out/step3  # assembly
```

Then start the viewer and open <http://127.0.0.1:8000>:

```bash
python server/app.py
```

Pick a book and a set, see the set framed on the rendered page, click a component row to highlight
the line it came from, download the delivered JSON, or upload a new PDF and watch it run all six steps.

Assembly calls the Anthropic API (`claude-opus-5`); put `ANTHROPIC_API_KEY` in the environment or a
`.env` file (see `.env.example`) — the first five steps need no key. Every model response is cached on
disk, so a book already under `data/out` re-runs offline at no cost; on an uncached book the five
deterministic steps take about 3 s for a 344-page manual and assembly spends one model call per set
block (Forest Park School: 1 call, 2,592 in / 682 out tokens). Cold over the whole corpus here that
came to 1,272 calls, 4,721,872 input and 1,939,247 output tokens.

The seven check suites are offline and free; each prints PASS/FAIL per fact and exits 1 on failure:

```bash
python pipeline/step1_checks.py
python pipeline/step1p5_checks.py
python pipeline/step2_checks.py
python pipeline/step3_checks.py
python pipeline/step3b_checks.py
python pipeline/step3c_checks.py
python pipeline/server_checks.py
```

`server_checks.py` is the one about serving rather than accuracy: discovery finds every stream the
pipeline produced, counts match an independent recount straight off the JSONL, every component anchor
resolves to a real box on a page the set claims, page PNGs come back byte-identical on a second call,
and an upload deletes whole — pdf, output and model cache — without touching the corpus tree.

Each of the first three steps also has a view tool. `dump` writes a text view beside the file;
`overlay` renders a page with the boxes drawn on it, which is the honest test of the location layer:

```bash
python pipeline/step2_view.py overlay data/out/step2/<project>/<stream>.blocks.jsonl 285
```

The API under the viewer is small: `/api/streams` lists the extracted regions,
`/api/streams/{project}/{stream}` returns one stream's sets with their boxes, and
`/api/streams/{project}/{stream}/export.json` is the delivered JSON that the Export button downloads.

Four environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `FRESCO_DATA_ROOT` | walks up from `server/catalog.py` to the first directory holding `data/out/step3` | Where the viewer reads products |
| `FRESCO_PAGE_CACHE` | `<root>/data/out/server_cache` | Page-image cache |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address |

Deployed: _(link to be added)_

## What's next

- **Accuracy against a hand-labeled ground truth** is the main gap: every specbook here informed the
  rules, so a number measured on them would report fit rather than generalization — the meaningful
  version needs specbooks the system has never seen.
- **Per-page column intervals.** Column spans are induced per stream, so a stream mixing prose pages
  with tables loses its column evidence and those components ship with `null` mechanical fields
  (StarHardware, 32 traced). The induction already clusters by page — it needs to key on the page.
- **Deployed link.** The server is read-only — JSONL plus page rendering, no key, no network — so what
  remains is baking a few books' products and pre-rendered pages into an image behind `FRESCO_DATA_ROOT`.
- **Feedback UI.** The viewer reads and accepts uploads; writing a correction back is not built.
- **Letter-spaced text.** One book prints rows with widened character spacing, so words arrive split
  into fragments the assembler only partly rejoins (SJC Well Behavioral); the fix is stitching
  characters by gap width in step 1.
- **Vocabulary hygiene.** Induced finish vocabularies still admit division numbers (`08`/`13`/`24`/`28`);
  delivery is covered by the column rejection above, but induction should drop them by coordinate.
- **Cosmetic leaks.** `---` placeholders stick to catalog numbers and notes, and `As Req.` in a
  quantity cell reaches the description instead of becoming `qty: null` plus a note. Separately, The
  Door Company prints 90 descriptions in the catalog column; that mismatch is recorded, no field moved.
