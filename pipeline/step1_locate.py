"""Step 1 of the v0.2 funnel: locate hardware-set regions, emit the line stream.

  project folder (several PDFs, distractors included)
     |  quick text scan of EVERY page (pypdfium2, fast)
     |  -> per-page signal scores -> strong/weak pages -> contiguous regions
     v
  region report (which file, which pages, why)  +  line stream with bbox
  (pdfplumber, region pages only) = the cross-page line flow that feeds
  step 1.5 (line roles) and step 2 (set chunking).

Design decisions (evidence: probe of Bridgeport + Vantage, 2026-08-17):
* Scan all PDFs; filename hints ("08 7x") are recorded but decide nothing.
* A page is STRONG when component-row density backs a set signal:
    - column-header line (>=3 of QTY/DESCRIPTION/CATALOG/FINISH/MFR/...) plus
      >=2 qty rows, or
    - a NUMBERED set header (HARDWARE GROUP NO. 103 / Heading #17 / HW SET #1)
      plus qty rows or finish codes, or
    - qty-row density alone (unit rows >=5, or bare rows >=5 with finishes).
  Narrative spec pages mention "hardware sets" in prose but never carry
  qty-row density -- that pairing is what separates them.
* A page is WEAK when it only has a numbered set header (a ghost set like
  "HARDWARE GROUP NO. 002 DO NOT APPLY DOOR NUMBERS TO SETS", or an
  items-only continuation page). Weak pages join an adjacent strong region
  but never form one alone.
* Gaps of <=2 pages inside a region are bridged (door-list-only pages).
* Region-level floor (v0.2, evidence: 5-project round 2026-08-17): a region
  must contain at least one NUMBERED set header, or >=3 column-header lines,
  summed over its pages. Product cut-sheet appendices (kitchen-equipment
  brochures: model suffixes like "-48D" collide with finish codes, spec
  tables fake qty rows) can trip the page rules on 1-2 page islands, but
  never carry set headers -- every true region so far has 12+. Regions that
  fail the floor are kept in the report under "rejected_regions" (marked,
  not deleted), and no stream is emitted for them.
* LLM page-flipping stays a fallback for books where these signals miss;
  none needed so far.

Miss-detection (the thresholds are constants calibrated on 7 books -- the
contract is not "they are right", it is "when they are wrong, it is loud"):
* rejected_regions: density fired but no set header recognized -> visible.
* image_only_pages: scanned book -> visible.
* suspect_pages: a WIDER line-anchored sweep (SET #/Set:/HARDWARE GROUP
  NO./Heading #...) over every page OUTSIDE accepted regions; hits are
  listed for eyeballing -- catches a second schedule in an alien dialect.
* project-level alarm: every challenge project is expected to contain
  hardware sets, so zero accepted regions across a whole project is itself
  an anomaly -> "alarm" in the report -> escalate to LLM fallback.
* title reconciliation: the strongest recall net. Spec writers put a
  "HARDWARE SETS" / "Hardware Sets:" / "DOOR HARDWARE SETS" heading above
  every set list (7/7 books so far) -- section-template language that is
  INDEPENDENT of how the sets themselves are formatted. Every such
  line-anchored title must have an accepted region within 6 pages after it (legend/preamble pages legally sit between the heading and the first set -- Morris keeps its manufacturer/finish legend there),
  or an explicit "Refer to ..." stub in the lines below (Livelle 08 17 00
  p541: "Refer to Door Hardware Section Schedule of Hardware for Sets").
  Anything else -> "unreconciled_set_titles" -> a human looks at ONE page,
  not hundreds. A book where set rules AND this template net both go blind
  is the residual risk the GT-sampled recall measurement (stage 4) covers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import pypdfium2 as pdfium

sys.path.insert(0, str(Path(__file__).parent))
from pdflines import extract_lines  # noqa: E402

# --- signals ---------------------------------------------------------------

QTY_UNITS = r"(?:EA|EACH|PRS?|PAIRS?|SETS?|LF|FT)"
RE_QTY_UNIT = re.compile(rf"^\s*\d{{1,3}}\s+{QTY_UNITS}\b", re.I)
RE_QTY_BARE = re.compile(r"^\s*\d{1,3}\s+[A-Za-z(]")
# set ids: 1, 3A, 15, 103A, C200C, 002 ... plus alpha-only ids like MISC
# (Morris p263 "Set #MISC", caught by the suspect sweep 2026-08-17). Alpha-only
# ids REQUIRE an explicit #/NO. separator so prose ("...hardware set number...")
# stays out.
# A letter prefix may be hyphen-joined to the number ("Set: EX-1.0", the four
# JC Ryan exterior sets on p24-27, 2026-08-18): without the hyphen those pages
# score set_hdr=0 and drop out of the region.  Corpus probe over ~9,500 pages
# flips exactly those pages plus Gerrard's "Set #U-01"/"#U-02" (already inside
# their region -- the count gets truer, the boundaries do not move).
SET_ID = r"[A-Z]{0,3}-?\d{1,4}[A-Z]{0,3}"
SET_KEYWORD = r"(?:(?:HW|HDW|HARDWARE)\s+(?:SETS?|GROUPS?)|HEADING|SET)"
# HEADING needs an explicit separator, unlike the other keywords.  Step 2's
# heading family has always required one ("Heading #1", Bridgeport's 90 sets);
# step 1 also accepted the spelled-out "Heading Number 1", and the only line in
# the 20-book corpus written that way is StarHardware p31 -- a *sample* of what
# a submittal should look like, printed inside the submittal requirements, with
# blank device numbers.  It made p31 a one-page phantom region that step 2 then
# could not cut a single block from.  Counting a header form step 2 cannot cut
# on is what creates phantom regions, so the two grammars are aligned here.
SET_KEYWORD_LOOSE = r"(?:(?:HW|HDW|HARDWARE)\s+(?:SETS?|GROUPS?)|SET)"
# "HW 01" / "HW G01" (SJC Well Behavioral p711-748, 2026-08-18): the keyword is
# the bare abbreviation -- no SET/GROUP word, no separator after it -- so 38
# pages of real schedule scored set_hdr=0 and were rejected wholesale.  This
# alternative is LINE-ANCHORED (re.M) because "hw" is two letters and would
# otherwise pick up mid-sentence noise; corpus probe over 42 PDFs / ~16,000
# pages flips exactly those 38 SJC pages and nothing else anywhere.
RE_SET_HDR = re.compile(
    rf"\b{SET_KEYWORD_LOOSE}\s*(?:NO\.?|NUMBER|#)?\s*[:#]?\s*{SET_ID}\b"
    rf"|\bHEADING\s*(?:NO\.?)?\s*[:#]\s*{SET_ID}\b"
    rf"|\b{SET_KEYWORD}\s*(?:NO\.?|#)\s*[A-Z]{{2,10}}\b"
    rf"|^[ \t]*(?:HW|HDW)\s*[-#:]?\s*{SET_ID}\b",
    re.I | re.M,
)
RE_FINISH = re.compile(r"\b(?:US\d{1,2}[A-Z]?|6\d{2}|C\d{2}D|\d{2}D)\b")
# wide-table dialect (Roselle, 2026-08-18): qty+finish sit MID-row -- "... 3 613
# (OIL RUBBED BRONZE) ..." / "-- BLACK" -- so the line-anchored qty signals never
# fire.  Count rows where a qty (or the "--" n/a marker) is immediately followed
# by a finish-shaped token; paired with a column-header line this separates a
# wide schedule page from prose (corpus probe 2026-08-18: flips exactly Roselle
# p15-17 across all 8 projects, wide_qty 52/66/58 vs threshold 3).
RE_WIDE_QTY = re.compile(
    r"(?:^|\s)(?:\d{1,3}|--)\s+(?:6\d{2}\b|US\d{1,2}[A-Z]?\b|BLACK\b)")
RE_SEC_08 = re.compile(r"\b08\s*[- ]?\s*7\d\s*[- ]?\s*\d{2}\b|\b087\d{3}\b")
COL_WORDS = {"QTY", "QUANTITY", "DESCRIPTION", "CATALOG", "MODEL", "PRODUCT",
             "FINISH", "MFR", "MANUFACTURER", "NOTES"}
RE_WORD = re.compile(r"[A-Z]+")
RE_FILENAME_HINT = re.compile(r"08[\s._-]?7\d|hardware", re.I)
# miss-detection net: wider than RE_SET_HDR but line-anchored with an explicit
# separator, so prose ("...the hardware sets indicated...") stays out
RE_SUSPECT = re.compile(
    r"^\s*(?:PART\s+\d{1,3}\s*[-–]\s*)?"
    r"(?:HARDWARE\s+(?:SETS?|GROUPS?)|(?:HW|HDW)\s+SETS?|HEADING|SET|GROUP)\s*"
    r"(?:NO\.?|#|:)\s*[A-Za-z0-9]",
    re.I,
)
# reconciliation net: boilerplate headings above set lists. Line- AND
# end-anchored so TOC rows and prose mentions stay out.
RE_SETS_TITLE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\s+|[A-Z]\.\s+|PART\s+\d+\s*[-–]\s*)?"
    r"(?:(?:DOOR\s+)?HARDWARE\s+(?:SETS?|GROUPS?|SCHEDULE)"
    r"|SCHEDULE\s+OF\s+(?:FINISH(?:ING)?\s+)?HARDWARE"
    r"|FINISH(?:ING)?\s+HARDWARE\s+SCHEDULE)"
    r"\s*:?\s*$",
    re.I,
)
RE_REFER = re.compile(r"\brefer\w*\s+to\b|\bsee\s+(?:section|division)\b", re.I)

# structural tail net (Forest Park p263, 2026-08-18): a region that stops
# short of END OF SECTION / the document's last page may have been cut by the
# strong-page thresholds -- a thin continuation page (225 chars, 3 rows)
# carries too few signals to qualify on its own.  Independent evidence that
# the schedule continues: spec-book footers repeat the CSI section number
# with a running page number ("Door Hardware 087100 - 16" -> "- 17" on the
# next page).  Three fences, each bought by a corpus counter-example:
# * only ACCEPTED regions are probed -- Market View keeps a rejected
#   qty-density island inside the painting section (099113); a non-schedule
#   tail deserves no rescue;
# * the footer page number must INCREMENT, not merely repeat the code --
#   Morris binds the 087100 section twice, so the code alone runs across
#   the restart ("087100 - 50" -> "087100 - 1");
# * the extension commits only when the probe actually reaches END OF
#   SECTION; anything else (footer break, cap, document end) reverts
#   without a trace -- Morris never prints an EOS at all.
# Thresholds stay untouched: they decide where a region STARTS, this net
# decides where it ENDS.
RE_TAIL_EOS = re.compile(r"\bEND\s+OF\s+SECTION\b", re.I)
RE_CSI_FOOT = re.compile(r"\b(\d{2}\s?\d{2}\s?\d{2})\s*[-–—]\s*(\d{1,3})\s*$")
TAIL_MAX_EXTEND = 15  # probe budget; EOS beyond it -> revert (no commit)

IMAGE_ONLY_CHARS = 30  # pages with fewer text chars count as image-only

# dense-rejection net thresholds (see the loop that fills
# dense_rejections): calibrated so SJC p711-748 fires and every other
# rejected island in the 20-book corpus stays quiet.
DENSE_REJECT_PAGES = 3
DENSE_REJECT_FINISH = 20
DENSE_REJECT_QTY = 50


def page_signals(text: str) -> dict:
    lines = [l for l in text.splitlines() if l.strip()]
    col_hdr = 0
    for l in lines:
        hits = {w for w in RE_WORD.findall(l.upper()) if w in COL_WORDS}
        if len(hits) >= 3:
            col_hdr += 1
    return {
        "set_hdr": len(RE_SET_HDR.findall(text)),
        "col_hdr": col_hdr,
        "qty_unit": sum(1 for l in lines if RE_QTY_UNIT.match(l)),
        "qty_bare": sum(1 for l in lines if RE_QTY_BARE.match(l)),
        "wide_qty": sum(1 for l in lines if RE_WIDE_QTY.search(l)),
        "finish": len(RE_FINISH.findall(text)),
        "sec_08_7x": len(RE_SEC_08.findall(text)),
        "chars": len(text),
    }


def classify_list(s: dict) -> str:
    """The list-dialect page rules (unchanged since v0.2), named so the
    wide-table rule can tell whether it alone carried a page."""
    qty = s["qty_unit"] + s["qty_bare"]
    if s["col_hdr"] >= 1 and qty >= 2:
        return "strong"
    if s["set_hdr"] >= 1 and (qty >= 3 or s["finish"] >= 3):
        return "strong"
    if s["qty_unit"] >= 5:
        return "strong"
    if s["qty_bare"] >= 5 and s["finish"] >= 5:
        return "strong"
    if s["set_hdr"] >= 1:
        return "weak"
    return "none"


def wide_table_strong(s: dict) -> bool:
    """Wide-table dialect rule: a column-header line backed by qty+finish
    adjacency density (RE_WIDE_QTY)."""
    return s["col_hdr"] >= 1 and s["wide_qty"] >= 3


def classify(s: dict) -> str:
    """Return 'strong' | 'weak' | 'none' for one page's signals."""
    c = classify_list(s)
    if c != "strong" and wide_table_strong(s):
        return "strong"
    return c


# --- region assembly -------------------------------------------------------

MAX_GAP = 2  # bridge holes of up to this many non-signal pages
# A page carrying nothing but running furniture (header/footer) is transparent
# to that budget.  HFH's schedule runs p20-183 with three such pages at
# p164-166; counting them as a hole split one schedule into two regions and
# stranded the rows of 'Hardware Group No. 197' (header on p163) in the second
# region's preamble.  Corpus probe (2026-08-18, ~9,500 pages): that hole is the
# only all-furniture hole of 3+ pages between strong pages in the whole corpus.
SPARSE_CHARS = 400  # furniture-only pages sit near 157 chars; real ones 1,300+


def sparse_page(text: str, s: dict) -> bool:
    """True for a page with no signals and almost no text: running furniture
    (HFH p164-166) or a set's stub tail line (JC Ryan p25 carries the last row
    of EX-2.0 and nothing else).  Prose pages are NOT sparse -- they run to
    thousands of characters, which is what keeps two schedules in one book
    (Morris p233-263 / p283-290) from merging across the narrative between."""
    return len(text.strip()) < SPARSE_CHARS and not any(
        s[k] for k in ("set_hdr", "col_hdr", "qty_unit", "qty_bare",
                       "wide_qty", "finish"))


def csi_footer(text: str) -> tuple[str, int] | None:
    """(code, page_no) from a page's CSI footer line ('Door Hardware 087100
    - 16' -> ('087100', 16)).  pdfium emits text in content-stream order,
    so the footer is NOT guaranteed to sit at the tail of the string
    (Forest Park prints it 2nd) -- scan every line, but only short
    standalone lines qualify: footers are short, prose that quotes a
    section number is not."""
    for l in text.splitlines():
        l = l.strip()
        if not l or len(l) > 60:
            continue
        m = RE_CSI_FOOT.search(l)
        if m:
            return re.sub(r"\s+", "", m.group(1)), int(m.group(2))
    return None


def extend_region_tails(accepted: list[dict], barriers: list[list[int]],
                        texts: list[str]) -> None:
    """Probe past each accepted region's tail; commit (mutating the entry:
    end / signals / page_hashes / tail note) only when the probe reaches
    END OF SECTION.  `barriers` are all region ranges (accepted+rejected):
    a probe never crosses into one."""
    for entry in accepted:
        end = entry["end"]
        if end >= len(texts) or RE_TAIL_EOS.search(texts[end - 1]):
            continue  # document end / natural terminator: clean tail
        foot = csi_footer(texts[end - 1])
        if not foot:
            continue  # no section footer to follow: nothing to probe with
        code, num = foot
        added = []
        while end < len(texts) and len(added) < TAIL_MAX_EXTEND:
            nxt = end + 1
            if any(a <= nxt <= b for a, b in barriers
                   if not (a <= entry["end"] <= b)):
                break  # never grow into another region
            nfoot = csi_footer(texts[nxt - 1])
            if nfoot != (code, num + 1):
                break  # code changed or page number not consecutive
            end, num = nxt, num + 1
            added.append(nxt)
            if RE_TAIL_EOS.search(texts[nxt - 1]):
                break  # the terminator page itself ends the probe
        if added and RE_TAIL_EOS.search(texts[end - 1]):
            start = entry["start"]
            entry["end"] = end
            entry["signals"] = {
                k: sum(page_signals(t)[k] for t in texts[start - 1:end])
                for k in ("set_hdr", "col_hdr", "qty_unit", "qty_bare",
                          "finish")}
            entry["page_hashes"] = [norm_hash(t)
                                    for t in texts[start - 1:end]]
            entry["tail"] = {
                "pages_added": added,
                "evidence": {"csi_footer": code, "eos_page": end},
            }
        # no EOS reached -> revert without a trace: the probe was internal


def build_regions(classes: list[str],
                  sparse: list[bool] | None = None) -> list[list[int]]:
    """classes[i] is the class of page i+1 -> list of [start, end], 1-based.

    sparse[i] marks pages that are transparent to region assembly: they do not
    count against MAX_GAP, and a weak page reaches its region across them.
    """
    strong = [i + 1 for i, c in enumerate(classes) if c == "strong"]
    if not strong:
        return []
    if sparse is None:
        sparse = [False] * len(classes)
    regions = []
    start = prev = strong[0]
    for p in strong[1:]:
        if sum(1 for q in range(prev + 1, p) if not sparse[q - 1]) <= MAX_GAP:
            prev = p
        else:
            regions.append([start, prev])
            start = prev = p
    regions.append([start, prev])
    # extend each region over adjacent weak pages, reaching across sparse ones:
    # JC Ryan's exterior sets sit on weak p24, one stub page (p25) away from the
    # strong start at p26 -- without the reach, EX-1.0/EX-2.0 stay outside.
    for r in regions:
        while True:
            i = r[0] - 2
            while i >= 0 and sparse[i]:
                i -= 1
            if i < 0 or classes[i] != "weak":
                break
            r[0] = i + 1
        while True:
            i = r[1]
            while i < len(classes) and sparse[i]:
                i += 1
            if i >= len(classes) or classes[i] != "weak":
                break
            r[1] = i + 1
    # merge overlaps created by the extension
    merged = [regions[0]]
    for r in regions[1:]:
        if r[0] <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], r[1])
        else:
            merged.append(r)
    return merged


# --- per-file scan ---------------------------------------------------------

def norm_hash(text: str) -> str:
    return hashlib.sha1(" ".join(text.split()).encode("utf-8")).hexdigest()[:16]


def scan_pdf(path: Path) -> dict:
    doc = pdfium.PdfDocument(str(path))
    texts = []
    for i in range(len(doc)):
        texts.append(doc[i].get_textpage().get_text_range() or "")
    doc.close()

    sigs = [page_signals(t) for t in texts]
    classes = [classify(s) if s["chars"] >= IMAGE_ONLY_CHARS else "none"
               for s in sigs]
    sparse = [sparse_page(t, s) for t, s in zip(texts, sigs)]
    regions = build_regions(classes, sparse)

    region_entries = []
    for start, end in regions:
        sub = sigs[start - 1:end]
        entry = {
            "start": start,
            "end": end,
            "strong_pages": sum(1 for c in classes[start - 1:end] if c == "strong"),
            "weak_pages": sum(1 for c in classes[start - 1:end] if c == "weak"),
            "signals": {k: sum(s[k] for s in sub)
                        for k in ("set_hdr", "col_hdr", "qty_unit", "qty_bare",
                                  "finish")},
            "page_hashes": [norm_hash(t) for t in texts[start - 1:end]],
        }
        # dialect tag: a region whose strong pages were carried entirely by
        # the wide-table rule is a wide-table schedule.  Absent key = the
        # default list dialect, so old books' reports stay byte-identical.
        strong_idx = [i for i in range(start - 1, end) if classes[i] == "strong"]
        if strong_idx and all(classify_list(sigs[i]) != "strong"
                              and wide_table_strong(sigs[i])
                              for i in strong_idx):
            entry["dialect"] = "wide_table"
        region_entries.append(entry)

    # region-level floor: real set regions carry set headers (or a column
    # header reprinted every page/set); qty/finish density alone can be faked
    # by product cut-sheet pages, so those islands get rejected here.
    accepted, rejected, dense_rejections = [], [], []
    for r in region_entries:
        s = r["signals"]
        # A region with no set header at all is admitted only where the
        # wide-table dialect carried it (qty+finish adjacency = the door
        # hardware fingerprint).  Column headers alone are not enough:
        # Woodridge p812-821 is a vehicle-lubrication equipment schedule whose
        # "Qty. Mfr. Model# Description Location" header fired the page rule 18
        # times with set_hdr=0 and finish=1 (2026-08-18), while Roselle's real
        # wide-table region carries finish=152 on that same branch.
        # A set header alone can be a prose accident: SJC's commissioning
        # activity schedule (p161, 2026-08-18) reads "Mechanical equipment set
        # 1 day" -- SET + a number -- next to bullet rows that look like bare
        # qty.  Demand at least one door-hardware fingerprint alongside the
        # header: a BHMA/US finish code, a unit qty ("1 EA"), or a column
        # header.  Every real region in the 20-book corpus clears this with
        # room to spare (the thinnest, JC Ryan, carries finish=4).
        hw_evidence = (s["finish"] >= 1 or s["qty_unit"] >= 1
                       or s["col_hdr"] >= 1)
        wide = s["col_hdr"] >= 3 and r.get("dialect") == "wide_table"
        if (s["set_hdr"] >= 1 and hw_evidence) or wide:
            accepted.append(r)
        else:
            r.pop("page_hashes", None)
            if s["set_hdr"] >= 1:
                r["reason"] = ("set header without any door-hardware "
                               "evidence (no finish code, no unit qty, no "
                               "column header)")
            elif s["col_hdr"] < 3:
                r["reason"] = ("no numbered set header and no column-header "
                               "density")
            else:
                r["reason"] = ("column headers without the wide-table "
                               "qty+finish dialect and no recognized set "
                               "header")
            rejected.append(r)

    # loud trace for a BIG rejection: rejected regions are meant to be small
    # product-catalog islands.  SJC p711-748 (2026-08-18) was a real 38-page
    # hardware schedule rejected because its "HW 01" header form was not yet in
    # the grammar -- the region_report recorded it, but silently, under an
    # accepted junk region elsewhere in the file.  A rejection this dense is a
    # miss until proven otherwise.  Corpus calibration over 20 books: fires on
    # SJC alone; Woodridge's genuine equipment table (10 pages, finish=1) and
    # Livelle's cut-sheet islands (2 pages) stay quiet.
    for r in rejected:
        s = r["signals"]
        if (r["end"] - r["start"] + 1 >= DENSE_REJECT_PAGES
                and s["finish"] >= DENSE_REJECT_FINISH
                and s["qty_unit"] + s["qty_bare"] >= DENSE_REJECT_QTY):
            dense_rejections.append({
                "start": r["start"], "end": r["end"],
                "signals": s, "reason": r["reason"],
                "note": "dense rejected region -- verify this is not a missed "
                        "schedule dialect",
            })

    # structural tail net: accepted regions only, after the floor
    extend_region_tails(accepted,
                        [[r["start"], r["end"]] for r in region_entries],
                        texts)

    # miss-detection: wider net over every page outside accepted regions
    acc_ranges = [(r["start"], r["end"]) for r in accepted]
    suspects = []
    for pno, text in enumerate(texts, start=1):
        if any(s <= pno <= e for s, e in acc_ranges):
            continue
        for line in text.splitlines():
            if RE_SUSPECT.match(line):
                suspects.append({"page": pno, "line": line.strip()[:80]})
                break  # one sample per page

    # title reconciliation: every "HARDWARE SETS"-style heading must be
    # answered by an accepted region within 3 pages, or carry an explicit
    # "Refer to ..." stub in the lines right below it
    stubs, unreconciled, downstream = [], [], []
    for pno, text in enumerate(texts, start=1):
        page_lines = text.splitlines()
        for i, line in enumerate(page_lines):
            if not RE_SETS_TITLE.match(line):
                continue
            if any(pno <= r["end"] and r["start"] - pno <= 6 for r in accepted):
                break  # answered by a region
            tail = "\n".join(page_lines[i + 1:i + 8])
            if len(tail) < 200 and pno < len(texts):
                tail += "\n" + texts[pno][:400]
            rec = {"page": pno, "line": line.strip()[:80]}
            if RE_REFER.search(tail):
                stubs.append({**rec, "note": "refers elsewhere"})
            elif any(r["start"] > pno for r in accepted):
                # the same PDF does carry a schedule, just further down than
                # the 6-page binding window: StarHardware announces "D.
                # Hardware Schedule:" inside its submittal requirements (p31)
                # and prints the sets 22 pages later (p53).  Not a miss -- this
                # net exists to catch a title with NO schedule anywhere after
                # it (Roselle's original alarm, Woodridge) -- but recorded
                # rather than swallowed.
                downstream.append({
                    **rec,
                    "note": "schedule appears later in this file"})
            else:
                unreconciled.append(rec)
            break

    image_only = sum(1 for s in sigs if s["chars"] < IMAGE_ONLY_CHARS)
    entry = {
        "file": path.name,
        "path": str(path),
        "pages": len(texts),
        "image_only_pages": image_only,
        "filename_hint": bool(RE_FILENAME_HINT.search(path.name)),
        "regions": accepted,
        "verdict": "sets_region_found" if accepted else (
            "image_only_no_text" if image_only == len(texts) else "no_sets"),
    }
    if rejected:
        entry["rejected_regions"] = rejected
    if dense_rejections:
        entry["dense_rejections"] = dense_rejections
    if suspects:
        entry["suspect_pages"] = {"count": len(suspects),
                                  "samples": suspects[:8]}
    if stubs:
        entry["set_title_stubs"] = stubs
    if downstream:
        entry["set_titles_answered_downstream"] = downstream
    if unreconciled:
        entry["unreconciled_set_titles"] = unreconciled
    if not accepted and (rejected or entry["filename_hint"]):
        entry["warning"] = (
            "page-level signals fired but every region was rejected -- eyeball "
            "rejected_regions / consider LLM fallback" if rejected else
            "filename suggests hardware but no set region found")
    return entry


def prune_stale_outputs(out_dir: Path, keep: set[str],
                        suffixes: tuple[str, ...]) -> list[str]:
    """Delete products of a previous run that this run no longer produces.

    Regions move when the rules change (StarHardware p31 stopped being a region
    on 2026-08-18).  Every step reads its predecessor's whole directory, so one
    leftover file walks a phantom region all the way to the delivered sets --
    and it looks exactly like a real one.  Each step therefore owns its own
    directory: what it did not just write, it removes.
    """
    removed = []
    for f in sorted(out_dir.iterdir()):
        if f.is_file() and f.name.endswith(suffixes) and f.name not in keep:
            f.unlink()
            removed.append(f.name)
    return removed


def flag_duplicates(files: list[dict]) -> None:
    """Mark regions whose page-hash sequence is contained in another file's.

    Bridgeport ships the same 49-page schedule twice (alone, and as the head
    of a 238-page file with vendor scans appended). Step 2 decides how to
    dedup; step 1 only surfaces the fact.
    """
    for f in files:
        for r in f["regions"]:
            hs = r["page_hashes"]
            for g in files:
                if g is f or r.get("duplicate_of"):
                    continue
                for r2 in g["regions"]:
                    hs2 = r2["page_hashes"]
                    if len(hs2) < len(hs):
                        continue
                    if hs == hs2 and g["pages"] >= f["pages"]:
                        continue  # equal regions: flag the bigger file's copy
                    for off in range(len(hs2) - len(hs) + 1):
                        if hs2[off:off + len(hs)] == hs:
                            r["duplicate_of"] = {
                                "file": g["file"],
                                "pages": [r2["start"] + off,
                                          r2["start"] + off + len(hs) - 1],
                            }
                            break
                    if r.get("duplicate_of"):
                        break


# --- emission --------------------------------------------------------------

def emit_stream(entry: dict, region: dict, out_dir: Path) -> Path:
    pages = extract_lines(entry["path"],
                          list(range(region["start"], region["end"] + 1)))
    stem = re.sub(r"[^\w.-]+", "_", Path(entry["file"]).stem)
    out = out_dir / f"{stem}-p{region['start']}-{region['end']}.jsonl"
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        meta = {
            "type": "meta",
            "file": entry["file"],
            "region": [region["start"], region["end"]],
            "bbox_convention": "PDF points, origin top-left: [x0, top, x1, bottom]",
            "pages": {str(p["page"]): [p["width"], p["height"]] for p in pages},
        }
        fh.write(json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n")
        for p in pages:
            for ln in p["lines"]:
                fh.write(json.dumps(ln, ensure_ascii=False, sort_keys=True) + "\n")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project_dir", help="folder holding the project PDFs")
    ap.add_argument("--out", default="data/out/step1",
                    help="output root (default: data/out/step1)")
    ap.add_argument("--no-stream", action="store_true",
                    help="report only, skip line-stream extraction")
    args = ap.parse_args()

    project = Path(args.project_dir)
    pdfs = sorted(project.rglob("*.pdf"))
    if not pdfs:
        print(f"no PDFs under {project}", file=sys.stderr)
        return 2

    files = [scan_pdf(p) for p in pdfs]
    flag_duplicates(files)

    project_name = project.name
    out_dir = Path(args.out) / re.sub(r"[^\w.-]+", "_", project_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    streams = []
    for f in files:
        for r in f["regions"]:
            if not args.no_stream:
                streams.append(str(emit_stream(f, r, out_dir)))
            r.pop("page_hashes", None)  # working data, not report material

    report = {"project": project_name, "generated_by": "step1_locate v0.3",
              "files": files, "streams": streams}
    if not any(f["regions"] for f in files):
        report["alarm"] = (
            "no set region accepted in any file of this project -- every "
            "challenge project is expected to contain hardware sets; escalate "
            "to LLM page scan or manual review")
    # stale-stream guard: regions move when the rules change (SJC p161 stopped
    # being a region on 2026-08-18) and a leftover .jsonl from the previous run
    # would be picked up by every downstream step as a phantom region.  The
    # report is the authority on which streams exist.
    declared = {Path(s).name for s in report.get("streams", [])}
    for stale in sorted(out_dir.glob("*.jsonl")):
        if stale.name not in declared:
            stale.unlink()
            print(f"  removed stale stream: {stale.name}")

    report_path = out_dir / "region_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    # console summary
    print(f"project: {project_name}")
    if "alarm" in report:
        print(f"  ALARM: {report['alarm']}")
    for f in files:
        regs = ", ".join(
            f"p{r['start']}-{r['end']}"
            + (" [wide_table]" if r.get("dialect") == "wide_table" else "")
            + (f" (dup of {r['duplicate_of']['file']}"
               f" p{r['duplicate_of']['pages'][0]}-{r['duplicate_of']['pages'][1]})"
               if r.get("duplicate_of") else "")
            for r in f["regions"]) or "-"
        rej = (f" rejected: " + ", ".join(
            f"p{r['start']}-{r['end']}" for r in f["rejected_regions"])
            if f.get("rejected_regions") else "")
        sus = (f" suspects: {f['suspect_pages']['count']}p"
               if f.get("suspect_pages") else "")
        print(f"  {f['verdict']:<20} {f['file']}"
              f" [{f['pages']}p, {f['image_only_pages']} image-only]"
              f" regions: {regs}{rej}{sus}")
        if f.get("warning"):
            print(f"    WARN: {f['warning']}")
        for d in f.get("dense_rejections", []):
            sig = d["signals"]
            print(f"    WARN: dense rejected region p{d['start']}-{d['end']}"
                  f" (qty {sig['qty_unit'] + sig['qty_bare']},"
                  f" finish {sig['finish']}) -- {d['reason']}")
        for t in f.get("unreconciled_set_titles", []):
            print(f"    WARN: set title without region: p{t['page']} {t['line']!r}")
        for t in f.get("set_title_stubs", []):
            print(f"    note: p{t['page']} {t['line']!r} -> refers elsewhere")
    print(f"report: {report_path}")
    for s in streams:
        print(f"stream: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
