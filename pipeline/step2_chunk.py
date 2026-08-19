"""Step 2 of the v0.2 funnel: cut the content view into set blocks.

  step-1.5 role-annotated streams (data/out/step1p5/<project>/*.jsonl)
     |  one pass over the role=="content" view: a line matching a set-header
     |  family opens a block, every following content line belongs to it
     v
  <stream>.blocks.jsonl per stream (block = id + per-page line spans + bbox
  unions = the location the challenge asks for) + chunks_report.json per
  project.

The cut itself is a two-state machine -- the hard parts (cross-page flow,
furniture, reprinted column headers) were consumed by steps 1/1.5, so blocks
are contiguous slices of the content view and three properties hold by
construction: every content line lands in exactly one of preamble / some
block / postamble; block spans never overlap; same input -> same bytes.

Header grammar (evidence: all 9 streams, 2026-08-17 desk run):
* Five families, line-anchored, case-insensitive, reusing step 1's keyword
  skeleton: HARDWARE GROUP NO. <id> (optional "PART <n> - " Word-outline
  prefix, Vantage; also title-case Lyons/National/Market View), Set #<id>
  (Morris), Heading #<id> (Bridgeport), Set: <id> (Livelle), and
  HW/HARDWARE SET #<id> (unseen so far; the SPEC's own "SET #1" example).
* The id is wider than step 1's SET_ID: step 1 counted pages, step 2 must
  capture the id whole -- dotted forms (87.1, 103.68, C00.EXT), long
  alphanumerics (PR38ICCL, SW38RXCA). Digit-bearing ids are free-form;
  alpha-only ids (MISC) keep step 1's rule: only behind an explicit #/NO.
  separator, 2+ chars, so prose stays out.
* Text after the id stays attached as "trailer", uninterpreted: inline
  descriptions (Market View "- ... EXT ..."), Lyons "- Not Used", the
  Vantage ghost instruction "DO NOT APPLY DOOR NUMBERS TO SETS". Step 3
  reads meaning into it; step 2 only cuts.
* "END OF SECTION" closes the open block (terminator, kept as postamble) --
  otherwise it would glue to Vantage's ghost 002 and break empty=true.
* A repeated id within one stream does NOT merge into the earlier block
  (no continuation reprints seen in 7 books; Vantage reprints only the
  column header). It opens a normal block and the report flags it loudly.
  Scope is per stream on purpose: Morris carries Set #MISC in BOTH of its
  087100 sections -- same id, different sets, merging would eat one.

Miss-visibility (same contract as steps 1/1.5 -- when the grammar is wrong
it must be loud, not silent):
* zero blocks in a stream -> alarm (the region exists, so an unreadable
  header dialect is an anomaly -> LLM-fallback trigger, mirrors step 1).
* suspect_headers: step 1's RE_SUSPECT (a WIDER, separately-written net)
  sweeps preamble and block interiors; a set header the strict families
  missed shows up here for eyeballing instead of silently merging blocks.
* preamble_qty_rows: component rows before the first recognized header
  mean the first header itself was missed -> counted, expected 0.
* oversized blocks (> 3x the stream's median lines) are listed for a
  glance -- legit ones exist (Bridgeport Heading #17 spans 76 lines: 68
  door items sharing one component list), so this reports, never rejects.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from step1_locate import (RE_QTY_UNIT, RE_SUSPECT,  # noqa: E402
                          prune_stale_outputs)

# --- header grammar --------------------------------------------------------

# digit-bearing ids free-form incl. dots (87.1, C00.EXT, PR38ICCL);
# alpha-only ids (MISC) 2+ chars -- always behind an explicit separator below
# hyphenated ids ("Set #U-01", Gerrard 2026-08-18: three add-alternate sets
# after #32) come first so the letter run does not stop short at "U"; the
# optional dotted tail keeps JC Ryan's exterior ids whole ("Set: EX-1.0" ..
# "EX-4.0", 2026-08-18) instead of cutting them to "EX-1" with ".0" left in
# the trailer -- same book writes its interior ids as plain "1.0".
ID = (r"(?:[A-Z]{1,3}-\d{1,4}(?:\.\d{1,3})?[A-Z]{0,3}"
      r"|(?=[A-Z0-9.]*\d)[A-Z0-9]+(?:\.[A-Z0-9]+)*|[A-Z]{2,10})")
PART_PREFIX = r"(?:PART\s+\d{1,3}\s*[-–—]\s*)?"

# digit-bearing ids only: used where the keyword is followed directly by the
# id with no separator, so "HW SET" cannot be read as id="SET" (2026-08-18).
ID_NUM = r"(?=[A-Z0-9.]*\d)[A-Z0-9]+(?:\.[A-Z0-9]+)*"

FAMILIES = [
    # "/SET" alternative: StarHardware writes "Hardware Group/Set #A1"
    # (2026-08-18, 67 header lines); a corpus probe over every content line of
    # the 20-book corpus matches this form in that one book.
    ("group_no", re.compile(
        rf"^{PART_PREFIX}(?:HW|HDW|HARDWARE)\s+GROUPS?(?:\s*/\s*SETS?)?"
        rf"\s*(?:NO\.?|NUMBER|#)\s*[:#]?\s*({ID})\b\s*(.*)$", re.I)),
    ("set_hash", re.compile(rf"^SET\s*#\s*({ID})\b\s*(.*)$", re.I)),
    ("heading", re.compile(rf"^HEADING\s*#\s*({ID})\b\s*(.*)$", re.I)),
    ("set_colon", re.compile(
        rf"^SET\s*(?:NO\.?|NUMBER)?\s*:\s*({ID})\b\s*(.*)$", re.I)),
    ("hw_set", re.compile(
        rf"^{PART_PREFIX}(?:HW|HDW|HARDWARE)\s+SETS?\s+(?:NO\.?|NUMBER|#)"
        rf"\s*[:#]?\s*({ID})\b\s*(.*)$", re.I)),
    # bare abbreviation + id, no SET/GROUP word and no separator: SJC Well
    # Behavioral writes "HW 01" / "HW G01" / "HW A02" (2026-08-18, 117 header
    # lines).  Restricted to digit-bearing ids and placed after hw_set so
    # "HW SET ..." can never be read as id="SET"; corpus probe over every
    # content line of the 20-book corpus matches this one book.
    ("hw_bare", re.compile(
        rf"^(?:HW|HDW)\s*[-#:]?\s*({ID_NUM})\b\s*(.*)$", re.I)),
    # the id can also sit inside the sentence that opens the set, when that
    # sentence ends by announcing the list: StarHardware p25/p26 "For doors
    # assigned Hardware Group/Set #103 on door schedule, provide the
    # following:".  Anchored on BOTH keyword+id AND the trailing "following:",
    # so a bare cross-reference in prose is not a header; last in the list so
    # every line-anchored family wins first.  Corpus probe: 2 hits, exactly the
    # two sets it is meant to open.
    ("assigned_following", re.compile(
        rf"^.*?(?:HW|HDW|HARDWARE)\s+GROUPS?(?:\s*/\s*SETS?)?"
        rf"\s*(?:NO\.?|NUMBER|#)\s*[:#]?\s*({ID})\b(.*\bfollowing\s*:)\s*$",
        re.I)),
]

# wide-table dialect (Roselle, 2026-08-18): the set id is a bare dotted
# decimal ("1.1" .. "8.3") that BEGINS the set's first component row -- no
# keyword at all.  The family is gated per stream on the book's own
# column-header announcement ("SET HARDWARE TYPE MANUFACTURER - PRODUCT
# QTY.FINISH NOTES", a page_header furniture line in the 1.5 stream):
# ungated it would collide with CSI outline numbers (corpus scan 2026-08-18:
# Vantage p389 "3.6 HARDWARE SETS:" is the one hit in 5,419 content lines).
# The trailer of a bare_dotted header is the first component row itself.
RE_WIDE_COL_HDR = re.compile(r"^SET\s+HARDWARE\s+TYPE\b", re.I)
WIDE_FAMILIES = [
    ("bare_dotted", re.compile(r"^(\d{1,2}\.\d{1,2})\s+(?=[A-Z(])(.*)$")),
]

# the id-to-title joiner is header syntax, not content: "Hardware Group
# No. 01: (Door U1 ...)" / "... 05 - Not Used" carried the ":"/"-" into
# the trailer and from there into the delivered set description
# (Valor x37, Market View x24, Lyons x4; corpus probe 2026-08-18).
RE_TRAILER_JOINER = re.compile(r"^[:,\-\u2013\u2014]+\s*")

# terminator: optional Word-outline PART prefix is the 4th observed form
# (Roselle p17 "PART 2 - END OF SECTION 087100", after the last set).
RE_TERMINATOR = re.compile(rf"^{PART_PREFIX}END\s+OF\s+SECTION\b", re.I)

OVERSIZE_FACTOR = 3  # report blocks > this x median lines (report, not reject)
OVERSIZE_MIN_BLOCKS = 5  # median means nothing below this many blocks


def match_header(text: str, families=FAMILIES):
    """Return (family, set_id, trailer) or None."""
    stripped = text.strip()
    for family, rx in families:
        m = rx.match(stripped)
        if m:
            return (family, m.group(1),
                    RE_TRAILER_JOINER.sub("", m.group(2).strip()))
    return None


# --- chunking --------------------------------------------------------------

def spans_of(lines: list[dict]) -> list[dict]:
    """Per-page line range + bbox union over one block's content lines."""
    by_page = defaultdict(list)
    for r in lines:
        by_page[r["page"]].append(r)
    spans = []
    for page in sorted(by_page):
        rows = by_page[page]
        spans.append({
            "page": page,
            "lines": [min(r["line"] for r in rows), max(r["line"] for r in rows)],
            "bbox": [min(r["bbox"][0] for r in rows),
                     min(r["bbox"][1] for r in rows),
                     max(r["bbox"][2] for r in rows),
                     max(r["bbox"][3] for r in rows)],
        })
    return spans


def chunk_stream(content: list[dict], families=FAMILIES) -> dict:
    """Cut one stream's content view into preamble / blocks / postamble."""
    blocks, preamble, postamble = [], [], []
    current = None  # lines of the open block (header included)
    seen_header = False
    for r in content:
        hdr = match_header(r["text"], families)
        if hdr:
            family, set_id, trailer = hdr
            current = [r]
            blocks.append({"family": family, "set_id": set_id,
                           "trailer": trailer, "lines": current})
            seen_header = True
        elif RE_TERMINATOR.match(r["text"].strip()) and current is not None:
            current = None
            postamble.append(r)
        elif current is not None:
            current.append(r)
        else:
            (postamble if seen_header else preamble).append(r)
    return {"blocks": blocks, "preamble": preamble, "postamble": postamble}


def block_records(blocks: list[dict]) -> list[dict]:
    recs = []
    for seq, b in enumerate(blocks, start=1):
        lines = b["lines"]
        recs.append({
            "type": "block",
            "seq": seq,
            "family": b["family"],
            "set_id": b["set_id"],
            "trailer": b["trailer"],
            "header": {"anchor": lines[0]["anchor"], "text": lines[0]["text"]},
            "n_lines": len(lines),
            "empty": len(lines) == 1,
            "anchor_first": lines[0]["anchor"],
            "anchor_last": lines[-1]["anchor"],
            "spans": spans_of(lines),
        })
    return recs


# --- miss-visibility nets --------------------------------------------------

def sweep_nets(cut: dict) -> dict:
    """Wider-grammar suspects + preamble qty rows + oversize + duplicate ids."""
    suspects = []
    for where, rows in (("preamble", cut["preamble"]),
                        *((f"block {b['set_id']}", b["lines"][1:])
                          for b in cut["blocks"])):
        for r in rows:
            if RE_SUSPECT.match(r["text"]):
                suspects.append({"in": where, "anchor": r["anchor"],
                                 "text": r["text"][:80]})
    qty_rows = sum(1 for r in cut["preamble"] if RE_QTY_UNIT.match(r["text"]))

    counts = [len(b["lines"]) for b in cut["blocks"]]
    oversized = []
    if len(counts) >= OVERSIZE_MIN_BLOCKS:
        med = statistics.median(counts)
        oversized = [{"set_id": b["set_id"], "n_lines": len(b["lines"]),
                      "median": med}
                     for b in cut["blocks"]
                     if len(b["lines"]) > OVERSIZE_FACTOR * med]

    dup_ids = sorted({sid for sid in
                      [b["set_id"] for b in cut["blocks"]]
                      if [b["set_id"] for b in cut["blocks"]].count(sid) > 1})
    return {"suspect_headers": suspects, "preamble_qty_rows": qty_rows,
            "oversized_blocks": oversized, "duplicate_ids_in_stream": dup_ids}


# --- emission --------------------------------------------------------------

def anchors_texts(rows: list[dict]) -> list[dict]:
    return [{"anchor": r["anchor"], "text": r["text"][:80]} for r in rows]


def process_stream(in_path: Path, out_path: Path) -> dict:
    recs = [json.loads(l) for l in in_path.read_text("utf-8").splitlines()]
    meta, lines = recs[0], recs[1:]
    content = [r for r in lines if r["role"] == "content"]

    # dialect gate: the wide-table head family switches on only when the
    # stream itself carries the wide column-header announcement (any role --
    # step 1.5 files it as page_header furniture).  Old streams: 0 hits
    # (corpus scan 2026-08-18), so their families list is unchanged.
    wide = any(RE_WIDE_COL_HDR.match(r["text"].strip()) for r in lines)
    families = FAMILIES + WIDE_FAMILIES if wide else FAMILIES
    cut = chunk_stream(content, families)
    recs_out = block_records(cut["blocks"])
    nets = sweep_nets(cut)

    meta_out = {
        "type": "meta",
        "file": meta["file"],
        "region": meta["region"],
        "source_stream": str(in_path),
        "source_pdf": meta.get("source_pdf"),
        "bbox_convention": meta["bbox_convention"],
        "pages": meta["pages"],
        "chunks": {
            "generated_by": "step2_chunk v0.1",
            "n_content_lines": len(content),
            "n_blocks": len(recs_out),
            "n_preamble_lines": len(cut["preamble"]),
            "n_postamble_lines": len(cut["postamble"]),
        },
    }
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(meta_out, ensure_ascii=False, sort_keys=True) + "\n")
        for r in recs_out:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    summary = {
        "stream": in_path.name,
        "file": meta["file"],
        "region": meta["region"],
        "n_content_lines": len(content),
        "n_blocks": len(recs_out),
        "block_ids": [b["set_id"] for b in cut["blocks"]],
        "families": dict(sorted(
            ((f, [b["family"] for b in cut["blocks"]].count(f))
             for f in {b["family"] for b in cut["blocks"]}))),
        "empty_blocks": [b["set_id"] for b, r in zip(cut["blocks"], recs_out)
                         if r["empty"]],
        "preamble": anchors_texts(cut["preamble"]),
        "postamble": anchors_texts(cut["postamble"]),
        **nets,
    }
    if not recs_out:
        summary["alarm"] = (
            "region exists but no set header recognized -- alien header "
            "dialect; eyeball the stream / escalate to LLM fallback")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("in_root", nargs="?", default="data/out/step1p5",
                    help="step-1.5 output root (default: data/out/step1p5)")
    ap.add_argument("--out", default="data/out/step2",
                    help="output root (default: data/out/step2)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    in_root, out_root = Path(args.in_root), Path(args.out)
    projects = sorted(p for p in in_root.iterdir()
                      if p.is_dir() and (p / "roles_report.json").exists())
    if not projects:
        print(f"no step-1.5 projects under {in_root}", file=sys.stderr)
        return 2

    for proj in projects:
        out_dir = out_root / proj.name
        out_dir.mkdir(parents=True, exist_ok=True)

        summaries = []
        print(f"project: {proj.name}")
        for stale in prune_stale_outputs(
                out_dir,
                {p.stem + ".blocks.jsonl" for p in proj.glob("*.jsonl")},
                (".blocks.jsonl",)):
            print(f"  removed stale blocks: {stale}")
        for stream in sorted(proj.glob("*.jsonl")):
            out_path = out_dir / (stream.stem + ".blocks.jsonl")
            s = process_stream(stream, out_path)
            summaries.append(s)
            ids = s["block_ids"]
            ids_view = ", ".join(ids[:6]) + (f", ... {ids[-1]}" if len(ids) > 7
                                             else (f", {ids[-1]}" if len(ids) == 7 else ""))
            print(f"  {s['stream']}")
            print(f"    blocks {s['n_blocks']} [{ids_view}]  "
                  f"content {s['n_content_lines']} = "
                  f"pre {len(s['preamble'])} + blocks + post {len(s['postamble'])}")
            if s["empty_blocks"]:
                print(f"    empty blocks: {', '.join(s['empty_blocks'])}")
            for k in ("duplicate_ids_in_stream", "oversized_blocks",
                      "suspect_headers"):
                if s[k]:
                    print(f"    {k}: {s[k]}")
            if s["preamble_qty_rows"]:
                print(f"    WARN: {s['preamble_qty_rows']} qty rows in preamble "
                      "(missed first header?)")
            if "alarm" in s:
                print(f"    ALARM: {s['alarm']}")

        dup_across = defaultdict(list)
        for s in summaries:
            for sid in set(s["block_ids"]):
                dup_across[sid].append(s["stream"])
        dup_across = {sid: sorted(streams)
                      for sid, streams in sorted(dup_across.items())
                      if len(streams) > 1}
        if dup_across:
            print(f"  cross-stream ids: {', '.join(dup_across)} "
                  "(marked, not merged -- assembly decides)")

        # cross-region split-schedule net (HFH group 197, 2026-08-18: found
        # by eyeball, not by a net -- blank pages split one schedule, the
        # set's header ended region 1 as an empty tail block and its rows
        # opened region 2's preamble).  The signature is exact: a stream
        # whose LAST block is empty AND that never saw its terminator, while
        # the same file carries a later region.  Vantage's tail ghosts
        # (001/002) sit before their END OF SECTION; Morris's two regions
        # both end closed; single-region files have no later region.
        # Validated on the stashed pre-fix HFH streams (fires); quiet on the
        # whole current corpus.
        suspects = []
        by_file = defaultdict(list)
        for s in summaries:
            by_file[s["file"]].append(s)
        for fname, group in sorted(by_file.items()):
            group = sorted(group, key=lambda s: s["region"][0])
            for a, b in zip(group, group[1:]):
                if (a["block_ids"] and a["block_ids"][-1] in a["empty_blocks"]
                        and not a["postamble"]):
                    suspects.append({
                        "file": fname,
                        "tail_stream": a["stream"],
                        "tail_set_id": a["block_ids"][-1],
                        "next_stream": b["stream"],
                        "why": "stream ends on an empty set header with no "
                               "terminator while the same file continues in "
                               "a later region -- the set's rows may sit in "
                               "that region's preamble (split schedule)",
                    })
        for s in suspects:
            print(f"    WARN: {s['tail_stream']} ends on empty set "
                  f"{s['tail_set_id']} with no terminator -- rows may sit in "
                  f"{s['next_stream']}'s preamble (split schedule?)")
        report = {"project": proj.name,
                  "generated_by": "step2_chunk v0.1",
                  "streams": summaries,
                  "duplicate_ids_across_streams": dup_across}
        if suspects:
            report["split_schedule_suspects"] = suspects
        (out_dir / "chunks_report.json").write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
