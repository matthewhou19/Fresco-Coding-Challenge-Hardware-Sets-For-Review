"""Layer-0 of the v0.2 funnel: PDF -> lines with coordinates.

Every text line keeps: page number, per-page line index (1-based, reading
order), text, and bbox in PDF points with top-left origin (x0, top, x1,
bottom).  Anchor ids look like "p396-L04" and are unique within one PDF.
Coordinates never leave this layer's records -- downstream steps (and the
LLM, later) refer to lines by anchor and trade the anchor back for the bbox.
"""
from __future__ import annotations

import logging

import pdfplumber

# pdfminer is chatty about CropBox defaults on these files
logging.getLogger("pdfminer").setLevel(logging.ERROR)


def anchor_id(page: int, line: int) -> str:
    return f"p{page}-L{line:02d}"


# --- strikethrough (revision markup) ---------------------------------------
# Three books in the corpus ship revision markup as struck-through text, and
# the text layer alone cannot tell a deleted row from a live one: SJC Well
# Behavioral ("DESIGN UPDATE") strikes whole superseded sets, Valor Acres
# Rev 2 strikes deleted component rows and individual door numbers inside a
# group header, HFH ("BULLETIN 023") strikes replaced catalog numbers inline.
# Without this the deleted rows are delivered as live components.
# A strikethrough is a hairline filled rect (or line) crossing the MIDDLE of
# the glyphs; an underline sits below them, and table rules sit outside the
# glyph box entirely -- so the band test separates the three.
BAR_MAX_HEIGHT = 1.6   # PDF points; hairline rules only
BAR_MIN_WIDTH = 4.0
BAND_LO, BAND_HI = 0.30, 0.70   # fraction of glyph height (top-left origin)
BAR_COVER = 0.5        # a bar must span half the glyph to strike it
STRUCK_MIN_RECORD = 0.05  # below this the line records nothing (default = live)


def thin_bars(page) -> list[tuple[float, float, float]]:
    """Hairline horizontal rules on a page as (x0, x1, y_center)."""
    bars = []
    for r in list(page.rects) + list(page.lines):
        if (abs(r["bottom"] - r["top"]) <= BAR_MAX_HEIGHT
                and (r["x1"] - r["x0"]) >= BAR_MIN_WIDTH):
            bars.append((r["x0"], r["x1"], (r["top"] + r["bottom"]) / 2))
    return bars


def struck_fraction(chars: list[dict], bars) -> float:
    """Share of a line's glyph width that a bar crosses through the middle."""
    total = hit = 0.0
    for c in chars:
        w = c["x1"] - c["x0"]
        if w <= 0:
            continue
        total += w
        h = c["bottom"] - c["top"]
        lo, hi = c["top"] + BAND_LO * h, c["top"] + BAND_HI * h
        for x0, x1, y in bars:
            over = min(x1, c["x1"]) - max(x0, c["x0"])
            if lo <= y <= hi and over > BAR_COVER * w:
                hit += w
                break
    return hit / total if total else 0.0


def extract_page_lines(page) -> dict:
    """One pdfplumber page -> {"page", "width", "height", "lines": [...]}."""
    raw = page.extract_text_lines(layout=False, strip=True, return_chars=True)
    bars = thin_bars(page)
    # geometry order (top-to-bottom, then left-to-right); pdfplumber is close
    # to this already but we sort so the anchor numbering is unambiguous
    raw.sort(key=lambda l: (round(l["top"], 1), round(l["x0"], 1)))
    lines = []
    n = 0
    for ln in raw:
        text = (ln.get("text") or "").strip()
        if not text:
            continue
        n += 1
        rec = {
            "anchor": anchor_id(page.page_number, n),
            "page": page.page_number,
            "line": n,
            "text": text,
            "bbox": [round(ln["x0"], 2), round(ln["top"], 2),
                     round(ln["x1"], 2), round(ln["bottom"], 2)],
        }
        # only written when the line actually carries markup, so books without
        # any revision strikethrough keep byte-identical streams
        if bars:
            frac = struck_fraction(ln.get("chars") or [], bars)
            if frac >= STRUCK_MIN_RECORD:
                rec["struck"] = round(frac, 2)
        lines.append(rec)
    return {
        "page": page.page_number,
        "width": round(float(page.width), 2),
        "height": round(float(page.height), 2),
        "lines": lines,
    }


def extract_lines(pdf_path: str, pages: list[int] | None = None) -> list[dict]:
    """Extract line records for the given 1-based page numbers (None = all).

    Returns a list of per-page dicts (see extract_page_lines). Deterministic:
    same file + same pages -> byte-identical JSON.
    """
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        wanted = sorted(set(pages)) if pages else range(1, len(pdf.pages) + 1)
        for pno in wanted:
            page = pdf.pages[pno - 1]
            out.append(extract_page_lines(page))
            page.flush_cache()  # keep memory flat on multi-hundred-page files
    return out
