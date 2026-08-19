"""Step 3b of the v0.2 funnel: rule-side field extraction inside each block.

  step-2 blocks + step-1.5 lines + step-3a dossier
     |  one pass per block: a small section state machine labels each line,
     |  qty/unit splits off the front of component rows, and the trailing
     |  slots are assigned to mfr / finish by the dossier's induced column
     |  schema -- validated against a value vocabulary, never by position
     |  alone.  Mechanical breakages (dropped finish+mfr rows, hyphen-split
     |  finishes, header trailer continuations) are stitched here.
     v
  <stream>.rules.jsonl  (per-block rule prediction: rows + doors + notes +
  unresolved lines) + <stream>.geometry.jsonl (word-level x0: every line
  of blocks flagged broken, plus every component row and its stitch
  sources -- column bands are induced from these) + rules_report.json
  per project.

This is the rule half of decision #3 (option C): mechanical fields only.
The judgment calls -- where description ends and catalog_number begins,
which qty-led lines are not components at all, how a fragment attaches,
how a scrambled row regroups -- are left for step 3c (LLM assembly), which
reads this file as both its input and its reconciliation baseline.

Field assignment (evidence: 9 streams, 2026-08-18 desk run):
* Column ORDER is never assumed. Each trailing slot is read at the index
  step 3a induced for it, and the token there is kept only if it validates
  against that column's value set: the book's legend closed set (high
  confidence) or the slot's own distribution vocabulary (medium). A token
  that fails validation yields null -- which is how "1 EA WIRE HARNESS ...
  SCH" gets mfr=SCH and finish=null with no special case, and how
  Bridgeport (finish@-1, no mfr column at all) reads correctly.
* The vocabulary is frequency AND shape: a token must appear in that slot
  at least twice and look like a code. Frequency alone would swallow
  Vantage's "1 EA GASKETING BY DOOR/FRAME" tail; shape alone would admit
  every catalog suffix. Livelle's "NO" (23 rows, no legend in that book)
  survives both filters -- the SPEC's own ambiguous code, resolved by
  column distribution exactly as the SPEC says to.
* The finish side is gated harder, by the BHMA value grammar (626 / 10BE /
  613E / US26D / C32D + the alpha finish words). In a no-legend book a
  recurring catalog model rides the finish slot with code-like shape --
  Pemko's 2113AV door bottom does exactly that on Livelle's "1 Door Bottom
  2113AV PE" rows -- and only the digit/letter structure of BHMA codes
  tells it from a finish. Bridgeport's fire ratings (20MIN, 1HR) fail the
  same gate. The loose RE_FINISH_SHAPE keeps deciding column IDENTITY in
  step 3a, where catalog noise averages out; admitting a VALUE into the
  assignment vocabulary is what needs the tight grammar.

Sections, and why the head region is positional:
* A block runs "head -> components", the boundary being the dialect's
  "PROVIDE EACH ... :" line. Everything above it is door-side (a bare
  count, "For use on Door #(s):", a door-number grid) and nothing there is
  ever a component. That one positional rule is what keeps National's
  "2 5 25 33" and Lyons's "222A-1 223 224 323" out of the component
  stream -- they are qty-led to any regex, and no token grammar separates
  them from real rows. Books with no such line (Bridgeport, Morris,
  Livelle) simply have an empty head and start in components.
* Inside components, "Note:" / "NOTE:" opens a note run that the next
  component row closes; "OPERATION:" runs to the end of the block;
  "Description:" / "Properties:" / "Opening Description:" label their own
  line only.

Mechanical stitches (only the unambiguous ones -- everything else is 3c's):
* dropped tail: a line that is nothing but a valid finish + mfr pair
  attaches to the component row above it (Lyons splits ~half its rows this
  way; "691 LCN" reads as a qty-led row to any naive parser).
* hyphen finish: a slot token ending in "-" plus a short next line
  ("630-" + "316" in National).
* trailer continuation: the first body line when the header's trailer has
  an unclosed parenthesis (Market View's set descriptions wrap mid-phrase).

Broken-block detection (what step 3c must read with geometry attached):
* a component row whose rest is empty after slot assignment -- the row's
  own text carries no description at all, so it is half of a scrambled
  table row (Livelle p681/p683, where one visual row became three lines);
* a short orphan fragment adjacent to a component row (Vantage "VDC",
  "(FAIL SECURE)", "BY DIV 28");
* an unattached dropped tail.
Long prose fragments are NOT brokenness -- they are note candidates.
For every line of a broken block, the word-level x0 positions go to
<stream>.geometry.jsonl: the line text alone loses column membership (on
Livelle p681 the naive reading puts "NEMW (EAC option)" in description
when its x0 says catalog), so the geometry travels with the block instead
of being reconstructed by an algorithm we would have to trust.

Every body line lands in exactly one bucket; the per-block `partition`
counter is asserted, mirroring step 2's cut being a true partition.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from step1_locate import prune_stale_outputs  # noqa: E402
from step3_dossier import (  # noqa: E402
    FINISH_WORDS, RE_FINISH_SHAPE, RE_MFR_SHAPE, RE_PART_PREFIX, clean,
    induce_stream, load_stream_blocks,
)

# --- line grammar ----------------------------------------------------------

RE_COMPONENTS_OPENER = re.compile(r"^PROVIDE\s+EACH\b.*:\s*$", re.I)

# (bucket, scope, regex) -- scope "run" stays until a component row,
# "end" stays to block end, "line" labels only the opener line itself.
SECTION_OPENERS = [
    ("doors", "line", re.compile(r"^For use on Door\s*#?\(?s?\)?\s*:", re.I)),
    ("doors", "line", re.compile(r"^Doors?\s*:", re.I)),
    ("notes", "run", re.compile(r"^NOTES?\s*:", re.I)),
    ("notes", "end", re.compile(r"^OPERATION\s*:", re.I)),
    ("description", "line", re.compile(r"^Description\s*:\s*(.*)$", re.I)),
    ("properties", "line",
     re.compile(r"^(?:Opening\s+Description|Properties)\s*:\s*(.*)$", re.I)),
]

# "1 Single Operator ..." is an auto-operator COMPONENT row, not a door-leaf
# count (Gerrard x18, 2026-08-18) -- the lookahead keeps it out of this net
RE_DOOR_ITEM = re.compile(
    r"^(?:Item\s*#\d+\b|\d+\s+(?:Pair|Single)\b(?!\s+Operator\b))", re.I)
RE_DIM = re.compile(r"^\d{3,4}(?:,\s*\d{3,4})*\s*x\s*\d{3,4}\b")
RE_BARE_QTY = re.compile(r"^\d+$")
# AMI dialect (2026-08-18): a BARE door_header ("For use on Door #(s):" with
# nothing after the colon) lists its door numbers on the FOLLOWING lines
# ("134" / "137A 210A 212B"), closed by the "Each to have:" boilerplate.
# The continuation state opens only on a bare header, so inline-doors books
# (Gerrard "Doors: OH115, ...") never enter it.
RE_DOOR_LIST = re.compile(r"^\d{1,4}[A-Z]{0,2}(?:\s+\d{1,4}[A-Z]{0,2})*$")
RE_EACH_TO_HAVE = re.compile(r"^Each\s+to\s+have\s*:?\s*$", re.I)
# a trailing "BY <words>" run is attribution prose riding the slot columns
# ("... GASKETING BY ALUMINUM DOOR |MANUFACTURER", AMI x6) -- every token
# after BY must be a bare word (no digits: "(BY DIV. 28) ... WHT GEN" keeps
# its real printed maker).  A COMPLETE by-phrase closes with a role noun,
# and what follows one is printed column data, not prose: "Furnished by
# Security Contractor HD" (Livelle x55) and "By Door Supplier CA"
# (Bridgeport x6) keep their trailing codes; only a phrase still missing
# its role noun (AMI's wrapped "BY ALUMINUM DOOR |MANUFACTURER") ends in a
# word that merely LOOKS like a code.
RE_BY_TAILWORD = re.compile(r"[A-Za-z][A-Za-z./&'-]*")
BY_ROLE_NOUNS = {"MANUFACTURER", "MFR", "SUPPLIER", "CONTRACTOR",
                 "CONSULTANT", "PROVIDER", "OWNER", "OTHERS", "VENDOR",
                 "INSTALLER", "DIVISION"}
# qty accepts the decimal dialect "3.0 Hinge ..." (Forest Park, 2026-08-18).
# The fraction is gated to .0/.5 (hardware counts come in halves at most) so
# spec article numbers ("1.1 SUMMARY", "3.7 DEMONSTRATION") never read as
# quantities; integral values normalise back to int, so integer books emit
# byte-identical output.
# unit spellings are per-book: the first books all wrote "EA"/"SET"/"PR" in
# caps, SJC writes "1 Ea." / "1 Set" and StarHardware "8 Ea." (2026-08-18).
# Case-sensitive alternatives rather than re.I on the whole pattern, so an
# ordinary lowercase first word of a description can never be eaten as a unit;
# corpus probe over every content row: 1,102 rows gain a unit (SJC 510,
# StarHardware 592) and the other 18 books are untouched.
# The -[A-Z] tail is Shubie's revision marker: "1 EA-R AUTO OPERATOR 9542
# ANCL LCN" sits among plain "1 EA ..." rows, and without it the whole unit
# fell into the description. One capital letter, not a general suffix, and
# the trailing \s+ still has to match -- so "1 SET-UP KITS" keeps reading as
# a bare qty. Corpus probe over all 26,290 content rows: exactly these 2
# rows change (both Shubie p174), and a literal "EA-R" alternative would
# change the same 2. The reject bought: a description whose first token is
# literally EA-/SET-/PR- plus one capital would lose it into `unit`.
RE_QTY_LED = re.compile(
    r"^(\d+(?:\.[05])?)\s+"
    r"(?:((?:EA|SET|SETS|PR|Ea|Set|Sets|Pr)(?:-[A-Z])?\.?)\s+)?"
    r"(\S.*)$")

MIN_VOCAB_COUNT = 2    # a slot token must recur to enter the vocabulary
# name-type manufacturer dialect (JC Ryan, 2026-08-18): maker NAMES at row
# tails, no legend, no code-shaped column.  Higher floor than codes (any
# capitalized English word passes the shape), and the tail must ride a
# description + catalog (>= 3 rest tokens): the corpus counter-example is
# "1 Door/Frame Harness" x11, a two-token row whose tail is the item noun
# itself -- the row-length fence removes it whole (probe 2026-08-18:
# admitted = Norton/Pemko/Rockwood/Sargent/Securitron, nothing else).
MIN_NAME_COUNT = 3
RE_MFR_NAME = re.compile(r"^[A-Z][a-z][A-Za-z&'-]+$")
ORPHAN_MAX_TOKENS = 3  # short dangling fragment = stitch material, not prose
ORPHAN_MAX_CHARS = 30

# never vocabulary members, whatever their frequency: these ride the ends of
# wrapped prose ("DOOR POSITION SWITCH BY" + "DIV 28") and pass shape checks
STOP_TOKENS = {"AND", "AS", "BY", "FOR", "IN", "OF", "ON", "OR", "PER",
               "THE", "TO", "WITH"}


def norm(text: str) -> str:
    """Icon glyphs stripped, Word outline prefix removed, whitespace collapsed."""
    return RE_PART_PREFIX.sub("", clean(text))


def open_section(text: str):
    """Return (bucket, scope, inline_value) if this line opens a section."""
    for bucket, scope, rx in SECTION_OPENERS:
        m = rx.match(text)
        if m:
            return bucket, scope, (m.group(m.lastindex).strip()
                                   if m.lastindex else "")
    return None


def qty_split(text: str):
    """Return (qty, unit, tokens) for a qty-led line, else None."""
    m = RE_QTY_LED.match(text)
    if not m:
        return None
    q = float(m.group(1))
    return (int(q) if q.is_integer() else q, m.group(2), m.group(3).split())


def unclosed_paren(text: str) -> bool:
    return text.count("(") > text.count(")")


# --- column vocabulary -----------------------------------------------------

def shape_ok(token: str, role: str) -> bool:
    if role == "mfr":
        return RE_MFR_SHAPE.match(token) is not None
    return RE_FINISH_SHAPE.match(token) is not None or token in FINISH_WORDS


# BHMA-family finish values: 626, 313, 10BE, 26D, 613E, US26D, C32D ...
# (2-3 digits with a short letter tail, or the US/C prefixed forms).
# 4+ digits or a 3-letter run after digits is catalog/rating territory:
# 2113AV, 312CR (Pemko models), 20MIN, 1HR (fire ratings) all fail here.
RE_FINISH_VALUE = re.compile(
    r"^(?:\d{2,3}|\d{2}[A-Z]{1,2}|\d{3}[A-Z]|US\d{1,2}[A-Z]?|C\d{2}[A-Z]?)$")


def finish_value_ok(token: str) -> bool:
    return RE_FINISH_VALUE.match(token) is not None or token in FINISH_WORDS


def build_vocab(rows: list[list[str]], schema: dict, legend: dict) -> dict:
    """Per-role accepted value set: legend closed set + slot distribution.

    Slash pairs ("626/626", one finish per leaf) are admitted only when both
    halves stand on their own -- "2/2134" is a door dimension whose shape
    passes any slash-finish regex, and only the halves test tells them apart.
    """
    vocab = {}
    for role in ("mfr", "finish"):
        slot = schema.get(f"{role}_slot")
        closed = set(legend.get(role, {}))
        seen = Counter()
        if slot is not None:
            for toks in rows:
                if len(toks) >= abs(slot) + 1:  # keep at least one rest token
                    seen[toks[slot]] += 1
        # Name-type manufacturer books: no legend, no induced mfr slot, and
        # the tail slot not owned by finish (Bridgeport/Valor).  Without this
        # branch the vocabulary stays empty and 3c's gate refuses every name
        # the model reads (JC Ryan: 205 refusals on the record, zero yield).
        # The book's own repetition is the evidence the legend never gave.
        if (role == "mfr" and slot is None and not closed
                and schema.get("finish_slot") != -1):
            for toks in rows:
                if (len(toks) >= 3 and RE_MFR_NAME.match(toks[-1])
                        and toks[-1].upper() not in STOP_TOKENS
                        and toks[-1].upper() not in BY_ROLE_NOUNS):
                    seen[toks[-1]] += 1
            vocab[role] = {"slot": None, "legend": closed,
                           "distribution": {t for t, c in seen.items()
                                            if c >= MIN_NAME_COUNT}}
            continue
        base = {t for t, c in seen.items()
                if c >= MIN_VOCAB_COUNT
                and (finish_value_ok(t) if role == "finish"
                     else shape_ok(t, "mfr"))
                and t not in STOP_TOKENS
                and ("/" not in t or t == "B/O")}  # B/O = By Others, a maker
        halves_ok = closed | base
        slashed = {t for t, c in seen.items()
                   if c >= MIN_VOCAB_COUNT and "/" in t and t not in base
                   and all(h in halves_ok for h in t.split("/"))}
        vocab[role] = {"slot": slot, "legend": closed,
                       "distribution": (base | slashed) - closed}
    return vocab


def in_vocab(token: str, role_vocab: dict) -> str | None:
    if token in role_vocab["legend"]:
        return "high"
    if token in role_vocab["distribution"]:
        return "medium"
    return None


def assign_slots(toks: list[str], vocab: dict) -> dict:
    """Read each induced slot; keep the token only if it validates."""
    out = {"mfr": None, "finish": None, "confidence": {},
           "rest_from": len(toks)}
    for role in ("mfr", "finish"):
        slot = vocab[role]["slot"]
        if slot is None or len(toks) < abs(slot) + 1:
            continue
        token = toks[slot]
        level = in_vocab(token, vocab[role])
        if level is None and role == "finish" and token.endswith("-") \
                and finish_value_ok(token[:-1]):
            level = "low"  # hyphen-split finish ("630-"); stitch appends the rest
        if level is None:
            continue
        out[role] = token
        out["confidence"][role] = level
        out["rest_from"] = min(out["rest_from"], len(toks) + slot)
    return out


def split_hint(toks: list[str]) -> int | None:
    """Naive description|catalog boundary: first token bearing a digit.

    Emitted as a baseline for step 3c to agree or disagree with -- it is
    wrong on rows with no digit-bearing token at all (6-18% per book).
    """
    for i, tok in enumerate(toks):
        if any(ch.isdigit() for ch in tok):
            return i
    return None


def is_dropped_tail(toks: list[str], vocab: dict) -> bool:
    """Exactly a finish + mfr pair on its own line (Lyons '691 LCN')."""
    return (len(toks) == 2
            and in_vocab(toks[0], vocab["finish"]) is not None
            and in_vocab(toks[1], vocab["mfr"]) is not None)


def looks_dropped_tail(toks: list[str]) -> bool:
    """Shape-only dropped tail, for the bootstrap pass before a vocabulary
    exists. Lyons is the book that needs it: step 3a declines to induce a
    column schema there *because* half its rows are split, so the repair
    must run first and the schema be induced from the repaired rows."""
    return (len(toks) == 2 and shape_ok(toks[0], "finish")
            and shape_ok(toks[1], "mfr"))


# --- per-block extraction --------------------------------------------------

def head_length(body: list[dict]) -> int:
    """Body lines before the dialect's components opener (0 if it has none)."""
    for i, rec in enumerate(body):
        if RE_COMPONENTS_OPENER.match(norm(rec["text"])):
            return i + 1  # the opener itself belongs to the head
    return 0


def extract_block(block: dict, lines: list[dict], vocab: dict) -> dict:
    """Rule prediction for one block. Every body line lands in one bucket."""
    body = lines[1:]
    out = {
        "type": "block", "seq": block["seq"], "set_id": block["set_id"],
        "family": block["family"], "trailer": block["trailer"],
        "empty": block["empty"], "header_anchor": lines[0]["anchor"],
        "description": None, "properties": [], "rows": [], "door_lines": [],
        "note_lines": [], "unresolved": [], "flags": [], "consumed": 0,
    }

    def take(bucket: str, rec: dict, **extra) -> None:
        out[bucket].append({"anchor": rec["anchor"], "text": rec["text"],
                            **extra})
        out["consumed"] += 1

    start = 0
    if body and out["trailer"] and unclosed_paren(out["trailer"]):
        out["trailer"] += " " + norm(body[0]["text"])
        out["flags"].append("trailer_continuation")
        out["consumed"] += 1
        start = 1

    head_end = head_length(body)
    section, section_scope = "components", None
    doors_open = False  # bare door_header seen; door numbers follow

    for i, rec in enumerate(body[start:], start=start):
        text = norm(rec["text"])
        if not text:
            take("unresolved", rec, kind="blank")
            continue

        if i < head_end:  # door-side region: never a component row
            kind = ("bare_qty" if RE_BARE_QTY.match(text)
                    else "opener" if RE_COMPONENTS_OPENER.match(text)
                    else "door")
            take("door_lines", rec, kind=kind)
            continue

        if doors_open:
            if RE_DOOR_LIST.match(text):
                take("door_lines", rec, kind="door")
                continue
            doors_open = False
            if RE_EACH_TO_HAVE.match(text):
                take("note_lines", rec, kind="note")
                continue

        opener = open_section(text)
        if opener and section_scope != "end":
            bucket, scope, value = opener
            if scope == "line":
                if bucket == "description" and out["description"] is None:
                    out["description"] = value
                    out["consumed"] += 1
                elif bucket == "properties":
                    take("properties", rec, value=value)
                else:
                    take("door_lines", rec, kind="door_header")
                    # bare header (nothing after the colon) -> the door
                    # numbers come on the following lines (AMI dialect)
                    if bucket == "doors" and ":" in text \
                            and not text.split(":", 1)[1].strip():
                        doors_open = True
                continue
            section, section_scope = bucket, scope
            take("note_lines", rec, kind="note_header")
            continue

        toks = text.split()
        split = qty_split(text)

        if section == "notes":
            if split and not is_dropped_tail(toks, vocab) \
                    and section_scope != "end":
                section, section_scope = "components", None  # row closes notes
            else:
                take("note_lines", rec, kind="note")
                continue

        if RE_DOOR_ITEM.match(text) or RE_DIM.match(text):
            take("door_lines", rec, kind="door")
        elif RE_BARE_QTY.match(text):
            # a bare number is a door count only in the head region; down
            # here it is stitch material (the "316" of a hyphen-split "630-")
            take("unresolved", rec, kind="orphan")
        elif is_dropped_tail(toks, vocab):
            take("unresolved", rec, kind="dropped_tail", tokens=toks)
        elif split:
            qty, unit, ctoks = split
            slots = assign_slots(ctoks, vocab)
            extra = {}
            assigned = [slots[r] for r in ("mfr", "finish")
                        if slots[r] is not None]
            if assigned:
                bi = next((j for j in range(len(ctoks) - 1, -1, -1)
                           if ctoks[j].upper() == "BY"), None)
                if bi is not None and bi < len(ctoks) - 1 \
                        and all(RE_BY_TAILWORD.fullmatch(t)
                                for t in ctoks[bi + 1:]) \
                        and all(t.isalpha() for t in assigned) \
                        and not any(t.upper().strip(".,") in BY_ROLE_NOUNS
                                    for t in ctoks[bi + 1:-1]):
                    # ".. BY ALUMINUM DOOR": a by-phrase that never reaches
                    # its role noun -- the trailing word is the phrase's own
                    # tail, not maker/finish data.  Code-shaped values and
                    # closed phrases survive ("SEALS BY DOOR MANUFACTURER
                    # B/O", "by Security Contractor HD").
                    slots = {"mfr": None, "finish": None, "confidence": {},
                             "rest_from": len(ctoks)}
                    extra["by_others"] = True
            rest = ctoks[:slots["rest_from"]]
            take("rows", rec, qty=qty, unit=unit, rest=" ".join(rest),
                 rest_tokens=len(rest), split_hint=split_hint(rest),
                 mfr=slots["mfr"], finish=slots["finish"],
                 confidence=slots["confidence"], stitched=[], **extra)
        elif len(toks) <= ORPHAN_MAX_TOKENS and len(text) <= ORPHAN_MAX_CHARS:
            take("unresolved", rec, kind="orphan")
        else:
            take("note_lines", rec, kind="prose")

    out["partition"] = {"n_body_lines": len(body), "consumed": out["consumed"]}
    del out["consumed"]
    return out


# --- mechanical stitches ---------------------------------------------------

def anchor_key(anchor: str) -> tuple[int, int]:
    page, line = anchor[1:].split("-L")
    return int(page), int(line)


def row_above(rows: list[dict], anchor: str):
    """The component row immediately above `anchor` in reading order."""
    key = anchor_key(anchor)
    above = [r for r in rows if anchor_key(r["anchor"]) < key]
    return max(above, key=lambda r: anchor_key(r["anchor"])) if above else None


def stitch(block: dict, vocab: dict) -> None:
    """Attach the unambiguous breakages; leave everything else unresolved."""
    keep = []
    for item in block["unresolved"]:
        prev = row_above(block["rows"], item["anchor"])
        if prev is None:
            keep.append(item)
            continue
        if item["kind"] == "dropped_tail" \
                and prev["finish"] is None and prev["mfr"] is None:
            fin, mfr = item["tokens"]
            prev["finish"], prev["mfr"] = fin, mfr
            prev["confidence"]["finish"] = in_vocab(fin, vocab["finish"])
            prev["confidence"]["mfr"] = in_vocab(mfr, vocab["mfr"])
            prev["stitched"].append({"anchor": item["anchor"],
                                     "how": "dropped_tail"})
            continue
        if item["kind"] == "orphan" and prev["finish"] \
                and prev["finish"].endswith("-"):
            prev["finish"] += norm(item["text"])
            prev["confidence"]["finish"] = "medium"
            prev["stitched"].append({"anchor": item["anchor"],
                                     "how": "hyphen_finish"})
            continue
        keep.append(item)
    block["unresolved"] = keep


RE_WORDISH = re.compile(r"[A-Za-z]{3,}")


def mark_broken(block: dict) -> None:
    """Flag blocks whose lines need word-level geometry in step 3c."""
    if any(not RE_WORDISH.search(r["rest"]) for r in block["rows"]):
        # no description word survives slot assignment -- half of a
        # scrambled table row (Livelle "1 US32D SA")
        block["flags"].append("row_without_description")
    if any(i["kind"] == "orphan" for i in block["unresolved"]):
        block["flags"].append("orphan_fragment")
    if any(i["kind"] == "dropped_tail" for i in block["unresolved"]):
        block["flags"].append("unattached_dropped_tail")
    block["broken"] = any(f != "trailer_continuation" for f in block["flags"])


# --- geometry for broken blocks --------------------------------------------

TOP_TOLERANCE = 1.0


def geometry_for(pdf_path: str, wanted: dict) -> list[dict]:
    """Word-level x0 per line, for the `wanted` (page, line) set.

    A word belongs to the wanted line whose y-interval holds its center
    (nearest line center when neighbours overlap).  Matching on the bbox
    top alone loses every word that sits lower than the line's tallest
    glyph: HFH's icon columns are 1.54pt above the text baseline, which
    silently emptied word geometry for 94% of that book's component rows
    (measured 2026-08-18: 1,828 of 1,940 rows carried icons only), so no
    column band could ever form there.
    """
    import pdfplumber

    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_no in sorted(wanted):
            page = pdf.pages[page_no - 1]
            words = page.extract_words()
            recs = [wanted[page_no][ln] for ln in sorted(wanted[page_no])]
            per = {rec["anchor"]: [] for rec in recs}
            for w in words:
                mid = (w["top"] + w["bottom"]) / 2
                best = None
                for rec in recs:
                    top, bottom = rec["bbox"][1], rec["bbox"][3]
                    if top - TOP_TOLERANCE <= mid <= bottom + TOP_TOLERANCE:
                        d = abs(mid - (top + bottom) / 2)
                        if best is None or d < best[0]:
                            best = (d, rec["anchor"])
                if best is not None:
                    per[best[1]].append(w)
            for rec in recs:
                on_line = sorted(per[rec["anchor"]], key=lambda w: w["x0"])
                out.append({
                    "anchor": rec["anchor"], "bbox": rec["bbox"],
                    "words": [{"text": w["text"], "x0": round(w["x0"], 2),
                               "top": round(w["top"], 2)} for w in on_line],
                })
            page.flush_cache()
    return out


# --- slot zones (column-as-interval; round A of the column-first plan) ------
#
# A band is a point (the modal x0 of validated values); a ZONE is the
# interval a column occupies, so membership can be tested for words the
# vocabulary never blessed.  Zones are measured, never guessed:
#   unit   = modal x0 of the rows' own unit words .. start of description
#   finish = band x0 .. start of the mfr zone
#   mfr    = band x0 .. line end; with NO band (name-type books: JC Ryan),
#            the rightmost position-only cell-start cluster qualifies iff
#            the book's mfr vocabulary is non-empty, support >= max(3, 5%
#            of rows), and it sits in the right 45% of the text width --
#            its measured wobble widens the interval (JC: 438-460 spans 22pt,
#            so ABH at 421.3 is still inside lo = min - span - 2).
# Zones gate only 3c's fill/detach bookkeeping; they never touch prompts.

ZONE_MIN_SHARE = 0.6   # dominant cluster must carry this share of samples


def _cluster_x0(xs: Counter) -> list[tuple[float, int]]:
    out = []
    for x, n in sorted(xs.items()):
        if out and x - out[-1][0] <= 2.0:
            ax, an = out[-1]
            out[-1] = ((ax * an + x * n) / (an + n), an + n)
        else:
            out.append((x, n))
    return out


def _dominant(xs: Counter):
    if not xs:
        return None
    clusters = _cluster_x0(xs)
    x, n = max(clusters, key=lambda c: c[1])
    if n < 3 or n < ZONE_MIN_SHARE * sum(xs.values()):
        return None
    return x, n


HDR_LABEL = (("QUANTITY", "qty"), ("QTY", "qty"), ("QT", "qty"),
             ("DESCRIPTION", "description"), ("CATALOG", "catalog"),
             ("MODEL", "catalog"), ("FINISH", "finish"), ("MFR", "mfr"),
             ("MANF", "mfr"), ("MANUFACTURER", "mfr"))


def header_anchors(pdf_path: str, stream_path: str) -> dict:
    """Column-header word x0 per role, read off the col_hdr lines.

    Headers are page furniture (never block members), but their words sit
    exactly on the column starts, so 8 of the 23 streams can LABEL their
    zones instead of inferring them (round B, 2026-08-19)."""
    hdr_lines = []
    with open(stream_path, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("role") == "col_hdr":
                hdr_lines.append(rec)
            elif rec.get("role") in ("content", "page_header"):
                # Roselle's header row repeats every page, so 1.5 filed it
                # as page furniture ("page_header"), and its line text
                # glues QTY.FINISH -- match on alpha runs, not words
                toks = set(re.findall(r"[A-Z]+", rec.get("text", "").upper()))
                if sum(1 for k, _ in HDR_LABEL if k in toks) >= 3:
                    hdr_lines.append(rec)
    if not hdr_lines:
        return {}
    import pdfplumber
    votes = {}
    by_page = defaultdict(list)
    for rec in hdr_lines:
        by_page[rec["page"]].append(rec)
    with pdfplumber.open(pdf_path) as pdf:
        for pg in sorted(by_page):
            page = pdf.pages[pg - 1]
            words = page.extract_words()
            for rec in by_page[pg]:
                top, bottom = rec["bbox"][1], rec["bbox"][3]
                for w in words:
                    mid = (w["top"] + w["bottom"]) / 2
                    if top - 1 <= mid <= bottom + 1:
                        wu = w["text"].upper()
                        # Roselle prints QTY.FINISH as one glued word --
                        # label each alpha run, at its proportional x
                        for m in re.finditer(r"[A-Z]+", wu):
                            frag = m.group(0)
                            for key, role in HDR_LABEL:
                                if frag == key:
                                    fx = w["x0"] + (m.start() / max(
                                        1, len(wu))) * (w["x1"] - w["x0"])
                                    votes.setdefault(role, Counter())[
                                        round(fx, 1)] += 1
            page.flush_cache()
    out = {}
    for role, xs in votes.items():
        x, n = xs.most_common(1)[0]
        out[role] = x
    return out


def induce_slot_zones(out_blocks: list[dict], geometry: list[dict],
                      bands: dict, vocab: dict) -> dict:
    words_of = {g["anchor"]: g["words"] for g in geometry}
    unit_xs, desc_xs = Counter(), Counter()
    starts = Counter()
    max_x1 = 0.0
    n_rows = 0
    for g in geometry:
        prev_x1 = None
        for w in g["words"]:
            max_x1 = max(max_x1, w.get("x1", w["x0"]))
            if prev_x1 is None or w["x0"] - prev_x1 >= 12.0:
                starts[round(w["x0"], 1)] += 1
            prev_x1 = w.get("x1", w["x0"] + 4.0)
    for b in out_blocks:
        for r in b["rows"]:
            n_rows += 1
            words = words_of.get(r["anchor"]) or []
            if r["unit"]:
                for w in words:
                    if w["text"] == r["unit"]:
                        unit_xs[round(w["x0"], 1)] += 1
                        break
            rest0 = (r["rest"].split() or [None])[0]
            if rest0:
                for w in words:
                    if w["text"] == rest0:
                        desc_xs[round(w["x0"], 1)] += 1
                        break
    zones = {}
    unit_c, desc_c = _dominant(unit_xs), _dominant(desc_xs)
    if unit_c and desc_c and unit_c[0] < desc_c[0] - 4:
        zones["unit"] = {"lo": round(unit_c[0] - 2, 2),
                         "hi": round(desc_c[0] - 2, 2),
                         "support": unit_c[1]}
    mfr_zone = None
    if bands.get("mfr"):
        b = bands["mfr"]
        mfr_zone = {"lo": round(b["x0"] - b["tol"], 2), "hi": None,
                    "support": b["support"], "from": "band"}
    elif (vocab["mfr"]["legend"] or vocab["mfr"]["distribution"]):
        # prose guard: a real mfr column is written on most lines, prose
        # tails only graze the right edge (JC Ryan 87/558 = 16% qualifies,
        # StarHardware's prose stream 110/3629 = 3% does not -- measured
        # 2026-08-19 after the ungated rule filled mfr="or"/"625" there)
        right = [(x, n) for x, n in _cluster_x0(starts)
                 if x > 0.55 * max_x1
                 and n >= max(3, 0.05 * n_rows, 0.10 * len(geometry))]
        if right:
            x, n = right[-1]
            members = [xx for xx in starts if abs(xx - x) <= 12.0]
            span = (max(members) - min(members)) if members else 0.0
            mfr_zone = {"lo": round(min(members or [x]) - span - 2, 2),
                        "hi": None, "support": n, "from": "rightmost"}
    if mfr_zone:
        zones["mfr"] = mfr_zone
    if bands.get("finish"):
        b = bands["finish"]
        zones["finish"] = {"lo": round(b["x0"] - b["tol"], 2),
                           "hi": mfr_zone["lo"] if mfr_zone else None,
                           "support": b["support"], "from": "band"}
    return zones


def induce_field_zones(zones: dict, out_blocks: list[dict],
                       geometry: list[dict], headers: dict) -> dict:
    """Round B: description/catalog intervals on top of the slot zones.

    A header word pins the boundary outright; without headers the catalog
    start is the 2nd percentile of the cell-start population between the
    description column and the slot columns -- percentile, not mode,
    because JC Ryan's catalog cells wobble across 20pt and the mode would
    strand the left edge (A500 at 248.7) in the description zone.
    Guards (all measured, 2026-08-19): the population must be at least
    max(5, 25% of rows, 10% of geometry lines) -- prose never qualifies
    (StarHardware p53: catalog-window starts are 3% of its lines); the
    description zone must be >= 50pt wide and >= 70% of rows must open
    their rest inside it, or the stream keeps slot-only zones (Valor's
    two-column dialect fails here by design)."""
    if not any(b["rows"] for b in out_blocks) and len(headers) >= 3:
        # row-less table (Roselle): the grammar never parsed a row, so
        # every zone hangs off the header words alone (veto round found
        # the same book had 228 correct LLM readings refused for want of
        # a row to validate against, 2026-08-19)
        zones = dict(zones)
        order = sorted((x, r) for r, x in headers.items())
        for i, (x, role) in enumerate(order):
            if role in zones or role == "description":
                continue
            hi = order[i + 1][0] - 2 if i + 1 < len(order) else None
            zones[role] = {"lo": round(x - 6, 2),
                           "hi": None if hi is None else round(hi, 2),
                           "support": 0, "from": "header"}
        return zones
    # header fallback for the slot columns themselves (veto round,
    # 2026-08-19): SAT's finish band never formed because the 253 "BY
    # DIVISION 28" riders at x=417 diluted the vote to 0.835 < 0.9 -- the
    # poison blocked the very instrument that could measure it.  A FINISH/
    # MFR header word pins the zone regardless (lo = x-6: the dual-template
    # books jitter 4pt).
    for role in ("mfr", "finish"):
        if role not in zones and headers.get(role) is not None:
            hi = zones["mfr"]["lo"] if role == "finish" and "mfr" in zones                 else None
            zones = dict(zones)
            zones[role] = {"lo": round(headers[role] - 6, 2), "hi": hi,
                           "support": 0, "from": "header"}
    # values fallback (screenshot round, 2026-08-19): a 2-page book can
    # have no band, no FINISH header word, and still write every finish in
    # one column -- Shubie's 21 accepted finishes sit in a 0.4pt column at
    # x=477, and the vocabulary-rejected ANCL/WHT/LGR/AA sit at the same
    # x.  The accepted values themselves are the instrument: their
    # dominant cluster (floor 5 samples on top of _dominant's share gate)
    # opens a zone.  Corpus probe: only Shubie qualifies -- StarHardware's
    # prose stream scatters 291 finishes across indents (dominant 23%),
    # every other stream already carries a band or header zone.
    if "finish" not in zones:
        fin_xs = Counter()
        words_of_f = {g["anchor"]: g["words"] for g in geometry}
        for b in out_blocks:
            for r in b["rows"]:
                if not r["finish"]:
                    continue
                for w in words_of_f.get(r["anchor"], []):
                    if w["text"] == r["finish"]:
                        fin_xs[round(w["x0"], 1)] += 1
                        break
        fc = _dominant(fin_xs)
        if fc and sum(fin_xs.values()) >= 5:
            x, _n = fc
            members = [xx for xx in fin_xs if abs(xx - x) <= 2.0]
            zones = dict(zones)
            zones["finish"] = {"lo": round(min(members) - 2, 2), "hi": None,
                               "support": _n, "from": "values"}
    if "finish" in zones and "mfr" in zones             and zones["finish"]["hi"] is None             and zones["finish"]["lo"] < zones["mfr"]["lo"]:
        zones = dict(zones)
        zones["finish"] = dict(zones["finish"], hi=zones["mfr"]["lo"])
    desc_xs = Counter()
    words_of = {g["anchor"]: g["words"] for g in geometry}
    n_rows = 0
    for b in out_blocks:
        for r in b["rows"]:
            n_rows += 1
            rest0 = (r["rest"].split() or [None])[0]
            if not rest0:
                continue
            for w in words_of.get(r["anchor"], []):
                if w["text"] == rest0:
                    desc_xs[round(w["x0"], 1)] += 1
                    break
    if headers.get("description") is not None:
        desc_lo = headers["description"] - 2
    else:
        dc = _dominant(desc_xs)
        if not dc:
            return zones
        desc_lo = dc[0] - 2
    slot_lo = min([z["lo"] for k, z in zones.items()
                   if k in ("finish", "mfr")] or [1e9])
    if headers.get("catalog") is not None:
        cat_lo = headers["catalog"] - 2
        support = sum(desc_xs.values())
    else:
        xs = []
        for g in geometry:
            prev_x1 = None
            for w in g["words"]:
                if (prev_x1 is None or w["x0"] - prev_x1 >= 12.0)                         and desc_lo + 25 <= w["x0"] < slot_lo - 10:
                    xs.append(w["x0"])
                prev_x1 = w.get("x1", w["x0"] + 4.0)
        xs.sort()
        floor = max(5, 0.25 * n_rows, 0.10 * len(geometry))
        if len(xs) < floor:
            return zones
        cat_lo = round(xs[int(0.02 * len(xs))] - 2, 2)
        support = len(xs)
    if cat_lo - desc_lo < 50:
        return zones
    inside = sum(n for x, n in desc_xs.items() if desc_lo <= x < cat_lo)
    if not desc_xs or inside < 0.70 * sum(desc_xs.values()):
        return zones
    hi = slot_lo if slot_lo < 1e9 else None
    zones = dict(zones)
    zones["description"] = {"lo": round(desc_lo, 2), "hi": round(cat_lo, 2),
                            "support": sum(desc_xs.values()),
                            "from": "header" if "description" in headers
                            else "cluster"}
    zones["catalog"] = {"lo": round(cat_lo, 2), "hi": hi,
                        "support": support,
                        "from": "header" if "catalog" in headers
                        else "percentile"}
    return zones


# --- column bands ----------------------------------------------------------
#
# The vocabulary answers "is this string a known value"; the band answers
# "does this token sit in the column".  Probe 2026-08-18 over the corpus:
# 15/18 streams put every accepted slot value at ONE x0 (Gerrard: 386/386
# rows at 535.7); of 168 vocabulary-rejected values, 139 sat within 0.4pt
# of their band while true negatives sat 9pt+ off (StarHardware CP).  So
# the band is the second acceptance evidence for values the vocabulary
# cannot know: legend gaps (Gerrard mfr legend is names-without-codes),
# surface mismatches (US32D vs legend 32D), sub-floor frequency (DE x1),
# non-BHMA shapes (ANCLR, 630-316, BBLK) -- the column position is the
# schedule's own per-row declaration and needs no legend at all.

BAND_TOLERANCE = 2.0   # pt; in-band |dx| measured <= 0.4, nearest negative 9.0
BAND_MIN_SUPPORT = 3   # accepted rows that must agree on the modal x0
BAND_MIN_SHARE = 0.9   # modal share of the accepted sample (HFH 0.89 -> none)


def tail_word(words: list[dict], toks: list[str], slot: int):
    """Geometry word at negative token index `slot`, or None when the
    pdfplumber tokenization does not mirror the line's tail token."""
    if len(words) < abs(slot) or len(toks) < abs(slot):
        return None
    w = words[slot]
    return w if w["text"] == toks[slot] else None


def induce_bands(out_blocks: list[dict], geometry: list[dict],
                 schema: dict) -> dict:
    """Per-role column x0, measured from the vocabulary-accepted rows.

    Stitched rows are excluded (their value arrived from another line),
    and a row only votes when the word at the slot equals the accepted
    value.  A band exists when >= BAND_MIN_SUPPORT rows agree on one x0
    and that x0 carries >= BAND_MIN_SHARE of the sample -- streams that
    fail (HFH multi-template) honestly get no band and keep today's
    vocabulary-only behaviour."""
    words_of = {g["anchor"]: g["words"] for g in geometry}
    bands = {}
    for role in ("mfr", "finish"):
        slot = schema.get(f"{role}_slot")
        if slot is None:
            continue
        xs = Counter()
        for b in out_blocks:
            for r in b["rows"]:
                if r[role] is None or r["stitched"]:
                    continue
                words = words_of.get(r["anchor"])
                if not words:
                    continue
                w = tail_word(words, norm(r["text"]).split(), slot)
                if w is not None and w["text"] == r[role]:
                    xs[round(w["x0"], 1)] += 1
        if not xs:
            continue
        x0, support = xs.most_common(1)[0]
        if support >= BAND_MIN_SUPPORT \
                and support / sum(xs.values()) >= BAND_MIN_SHARE:
            bands[role] = {"slot": slot, "x0": x0, "tol": BAND_TOLERANCE,
                           "support": support, "n": sum(xs.values())}
    return bands


def mark_column_filled(out_blocks: list[dict], geometry: list[dict],
                       schema: dict, bands: dict) -> int:
    """Recover the token assign_slots silently cut.

    rest = tokens before the innermost ACCEPTED slot, so a failed outer
    token is trimmed with the tail ("... 630 DE": finish 630 accepted at
    -2 cuts DE at -1 -- not in rest, not in any flag, gone).  When that
    token sits at its role's band x0, record it as `column_filled` on the
    row for 3c to admit.  The row's own mfr/finish stay null here: the 3c
    prompt renders them, and the prompt must not change (cache
    invariance)."""
    words_of = {g["anchor"]: g["words"] for g in geometry}
    n = 0
    for b in out_blocks:
        for r in b["rows"]:
            if r["stitched"] or r.get("by_others"):
                continue
            toks = norm(r["text"]).split()
            cut = max((abs(schema[f"{other}_slot"])
                       for other in ("mfr", "finish")
                       if r[other] is not None
                       and schema.get(f"{other}_slot") is not None),
                      default=0)
            for role in ("mfr", "finish"):
                slot = schema.get(f"{role}_slot")
                band = bands.get(role)
                if (r[role] is not None or slot is None or band is None
                        or abs(slot) >= cut or len(toks) < abs(slot)):
                    continue
                tok = toks[slot]
                if tok.upper() in STOP_TOKENS \
                        or tok.upper().strip(".,") in BY_ROLE_NOUNS:
                    continue
                w = tail_word(words_of.get(r["anchor"], []), toks, slot)
                if w is None or abs(w["x0"] - band["x0"]) > band["tol"]:
                    continue
                r.setdefault("column_filled", {})[role] = tok
                n += 1
    return n


# --- per-stream run --------------------------------------------------------

def block_members(blocks: list[dict], content: list[dict]) -> dict:
    by_pos = {(r["page"], r["line"]): r for r in content}
    members = {}
    for b in blocks:
        rows = []
        for s in b["spans"]:
            for ln in range(s["lines"][0], s["lines"][1] + 1):
                if (s["page"], ln) in by_pos:
                    rows.append(by_pos[(s["page"], ln)])
        members[b["seq"]] = rows
    return members


def process_stream(blocks_path: Path, dossier: dict, out_dir: Path) -> dict:
    meta, blocks, content = load_stream_blocks(blocks_path)
    name = blocks_path.stem.replace(".blocks", "")
    induced = dossier["streams"][name]
    legend = dossier["pdfs"][induced["source_pdf"]]["legend"]
    members = block_members(blocks, content)

    # pass 1: repair split rows, then build the vocabulary from the repaired
    # sample. The tail lines themselves seed the repair -- they are pure
    # (finish, mfr) evidence, so "is this row already complete?" is answered
    # against the seed set rather than token shape (shape alone let catalog
    # tails like "188SBK PSA" pose as complete and poisoned the vocabulary).
    seed_fin, seed_mfr = set(legend["finish"]), set(legend["mfr"])
    for b in blocks:
        body = members[b["seq"]][1:]
        for rec in body[head_length(body):]:
            toks = norm(rec["text"]).split()
            if looks_dropped_tail(toks):
                seed_fin.add(toks[0])
                seed_mfr.add(toks[1])

    def complete(row: list[str]) -> bool:
        return (len(row) >= 2
                and row[-1] in seed_mfr and row[-2] in seed_fin)

    sample = []
    for b in blocks:
        body = members[b["seq"]][1:]
        last = None
        for rec in body[head_length(body):]:
            toks = norm(rec["text"]).split()
            if last is not None and looks_dropped_tail(toks) \
                    and not complete(last):
                last.extend(toks)
                continue
            split = qty_split(" ".join(toks))
            last = list(split[2]) if split and len(split[2]) >= 2 else None
            if last is not None:
                sample.append(last)

    schema = dict(induced["column_schema"])
    reinduced = None
    if any(v is None for v in schema.values()):
        candidate = induce_stream(sample, set(legend["mfr"]),
                                  set(legend["finish"]))["column_schema"]
        if candidate != schema:
            reinduced = {"from": schema, "to": candidate}
            schema = candidate
    vocab = build_vocab(sample, schema, legend)

    # pass 2: extract, stitch, flag
    out_blocks = []
    for b in blocks:
        rec = extract_block(b, members[b["seq"]], vocab)
        stitch(rec, vocab)
        mark_broken(rec)
        out_blocks.append(rec)

    # geometry: every line of a broken block (scrambled-row regrouping needs
    # the full neighbourhood) + every component row and its stitch sources
    # (column bands need the slot token's x0 on ordinary rows too).
    wanted = defaultdict(dict)
    for b, rec in zip(blocks, out_blocks):
        # every line of every block: zone fill (3c) needs word x0 on the
        # rowless note/unresolved lines too (HFH's unit-led by-others rows,
        # JC Ryan's qty-less hinge rows), and owning words line-by-line
        # keeps a wrapped cell's words from being vacuumed into whichever
        # neighbour happened to be the only wanted line (JC p32-L15).
        for line in members[b["seq"]]:
            wanted[line["page"]][line["line"]] = line
    geometry = geometry_for(meta["source_pdf"], wanted) if wanted else []
    bands = induce_bands(out_blocks, geometry, schema)
    n_column_filled = mark_column_filled(out_blocks, geometry, schema, bands)
    slot_zones = induce_slot_zones(out_blocks, geometry, bands, vocab)
    slot_zones = induce_field_zones(
        slot_zones, out_blocks, geometry,
        header_anchors(meta["source_pdf"], meta["source_stream"]))

    rows = [r for b in out_blocks for r in b["rows"]]
    summary = {
        "stream": name,
        "file": meta["file"],
        "column_schema": schema,
        "reinduced": reinduced,
        "vocabulary": {role: {"slot": vocab[role]["slot"],
                              "legend": sorted(vocab[role]["legend"]),
                              "distribution": sorted(vocab[role]["distribution"])}
                       for role in ("mfr", "finish")},
        "n_blocks": len(out_blocks),
        "n_rows": len(rows),
        "n_mfr": sum(1 for r in rows if r["mfr"]),
        "n_finish": sum(1 for r in rows if r["finish"]),
        "n_stitched": sum(len(r["stitched"]) for r in rows),
        "n_door_lines": sum(len(b["door_lines"]) for b in out_blocks),
        "n_note_lines": sum(len(b["note_lines"]) for b in out_blocks),
        "n_unresolved": sum(len(b["unresolved"]) for b in out_blocks),
        "broken_blocks": [b["set_id"] for b in out_blocks if b["broken"]],
        "flags": dict(sorted(Counter(f for b in out_blocks
                                     for f in b["flags"]).items())),
        "partition_mismatches": [b["set_id"] for b in out_blocks
                                 if b["partition"]["n_body_lines"]
                                 != b["partition"]["consumed"]],
        "n_geometry_lines": len(geometry),
        "column_bands": bands,
        "slot_zones": slot_zones,
        "n_column_filled": n_column_filled,
    }

    meta_out = {
        "type": "meta", "file": meta["file"], "region": meta["region"],
        "source_stream": meta["source_stream"], "source_pdf": meta["source_pdf"],
        "source_blocks": str(blocks_path),
        "generated_by": "step3_rules v0.1 (3b)",
        "column_schema": schema,
        "reinduced": reinduced,
        "column_bands": bands,
        "slot_zones": slot_zones,
        "vocabulary": summary["vocabulary"],
        "rules": {k: summary[k] for k in
                  ("n_blocks", "n_rows", "n_mfr", "n_finish", "n_stitched")},
    }
    with (out_dir / f"{name}.rules.jsonl").open(
            "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(meta_out, ensure_ascii=False, sort_keys=True) + "\n")
        for rec in out_blocks:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    # always written, empty when no block is broken (Forest Park, 2026-08-18)
    # -- every stream ships the same artifact set
    with (out_dir / f"{name}.geometry.jsonl").open(
            "w", encoding="utf-8", newline="\n") as fh:
        for rec in geometry:
            fh.write(json.dumps(rec, ensure_ascii=False,
                                sort_keys=True) + "\n")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("in_root", nargs="?", default="data/out/step2",
                    help="step-2 output root (default: data/out/step2)")
    ap.add_argument("--dossiers", default="data/out/step3",
                    help="step-3a dossier root (default: data/out/step3)")
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
        dossier_path = Path(args.dossiers) / proj.name / "dossier.json"
        if not dossier_path.exists():
            print(f"missing dossier for {proj.name} -- run step3_dossier first",
                  file=sys.stderr)
            return 2
        dossier = json.loads(dossier_path.read_text("utf-8"))
        out_dir = out_root / proj.name
        out_dir.mkdir(parents=True, exist_ok=True)
        for stale in prune_stale_outputs(
                out_dir,
                {b.name.replace(".blocks.jsonl", suf)
                 for b in proj.glob("*.blocks.jsonl")
                 for suf in (".rules.jsonl", ".geometry.jsonl")},
                (".rules.jsonl", ".geometry.jsonl")):
            print(f"  removed stale rules/geometry: {stale}")

        print(f"project: {proj.name}")
        proj_summaries = []
        for blocks_path in sorted(proj.glob("*.blocks.jsonl")):
            s = process_stream(blocks_path, dossier, out_dir)
            print(f"  {s['stream']}")
            print(f"    rows {s['n_rows']}  mfr {s['n_mfr']}  "
                  f"finish {s['n_finish']}  stitched {s['n_stitched']}  "
                  f"doors {s['n_door_lines']}  notes {s['n_note_lines']}  "
                  f"unresolved {s['n_unresolved']}")
            print(f"    broken {len(s['broken_blocks'])}/{s['n_blocks']} blocks"
                  f"  geometry lines {s['n_geometry_lines']}  "
                  f"flags {s['flags']}")
            if s["partition_mismatches"]:
                print("    WARN: line partition mismatch in "
                      f"{s['partition_mismatches']}")
            proj_summaries.append(s)

        (out_dir / "rules_report.json").write_text(
            json.dumps({"project": proj.name,
                        "generated_by": "step3_rules v0.1 (3b)",
                        "streams": proj_summaries},
                       ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
