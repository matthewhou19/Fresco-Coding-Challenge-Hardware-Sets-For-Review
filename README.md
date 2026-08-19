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
book's legend plus the column layout induced from that same book, sets confidence rather than
membership; semantics only splits fields within a column.

Across 20 specbooks (42 PDFs, 16,177 pages) it extracts **1,295 hardware sets and 11,358
components**; 23 of those sets are NOT USED headers that legitimately carry none. One book has no
schedule at all — its Division 08 section makes the schedule a contractor shop-drawing submittal — so
it returns an empty result plus an alarm naming every rejected region. That is the right answer.

How I check correctness: the same input run twice reproduces all 2,249 products byte for byte, and
747 assertions across seven `*_checks.py` suites hold the output against facts I confirmed in
the PDFs. The largest suite:

```bash
python pipeline/step3c_checks.py data/out/step3
```

## Running it

Python 3.13 — what this was built and verified on; the pinned dependencies declare 3.10 as their floor.

```bash
pip install -r requirements.txt
cp .env.example .env            # then put your ANTHROPIC_API_KEY in it
python server/app.py
```

Open <http://127.0.0.1:8000> and press **Upload PDF**. That is the whole flow: neither `pdfs/` nor
`data/` is in git, so a fresh clone starts with an empty list and the upload is how a book gets in. It
runs all six steps, one stage at a time with the log on screen, and the book lands in the list when it
finishes — or it stops at step 1 and reports the alarm, if the book has no schedule to find. Then pick
a set to see it framed on the rendered page, click a component row to highlight the line it came from,
**Export JSON** for the delivered record, **Delete this upload** to remove the book whole — pdf, output
and model cache.

That key is what the sixth step needs: assembly calls the Anthropic API (`claude-opus-5`), one model
call per set block — `ANTHROPIC_API_KEY` in the environment works in place of the file. The five steps
before it need no key and take about 3 s on a 344-page manual (Forest Park School: 1 call, 2,592 in /
682 out tokens).

`FRESCO_DATA_ROOT` points the viewer at another data directory — that is the deploy knob;
`FRESCO_PAGE_CACHE` moves the page-image cache; `HOST` and `PORT` set the bind address.

Deployed: _(link to be added)_

## What's next

I used every book in that folder, so I have no held-out number: every specbook here informed the
rules, and a number measured on them would report fit rather than generalization. What I do have is
what each new book cost me — one dialect rule each time, never an architecture change.

Three things next.

- **The variance wall is set headers.** Every new book still costs a rule for how its set headers
  look. Finding the set blocks is the one place I would put a model.
- **One reader handles every book today.** It should sort the layout into classes first, then hand
  each class to a reader built for it — tabular schedules first, the format this handles worst.
- **Columns still bleed.** The zones are a patch on a line-based model. The real fix is to address the
  page by cell, not by line (StarHardware: 32 components ship with `null` mechanical fields).

Two walls at the start: location and variance. Location is solved, structurally. Variance I have
handled one dialect at a time — that is where the model goes next.
