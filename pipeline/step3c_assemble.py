"""Step 3c of the v0.2 funnel: LLM reading-assembly inside each block.

  step-2 blocks (location spans) + step-3b rules.jsonl (mechanical fields,
  line buckets, stitches) + step-3a dossier (book legend, qty notes) +
  step-3b geometry.jsonl (word-level x0 for broken blocks)
     |  one LLM call per block: group lines into components, split each
     |  row's `rest` into description vs catalog_number, attach notes,
     |  demote pseudo-components, regroup scrambled rows by word x0.
     v
  <stream>.sets.jsonl  (assembled sets, SPEC-shaped: set_number /
  description / location / components[qty, description, catalog_number,
  mfr, finish, notes]) + assembly_report.json per project.

This is the LLM half of decision #3 (option C).  The rule side (3b) owns
the mechanical fields; this side owns the judgment calls it marked but did
not make.  The reconciliation contract (step3-extraction.md section 10):

* The LLM never overwrites a mechanical field.  qty/unit/mfr/finish are
  copied from the block's rule rows; the LLM does not even output them.
  The one sanctioned exception is a slot the rules left null ("text is
  kept, 3c may rescue"): the LLM may report mfr_from_text/finish_from_text
  read from that row's own text, and the value is admitted only if it
  validates against the book's rule-side vocabulary (legend union
  distribution) OR sits at the stream's induced column band x0 in the
  row's own word geometry (slot_recovered_from_column) -- LLM reads,
  rules verify.  3b additionally marks tokens its slot cut silently
  (column_filled); they are admitted here with slot_filled_from_column.
* Anchor census: every semantic line of the block (rule rows, note lines,
  unresolved fragments) must be consumed exactly once -- by one component,
  one set note, or one demotion.  A row the LLM failed to cite is
  reassembled from the rule baseline (split_hint) and flagged loudly
  (llm_missed_row) instead of being dropped; phantom or duplicate anchors
  are stripped and flagged.  Same partition philosophy as steps 2/3b.
* Token conservation: the LLM is told to copy text verbatim, and we check
  it -- each component's description + catalog_number + notes must carry
  exactly the tokens of its source lines (as a multiset: scrambled-row
  regrouping reorders legitimately).  Strict match, then a
  punctuation/case-insensitive retry, then fall back to the split_hint
  baseline with confidence low.  Nothing the model paraphrases can leak
  into the output silently.
* Determinism: all randomness lives behind the disk cache (llm_client);
  a second run replays every response byte for byte.  Blocks are
  dispatched to a thread pool but written in seq order.

Confidence on the assembly (feeds the bonus-2 story): high = strict
conservation, single-row component, no flags; medium = normalized
conservation, multi-line regrouping, or a rescued slot; low = any
fallback, missed row, or conservation failure.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from step1_locate import prune_stale_outputs  # noqa: E402
from llm_client import LLMClient  # noqa: E402

GENERATED_BY = "step3c_assemble v0.1 (3c)"

# --- LLM contract ------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the reading-assembly stage of a door-hardware schedule extraction \
pipeline.  You receive one block (one hardware set).  Deterministic rules \
already extracted the mechanical fields of every component row (qty, unit, \
manufacturer code, finish code) and bucketed every line; your job is only \
the reading judgment the rules could not make:

- Group the listed lines into components.  Most component rows are one \
component each.  A fragment line belongs to the component it visually \
continues (broken catalog text, a dangling qualifier).  When word x0 \
geometry is provided, the block contains scrambled table rows: one visual \
row was split into several lines, and x0 (the left edge of each word) is \
the only reliable evidence of which column a word belongs to -- regroup \
those lines into their true component and use x0, not line order, to \
decide what is description and what is catalog text.
- Split each component's remaining text into `description` (what the item \
is, in human words) vs `catalog_number` (model/part codes, sizes, option \
codes -- the machine string).  The digit-token index given per row is the \
rules' naive guess, not the answer; lines with no digits still split.  \
When the catalog position holds a prose instruction instead of codes \
(e.g. "Per Detail / Type as Req"), it is a note: catalog_number stays \
null.
- Notes: parenthetical asides (e.g. "(mount inside room)"), positioning \
qualifiers (e.g. "@ 42\\" Top Down"), by-others attributions (e.g. "BY \
DIV 28"), and orphan commentary attach to their component as `notes` \
entries, not to catalog_number.  Standalone prose lines (installation \
rules, OPERATION descriptions, block-level legends) group into coherent \
`set_notes`; join lines that read as one sentence or one paragraph.
- Pseudo-components: a qty-led line that does not name a suppliable piece \
of hardware -- a drawing/schematic reference, a description of behaviour \
(e.g. "1 Door Opens Automatically w/ Auto Operator"), a pure coordination \
instruction -- gets `pseudo: true` with a short reason.  A real piece of \
hardware supplied by another party (e.g. a card reader marked "BY DIV \
28" or "By Security Contractor") is NOT pseudo: keep it as a component \
with the attribution as a note.

Hard rules:
- Copy text VERBATIM.  Never paraphrase, correct spelling, or drop \
tokens.  Every token of every line you cite must land in exactly one of: \
description, catalog_number, a note, or the set_note text.
- Every line anchor in the block must be used exactly once: in exactly \
one component's `anchors`, or exactly one set_note's `anchors`.
- Do not restate qty/unit/mfr/finish -- the rules own them.  Exception: \
when a row is shown with mfr=null or finish=null and the value is \
readable in that row's own text (or in the scrambled-row group it belongs \
to), report it in `mfr_from_text` / `finish_from_text`; the pipeline \
validates it against the book vocabulary before accepting.
- If a component's text names its manufacturer in prose (the block has \
no manufacturer column), report the name in `mfr_hint` -- but the tokens \
STAY in description/catalog_number; the hint is a report, not a move.
- `description` and `catalog_number` are null when the component has no \
tokens for them.  Empty notes are [], not null."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "components": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "anchors": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": ["string", "null"]},
                    "catalog_number": {"type": ["string", "null"]},
                    "notes": {"type": "array", "items": {"type": "string"}},
                    "pseudo": {"type": "boolean"},
                    "pseudo_reason": {"type": ["string", "null"]},
                    "mfr_from_text": {"type": ["string", "null"]},
                    "finish_from_text": {"type": ["string", "null"]},
                    "mfr_hint": {"type": ["string", "null"]},
                },
                "required": ["anchors", "description", "catalog_number",
                             "notes", "pseudo", "pseudo_reason",
                             "mfr_from_text", "finish_from_text", "mfr_hint"],
                "additionalProperties": False,
            },
        },
        "set_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "anchors": {"type": "array", "items": {"type": "string"}},
                    "text": {"type": "string"},
                },
                "required": ["anchors", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["components", "set_notes"],
    "additionalProperties": False,
}

# --- prompt building ---------------------------------------------------------


def book_context(dossier: dict, meta: dict) -> str:
    pdf = dossier["pdfs"].get(meta["source_pdf"], {})
    legend = pdf.get("legend", {"mfr": {}, "finish": {}})
    vocab = meta["vocabulary"]
    lines = ["BOOK CONTEXT"]
    for role, label in (("mfr", "manufacturer"), ("finish", "finish")):
        codes = legend.get(role) or {}
        if codes:
            listed = ", ".join(f"{k}={v}" for k, v in sorted(codes.items()))
            lines.append(f"- {label} codes (book legend): {listed}")
        else:
            dist = vocab[role]["distribution"]
            if dist:
                lines.append(f"- no {label} legend in this book; codes seen "
                             f"in that column: {', '.join(dist)}")
            else:
                lines.append(f"- this book has no {label} column")
    schema = meta["column_schema"]
    slot_names = {-1: "last token", -2: "second-to-last token",
                  -3: "third-to-last token"}
    placed = [f"{slot_names.get(slot, f'slot {slot}')} = {role}"
              for role, slot in (("manufacturer", schema.get("mfr_slot")),
                                 ("finish", schema.get("finish_slot")))
              if slot is not None]
    if placed:
        lines.append(f"- induced column layout on component rows: "
                     f"{'; '.join(placed)} (already stripped from `rest`)")
    for note in pdf.get("qty_notes") or []:
        lines.append(f"- book quantity note: {note['text']}")
    return "\n".join(lines)


RE_ANCHOR = re.compile(r"^p(\d+)-L(\d+)$")


def anchor_key(anchor: str) -> tuple[int, int]:
    m = RE_ANCHOR.match(anchor)
    return (int(m.group(1)), int(m.group(2)))


def line_listing(block: dict) -> tuple[str, list[str]]:
    """Render the block's semantic lines in READING order (page, line) --
    scrambled-row regrouping only works if the LLM sees the document order,
    not the rule buckets; return (text, required_anchors in that order)."""
    entries = []
    for r in block["rows"]:
        mech = (f"qty={r['qty']} unit={r['unit']} "
                f"mfr={r['mfr']} finish={r['finish']}")
        hint = "none" if r["split_hint"] is None else str(r["split_hint"])
        rest = r["rest"] if r["rest"] else "(no text left after slot removal)"
        entries.append((r["anchor"],
                        f"{r['anchor']} | component row ({mech}) | "
                        f"rest: {rest} | first-digit-token-index: {hint}"))
    for n in block["note_lines"]:
        entries.append((n["anchor"], f"{n['anchor']} | {n['kind']} | {n['text']}"))
    for u in block["unresolved"]:
        if u["kind"] == "blank" or not u["text"].strip():
            continue
        entries.append((u["anchor"],
                        f"{u['anchor']} | unassigned fragment ({u['kind']}) | "
                        f"{u['text']}"))
    entries.sort(key=lambda e: anchor_key(e[0]))
    return ("LINES (anchor | kind | content):\n"
            + "\n".join(e[1] for e in entries),
            [e[0] for e in entries])


def geometry_section(block: dict, geometry: dict) -> str:
    anchors = sorted((a for a in
                      ([r["anchor"] for r in block["rows"]]
                       + [n["anchor"] for n in block["note_lines"]]
                       + [u["anchor"] for u in block["unresolved"]])
                      if a in geometry), key=anchor_key)
    if not block["broken"] or not anchors:
        return ""
    lines = ["", "WORD GEOMETRY (x0 = left edge of each word; use it to "
                 "decide column membership and regroup scrambled rows):"]
    for a in anchors:
        words = " ".join(f"{w['text']}@{w['x0']:.0f}"
                         for w in geometry[a]["words"])
        lines.append(f"{a}: {words}")
    return "\n".join(lines)


def build_prompt(block: dict, meta: dict, dossier: dict,
                 geometry: dict) -> tuple[str, list[str]]:
    listing, required = line_listing(block)
    head = [book_context(dossier, meta), ""]
    head.append(f"BLOCK set {block['set_id']} (family {block['family']})")
    if block["trailer"]:
        head.append(f"header trailer: {block['trailer']}")
    if block["description"]:
        head.append(f"set description: {block['description']}")
    for p in block["properties"]:
        head.append(f"properties line: {p}")
    head.append("")
    head.append(listing)
    geo = geometry_section(block, geometry)
    if geo:
        head.append(geo)
    head.append("")
    head.append("Assemble this block.")
    return "\n".join(head), required

# --- reconciliation ----------------------------------------------------------

RE_PUNCT = re.compile(r"[^\w]+", re.UNICODE)

# unit spellings seen in the corpus (HFH "EA", Star "Ea.", Shubie "EA-R");
# zone fill only consults this list when the unit zone measured a word there
UNIT_WORDS = {"EA", "EA.", "EA-R", "Ea", "Ea.", "SET", "SETS", "PR", "PRS",
              "PAIR", "PAIRS", "EACH"}


def tokens(text: str | None) -> list[str]:
    return text.split() if text else []


def norm_tokens(toks: list[str]) -> list[str]:
    out = []
    for t in toks:
        t = RE_PUNCT.sub("", t).casefold()
        if t:
            out.append(t)
    return out


def source_tokens(anchors: list[str], line_index: dict) -> list[str]:
    toks = []
    for a in anchors:
        kind, rec = line_index[a]
        toks += tokens(rec["rest"] if kind == "row" else rec["text"])
    return toks


def output_tokens(comp: dict) -> list[str]:
    toks = tokens(comp["description"]) + tokens(comp["catalog_number"])
    for n in comp["notes"]:
        toks += tokens(n)
    return toks


def conservation(comp_out: list[str], src: list[str]) -> str:
    if Counter(comp_out) == Counter(src):
        return "strict"
    no, ns = norm_tokens(comp_out), norm_tokens(src)
    if Counter(no) == Counter(ns):
        return "normalized"
    # Splicing a hyphen-broken catalog code ("110MD-" + "CON" ->
    # "110MD-CON") changes the token boundaries but not the characters.
    if "".join(no) == "".join(ns):
        return "normalized"
    return "failed"


def baseline_split(row: dict) -> tuple[str | None, str | None]:
    """The rule-side fallback: split `rest` at the first digit token."""
    toks = tokens(row["rest"])
    if not toks:
        return None, None
    hint = row["split_hint"]
    if hint is None or hint <= 0 or hint > len(toks):
        return " ".join(toks), None
    return (" ".join(toks[:hint]) or None,
            " ".join(toks[hint:]) or None)


def validate_slot(value: str | None, role: str, meta: dict,
                  words_lists: list[list[dict]] | None = None) -> str | None:
    """Admission for a slot value the rules left null.  Two evidence
    lines: the book's vocabulary (legend union distribution), or -- when
    3b induced a column band for this stream -- the value sitting at the
    band's x0 in the component's own word geometry.  The band admits what
    no list can know (legend gaps, sub-floor frequency, non-BHMA shapes:
    probe 2026-08-18 found 139/168 rejected values within 0.4pt of their
    band); a value off the band stays rejected exactly as before."""
    if not value:
        return None
    voc = meta["vocabulary"][role]
    if value in voc["legend"] or value in voc["distribution"]:
        return "vocabulary"
    band = (meta.get("column_bands") or {}).get(role)
    if band and words_lists:
        vtoks = value.split()
        for words in words_lists:
            for i in range(len(words) - len(vtoks) + 1):
                if all(words[i + j]["text"] == vtoks[j]
                       for j in range(len(vtoks))) \
                        and abs(words[i]["x0"] - band["x0"]) <= band["tol"]:
                    return "column"
    # zone-interval arm (screenshot round, 2026-08-19): a BOUNDED slot
    # zone admits a claimed value whose every token was measured inside
    # it -- Shubie has no bands (2 pages, values-seeded zone) and SAT's
    # finish band was poison-blocked (veto round), yet WHT sits
    # dead-centre in both books' finish columns.  Tokens are checked
    # per-occurrence, not per-line: Livelle's scrambled rows print
    # "Dark" and "Bronze" on separate lines, both at the finish
    # column's x=441.7, and any ink inside a bounded slot column IS
    # that column's content.  Unbounded zones stay out: the
    # rightmost-cluster mfr zone has no right edge, and admission
    # through it would take any prose tail (the asymmetric evidence
    # standard cuts both ways).
    zone = (meta.get("slot_zones") or {}).get(role)
    if zone and zone.get("hi") is not None and words_lists:
        def in_zone(tok: str) -> bool:
            return any(w["text"] == tok
                       and zone["lo"] <= w["x0"] < zone["hi"]
                       for words in words_lists for w in words)
        if all(in_zone(t) for t in value.split()):
            return "column"
    return None


def rotated_lines(geometry: dict) -> set[str]:
    """Anchors whose ink runs vertically up the sheet edge.

    StarHardware prints its confidentiality notice rotated 90 degrees
    along the border of every sheet -- one word per stream line, 1,152
    lines across both streams, and pdfplumber emits each rotated word's
    characters in REVERSE ("document)" -> ")tnemucod").  The LLM routes
    them into one garbage set note per set (62 sets delivered it).
    Geometry is the tell: a rotated word's line bbox is taller than wide
    and only 6-7pt wide, and the words stand in a narrow x-column shared
    by many lines of the page.  The stack requirement (>= 3 tall-narrow
    lines within 2pt of the same x0) plus the 8pt width cap spare the
    corpus's one tall-narrow REAL word (Morris p233 'or', 9.7pt wide,
    alone on its page -- probe 2026-08-19)."""
    by_page: dict[str, list] = defaultdict(list)
    for a, g in geometry.items():
        x0, top, x1, bot = g["bbox"]
        alnum = sum(ch.isalnum() for w in g["words"] for ch in w["text"])
        by_page[a.split("-")[0]].append(
            (a, x0, x1 - x0, bot - top, alnum))
    out: set[str] = set()
    for lines in by_page.values():
        cands = sorted((x0, a) for a, x0, w, h, alnum in lines
                       if h > w and w <= 8.0 and alnum >= 2)
        cols: list[list] = []
        for x0, a in cands:
            if cols and x0 - cols[-1][-1][0] <= 2.0:
                cols[-1].append((x0, a))
            else:
                cols.append([(x0, a)])
        for col in cols:
            if len(col) < 3:
                continue
            cx = sum(x for x, _ in col) / len(col)
            out.update(a for a, x0, w, h, _ in lines
                       if abs(x0 - cx) <= 2.0 and w <= 8.0)
    return out


def assembly_confidence(level: str, anchors: list[str],
                        flags: list[str]) -> str:
    if level == "failed" or any(
            f.startswith(("phantom_anchor", "duplicate_anchor"))
            for f in flags):
        return "low"
    if (level == "normalized" or len(anchors) > 1
            or any(f.startswith(("slot_recovered", "slot_filled",
                                 "slot_echo_dropped", "multi_row",
                                 "column_rerouted", "slot_vetoed",
                                 "component_without_row"))
                   for f in flags)):
        return "medium"
    return "high"

# --- per-block assembly ------------------------------------------------------


def mechanical_fields(rows: list[dict]) -> dict:
    """Copy qty/unit/mfr/finish from the component's rule rows."""
    if not rows:
        return {"qty": None, "unit": None, "mfr": None, "finish": None,
                "confidence": {}}
    r = rows[0]
    return {"qty": r["qty"], "unit": r["unit"], "mfr": r["mfr"],
            "finish": r["finish"], "confidence": dict(r["confidence"])}


def assemble_block(block: dict, meta: dict, dossier: dict, geometry: dict,
                   spans: list, client: LLMClient,
                   rotated: set[str] | None = None) -> dict:
    rotated = rotated or set()
    line_index = {}
    stitched_of = {}
    for r in block["rows"]:
        line_index[r["anchor"]] = ("row", r)
        stitched_of[r["anchor"]] = [s["anchor"] for s in r["stitched"]]
    for n in block["note_lines"]:
        line_index[n["anchor"]] = ("note", n)
    for u in block["unresolved"]:
        line_index[u["anchor"]] = ("unresolved", u)

    out = {
        "type": "set", "seq": block["seq"], "set_id": block["set_id"],
        "family": block["family"], "header_anchor": block["header_anchor"],
        "location": spans,
        "description": block["description"] or block["trailer"] or None,
        "properties": block["properties"],
        "doors": block["door_lines"],
        "components": [], "demoted": [], "set_notes": [],
        "unassigned": [{"anchor": u["anchor"], "kind": u["kind"],
                        "text": u["text"]} for u in block["unresolved"]
                       if u["kind"] == "blank" or not u["text"].strip()],
        "empty": block["empty"],
        "flags": [],
        "llm": None,
        "reconciliation": None,
    }

    if block["empty"] or not line_index:
        out["llm"] = {"skipped": True}
        out["reconciliation"] = {"census": "trivial",
                                 "conservation": {"strict": 0,
                                                  "normalized": 0,
                                                  "failed": 0}}
        return out

    user, required = build_prompt(block, meta, dossier, geometry)
    result = client.complete(SYSTEM_PROMPT, user, OUTPUT_SCHEMA)
    data = result["data"]
    # cache_hit is a property of the RUN, not of the data -- it goes to the
    # report only, so a warm re-run reproduces sets.jsonl byte for byte.
    out["_cache_hit"] = result["cache_hit"]
    out["llm"] = {"skipped": False,
                  "fingerprint": result["fingerprint"],
                  "usage": result["usage"]}

    required_set = set(required)
    consumed = set()
    recon = {"census": "ok", "phantom_anchors": 0, "duplicate_anchors": 0,
             "missed_lines": 0,
             "conservation": {"strict": 0, "normalized": 0, "failed": 0},
             "slot_recovered": 0, "slot_rejected": 0, "slot_column_filled": 0}

    def claim(anchors: list[str], flags: list[str]) -> list[str]:
        kept = []
        for a in anchors:
            if a not in required_set:
                recon["phantom_anchors"] += 1
                flags.append(f"phantom_anchor:{a}")
            elif a in consumed:
                recon["duplicate_anchors"] += 1
                flags.append(f"duplicate_anchor:{a}")
            else:
                consumed.add(a)
                kept.append(a)
        return kept

    for comp in data["components"]:
        flags: list[str] = []
        anchors = claim(comp["anchors"], flags)
        if not anchors:
            out["flags"].append("component_with_no_valid_anchors")
            continue
        # rotated border words swept into a component (screenshot round):
        # the LLM folds the odd border word into a long multi-line group
        # (15 anchors across StarHardware p53-113).  Drop the anchor and
        # excise that line's tokens from whichever field carries them --
        # source and output shrink together, so conservation still
        # balances and the fallback never has to fire.
        rot = [a for a in anchors if a in rotated]
        if rot:
            anchors = [a for a in anchors if a not in rotated]
            if not anchors:
                out["flags"].append(
                    f"component_rotated_dropped:{len(rot)}")
                continue
            comp = dict(comp)
            comp["notes"] = list(comp["notes"])
            for t in (t for a in rot
                      for t in tokens(line_index[a][1]["text"])):
                for key in ("description", "catalog_number"):
                    v = comp[key]
                    if v and t in v.split():
                        toks2 = v.split()
                        toks2.remove(t)
                        comp[key] = " ".join(toks2) or None
                        break
                else:
                    for i, nte in enumerate(comp["notes"]):
                        if t in nte.split():
                            toks2 = nte.split()
                            toks2.remove(t)
                            if " ".join(toks2):
                                comp["notes"][i] = " ".join(toks2)
                            else:
                                comp["notes"].pop(i)
                            break
            flags.append(f"rotated_lines_dropped:{len(rot)}")
        anchors = sorted(anchors, key=anchor_key)
        rows = [line_index[a][1] for a in anchors
                if line_index[a][0] == "row"]
        if len(rows) > 1:
            flags.append("multi_row_component")
        if not rows and not comp["pseudo"]:
            flags.append("component_without_row")

        mech = mechanical_fields(rows)
        geo_words = [geometry[ga]["words"] for ga in
                     list(anchors) + [s for a2 in anchors
                                      for s in stitched_of.get(a2, [])]
                     if ga in geometry]

        src = source_tokens(anchors, line_index)
        # A slot value may legitimately be MISSING from the text output at
        # most once per slot: the LLM rescued it out of a null slot into
        # mfr/finish (scrambled rows -- only honoured when the value will
        # pass vocabulary validation), or it dropped a mid-line echo of the
        # already-assigned slot value (Bridgeport prints the finish twice).
        # A value the LLM reported but also KEPT in the text exempts
        # nothing, and a null slot with a rejected candidate exempts
        # nothing -- dropped tokens there must fail conservation loudly.
        out_toks = output_tokens(comp)
        for role, key in (("mfr", "mfr_from_text"),
                          ("finish", "finish_from_text")):
            if mech[role] is not None:
                val, echo = mech[role], True
            else:
                # Rescue is only honoured on rule-recognized component rows.
                claimed = comp[key] if rows else None
                val, echo = (claimed if claimed
                             and validate_slot(claimed, role, meta, geo_words)
                             else None), False
            if val and Counter(src)[val] > Counter(out_toks)[val]:
                src.remove(val)
                if echo:
                    flags.append(f"slot_echo_dropped:{role}={val}")
        level = conservation(out_toks, src)
        recon["conservation"][level] += 1
        desc, catalog, notes = (comp["description"], comp["catalog_number"],
                                comp["notes"])
        if level == "failed":
            flags.append("conservation_failed")
            if len(anchors) == 1 and rows:
                desc, catalog = baseline_split(rows[0])
                notes = []
                flags.append("fell_back_to_split_hint")

        for role, key in (("mfr", "mfr_from_text"),
                          ("finish", "finish_from_text")):
            claimed = comp[key] if rows else None
            if comp[key] and not rows:
                # row-less rescue (Roselle round, 2026-08-19): no row to
                # validate against, but a measured zone can testify -- the
                # proposal's own words sitting inside the slot's column is
                # the same evidence the fill arm accepts
                zn = (meta.get("slot_zones") or {}).get(role)
                vt = comp[key].split()
                hit = False
                # same asymmetric standard as the veto: acting on a
                # row-less proposal needs band/header-grade evidence
                # (a rightmost-cluster zone re-broke JC's ledger here)
                if zn and zn.get("from") in ("band", "header")                         and mech[role] is None:
                    zhi = zn["hi"] if zn["hi"] is not None else 1e9
                    for lst in geo_words:
                        for i in range(len(lst) - len(vt) + 1):
                            if all(lst[i + j]["text"] == vt[j]
                                   for j in range(len(vt)))                                     and zn["lo"] <= lst[i]["x0"] < zhi:
                                hit = True
                if hit:
                    mech[role] = comp[key]
                    mech["confidence"][role] = "medium"
                    flags.append(
                        f"slot_recovered_from_column:{role}={comp[key]}")
                    recon["slot_recovered"] += 1
                else:
                    flags.append(
                        f"slot_recovery_unvalidatable:{role}={comp[key]}")
            if mech[role] is None and claimed:
                how = validate_slot(claimed, role, meta, geo_words)
                if how:
                    mech[role] = claimed
                    mech["confidence"][role] = "medium"
                    flags.append(
                        f"slot_recovered_from_text:{role}={claimed}"
                        if how == "vocabulary" else
                        f"slot_recovered_from_column:{role}={claimed}")
                    recon["slot_recovered"] += 1
                else:
                    flags.append(f"slot_recovery_rejected:{role}={claimed}")
                    recon["slot_rejected"] += 1

        # 3b marked tokens assign_slots cut silently (inner slot accepted,
        # outer failed, band-aligned): admit them here, where flags live.
        if rows:
            for role, val in sorted(
                    (rows[0].get("column_filled") or {}).items()):
                if mech[role] is None:
                    mech[role] = val
                    mech["confidence"][role] = "medium"
                    flags.append(f"slot_filled_from_column:{role}={val}")
                    recon["slot_column_filled"] += 1

        # --- zone bookkeeping (round A of the column-first plan) ----------
        # Position owns column membership: a word measured inside a slot's
        # zone may fill that slot when the rules and the LLM both left it
        # null (HFH's unit-led by-others rows, JC Ryan's qty-less hinge
        # rows), and a value delivered in a slot leaves the catalog tail
        # when EVERY occurrence of it sits inside the slot's zone (FP's
        # "4420 SERIES US32D ABH"; Bridgeport's inline "104S C26D" echo has
        # an occurrence in catalog territory, so it stays).  Moved values
        # are re-counted as covered, so conservation reports the truth of
        # the delivery, not the location of the ink.
        zones = meta.get("slot_zones") or {}
        zone_flags: list[str] = []

        def zone_words(zone: dict) -> list[dict]:
            hi = zone["hi"] if zone["hi"] is not None else 1e9
            return [w for lst in geo_words for w in lst
                    if zone["lo"] <= w["x0"] < hi
                    and any(ch.isalnum() for ch in w["text"])]

        # position veto (2026-08-19): a slot value whose every measured
        # occurrence sits far OUTSIDE its own column is a rider, not a
        # resident -- "BY DIVISION 28" put 28 into three books' finish
        # vocabularies (BHMA-legal shape, US28 exists) and 389 components
        # delivered it as a finish.  The vocabulary cannot refuse a
        # shape-legal squatter; the coordinate can.  10pt margin keeps the
        # dual-template 4pt jitter safe; any in-zone occurrence keeps the
        # value (Bridgeport prints the finish twice).
        for role in ("mfr", "finish"):
            val, zone = mech[role], zones.get(role)
            # asymmetric evidence standard: admission may ride a rightmost
            # cluster, a VETO may not -- JC's wobble-shadow pages shift the
            # whole mfr column left of the cluster edge and the ungated
            # veto amputated 9 correct names there (2026-08-19)
            if not (val and zone)                     or zone.get("from") not in ("band", "header")                     or any(f.endswith(f":{role}={val}")
                           and "_from_column" in f for f in flags):
                continue
            vt = val.split()
            occ = []
            for lst in geo_words:
                for i in range(len(lst) - len(vt) + 1):
                    if all(lst[i + j]["text"] == vt[j]
                           for j in range(len(vt))):
                        occ.append(lst[i]["x0"])
            if not occ:
                continue
            hi = zone["hi"] if zone["hi"] is not None else 1e9
            if all(x < zone["lo"] - 10 or x >= hi + 10 for x in occ):
                mech[role] = None
                mech["confidence"].pop(role, None)
                zone_flags.append(f"slot_vetoed_off_column:{role}={val}")

        if mech["qty"] is None and zones.get("qty") and not rows:
            cands = {w["text"] for w in zone_words(zones["qty"])
                     if w["text"].replace(".", "").isdigit()}
            if len(cands) == 1:
                mech["qty"] = int(float(cands.pop()))
                zone_flags.append(
                    f"slot_filled_from_column:qty={mech['qty']}")
        if mech["unit"] is None and zones.get("unit"):
            cands = {w["text"] for w in zone_words(zones["unit"])
                     if w["text"] in UNIT_WORDS}
            if len(cands) == 1:
                mech["unit"] = cands.pop()
                zone_flags.append(
                    f"slot_filled_from_column:unit={mech['unit']}")
        for role in ("mfr", "finish"):
            zone = zones.get(role)
            if zone and mech[role] is None:
                cands = {w["text"] for w in zone_words(zone)}
                if len(cands) == 1:
                    mech[role] = cands.pop()
                    mech["confidence"][role] = "medium"
                    zone_flags.append(
                        f"slot_filled_from_column:{role}={mech[role]}")
                    recon["slot_column_filled"] += 1
        moved = True
        while moved and catalog:
            moved = False
            for role in ("mfr", "finish"):
                val, zone = mech[role], zones.get(role)
                if not (val and zone and len(val.split()) == 1):
                    continue
                if not (catalog == val or catalog.endswith(" " + val)):
                    continue
                occ = [w for lst in geo_words for w in lst
                       if w["text"] == val]
                hi = zone["hi"] if zone["hi"] is not None else 1e9
                if occ and all(zone["lo"] <= w["x0"] < hi for w in occ):
                    catalog = catalog[:-len(val)].rstrip() or None
                    zone_flags.append(f"catalog_detached:{role}={val}")
                    moved = True

        # round B: cross-zone reroute between the free-text fields.  A
        # token whose every measured occurrence sits in the OTHER field's
        # column moves there (contiguous runs, document order); a moved
        # catalog-zone run led by BY is attribution prose and goes to
        # notes, matching the by-others convention.  Skips: tokens seen in
        # more than one zone (ambiguous), normalized length < 3 (SJC's
        # letterspaced fragments), and slot-value echoes (the detach and
        # echo machinery own those).
        dzone, czone = zones.get("description"), zones.get("catalog")
        if dzone and czone and (desc or catalog):
            occ_zones: dict[str, set] = {}
            ztab = [(nm, zones[nm]) for nm in
                    ("description", "catalog", "finish", "mfr", "unit")
                    if zones.get(nm)]
            for lst in geo_words:
                for w in lst:
                    for nm, zn in ztab:
                        zhi = zn["hi"] if zn["hi"] is not None else 1e9
                        if zn["lo"] <= w["x0"] < zhi:
                            occ_zones.setdefault(w["text"], set()).add(nm)
                            break
            slot_echo = {RE_PUNCT.sub("", v).casefold()
                         for v in (mech["finish"], mech["mfr"]) if v}

            def sole_zone(tok: str):
                zs = occ_zones.get(tok)
                if not zs or len(zs) != 1:
                    return None
                nt = RE_PUNCT.sub("", tok).casefold()
                if len(nt) < 3 or nt in slot_echo:
                    return None
                return next(iter(zs))

            def split_runs(text: str, wrong_zone: str, own_zone: str):
                # W = proven in the wrong column, R = proven anywhere
                # else, N = unprovable (short, ambiguous, unfound).
                # Neutrals attach to an adjacent W-run, so a phrase like
                # "AS SPECIFIED IN DIVISION 28" travels whole -- the
                # ungapped version stranded desc="... AS IN" and
                # catalog="SPECIFIED DIVISION" (measured 2026-08-19).
                kept, runs, cur, pend = [], [], None, []
                for tok in tokens(text):
                    z = sole_zone(tok)
                    if z == wrong_zone:
                        if cur is None:
                            cur = []
                        cur.extend(pend)
                        pend = []
                        cur.append(tok)
                    elif z is None:
                        pend.append(tok)
                    else:
                        if cur is not None:
                            runs.append(cur)
                            cur = None
                        kept.extend(pend)
                        pend = []
                        kept.append(tok)
                if cur is not None:
                    cur.extend(pend)
                    runs.append(cur)
                else:
                    kept.extend(pend)
                return (" ".join(kept) or None), runs

            new_desc, out_runs = split_runs(desc or "", "catalog",
                                            "description")
            if new_desc is None and out_runs:
                # the whole description sits in the catalog column: that is
                # the BOOK writing its text on the wrong grid line (Vantage
                # prints 92 by-others rows this way, GASKETING at 274.6),
                # not the assembler misfiling it.  Keep the field, say so.
                zone_flags.append("column_mismatch:description_in_catalog_zone")
            else:
                desc = new_desc
                for run in out_runs:
                    text = " ".join(run)
                    if run[0].upper() in ("BY", "AS", "PER"):
                        notes = notes + [text]
                        zone_flags.append(
                            f"column_rerouted:description->notes:{text}")
                    else:
                        catalog = f"{catalog} {text}" if catalog else text
                        zone_flags.append(
                            f"column_rerouted:description->catalog:{text}")
            catalog, back_runs = split_runs(catalog or "", "description",
                                            "catalog")
            for run in back_runs:
                text = " ".join(run)
                desc = f"{desc} {text}" if desc else text
                zone_flags.append(
                    f"column_rerouted:catalog->description:{text}")
        rescued = [f for f in flags if not rows
                   and f.startswith("slot_recovered_from_column:")]
        if zone_flags or rescued:
            flags += zone_flags
            out2 = output_tokens({"description": desc,
                                  "catalog_number": catalog, "notes": notes})
            src2 = list(src)
            for zf in zone_flags + rescued:
                # only fills/detaches carry a moved value; reroutes shift
                # tokens between fields that conservation already counts
                if not zf.startswith(("slot_filled_from_column:",
                                      "catalog_detached:",
                                      "slot_recovered_from_column:")):
                    continue
                v = zf.split("=", 1)[1]
                if Counter(src2)[v] > Counter(out2)[v]:
                    src2.remove(v)
            new_level = conservation(out2, src2)
            if new_level != level:
                recon["conservation"][level] -= 1
                recon["conservation"][new_level] += 1
                if (level == "failed" and new_level != "failed"
                        and "conservation_failed" in flags):
                    flags.remove("conservation_failed")
                level = new_level

        all_anchors = sorted(
            set(anchors) | {s for a in anchors for s in stitched_of.get(a, [])},
            key=anchor_key)
        record = {
            "anchors": all_anchors,
            "qty": mech["qty"], "unit": mech["unit"],
            "description": desc, "catalog_number": catalog,
            "mfr": mech["mfr"], "finish": mech["finish"],
            "notes": notes,
            "mfr_hint": comp["mfr_hint"],
            "confidence": {**mech["confidence"],
                           "assembly": assembly_confidence(level, anchors,
                                                           flags)},
            "flags": flags,
        }
        if comp["pseudo"]:
            out["demoted"].append({
                "anchors": all_anchors,
                "text": " / ".join(line_index[a][1]["text"] for a in anchors),
                "reason": comp["pseudo_reason"],
                "was": record,
            })
        else:
            out["components"].append(record)

    # borrowed-line return (screenshot round, 2026-08-19): JC Ryan's
    # wobble pages print a catalog cell ~6pt above its own row, the line
    # splitter strands it as a separate line, and the LLM pins it to the
    # nearest catalog-less component -- set 18.0's row-less "Pivot Set"
    # delivered the Stop's "RM860 / 446 / 9-ADJ Series".  The ink's own
    # y-interval names the owner: the stray line OVERLAPS the Stop row
    # (4.2pt) and sits 20pt clear of Pivot Set.  Corpus probe found
    # exactly this one instance; the guards (row-less holder, y-overlap
    # with a catalog-less row, holder's catalog fully inked on the stray
    # line) keep it that narrow.  Assembly confidence is recomputed for
    # both sides; the conservation ledger keeps its counts (measured
    # strict -> strict on the one instance).
    def _bbox(a: str):
        g = geometry.get(a)
        return g.get("bbox") if g else None

    for comp_a in out["components"]:
        if "component_without_row" not in comp_a["flags"] \
                or len(comp_a["anchors"]) < 2 \
                or not comp_a["catalog_number"]:
            continue
        for x in list(comp_a["anchors"]):
            if x not in line_index or line_index[x][0] == "row" \
                    or not _bbox(x):
                continue
            bx, page = _bbox(x), x.split("-")[0]
            target = None
            for comp_b in out["components"]:
                if comp_b is comp_a or comp_b["catalog_number"]:
                    continue
                for a2 in comp_b["anchors"]:
                    if (a2 in line_index and line_index[a2][0] == "row"
                            and a2.split("-")[0] == page and _bbox(a2)):
                        b2 = _bbox(a2)
                        ov = min(bx[3], b2[3]) - max(bx[1], b2[1])
                        if ov > 0 and (target is None or ov > target[0]):
                            target = (ov, comp_b)
            if target is None:
                continue
            xtoks = Counter(tokens(line_index[x][1]["text"]))
            if Counter(tokens(comp_a["catalog_number"])) - xtoks:
                continue
            comp_b = target[1]
            moved = [n for n in comp_a["notes"]
                     if not Counter(tokens(n)) - xtoks]
            comp_b["catalog_number"] = comp_a["catalog_number"]
            comp_b["notes"] = moved + comp_b["notes"]
            comp_a["catalog_number"] = None
            comp_a["notes"] = [n for n in comp_a["notes"] if n not in moved]
            comp_a["anchors"] = [a for a in comp_a["anchors"] if a != x]
            comp_b["anchors"] = sorted(comp_b["anchors"] + [x],
                                       key=anchor_key)
            comp_a["flags"].append(f"borrowed_line_returned:{x}")
            comp_b["flags"].append(f"borrowed_line_received:{x}")
            for c in (comp_a, comp_b):
                src = source_tokens([a for a in c["anchors"]
                                     if a in line_index], line_index)
                outt = output_tokens(c)
                for v in (c["mfr"], c["finish"]):
                    if v and Counter(src)[v] > Counter(outt)[v]:
                        src.remove(v)
                c["confidence"]["assembly"] = assembly_confidence(
                    conservation(outt, src), c["anchors"], c["flags"])
            break

    for note in data["set_notes"]:
        flags = []
        anchors = claim(note["anchors"], flags)
        if not anchors:
            out["flags"].append("set_note_with_no_valid_anchors")
            continue
        # rotated border words never carry a note (screenshot round,
        # 2026-08-19): drop them from the claim; a note with nothing
        # left was pure border text and leaves only its trace.  A mixed
        # note keeps its real anchors -- conservation then fails against
        # the shrunken source and the line-join fallback rebuilds the
        # text from the survivors.
        rot = [a for a in anchors if a in rotated]
        if rot:
            anchors = [a for a in anchors if a not in rotated]
            if not anchors:
                out["flags"].append(f"set_note_rotated_dropped:{len(rot)}")
                continue
            flags.append(f"rotated_lines_dropped:{len(rot)}")
        anchors = sorted(anchors, key=anchor_key)
        src = source_tokens(anchors, line_index)
        level = conservation(tokens(note["text"]), src)
        recon["conservation"][level] += 1
        text = note["text"]
        if level == "failed":
            flags.append("conservation_failed")
            text = " ".join(
                (line_index[a][1]["text"] if line_index[a][0] != "row"
                 else line_index[a][1]["rest"]) for a in anchors)
            flags.append("fell_back_to_line_join")
        out["set_notes"].append({"anchors": anchors, "text": text,
                                 "flags": flags})

    n_rot_unclaimed = 0
    for a in required:
        if a in consumed:
            continue
        if a in rotated:
            # an unclaimed rotated border word is not a missed line --
            # resurrecting it would put the reversed text right back
            n_rot_unclaimed += 1
            continue
        recon["missed_lines"] += 1
        kind, rec = line_index[a]
        if kind == "row":
            desc, catalog = baseline_split(rec)
            mech = mechanical_fields([rec])
            out["components"].append({
                "anchors": sorted([a] + stitched_of.get(a, []),
                                  key=anchor_key),
                "qty": mech["qty"], "unit": mech["unit"],
                "description": desc, "catalog_number": catalog,
                "mfr": mech["mfr"], "finish": mech["finish"],
                "notes": [], "mfr_hint": None,
                "confidence": {**mech["confidence"], "assembly": "low"},
                "flags": ["llm_missed_row"],
            })
        else:
            out["set_notes"].append({"anchors": [a], "text": rec["text"],
                                     "flags": ["llm_missed_line"]})
    if n_rot_unclaimed:
        out["flags"].append(f"rotated_lines_unclaimed:{n_rot_unclaimed}")
    if recon["missed_lines"]:
        out["flags"].append(f"llm_missed_{recon['missed_lines']}_lines")
        recon["census"] = "repaired"

    # header-line component (Roselle round 2, 2026-08-19): bare_dotted
    # blocks put the first component ON the set-header line ("4.6 MORTISE
    # HINGE IVES - 5BB1 ... 5 613 (OIL RUBBED BRONZE) ..."), so step 2
    # consumed it as header and every set delivered its first row as a
    # set DESCRIPTION instead of a component.  With header-grade zones
    # the line splits mechanically -- no LLM, no census entry, flagged.
    zones_hdr = meta.get("slot_zones") or {}
    if (block["family"] == "bare_dotted" and block["trailer"]
            and zones_hdr.get("mfr", {}).get("from") == "header"
            and zones_hdr.get("finish") and zones_hdr.get("qty")
            and block["header_anchor"] in geometry):
        words = geometry[block["header_anchor"]]["words"]
        zm, zq, zf = zones_hdr["mfr"], zones_hdr["qty"], zones_hdr["finish"]
        sid = str(block["set_id"])
        desc_w, blob_w, fin_w, note_w = [], [], [], []
        qty_v = None
        depth = 0
        for i, w in enumerate(words):
            x, t = w["x0"], w["text"]
            if i == 0 and t == sid:
                continue
            # qty first: the column prints right at the mfr/qty seam
            # (2@300.0 vs mfr.hi 300.7), and the seam-order bug swallowed
            # 12 of 33 header quantities into the catalog blob before the
            # user caught it (2026-08-19) -- a pure-digit word this far
            # right is never part of the catalog text
            if x >= zq["lo"] - 6 and x < zq["hi"]                     and t.replace(".", "").isdigit() and qty_v is None:
                qty_v = int(float(t))
            elif x < zm["lo"]:
                desc_w.append(t)
            elif x < zm["hi"]:
                blob_w.append(t)
            elif x >= zf["lo"]:
                # finish = leading number + its parenthetical name; the
                # notes column has no measured zone in this book, but the
                # finish cell always prints "NNN (NAME)" -- track parens
                if not fin_w:
                    fin_w.append(t)
                    depth += t.count("(") - t.count(")")
                elif depth > 0 or t.startswith("("):
                    fin_w.append(t)
                    depth += t.count("(") - t.count(")")
                else:
                    note_w.append(t)
        blob = " ".join(blob_w)
        if " - " in blob:
            mfr_v, cat_v = blob.split(" - ", 1)
        else:
            mfr_v, cat_v = None, blob or None
        hflags = ["component_from_header_line"]
        if qty_v is not None:
            hflags.append(f"slot_filled_from_column:qty={qty_v}")
        if mfr_v:
            hflags.append(f"slot_filled_from_column:mfr={mfr_v}")
        if fin_w:
            hflags.append(
                f"slot_filled_from_column:finish={' '.join(fin_w)}")
        if desc_w or blob_w:
            out["components"].append({
                "anchors": [block["header_anchor"]],
                "qty": qty_v, "unit": None,
                "description": " ".join(desc_w) or None,
                "catalog_number": cat_v,
                "mfr": mfr_v, "finish": " ".join(fin_w) or None,
                "notes": [" ".join(note_w)] if note_w else [],
                "mfr_hint": None,
                "confidence": {**({"mfr": "medium"} if mfr_v else {}),
                               **({"finish": "medium"} if fin_w else {}),
                               "assembly": "medium"},
                "flags": hflags,
            })
            out["description"] = None
            out["flags"].append("header_row_component_extracted")

    out["components"].sort(key=lambda c: anchor_key(c["anchors"][0]))
    out["demoted"].sort(key=lambda d: anchor_key(d["anchors"][0]))
    out["set_notes"].sort(key=lambda n: anchor_key(n["anchors"][0]))
    out["reconciliation"] = recon
    return out

# --- per-stream driver -------------------------------------------------------


def load_jsonl(path: Path) -> tuple[dict, list[dict]]:
    with path.open(encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh]
    return records[0], records[1:]


def process_stream(rules_path: Path, blocks_root: Path, project: str,
                   dossier: dict, out_dir: Path, client: LLMClient,
                   workers: int, only_seqs: set[int] | None) -> dict:
    name = rules_path.name[:-len(".rules.jsonl")]
    meta, blocks = load_jsonl(rules_path)

    geometry = {}
    geo_path = rules_path.with_name(f"{name}.geometry.jsonl")
    if geo_path.is_file():
        with geo_path.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                geometry[rec["anchor"]] = rec

    _, step2_blocks = load_jsonl(
        blocks_root / project / f"{name}.blocks.jsonl")
    spans_of = {b["seq"]: b["spans"] for b in step2_blocks}

    if only_seqs is not None:
        blocks = [b for b in blocks if b["seq"] in only_seqs]

    rotated = rotated_lines(geometry)

    def run(block: dict) -> dict:
        return assemble_block(block, meta, dossier, geometry,
                              spans_of[block["seq"]], client, rotated)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        assembled = list(pool.map(run, blocks))
    assembled.sort(key=lambda b: b["seq"])

    usage = {"input_tokens": 0, "output_tokens": 0}
    cache_hits = llm_calls = 0
    for b in assembled:
        if b["llm"] and not b["llm"]["skipped"]:
            llm_calls += 1
            cache_hits += 1 if b.pop("_cache_hit", False) else 0
            for k in usage:
                usage[k] += b["llm"]["usage"][k]

    conservation_totals = Counter()
    for b in assembled:
        conservation_totals.update(b["reconciliation"].get("conservation", {}))
    summary = {
        "stream": name,
        "file": meta["file"],
        "n_blocks": len(assembled),
        "n_components": sum(len(b["components"]) for b in assembled),
        "n_demoted": sum(len(b["demoted"]) for b in assembled),
        "n_set_notes": sum(len(b["set_notes"]) for b in assembled),
        "llm_calls": llm_calls,
        "cache_hits": cache_hits,
        "usage": usage,
        "conservation": dict(sorted(conservation_totals.items())),
        "census_repaired": [b["set_id"] for b in assembled
                            if b["reconciliation"].get("census") == "repaired"],
        "slot_recovered": sum(b["reconciliation"].get("slot_recovered", 0)
                              for b in assembled),
        "slot_rejected": sum(b["reconciliation"].get("slot_rejected", 0)
                             for b in assembled),
        "slot_column_filled": sum(
            b["reconciliation"].get("slot_column_filled", 0)
            for b in assembled),
        "assembly_confidence": dict(sorted(Counter(
            c["confidence"]["assembly"] for b in assembled
            for c in b["components"]).items())),
    }

    if only_seqs is not None:
        print(json.dumps(assembled, ensure_ascii=False, indent=1))
        return summary

    meta_out = {
        "type": "meta", "file": meta["file"], "region": meta["region"],
        "source_stream": meta["source_stream"],
        "source_pdf": meta["source_pdf"],
        "source_rules": str(rules_path),
        "generated_by": GENERATED_BY,
        "model": client.model, "effort": client.effort,
        "assembly": {k: summary[k] for k in
                     ("n_blocks", "n_components", "n_demoted", "n_set_notes",
                      "llm_calls")},
    }
    with (out_dir / f"{name}.sets.jsonl").open(
            "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(meta_out, ensure_ascii=False, sort_keys=True) + "\n")
        for rec in assembled:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("in_root", nargs="?", default="data/out/step3",
                    help="step-3 output root with rules/dossier "
                         "(default: data/out/step3)")
    ap.add_argument("--blocks", default="data/out/step2",
                    help="step-2 blocks root (default: data/out/step2)")
    ap.add_argument("--out", default="data/out/step3",
                    help="output root (default: data/out/step3)")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--effort", default="low",
                    choices=["low", "medium", "high"])
    ap.add_argument("--max-tokens", type=int, default=12000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--project", default=None,
                    help="only projects whose name contains this substring")
    ap.add_argument("--seq", default=None,
                    help="comma-separated block seqs: smoke mode -- print "
                         "assembled JSON to stdout, write nothing")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    in_root, blocks_root, out_root = (Path(args.in_root), Path(args.blocks),
                                      Path(args.out))
    only_seqs = (set(int(s) for s in args.seq.split(","))
                 if args.seq else None)

    projects = sorted(p for p in in_root.iterdir()
                      if p.is_dir() and (p / "dossier.json").exists())
    if args.project:
        projects = [p for p in projects if args.project.lower()
                    in p.name.lower()]
    if not projects:
        print(f"no step-3 projects under {in_root}", file=sys.stderr)
        return 2

    grand = {"input_tokens": 0, "output_tokens": 0}
    for proj in projects:
        dossier = json.loads((proj / "dossier.json").read_text("utf-8"))
        out_dir = out_root / proj.name
        out_dir.mkdir(parents=True, exist_ok=True)
        for stale in prune_stale_outputs(
                out_dir,
                {p.name.replace(".rules.jsonl", ".sets.jsonl")
                 for p in proj.glob("*.rules.jsonl")},
                (".sets.jsonl",)):
            print(f"  removed stale sets: {stale}")
        client = LLMClient(out_dir / "llm_cache", model=args.model,
                           effort=args.effort, max_tokens=args.max_tokens)

        print(f"project: {proj.name}")
        summaries = []
        for rules_path in sorted(proj.glob("*.rules.jsonl")):
            s = process_stream(rules_path, blocks_root, proj.name, dossier,
                               out_dir, client, args.workers, only_seqs)
            print(f"  {s['stream']}")
            print(f"    blocks {s['n_blocks']}  components {s['n_components']}"
                  f"  demoted {s['n_demoted']}  set_notes {s['n_set_notes']}")
            print(f"    llm calls {s['llm_calls']} (cache {s['cache_hits']})"
                  f"  conservation {s['conservation']}"
                  f"  assembly {s['assembly_confidence']}")
            if s["census_repaired"]:
                print(f"    WARN census repaired in {s['census_repaired']}")
            if s["slot_rejected"]:
                print(f"    slot recovery: +{s['slot_recovered']} "
                      f"/ rejected {s['slot_rejected']}")
            for k in grand:
                grand[k] += s["usage"][k]
            summaries.append(s)

        if only_seqs is None:
            (out_dir / "assembly_report.json").write_text(
                json.dumps({"project": proj.name,
                            "generated_by": GENERATED_BY,
                            "model": args.model, "effort": args.effort,
                            "streams": summaries},
                           ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8", newline="\n")

    print(f"total usage: {grand['input_tokens']} in / "
          f"{grand['output_tokens']} out tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
