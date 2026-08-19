"""Step 1.5 of the v0.2 funnel: tag every stream line with a layout role.

  step-1 line streams (data/out/step1/<project>/*.jsonl)
     |  per-stream furniture detection + lexical rules
     v
  same streams with a "role" on every line (data/out/step1p5/...)
  + roles_report.json per project

Roles: page_header | page_footer | col_hdr | noise | struck | content.
(struck = the source crossed the line out; see STRUCK_ROLE_MIN below.)
Mark, don't delete: every input line is re-emitted with its original anchor,
text and bbox untouched -- step 2 chunks the content view, but the anchor ->
bbox trade keeps working for every line, and furniture keeps its intel (the
Vantage/Livelle footers carry the section number).

Detection (evidence: probe of all 9 step-1 streams, 2026-08-17):
* Furniture = repetition x stability x position, all three:
    - digit-normalized text recurring on >=60% of the region's pages
      (every real header/footer measured 100%; the floor leaves room for
      first/last-page variants),
    - at a stable height: max |top - median| <= 3.0 pt (real furniture
      measures 0.00 across all 7 books; nearest non-rigid content 13.44),
    - inside a band: top <=10% of page height for headers, >=85% for
      footers. National's "#2024814 DOOR HARDWARE ..." footer at 0.888 is
      the binding constraint below; headers measure <=0.079.
  The band is load-bearing, not cosmetic. Morris lays out one set per page,
  so plain content is repetition-stable there: "Set #101" (frac .97, dev
  1.92, pos .124), "4 Hinges MPB79 ..." (frac .81, dev 0.00, pos .234),
  "Opening Description: ..." (frac .65, dev 0.00, pos .185) would all pass
  a repetition+stability test and die only on the band.
* Set-header guard: a line matching step 1's set-header grammar is never
  anything but content, whatever the stats say ("Set #101" above; also
  Vantage's "PART 6 - HARDWARE GROUP NO. 103" prefixed heads).
* col_hdr is lexical, not positional: >=3 distinct column words AND >=70%
  of the line's alphabetic tokens are column words. Position can't be
  trusted either way: Lyons/National/Market View reprint the header per
  set mid-page (0 at page top), Vantage also reprints it at page top on
  continuation pages (the step-2 rule "column header at page top != new
  set" needs exactly this tag). The 70% ratio is what keeps Morris's
  "Note: - Product shall be US10B ... finish per catalog selection" prose
  (25 lines, hit ratio 0.27) out -- a bare >=3-words test marks all of it.
* noise = two lexical shapes: "PART <n> -" empty shells (Vantage Word-
  outline residue, 44 lines; their y varies wildly, repetition can't see
  them) and lines with no alphanumeric character at all (Lyons: 131
  pure-symbol lines, U+F09D cut-sheet-link icons in a side column).
* Everything else is content.

Miss-visibility (same contract as step 1 -- thresholds are calibrated
constants, so when they are wrong it must be loud, not silent):
* suspect_furniture in the report lists repeated groups that pass some but
  not all furniture gates (stable+frequent outside the bands, or in-band
  below the coverage floor). They stay content in the stream; a human
  glances at the report instead of re-reading pages. Morris's rigid rows
  land here by design. Set-header lines are exempt (guarded content).
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
from step1_locate import (RE_SET_HDR,  # noqa: E402  (one set-header grammar)
                          prune_stale_outputs)

ROLES = ("page_header", "page_footer", "col_hdr", "noise", "struck", "content")

# a line the source itself crossed out (step 1 measures the fraction of glyph
# width a hairline bar runs through).  Only an end-to-end strike leaves the
# content view: SJC strikes whole superseded sets and Valor whole deleted
# component rows, both at 1.00, while HFH's inline catalog-number edits land
# between 0.2 and 0.9 and must stay -- their rows are live, one token changed.
# Partly struck lines keep the measurement and are listed in the report.
STRUCK_ROLE_MIN = 0.95
STRUCK_REPORT_MIN = 0.30

FRAC_MIN = 0.6    # furniture recurs on >= this fraction of region pages
DEV_MAX = 3.0     # pt; max deviation from the group's median top
TOP_BAND = 0.10   # headers live in the top 10% of the page
BOT_BAND = 0.85   # footers live in the bottom 15% (National footer: 0.888)
MIN_PAGES = 3     # below this many pages repetition means nothing

SUSPECT_FRAC = 0.3  # report floor for near-miss furniture groups

COL_WORDS = {"QTY", "QUANTITY", "DESCRIPTION", "CATALOG", "MODEL", "PRODUCT",
             "FINISH", "MFR", "MANUFACTURER", "NOTES", "NUMBER", "NO"}
RE_ALPHA_TOKEN = re.compile(r"[A-Za-z]{2,}")
RE_PART_SHELL = re.compile(r"^\s*PART\s+\d{1,3}\s*[-–—]\s*$", re.I)
RE_ALNUM = re.compile(r"[0-9A-Za-z]")


def norm(text: str) -> str:
    """Digit runs -> '#', whitespace collapsed, case folded."""
    return " ".join(re.sub(r"\d+", "#", text.upper()).split())


def is_col_hdr(text: str) -> bool:
    tokens = [t.upper() for t in RE_ALPHA_TOKEN.findall(text)]
    if not tokens:
        return False
    hits = {t for t in tokens if t in COL_WORDS}
    return len(hits) >= 3 and len([t for t in tokens if t in COL_WORDS]) / len(tokens) >= 0.7


def is_noise(text: str) -> bool:
    return bool(RE_PART_SHELL.match(text)) or not RE_ALNUM.search(text)


# --- furniture detection ---------------------------------------------------

def furniture_groups(lines: list[dict], page_heights: dict[int, float],
                     ) -> tuple[dict, list, list]:
    """Group lines by normalized text; qualify groups as furniture.

    Returns (roles, furniture, near_miss): roles maps norm_text ->
    page_header/page_footer; furniture and near_miss are report entries
    (near-misses pass some but not all gates and stay content).
    """
    npages = len({r["page"] for r in lines})
    groups = defaultdict(list)
    for r in lines:
        groups[norm(r["text"])].append(r)

    # a 2-page region can still prove furniture by 2/2 repetition (Forest
    # Park p262-263, 2026-08-18: "000053277 CHSD218 Phase 3" tops both
    # pages); regions of >= 3 pages keep the stricter floor, and a 1-page
    # region cannot prove repetition at all.
    min_pages = 2 if npages == 2 else MIN_PAGES

    roles, furniture, near_miss = {}, [], []
    for key, members in sorted(groups.items()):
        pgs = {m["page"] for m in members}
        if len(pgs) < min_pages:
            continue
        frac = len(pgs) / npages
        if frac < SUSPECT_FRAC:
            continue
        if any(RE_SET_HDR.search(m["text"]) for m in members):
            continue  # guarded content; not even suspect-worthy
        tops = [m["bbox"][1] for m in members]
        med = statistics.median(tops)
        dev = max(abs(t - med) for t in tops)
        pos = med / statistics.median([page_heights[m["page"]] for m in members])
        in_band = pos <= TOP_BAND or pos >= BOT_BAND
        stats = {"norm": key, "sample": members[0]["text"][:70],
                 "pages": len(pgs), "page_frac": round(frac, 3),
                 "y_median": round(med, 2), "y_dev": round(dev, 2),
                 "page_pos": round(pos, 3)}
        if dev > DEV_MAX:
            continue  # ordinary flowing content
        if in_band and frac >= FRAC_MIN:
            role = "page_header" if pos <= TOP_BAND else "page_footer"
            roles[key] = role
            furniture.append({**stats, "role": role, "members": len(members)})
        elif in_band:
            near_miss.append({**stats, "why": "in_band_below_coverage_floor"})
        elif frac >= FRAC_MIN:
            near_miss.append({**stats, "why": "stable_repeat_outside_bands"})
    return roles, furniture, near_miss


# --- annotation ------------------------------------------------------------

def assign_role(text: str, furn_roles: dict) -> str:
    if RE_SET_HDR.search(text):
        return "content"
    if is_col_hdr(text):
        return "col_hdr"
    if is_noise(text):
        return "noise"
    return furn_roles.get(norm(text), "content")


def annotate_stream(in_path: Path, out_path: Path, pdf_paths: dict) -> dict:
    recs = [json.loads(l) for l in in_path.read_text("utf-8").splitlines()]
    meta, lines = recs[0], recs[1:]
    pdf_path = pdf_paths.get(meta["file"])
    heights = {int(p): meta["pages"][p][1] for p in meta["pages"]}

    furn_roles, furniture, near_miss = furniture_groups(lines, heights)

    counts = dict.fromkeys(ROLES, 0)
    out_lines, partly_struck = [], []
    for r in lines:
        frac = r.get("struck", 0.0)
        if frac >= STRUCK_ROLE_MIN:
            role = "struck"
        else:
            role = assign_role(r["text"], furn_roles)
            if frac >= STRUCK_REPORT_MIN:
                partly_struck.append({"anchor": r["anchor"], "struck": frac,
                                      "text": r["text"][:80]})
        counts[role] += 1
        out_lines.append({**r, "role": role})

    meta_out = dict(meta)
    if pdf_path:
        meta_out["source_pdf"] = pdf_path
    meta_out["roles"] = {
        "generated_by": "step1p5_roles v0.1",
        "counts": counts,
        "furniture": furniture,
        "suspect_furniture": near_miss,
    }
    if partly_struck:
        meta_out["roles"]["partly_struck"] = partly_struck
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(meta_out, ensure_ascii=False, sort_keys=True) + "\n")
        for r in out_lines:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    return {"stream": in_path.name, "file": meta["file"],
            "region": meta["region"], "lines": len(lines), "counts": counts,
            "furniture": furniture, "suspect_furniture": near_miss,
            **({"partly_struck": partly_struck} if partly_struck else {})}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("in_root", nargs="?", default="data/out/step1",
                    help="step-1 output root (default: data/out/step1)")
    ap.add_argument("--out", default="data/out/step1p5",
                    help="output root (default: data/out/step1p5)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    in_root, out_root = Path(args.in_root), Path(args.out)
    projects = sorted(p for p in in_root.iterdir()
                      if p.is_dir() and (p / "region_report.json").exists())
    if not projects:
        print(f"no step-1 projects under {in_root}", file=sys.stderr)
        return 2

    for proj in projects:
        report = json.loads((proj / "region_report.json").read_text("utf-8"))
        pdf_paths = {f["file"]: f["path"] for f in report["files"]}
        out_dir = out_root / proj.name
        out_dir.mkdir(parents=True, exist_ok=True)

        summaries = []
        print(f"project: {proj.name}")
        for stale in prune_stale_outputs(
                out_dir, {p.name for p in proj.glob("*.jsonl")}, (".jsonl",)):
            print(f"  removed stale stream: {stale}")
        for stream in sorted(proj.glob("*.jsonl")):
            s = annotate_stream(stream, out_dir / stream.name, pdf_paths)
            summaries.append(s)
            c = s["counts"]
            furn = "; ".join(f"{f['role']}[{f['pages']}p] {f['sample']}"
                             for f in s["furniture"]) or "-"
            print(f"  {s['stream']}")
            print(f"    lines {s['lines']}: header {c['page_header']}, "
                  f"footer {c['page_footer']}, col_hdr {c['col_hdr']}, "
                  f"noise {c['noise']}, struck {c['struck']}, "
                  f"content {c['content']}")
            if s.get("partly_struck"):
                print(f"    partly struck (kept as content, one token edited "
                      f"inline): {len(s['partly_struck'])} lines")
            print(f"    furniture: {furn}")
            for n in s["suspect_furniture"]:
                print(f"    suspect ({n['why']}): {n['sample']!r} "
                      f"[{n['pages']}p, dev {n['y_dev']}, pos {n['page_pos']}]")

        (out_dir / "roles_report.json").write_text(
            json.dumps({"project": proj.name,
                        "generated_by": "step1p5_roles v0.1",
                        "streams": summaries},
                       ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
