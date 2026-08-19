"""Step 3a of the v0.2 funnel: per-book dossier (decision #3, option C).

  step-2 block indexes (data/out/step2/<project>/*.blocks.jsonl)
     |  legend harvest: block preamble + a page window just before each
     |  region of the source PDF -> mfr / finish / option code tables
     |  slot induction: clean component-candidate lines -> trailing token
     |  slots -> per-slot column identity (closed set x distribution)
     v
  dossier.json per project: what step 3b (rule-side extraction) and step 3c
  (LLM block assembly) attach to every block of that book.

Design facts this encodes (hand-verified on the sample corpus, 2026-08-18):
* Column ORDER is never assumed (a new project may swap columns). Trailing
  slots are only the coordinate system statistics are computed over; each
  slot's identity (mfr / finish / neither) is induced per book from the
  legend closed set and the slot's own token distribution. "mfr sits in
  slot -1" is a per-book RESULT, not a rule.
* Legends live in three places: inside the step-2 preamble (National,
  Lyons), on pages just before the region (Morris p230-232, Bridgeport p2),
  or nowhere at all (Livelle) -- then the dossier is distribution-only.
* Section routing, not row shape, decides what a code table is about:
  Morris p231's option codes and p232's finish codes parse identically;
  only the "Hardware Options/Codes:" / "Hardware Finish List" headers
  above them tell them apart.
* Induction sampling is conservative on purpose: door lines, dimension
  rows, door-number grids, bare counts, and the Lyons "691 LCN" dropped
  finish+mfr rows (qty look-alikes) are excluded; private-use icon chars
  (U+E000-F8FF, embedded in Vantage/Market View component lines) are
  stripped before tokenizing or the tallies skew.
* Unparsed rows inside a legend section are kept as suspect_rows, and a
  book with no legend found says so loudly -- same mark-don't-delete /
  miss-must-leave-a-trace contract as steps 1/1.5/2.
* Two independent nets keep prose out of the code tables (2026-08-18); each
  catches what the other cannot, and each leaves its rejects in a trace bin:
  - the ROLE VIEW. The pre-region window is raw page text, so its running
    header/footer are still there while every other consumer in the funnel
    reads step 1.5's content view. StarHardware's header "OCI Design IFC
    12.12.2025 The Commons Lane" therefore parsed as two manufacturer codes
    on every page of an open section, and Gerrard's "2535 Gerrard Shelter
    08 71 00 - Door Hardware" as a third. -> legend.furniture_rows
  - the TABLE SHAPE. Furniture repeats and is adjacent, so repetition alone
    cannot see one-off prose that happens to parse: "PART 3 EXECUTION",
    "NFPA 80.", "ADA - Americans with Disabilities Act...", "4. 90 min.
    fire rating.". Those sit alone, far from the real table (MAX_ROW_GAP).
    -> legend.isolated_rows
  Together they take 20 rows out of 3 books' mfr tables (13 distinct
  codes -- Gerrard's 7 sit in both of its bindings). None of the 20 was
  ever assigned to a component, so this is precision in the book context,
  not a change to delivered fields.
  Gerrard's mfr table ends up empty, and that is honest but not complete:
  its REAL "C. Manufacturer List" (p164 L37-L39) holds only 3 codes --
  CMND / DE / HA -- so MIN_TABLE_CODES rejects it, before this change as
  much as after. 772 of its components still get HA or CMND from the slot
  distribution; the three rows stay in suspect_rows. Lowering the floor to
  3 is a corpus-wide change (the floor is what keeps Livelle's repeating
  page footer out) and wants its own probe.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pdflines import extract_lines  # noqa: E402
from step1p5_roles import assign_role, furniture_groups  # noqa: E402

# --- line hygiene -----------------------------------------------------------

RE_PUA = re.compile("[\ue000-\uf8ff]")
RE_PART_PREFIX = re.compile(r"^PART\s+\d{1,3}\s*[-–—]\s*", re.I)


def clean(text: str) -> str:
    """Strip private-use icon glyphs and collapse whitespace."""
    return " ".join(RE_PUA.sub(" ", text).split())


# --- component-candidate filter (induction sampling) ------------------------

RE_LABEL = re.compile(
    r"^(OPERATION\s*:|Description\s*:|Opening Description\s*:|Properties\s*:"
    r"|Notes?\s*:|For use on Door|Doors?\s*:|Provide each\b.*:$)", re.I)
# same Operator lookahead as step 3b: "1 Single Operator" is a component
RE_DOOR_ITEM = re.compile(
    r"^(Item\s*#\d+\b|\d+\s+(Pair|Single)\b(?!\s+Operator\b))", re.I)
RE_DIM = re.compile(r"^\d{3,4}(,\s*\d{3,4})*\s*x\s*\d{3,4}\b")
# decimal dialect "3.0 Hinge ..." (Forest Park, 2026-08-18); the .0/.5 gate
# keeps spec article numbers ("1.1 SUMMARY") out of the candidate pool
RE_QTY_LED = re.compile(r"^(\d+(?:\.[05])?)\s+(.*)$")
UNIT_TOKENS = {"EA", "SET", "SETS", "PR"}
RE_ALPHA_WORD = re.compile(r"^[A-Za-z][A-Za-z/&.,'-]{2,}$")


def component_candidate(text: str) -> list[str] | None:
    """Tokens after qty/unit if the line looks like a clean component row.

    Conservative: this feeds column induction, so door grids ("2 5 25 33"),
    dimension rows, bare counts, and dropped finish+mfr fragments
    ("691 LCN") must stay out; a few missed real rows cost nothing.
    """
    t = RE_PART_PREFIX.sub("", clean(text))
    if RE_LABEL.match(t) or RE_DOOR_ITEM.match(t) or RE_DIM.match(t):
        return None
    m = RE_QTY_LED.match(t)
    if not m:
        return None
    toks = m.group(2).split()
    if toks and toks[0].upper() in UNIT_TOKENS:
        toks = toks[1:]
    # first word after qty/unit must read like a description word
    if len(toks) < 2 or not RE_ALPHA_WORD.match(toks[0]):
        return None
    return toks


# --- slot induction ---------------------------------------------------------

# short alpha(+digit) maker codes: IVE, SCH, B/O, MED1, VA01 ...
RE_MFR_SHAPE = re.compile(r"^(?:[A-Z]{2,4}\d{0,2}|B/O)$")
# digit-bearing finish codes: 626, 630, 10BE, US26D, C32D, 626/626, 630- ...
RE_FINISH_SHAPE = re.compile(
    r"^(?:US)?\d{1,4}[A-Z]{0,3}-?(?:/(?:US)?\d{1,4}[A-Z]{0,3})?$"
    r"|^C\d{2}[A-Z]?$")
# alpha-only finishes seen in schedules (GRY grey, BLK black, BSP is the
# SPEC's own example ...); legend mfr codes override this list per book
FINISH_WORDS = {"A", "AA", "BK", "BLK", "BSP", "CA", "EB", "EN", "GRY",
                "PT", "STST", "US"}

CONF_HIGH_LEGEND = 0.80
CONF_FLOOR = 0.50
CONF_HIGH_SHAPE = 0.70
CONF_HIGH_MIN_N = 50


def induce_slot(counter: Counter, n: int, legend_mfr: set[str],
                legend_finish: set[str]) -> dict:
    """Score one trailing slot's distribution -> column identity."""
    def share(pred) -> float:
        return round(sum(c for t, c in counter.items() if pred(t)) / n, 3) if n else 0.0

    s_leg_mfr = share(lambda t: t in legend_mfr)
    s_leg_fin = share(lambda t: t in legend_finish)
    s_shape_mfr = share(lambda t: t not in legend_finish
                        and RE_MFR_SHAPE.match(t) is not None)
    s_shape_fin = share(lambda t: t not in legend_mfr
                        and (RE_FINISH_SHAPE.match(t) is not None
                             or t in FINISH_WORDS))

    identity, conf = "unclear", "low"
    if legend_mfr and s_leg_mfr >= CONF_FLOOR:
        identity = "mfr"
        conf = "high" if s_leg_mfr >= CONF_HIGH_LEGEND else "medium"
    elif legend_finish and s_leg_fin >= CONF_FLOOR:
        identity = "finish"
        conf = "high" if s_leg_fin >= CONF_HIGH_LEGEND else "medium"
    elif s_shape_mfr >= CONF_FLOOR and s_shape_mfr > s_shape_fin:
        identity = "mfr"
        conf = ("high" if s_shape_mfr >= CONF_HIGH_SHAPE and n >= CONF_HIGH_MIN_N
                else "medium")
    elif s_shape_fin >= CONF_FLOOR and s_shape_fin > s_shape_mfr:
        identity = "finish"
        conf = ("high" if s_shape_fin >= CONF_HIGH_SHAPE and n >= CONF_HIGH_MIN_N
                else "medium")
    return {
        "n": n,
        "top": [[t, c] for t, c in counter.most_common(10)],
        "legend_mfr_share": s_leg_mfr,
        "legend_finish_share": s_leg_fin,
        "shape_mfr_share": s_shape_mfr,
        "shape_finish_share": s_shape_fin,
        "identity": identity,
        "confidence": conf,
    }


def induce_stream(component_rows: list[list[str]], legend_mfr: set[str],
                  legend_finish: set[str]) -> dict:
    slots = {}
    for slot, min_len in (("-1", 2), ("-2", 3)):
        cnt = Counter(toks[int(slot)] for toks in component_rows
                      if len(toks) >= min_len)
        slots[slot] = induce_slot(cnt, sum(cnt.values()),
                                  legend_mfr, legend_finish)
    schema = {"mfr_slot": None, "finish_slot": None}
    for slot, info in slots.items():
        key = f"{info['identity']}_slot"
        if key in schema and schema[key] is None:
            schema[key] = int(slot)
    return {"slots": slots, "column_schema": schema,
            "n_component_candidates": len(component_rows)}


# --- legend harvest ---------------------------------------------------------

LEGEND_WINDOW = 6  # pages scanned before each region (step 1's ledger slack)

# section keywords as whole words: "Finishes" opens a table, Bridgeport's
# cover title "Tender FinishING Hardware Schedule" must not
RE_KW_MFR = re.compile(r"\b(abbreviations?|manufacturer(?:'s)?s?)\b", re.I)
RE_KW_FINISH = re.compile(r"\bfinish(?:es)?\b", re.I)
RE_KW_OPTION = re.compile(r"\boptions?\b", re.I)
RE_KW_LEGEND = re.compile(r"\blegend\b", re.I)
# numbered spec articles ("3.5 FINISHES", "3) Manufacturers:", "PART 2 -")
# are prose, never legend-table headers (Morris's real ones are lettered)
RE_ARTICLE = re.compile(r"^(\d+[.)]|PART\s+\d)", re.I)
# "1. AD Adams Rite" / "B/O By Others" / "26D - Satin chromium..." / "10BE ..."
# hyphenated codes ("C-R Corbin Russwin", Forest Park 2026-08-18) are codes,
# not name-only rows; the dash counts as a code char only when another
# alnum follows, so "26D - Satin ..." keeps reading as code + separator
RE_CODE_ROW = re.compile(
    r"^(?:\d{1,2}\.\s+)?([A-Z0-9](?:[A-Z0-9/]|-(?=[A-Z0-9])){1,7})"
    r"\s*[-–—]?\s+(\S.*)$")
RE_PGS_SUFFIX = re.compile(r"\s*\(Pgs?\s+[\d\s-]+\)$")
MAX_HDR_LEN = 45
MAX_HDR_WORDS = 6
MAX_ROW_LEN = 90
MAX_CAPS_NAME = 12  # all-caps legend names this short still count ("HS HES")
MIN_TABLE_CODES = 4  # a section run commits only as a table-shaped run
# ... and its rows must sit next to each other. A legend is a TABLE; a lone
# code-shaped line 20 lines away from the table is prose that happens to
# parse ("PART 3 EXECUTION", "NFPA 80.", "4. 90 min. fire rating.").
# Probe of every committed legend row in the 20-book corpus (195 rows):
# real rows measure a nearest-neighbour distance of 1 (166), 2 (14), 3 (1)
# and 4 (Morris's MS&ES25 option row) -- every one of the 12 rows measuring
# >= 11 is fake. Any threshold in 5..10 gives byte-identical output on this
# corpus; 8 is twice the observed real maximum. The reject we buy: a real
# legend table split across a page break with more than 8 lines of prose
# between its halves would lose the smaller half (not present in the corpus
# -- no committed run spans a page break with a gap above 4).
MAX_ROW_GAP = 8


def route_section(t: str, current: str | None) -> str | None:
    """Active legend section after seeing line `t` (already cleaned)."""
    words = t.split()
    if (len(t) > MAX_HDR_LEN or len(words) > MAX_HDR_WORDS
            or RE_ARTICLE.match(t) or not t[:1].isupper()
            or t.endswith(".")                    # sentence, not a header
            or (len(words) == 1 and t.isupper())):  # bare article word
        return current
    if re.search(r"\bhardware\s+(?:sets?|groups?)\b\s*:?\s*$", t, re.I):
        return None            # the schedule itself begins: legend zone over
    has_mfr, has_fin = RE_KW_MFR.search(t), RE_KW_FINISH.search(t)
    if has_mfr and has_fin:
        return None            # "Manufacturers & Finishes" page title
    if RE_KW_OPTION.search(t):
        return "option"
    if has_mfr:
        return "mfr"
    if has_fin:
        return "finish"
    if RE_KW_LEGEND.search(t):
        return "icon"
    return current


def parse_code_row(text: str) -> tuple[str, str] | None:
    m = RE_CODE_ROW.match(RE_PGS_SUFFIX.sub("", text))
    if not m or len(text) > MAX_ROW_LEN:
        return None
    code, name = m.group(1), m.group(2).strip()
    if "-" in code and code.replace("-", "").isdigit():
        return None  # "81-85 Bridgeport": a number range/address, not a code
    if re.search(r"[a-z]", name) or len(name) <= MAX_CAPS_NAME:
        return code, name
    return None


def table_shaped(run_rows: dict[str, tuple[int, str]]):
    """Split a run's rows into (neighbours, isolated) by MAX_ROW_GAP."""
    positions = sorted(pos for pos, _name in run_rows.values())
    keep: dict[str, tuple[int, str]] = {}
    isolated: list[tuple[str, str]] = []
    for code, (pos, name) in run_rows.items():
        others = [p for p in positions if p != pos]
        gap = min(abs(p - pos) for p in others) if others else None
        if gap is not None and gap <= MAX_ROW_GAP:
            keep[code] = (pos, name)
        else:
            isolated.append((code, name))
    return keep, isolated


def harvest_lines(lines: list[dict], legend: dict, where: str,
                  is_content=None) -> None:
    """One contiguous scan (a window's pages, or one preamble) through the
    section router. Section state carries across page breaks (Morris's
    finish header sits at the foot of p231, its rows on p232) but a run
    only commits if it is table-shaped: >= MIN_TABLE_CODES distinct codes,
    each with a neighbouring row within MAX_ROW_GAP lines.
    Prose sub-headers ("A. Finish: BHMA 626...") do open sections, but the
    stray rows they catch never reach that floor -- the whole run degrades
    to suspect_rows (marked, not deleted). Livelle's page footer
    "PE Project 96050.00 BID SET..." is the reason the floor counts
    DISTINCT codes: it repeats on every page and would pass a raw count.

    `is_content(line) -> bool` is step 1.5's role view (None = every line
    counts, which is what preambles want: step 2 already handed us the
    content view there). A line it rejects may still steer the section
    router -- furniture sits between a header and its rows without ending
    the run, and filtering the router's input instead would silently merge
    runs and promote scattered prose out of suspect_rows into accepted
    names (Lyons: 5 -> 48, measured). It just may not become DATA."""
    section: str | None = None
    run_rows: dict[str, tuple[int, str]] = {}
    run_names: list[str] = []

    def flush() -> None:
        nonlocal run_rows, run_names
        rows, isolated = table_shaped(run_rows)
        if section in ("mfr", "finish", "option") and len(rows) >= MIN_TABLE_CODES:
            for code, (_pos, name) in rows.items():
                legend[section].setdefault(code, name)
            for code, name in isolated:
                legend["isolated_rows"].append(
                    {"where": where, "section": section,
                     "text": f"{code} {name}"[:80]})
        else:
            for code, (_pos, name) in run_rows.items():
                legend["suspect_rows"].append(
                    {"where": where, "run_below_floor": section,
                     "text": f"{code} {name}"[:80]})
        if section == "mfr" and len(set(run_names)) >= MIN_TABLE_CODES:
            legend["mfr_names_without_codes"].extend(run_names)
        else:
            for n in run_names:
                legend["suspect_rows"].append(
                    {"where": where, "run_below_floor": section, "text": n[:80]})
        run_rows, run_names = {}, []

    for pos, r in enumerate(lines):
        text = clean(r["text"])
        if not text:
            continue
        row = parse_code_row(text)
        if row is not None and text.rstrip().endswith(":") \
                and len(text.split()) <= MAX_HDR_WORDS:
            # "FINISH LIST:" / "OPTION LIST:" parse as code rows (short first
            # word + colon tail), shadowing the router -- Forest Park's
            # legends sit exactly under such headers (2026-08-18).  A short
            # colon-ended line is a section header first, data second.
            if route_section(text, section) != section:
                row = None
        if row is None:  # a code row is data even when it names a section
            new_section = route_section(text, section)
            if new_section != section:
                flush()
                section = new_section
                legend["source_hits"].append(
                    {"where": where, "anchor": r.get("anchor"),
                     "section": section, "text": text[:60]})
                continue
        if section in (None, "icon"):
            continue
        if re.search(r"quantit", text, re.I):
            continue  # qty notes are collected separately
        if is_content is not None and not is_content(r):
            legend["furniture_rows"].append(
                {"where": where, "anchor": r.get("anchor"),
                 "section": section, "text": text[:80]})
            continue
        if row is not None:
            run_rows.setdefault(row[0], (pos, row[1]))
        elif section == "mfr" and re.match(r"^[A-Z<]", text) \
                and len(text) <= MAX_ROW_LEN:
            # Bridgeport style: maker NAMES with no short codes at all
            run_names.append(RE_PGS_SUFFIX.sub("", text))
        else:
            legend["suspect_rows"].append({"where": where, "text": text[:80]})
    flush()


def harvest_pdf_legend(pdf_path: str, regions: list[list[int]],
                       preambles: list[list[dict]]) -> tuple[dict, list[dict]]:
    legend = {"mfr": {}, "finish": {}, "option": {},
              "mfr_names_without_codes": [], "suspect_rows": [],
              "source_hits": [], "scan_windows": [],
              "furniture_rows": [], "isolated_rows": []}
    qty_notes: list[dict] = []

    # merge overlapping per-region windows; section state must NOT leak
    # across disjoint windows (Morris: p232's finish table into p277 prose)
    windows: list[list[int]] = []
    for start, _end in sorted(regions):
        lo = max(1, start - LEGEND_WINDOW)
        if windows and lo <= windows[-1][1] + 1:
            windows[-1][1] = max(windows[-1][1], start - 1)
        elif start - 1 >= lo:
            windows.append([lo, start - 1])
    legend["scan_windows"] = windows
    for lo, hi in windows:
        lines: list[dict] = []
        heights: dict[int, float] = {}
        for pg in extract_lines(pdf_path, list(range(lo, hi + 1))):
            heights[pg["page"]] = pg["height"]
            lines.extend(pg["lines"])
        # the window is raw page text, so it still carries the running header
        # and footer that step 1.5 strips everywhere else; run the same
        # furniture detector over it (it takes any line set) and let its
        # verdict decide what may become legend data.
        furn, _f, _n = furniture_groups(lines, heights)
        harvest_lines(lines, legend, f"window p{lo}-{hi}",
                      lambda r, f=furn: assign_role(r["text"], f) == "content")
        for r in lines:
            if re.search(r"quantit(y|ies)", r["text"], re.I):
                qty_notes.append({"where": r["anchor"], "text": clean(r["text"])})
    for pre in preambles:
        harvest_lines(pre, legend, "preamble")  # already the content view
        for r in pre:
            if re.search(r"quantit(y|ies)", r["text"], re.I):
                qty_notes.append({"where": r["anchor"], "text": clean(r["text"])})
    # same contract as the struck fraction in pdflines: a trace bin is only
    # written when it caught something, so books with clean windows keep
    # byte-identical dossiers
    for key in ("furniture_rows", "isolated_rows"):
        if not legend[key]:
            del legend[key]
    return legend, qty_notes


# --- per-project run --------------------------------------------------------

def load_stream_blocks(blocks_path: Path) -> tuple[dict, list[dict], list[dict]]:
    recs = [json.loads(l) for l in blocks_path.read_text("utf-8").splitlines()]
    meta, blocks = recs[0], recs[1:]
    stream = [json.loads(l) for l in
              Path(meta["source_stream"]).read_text("utf-8").splitlines()][1:]
    content = [r for r in stream if r["role"] == "content"]
    return meta, blocks, content


def split_blocks(blocks: list[dict], content: list[dict]):
    """Content view -> (preamble lines, in-block non-header lines)."""
    owned, headers = set(), set()
    first = None
    for b in blocks:
        headers.add(b["header"]["anchor"])
        for s in b["spans"]:
            for line in range(s["lines"][0], s["lines"][1] + 1):
                owned.add((s["page"], line))
                if first is None or (s["page"], line) < first:
                    first = (s["page"], line)
    preamble = [r for r in content
                if first is None or (r["page"], r["line"]) < first]
    in_block = [r for r in content if (r["page"], r["line"]) in owned
                and r["anchor"] not in headers]
    return preamble, in_block


def process_project(proj_dir: Path, out_root: Path) -> None:
    out_dir = out_root / proj_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"project: {proj_dir.name}")

    per_pdf: dict[str, dict] = defaultdict(
        lambda: {"regions": [], "preambles": [], "streams": []})
    stream_rows: dict[str, list[list[str]]] = {}

    for blocks_path in sorted(proj_dir.glob("*.blocks.jsonl")):
        meta, blocks, content = load_stream_blocks(blocks_path)
        preamble, in_block = split_blocks(blocks, content)
        rows = [toks for r in in_block
                if (toks := component_candidate(r["text"]))]
        name = blocks_path.stem.replace(".blocks", "")
        stream_rows[name] = rows
        agg = per_pdf[meta["source_pdf"]]
        agg["regions"].append(meta["region"])
        agg["preambles"].append(preamble)
        agg["streams"].append(name)

    pdfs_out, streams_out = {}, {}
    for pdf_path, agg in sorted(per_pdf.items()):
        legend, qty_notes = harvest_pdf_legend(
            pdf_path, agg["regions"], agg["preambles"])
        legend_missing = not legend["mfr"] and not legend["finish"]
        pdfs_out[pdf_path] = {"legend": legend, "qty_notes": qty_notes,
                              "legend_missing": legend_missing}
        print(f"  pdf: {Path(pdf_path).name}")
        print(f"    legend: mfr {len(legend['mfr'])} codes, "
              f"finish {len(legend['finish'])}, option {len(legend['option'])}, "
              f"name-only mfrs {len(legend['mfr_names_without_codes'])}, "
              f"suspect rows {len(legend['suspect_rows'])}"
              + ("  [NO LEGEND FOUND -> distribution-only dossier]"
                 if legend_missing else ""))
        if qty_notes:
            print(f"    qty notes: {[q['text'][:60] for q in qty_notes]}")

        legend_mfr = set(legend["mfr"])
        legend_finish = set(legend["finish"])
        for name in agg["streams"]:
            ind = induce_stream(stream_rows[name], legend_mfr, legend_finish)
            ind["source_pdf"] = pdf_path
            streams_out[name] = ind
            s = ind["slots"]
            print(f"    {name}")
            print(f"      candidates {ind['n_component_candidates']}  "
                  f"schema {ind['column_schema']}")
            for slot in ("-1", "-2"):
                i = s[slot]
                top = " ".join(f"{t}:{c}" for t, c in i["top"][:6])
                print(f"      slot {slot}: {i['identity']:7s} ({i['confidence']}) "
                      f"n={i['n']}  [{top}]")
            if ind["column_schema"]["mfr_slot"] is None:
                print("      note: no mfr column induced "
                      "(book-level fact or low-signal stream)")

    (out_dir / "dossier.json").write_text(
        json.dumps({"project": proj_dir.name,
                    "generated_by": "step3_dossier v0.1 (3a)",
                    "legend_window_pages": LEGEND_WINDOW,
                    "pdfs": pdfs_out,
                    "streams": streams_out},
                   ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("in_root", nargs="?", default="data/out/step2",
                    help="step-2 output root (default: data/out/step2)")
    ap.add_argument("--out", default="data/out/step3",
                    help="output root (default: data/out/step3)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    in_root, out_root = Path(args.in_root), Path(args.out)
    projects = sorted(p for p in in_root.iterdir()
                      if p.is_dir() and (p / "chunks_report.json").exists())
    if not projects:
        print(f"no step-2 projects under {in_root}", file=sys.stderr)
        return 2
    for proj in projects:
        process_project(proj, out_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
