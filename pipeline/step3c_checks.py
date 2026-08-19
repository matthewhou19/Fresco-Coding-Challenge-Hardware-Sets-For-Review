"""Acceptance checks for step 3c (LLM reading-assembly): asserts
hand-verified facts from the sample corpus (2026-08-18):
the four hand-walked target blocks plus the Livelle scrambled three-line
rows (p681/p683) whose word-level x0 was pulled from the raw PDF.

Two kinds of checks, same contract as steps 1..3b:

* Target-block assertions pin the SEMANTIC assembly -- where description
  ends and catalog begins, which fragment attached where, what was demoted
  and what was kept, which null slot the LLM rescued -- to the hand-walked
  ground truth.  These are assertions about the cached LLM responses: they
  freeze the reviewed behaviour, and a cache rebuild that changes any of
  them must come back here.
* Structural invariants are recomputed from the step-3b rules files and
  step-2 block indexes (found through each sets file's own meta record),
  never read back from the assembly report: anchor census is a true
  partition, mechanical fields are byte-equal to the rule side (or a
  vocabulary-validated rescue of a null slot, flagged), location equals
  the step-2 spans, conservation failures match their flags, and the
  report's cache counter proves the run was served from the cache.

Usage:  python pipeline/step3c_checks.py [step3_root]
Default: data/out/step3; run from the repo root like every other step.
Prints PASS/FAIL per fact; exit 1 on any failure.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from step3c_assemble import rotated_lines  # noqa: E402

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILURES.append(name)


def load_jsonl(path: Path):
    recs = [json.loads(l) for l in path.read_text("utf-8").splitlines()]
    return recs[0], recs[1:]


def load_sets(root: Path, project: str, glob: str):
    path = next((root / project).glob(glob))
    meta, blocks = load_jsonl(path)
    return path, meta, blocks


def by_seq(blocks: list, seq: int) -> dict:
    return next(b for b in blocks if b["seq"] == seq)


def comp_at(block: dict, anchor: str) -> dict:
    return next(c for c in block["components"] if anchor in c["anchors"])


def tokens(text):
    return text.split() if text else []


RE_SLOT_RECOVERED = re.compile(r"^slot_recovered_from_text:(mfr|finish)=(.+)$")
RE_FROM_COLUMN = re.compile(
    r"^slot_(?:recovered|filled)_from_column:(mfr|finish)=(.+)$")
RE_ANY_FILL = re.compile(
    r"^slot_filled_from_column:(qty|unit|mfr|finish)=(.+)$")

# --- target blocks -----------------------------------------------------------


def check_vantage(root: Path) -> None:
    print("target 1+2: Vantage set 103 / C200C (hand-walked 2026-08-18)")
    _, _, sets = load_sets(root, "The_Door_Company__Copy_",
                           "Vantage_TX-22_Div_01_08-*.sets.jsonl")

    b = by_seq(sets, 1)  # set 103, p389-L06..15
    check("103: exactly 5 components", len(b["components"]) == 5,
          str(len(b["components"])))
    c = comp_at(b, "p389-L11")
    check("103: PERMANENT CORE splits desc|note, no catalog",
          c["description"] == "PERMANENT CORE"
          and c["catalog_number"] is None
          and c["notes"] == ["COORDINATE WITH OWNER"],
          f"{c['description']!r}|{c['catalog_number']!r}|{c['notes']!r}")
    check("103: PERMANENT CORE keeps rule slots SCH/626",
          c["mfr"] == "SCH" and c["finish"] == "626")
    c = comp_at(b, "p389-L09")
    check("103: HINGE | 5BB1HW 4.5 X 4.5",
          c["description"] == "HINGE"
          and c["catalog_number"] == "5BB1HW 4.5 X 4.5")
    c = comp_at(b, "p389-L13")
    check("103: SILENCER | SR64, finish GRY",
          c["description"] == "SILENCER" and c["catalog_number"] == "SR64"
          and c["finish"] == "GRY")
    check("103: hinge-rule prose is ONE set note (L14+L15)",
          len(b["set_notes"]) == 1
          and b["set_notes"][0]["anchors"] == ["p389-L14", "p389-L15"]
          and "ADDITIONAL HINGE" in b["set_notes"][0]["text"])
    check("103: census ok, zero conservation failures",
          b["reconciliation"]["census"] == "ok"
          and b["reconciliation"]["conservation"]["failed"] == 0)

    b = by_seq(sets, 16)  # C200C, p395-L29..p397-L04, 33 lines
    check("C200C: 17 components, nothing demoted",
          len(b["components"]) == 17 and len(b["demoted"]) == 0,
          f"{len(b['components'])}/{len(b['demoted'])}")
    c = comp_at(b, "p396-L08")
    check("C200C: lock row stitches its two orphans (L08+L09+L10)",
          c["anchors"] == ["p396-L08", "p396-L09", "p396-L10"])
    check("C200C: VDC sewn into catalog, (FAIL SECURE) is a note",
          "VDC" in (c["catalog_number"] or "")
          and "(FAIL SECURE)" in c["notes"],
          f"{c['catalog_number']!r}|{c['notes']!r}")
    c = comp_at(b, "p396-L21")
    check("C200C: ACCESS CONTROL READER(S) kept as component, BY DIV 28 note",
          c["description"] == "ACCESS CONTROL READER(S)"
          and c["catalog_number"] is None
          and c["mfr"] is None and c["finish"] is None
          and "BY DIV 28" in c["notes"])
    c = comp_at(b, "p396-L23")
    check("C200C: DOOR POSITION SWITCH stitched across its break (L23+L24)",
          c["anchors"] == ["p396-L23", "p396-L24"]
          and c["description"] == "DOOR POSITION SWITCH"
          and "BY DIV 28" in c["notes"])
    c = comp_at(b, "p396-L13")
    check("C200C: (TO SUIT FRAME) attaches to MOUNTING BRACKET as note",
          "p396-L14" in c["anchors"]
          and "(TO SUIT FRAME)" in c["notes"]
          and "(TO SUIT FRAME)" not in (c["catalog_number"] or ""))
    c = comp_at(b, "p396-L20")
    check("C200C: WIRE HARNESS keeps rule verdict mfr=SCH finish=null",
          c["mfr"] == "SCH" and c["finish"] is None)
    op = next(n for n in b["set_notes"] if n["text"].startswith("OPERATION:"))
    check("C200C: OPERATION prose is one note spanning the page break",
          "p396-L28" in op["anchors"] and "p397-L03" in op["anchors"]
          and "p397-L04" in op["anchors"])
    check("C200C: census ok, zero conservation failures",
          b["reconciliation"]["census"] == "ok"
          and b["reconciliation"]["conservation"]["failed"] == 0)


def check_bridgeport(root: Path) -> None:
    print("target 3: Bridgeport Heading #2 (hand-walked 2026-08-18)")
    _, _, sets = load_sets(root, "81-85_Bridgeport",
                           "08-70-00-Hardware-Schedule-p3-49.sets.jsonl")
    b = by_seq(sets, 2)

    check("H2: exactly 2 demoted pseudo-components",
          len(b["demoted"]) == 2, str(len(b["demoted"])))
    demoted_anchors = {a for d in b["demoted"] for a in d["anchors"]}
    check("H2: Schematic (L25) and behaviour line (L27) are the demotions",
          demoted_anchors == {"p4-L25", "p4-L27"}, str(sorted(demoted_anchors)))
    check("H2: every demotion carries a reason",
          all(d["reason"] for d in b["demoted"]))
    c = comp_at(b, "p4-L16")
    check("H2: Card Reader & Fobs stays a component, attribution is a note",
          "By Security Contractor" in c["notes"]
          and c["catalog_number"] is None)
    c = comp_at(b, "p4-L06")
    check("H2: @ 42\" Top Down attaches to the continuous hinge as note",
          "p4-L07" in c["anchors"] and '@ 42" Top Down' in c["notes"])
    c = comp_at(b, "p4-L19")
    check("H2: Auto Operator yields mfr_hint Horton (no mfr column here)",
          c["mfr_hint"] == "Horton" and c["mfr"] is None)
    check("H2: Horton stays in the text fields (hint is not a move)",
          "Horton" in (c["description"] or "") + " "
          + (c["catalog_number"] or ""))
    c = comp_at(b, "p4-L15")
    check("H2: Door Pull finish rescued as C32D from C32D-316 (legend hit)",
          c["finish"] == "C32D"
          and any(f == "slot_recovered_from_text:finish=C32D"
                  for f in c["flags"])
          and "C32D-316" in (c["catalog_number"] or ""))
    check("H2: double-leaf dimension line is doors-side, not a component",
          any("965, 965 x 2147 x 50" in d["text"] for d in b["doors"]))

    # Rev_0 is the same PDF re-issued: same prompts, same cache fingerprints,
    # so the assembled blocks must be identical records.
    _, _, sets_rev = load_sets(root, "81-85_Bridgeport",
                               "08-70-00-Hardware-Schedule_Rev_0-*.sets.jsonl")
    same = all(json.dumps(a, sort_keys=True) == json.dumps(x, sort_keys=True)
               for a, x in zip(sets, sets_rev)) and len(sets) == len(sets_rev)
    check("H2: Rev_0 twin stream assembles to identical block records", same)


def check_livelle(root: Path) -> None:
    print("target 4 + scrambled rows: Livelle 4.0 / 108.0")
    _, _, sets = load_sets(root, "Livelle_Mulholland_-_Life_Plan_Community",
                           "*.sets.jsonl")

    b = by_seq(sets, 4)  # Set: 4.0, p644-L29..p645-L03
    check("4.0: Description: line becomes the set description",
          b["description"] == "Unit Garage Door U1F", repr(b["description"]))
    c = comp_at(b, "p644-L34")
    check("4.0: closer catalog 1601 P, parenthetical is a note",
          c["catalog_number"] == "1601 P"
          and c["notes"] == ["(mount inside room)"]
          and c["mfr"] == "NO" and c["finish"] == "689")
    c = comp_at(b, "p645-L03")
    check("4.0: 'Per Detail / Type as Req' is a note, catalog null",
          c["catalog_number"] is None
          and any("Per Detail / Type as Req" in n for n in c["notes"])
          and c["mfr"] == "PE" and c["finish"] is None)
    check("4.0: BHMA-gated nulls survive (S88BL / 2113AV rows keep "
          "finish=null)",
          comp_at(b, "p645-L01")["finish"] is None
          and comp_at(b, "p645-L02")["finish"] is None)

    b = by_seq(sets, 114)  # Set: 108.0 -- the scrambled three-line rows
    c = comp_at(b, "p681-L30")
    check("108.0: scrambled trio regroups into ONE component (L29+L30+L31)",
          c["anchors"] == ["p681-L29", "p681-L30", "p681-L31"])
    check("108.0: 'Exit' lands in description by x0, NEMW in catalog",
          c["description"] == "Access Control Concealed Vert Rod Exit"
          and c["catalog_number"] == "NB 18 IN100-WDPE8678-BIPS NEMW"
          and "(EAC option)" in c["notes"])
    check("108.0: null finish rescued as US32D (distribution hit), mfr "
          "kept SA",
          c["finish"] == "US32D" and c["mfr"] == "SA" and c["qty"] == 1
          and any(f == "slot_recovered_from_text:finish=US32D"
                  for f in c["flags"]))
    check("108.0: census ok (all three scrambled lines consumed)",
          b["reconciliation"]["census"] == "ok")

    b113 = next(x for x in sets if x["set_id"] == "113.0")
    c = comp_at(b113, "p683-L35")
    check("p683 twin: second scrambled trio regroups (L34+L35+L36), "
          "finish rescued",
          c["anchors"] == ["p683-L34", "p683-L35", "p683-L36"]
          and c["description"] == "Access Control Concealed Vert Rod Exit"
          and c["finish"] == "US32D"
          and "conservation_failed" not in c["flags"])

# --- structural invariants (recomputed, not read back) ------------------------


def check_roselle(root: Path) -> None:
    print("Roselle (wide-table dialect: location correct; fields are the "
          "documented 3b-adapter boundary -- degradation pinned as fact)")
    _, sets = load_jsonl(next((root / "Roselle_Public_Library")
                              .glob("*.sets.jsonl")))
    check("33 sets, 1.1 first, 8.3 last",
          len(sets) == 33 and sets[0]["set_id"] == "1.1"
          and sets[-1]["set_id"] == "8.3")
    b11 = next(b for b in sets if b["set_id"] == "1.1")
    check("1.1 location == step-2 spans verbatim (p15 L5-8)",
          b11["location"] == [{"page": 15, "lines": [5, 8],
                               "bbox": [55.3, 87.07, 502.25, 111.01]}])
    # Roselle round (2026-08-19): still zero 3b rows, but the header row
    # ("SET HARDWARE TYPE MANUFACTURER - PRODUCT QTY.FINISH NOTES", filed
    # as page furniture, QTY.FINISH glued) pins header-grade zones, and
    # the 228 LLM readings that sat refused in slot_recovery_unvalidatable
    # get re-tried against the zones: 122 qty + 111 mfr + 117 finish
    # delivered, every one flagged *_from_column, none high-confidence.
    comps = [c for b in sets for c in b["components"]]
    hdr = [c for c in comps if "component_from_header_line" in c["flags"]]
    check("Roselle risen: 197 components (164 body + 33 header-line "
          "synths -- the first row of every set rode the set header and "
          "was delivered as a set DESCRIPTION until 2026-08-19), zones "
          "deliver qty 155 / mfr 144 / finish 150 (was 0/0/0); all 33 "
          "header rows carry their qty (the seam-order bug that swallowed "
          "12 into the catalog blob is pinned dead)",
          len(comps) == 197 and len(hdr) == 33
          and all(c["qty"] is not None for c in hdr)
          and not any((c["catalog_number"] or "").rstrip()[-2:] in
                      (" 2", " 3", " 4", " 5") for c in hdr)
          and sum(1 for c in comps if c["qty"] is not None) == 155
          and sum(1 for c in comps if c["mfr"] is not None) == 144
          and sum(1 for c in comps if c["finish"] is not None) == 150
          and all("component_without_row" in c["flags"]
                  or "component_from_header_line" in c["flags"]
                  for c in comps)
          and all(any(ff.startswith(("slot_filled_from_column:qty",))
                      for ff in c["flags"]) for c in comps
                  if c["qty"] is not None)
          and all(any(ff.endswith(f":mfr={c['mfr']}")
                      and "_from_column" in ff
                      for ff in c["flags"]) for c in comps
                  if c["mfr"] is not None)
          and all(any(ff.endswith(f":finish={c['finish']}")
                      and "_from_column" in ff for ff in c["flags"])
                  for c in comps if c["finish"] is not None),
          str((len(comps),
               sum(1 for c in comps if c["qty"] is not None),
               sum(1 for c in comps if c["mfr"] is not None),
               sum(1 for c in comps if c["finish"] is not None))))
    h46 = next(c for b in sets if str(b["set_id"]) == "4.6"
               for c in b["components"]
               if "component_from_header_line" in c["flags"])
    check("4.6 header row splits mechanically: 5 / MORTISE HINGE / IVES / "
          "5BB1 4.5\" x 4.5\" / 613 (OIL RUBBED BRONZE) / notes",
          h46["qty"] == 5 and h46["description"] == "MORTISE HINGE"
          and h46["mfr"] == "IVES"
          and h46["catalog_number"] == '5BB1 4.5" x 4.5"'
          and h46["finish"] == "613 (OIL RUBBED BRONZE)"
          and h46["notes"] == ["FULL MORTISE, FIVE KNUCKLE BB HINGE"],
          str(h46)[:160])
    check("every set that got a header component cleared its trailer-"
          "derived description (the component is no longer a title)",
          all(b["description"] is None for b in sets
              if any("component_from_header_line" in c["flags"]
                     for c in b["components"])))
    check("no component claims high assembly confidence (no-row semantics)",
          all(c["confidence"]["assembly"] != "high"
              for b in sets for c in b["components"]))


def check_forest_park(root: Path) -> None:
    print("Forest Park (decimal-qty book, 5-row set spanning the tail-net "
          "page; hand-checked 2026-08-18)")
    _, sets = load_jsonl(next((root / "Forest_Park_School")
                              .glob("*.sets.jsonl")))
    check("exactly one set, id 1, doors line captured",
          len(sets) == 1 and sets[0]["set_id"] == "1"
          and [d["text"] for d in sets[0]["doors"]]
          == ["Doors: 256a, 2p57a"])
    s = sets[0]
    check("location spans both pages (p262 L36-39 + p263 L2-4)",
          [[loc["page"]] + loc["lines"] for loc in s["location"]]
          == [[262, 36, 39], [263, 2, 4]])
    got = [(c["anchors"][0], c["qty"], c["description"], c["catalog_number"],
            c["mfr"], c["finish"]) for c in s["components"]]
    check("all 5 components assembled with mechanical fields from the rule "
          "side",
          got == [("p262-L38", 3, "Hinge", "FBB179 NRP 4.5X4.5",
                   "BES", "26D"),
                  ("p262-L39", 1, "Mortise Lock-Storeroom",
                   "45H-0D-15H LESS CYLINDER", "BES", "626"),
                  ("p263-L02", 1, "Mortise Cylinder",
                   "CR1000-XXX-CAM-7 59D1", "C-R", "626"),
                  # zone round 2026-08-19: the recovered tokens LEAVE the
                  # catalog once every occurrence sits inside its slot's
                  # zone -- position owns column membership, so the catalog
                  # stops carrying the finish/mfr cells' ink.
                  ("p263-L03", 1, "Surface Overhead Stop",
                   "4420 SERIES", "ABH", "US32D"),
                  ("p263-L04", 3, "Silencers", "500", "BRN", "Gray")],
          str(got))
    check("the two once-null rows now carry column recoveries (ABH not in "
          "the book's legend, US32D vs legend 32D, Gray non-BHMA -- all "
          "three sit at the induced column x0; was rejected before "
          "2026-08-18)",
          any(f == "slot_recovered_from_column:mfr=ABH"
              for f in s["components"][3]["flags"])
          and any(f == "slot_recovered_from_column:finish=US32D"
                  for f in s["components"][3]["flags"])
          and any(f == "slot_recovered_from_column:finish=Gray"
                  for f in s["components"][4]["flags"]),
          str(s["components"][3]["flags"] + s["components"][4]["flags"]))
    check("census ok, nothing missed / phantom / duplicated, 3 column "
          "recoveries and zero rejections on record",
          s["reconciliation"]["census"] == "ok"
          and s["reconciliation"]["missed_lines"] == 0
          and s["reconciliation"]["phantom_anchors"] == 0
          and s["reconciliation"]["duplicate_anchors"] == 0
          and s["reconciliation"]["slot_recovered"] == 3
          and s["reconciliation"]["slot_rejected"] == 0)


def check_ami(root: Path) -> None:
    print("AMI (bare door_header dialect: door numbers now reach doors[], "
          "2026-08-18)")
    _, sets = load_jsonl(next((root / "AMI__Copy_").glob("*.sets.jsonl")))
    check("44 sets, every one carries real door numbers (kind=door)",
          len(sets) == 44
          and all(any(d["kind"] == "door" for d in s["doors"])
                  for s in sets))
    s03 = next(s for s in sets if s["set_id"] == "03")
    check("set 03 doors == ['120 157']",
          [d["text"] for d in s03["doors"] if d["kind"] == "door"]
          == ["120 157"])
    check("zero demoted (the door-number fake rows are gone at the source)",
          sum(len(s["demoted"]) for s in sets) == 0)
    gask = [c for s in sets for c in s["components"]
            if c["description"] and "PERIMETER GASKETING" in c["description"]]
    check("6 by-others gasketing rows: mfr/finish honest null "
          "(no more mfr='DOOR')",
          len(gask) == 6 and all(c["mfr"] is None and c["finish"] is None
                                 for c in gask))


def stream_invariants(root: Path) -> None:
    print("structural invariants, all streams (recomputed from 3b + step 2)")
    n_streams = 0
    grand = Counter()
    for project in sorted(p for p in root.iterdir() if p.is_dir()):
        for sets_path in sorted(project.glob("*.sets.jsonl")):
            n_streams += 1
            name = sets_path.name[:-len(".sets.jsonl")]
            smeta, sblocks = load_jsonl(sets_path)
            rmeta, rblocks = load_jsonl(
                sets_path.with_name(f"{name}.rules.jsonl"))
            _, s2blocks = load_jsonl(
                Path(rmeta["source_blocks"]))
            rules_by_seq = {b["seq"]: b for b in rblocks}
            spans_by_seq = {b["seq"]: b["spans"] for b in s2blocks}
            # independent recompute of the rotated-border set (screenshot
            # round): the census below must see the same lines 3c dropped
            geo_path = sets_path.with_name(f"{name}.geometry.jsonl")
            geo_recs = {}
            if geo_path.exists():
                for line in geo_path.read_text("utf-8").splitlines():
                    g = json.loads(line)
                    geo_recs[g["anchor"]] = g
            rotated = rotated_lines(geo_recs)

            ok_counts = (len(sblocks) == len(rblocks)
                         and {b["seq"] for b in sblocks}
                         == {b["seq"] for b in rblocks})
            ok_census = ok_mech = ok_loc = ok_empty = ok_cons = ok_dem = True
            for sb in sblocks:
                rb = rules_by_seq[sb["seq"]]
                rows_by_anchor = {r["anchor"]: r for r in rb["rows"]}
                stitched = {s["anchor"] for r in rb["rows"]
                            for s in r["stitched"]}
                required = ([r["anchor"] for r in rb["rows"]]
                            + [n["anchor"] for n in rb["note_lines"]]
                            + [u["anchor"] for u in rb["unresolved"]
                               if u["kind"] != "blank" and u["text"].strip()])
                consumed = Counter(
                    a for group in (sb["components"], sb["demoted"],
                                    sb["set_notes"])
                    for item in group for a in item["anchors"]
                    if "component_from_header_line" not in item.get(
                        "flags", []))
                # rotated border words (screenshot round) are legally
                # unconsumed: they may appear in NO container, and every
                # one must be accounted for by a rotated_* flag count
                rot_req = [a for a in required if a in rotated]
                if rot_req:
                    n_flagged = sum(
                        int(f.rsplit(":", 1)[1]) for f in sb["flags"]
                        if f.startswith(("set_note_rotated_dropped:",
                                         "rotated_lines_unclaimed:",
                                         "component_rotated_dropped:")))
                    n_flagged += sum(
                        int(f.rsplit(":", 1)[1])
                        for item in (list(sb["set_notes"])
                                     + list(sb["components"])
                                     + [d["was"] for d in sb["demoted"]])
                        for f in item.get("flags", [])
                        if f.startswith("rotated_lines_dropped:"))
                    if (n_flagged != len(rot_req)
                            or any(consumed[a] for a in rot_req)):
                        ok_census = False
                    required = [a for a in required if a not in rotated]
                if (any(consumed[a] != 1 for a in required)
                        or any(a not in set(required) | stitched
                               or n > 1 for a, n in consumed.items())):
                    ok_census = False
                blanks = {u["anchor"] for u in rb["unresolved"]
                          if u["kind"] == "blank" or not u["text"].strip()}
                if {u["anchor"] for u in sb["unassigned"]} != blanks:
                    ok_census = False

                for c in (list(sb["components"])
                          + [d["was"] for d in sb["demoted"]]):
                    crows = [rows_by_anchor[a] for a in c["anchors"]
                             if a in rows_by_anchor]
                    if not crows:
                        # zone round 2026-08-19: a row-less component may
                        # carry unit/mfr/finish measured out of its slot
                        # zone -- every such value arrives flagged, and the
                        # corpus-wide recompute below re-verifies the x0.
                        filled = {m.group(1) for f in c["flags"]
                                  if (m := RE_ANY_FILL.match(f))}
                        filled |= {f.split(":", 1)[1].split("=", 1)[0]
                                   for f in c["flags"] if f.startswith(
                                       "slot_recovered_from_column:")}
                        for role in ("qty", "unit", "mfr", "finish"):
                            if c[role] is not None and role not in filled:
                                ok_mech = False
                        continue
                    r = crows[0]
                    if c["qty"] != r["qty"]:
                        ok_mech = False
                    if c["unit"] != r["unit"] and not (
                            r["unit"] is None and any(
                                (m := RE_ANY_FILL.match(f))
                                and m.group(1) == "unit"
                                and m.group(2) == c["unit"]
                                for f in c["flags"])):
                        ok_mech = False
                    for role in ("mfr", "finish"):
                        if r[role] is not None:
                            if c[role] != r[role] and not (
                                    c[role] is None and
                                    f"slot_vetoed_off_column:{role}="
                                    f"{r[role]}" in c["flags"]):
                                ok_mech = False
                        elif c[role] is not None:
                            voc = rmeta["vocabulary"][role]
                            legit = (c[role] in voc["legend"]
                                     or c[role] in voc["distribution"])
                            flagged = any(
                                (m := RE_SLOT_RECOVERED.match(f))
                                and m.group(1) == role
                                and m.group(2) == c[role]
                                for f in c["flags"])
                            col = ((rmeta.get("column_bands", {}).get(role)
                                    is not None
                                    or (rmeta.get("slot_zones") or {})
                                    .get(role) is not None) and any(
                                       (m := RE_FROM_COLUMN.match(f))
                                       and m.group(1) == role
                                       and m.group(2) == c[role]
                                       for f in c["flags"]))
                            if not ((legit and flagged) or col):
                                ok_mech = False

                if sb["location"] != spans_by_seq[sb["seq"]]:
                    ok_loc = False
                if sb["empty"] and (sb["components"] or sb["demoted"]
                                    or sb["set_notes"]
                                    or not sb["llm"]["skipped"]):
                    ok_empty = False
                failed_flags = sum(
                    1 for c in (list(sb["components"])
                                + [d["was"] for d in sb["demoted"]]
                                + list(sb["set_notes"]))
                    if "conservation_failed" in c.get("flags", []))
                if failed_flags != sb["reconciliation"].get(
                        "conservation", {}).get("failed", 0):
                    ok_cons = False
                if any(not d["reason"] for d in sb["demoted"]):
                    ok_dem = False
                grand.update(
                    n_components=len(sb["components"]),
                    n_demoted=len(sb["demoted"]),
                    conservation_failed=sb["reconciliation"]
                    .get("conservation", {}).get("failed", 0))

            check(f"{name[:44]}: block count + seq set match 3b", ok_counts)
            check(f"{name[:44]}: anchor census is a true partition",
                  ok_census)
            check(f"{name[:44]}: mechanical fields rule-equal, or a "
                  "vocabulary/column-validated rescue", ok_mech)
            check(f"{name[:44]}: location equals step-2 spans", ok_loc)
            check(f"{name[:44]}: empty blocks assemble empty, no LLM",
                  ok_empty)
            check(f"{name[:44]}: conservation failures all flagged", ok_cons)
            check(f"{name[:44]}: demotions all carry reasons", ok_dem)

        report_path = project / "assembly_report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text("utf-8"))
            check(f"{project.name[:34]}: report run fully cache-served "
                  "(warm re-run)",
                  all(s["cache_hits"] == s["llm_calls"]
                      for s in report["streams"]))
    check("all 23 streams assembled", n_streams == 23, str(n_streams))
    print(f"  (corpus totals: {grand['n_components']} components, "
          f"{grand['n_demoted']} demoted, "
          f"{grand['conservation_failed']} conservation failures)")


def check_five_new_books(root: Path) -> None:
    print("five new books (2026-08-18): HFH cross-gap set, JC Ryan honest "
          "degradation, Valor two-column, Oswego clean generalisation")
    _, hfh = load_jsonl(next((root / "HFH_DG_-_HOSPITAL")
                             .glob("*.sets.jsonl")))
    # 1932 -> 1919 on 2026-08-18: BULLETIN 023 struck 13 component rows out
    # of this book, and they used to be delivered as live hardware
    check("HFH 190 sets, 1919 components (largest book in the corpus; 13 "
          "fewer than before -- the rows BULLETIN 023 struck out)",
          len(hfh) == 190
          and sum(len(b["components"]) for b in hfh) == 1919,
          str((len(hfh), sum(len(b["components"]) for b in hfh))))
    s197 = next(b for b in hfh if b["set_id"] == "197")
    check("HFH set 197 spans both sides of the three furniture-only pages "
          "(location p163 + p167) -- the rows the old gap rule stranded",
          [(l["page"], tuple(l["lines"])) for l in s197["location"]]
          == [(163, (33, 33)), (167, (1, 5))],
          str(s197["location"]))
    check("HFH set 197 consumed its rows: the by-others line is demoted "
          "WITH a reason, not dropped",
          s197["reconciliation"]["census"] == "ok"
          and len(s197["demoted"]) == 1 and s197["demoted"][0]["reason"])

    _, jc = load_jsonl(next((root / "JC_Ryan_2").glob("*.sets.jsonl")))
    comps = [c for b in jc for c in b["components"]]
    check("JC Ryan 38 sets / 266 components, EX-1.0 first",
          len(jc) == 38 and len(comps) == 266 and jc[0]["set_id"] == "EX-1.0",
          str((len(jc), len(comps), jc[0]["set_id"])))
    # name-vocabulary round (2026-08-18): the book's own tail distribution
    # (Norton/Pemko/Rockwood/Sargent/Securitron, 3b) now validates what the
    # model reads -- LLM reads, rules verify, same contract as everywhere.
    got = Counter(c["mfr"] for c in comps if c["mfr"])
    # zone round (2026-08-19): the mfr COLUMN (rightmost cell-start cluster,
    # measured wobble widens the interval) now fills what the frequency
    # floor and the qty-led row grammar kept out: McKinney lived on 28
    # qty-less hinge rows (0 ever entered the tail sample), ABH/LCN/NGP/
    # Rixson sat under the count>=3 floor.  201 text-validated + 34 zone
    # fills = 235 carriers; the vocabulary still owns confidence, the
    # column owns admission -- same split as the band arm.
    check("JC Ryan: 235 components carry a manufacturer -- 201 validated "
          "against the tail distribution + 34 admitted by the measured mfr "
          "column (McKinney 25 of 28 hinge rows among them)",
          dict(got) == {"Rockwood": 78, "Pemko": 56, "Sargent": 32,
                        "Norton": 29, "McKinney": 25, "Securitron": 10,
                        "LCN": 2, "ABH": 1, "NGP": 1, "Rixson": 1},
          str(dict(got)))
    check("JC Ryan: every admitted mfr is flagged -- text rescue or column "
          "fill, none arrives silently",
          all(any(f == f"slot_recovered_from_text:mfr={c['mfr']}"
                  or f == f"slot_filled_from_column:mfr={c['mfr']}"
                  for f in c["flags"]) for c in comps if c["mfr"]))
    rej = [f.split("=", 1)[1] for c in comps for f in c["flags"]
           if f.startswith("slot_recovery_rejected:mfr=")]
    check("JC Ryan: the count>=3 floor still refuses the same 5 low-"
          "frequency proposals on the record; the column then admits the "
          "ones it can measure (dual trace, vocabulary never forged)",
          sorted(rej) == ["ABH", "ABH", "LCN", "LCN", "NGP"], str(rej))
    unval = [c for c in comps
             if any(f.startswith("slot_recovery_unvalidatable:")
                    for f in c["flags"])]
    check("JC Ryan: row-less proposals still drop ON the record "
          "(slot_recovery_unvalidatable), all on component_without_row -- "
          "the zone fill that follows is a separate, flagged admission",
          len(unval) == 33
          and all("component_without_row" in c["flags"] for c in unval),
          str(len(unval)))
    check("JC Ryan: finish stays null throughout (the book has no finish "
          "column -- no finish vocabulary was invented)",
          all(c["finish"] is None for c in comps))
    s10 = next(b for b in jc if b["set_id"] == "1.0")
    lock = next(c for c in s10["components"]
                if c["description"] == "Storeroom/Closet Lock")
    check("JC Ryan set 1.0 lock row splits three ways: catalog '8204 LNMI' "
          "+ mfr Sargent (was: catalog carrying the maker name, mfr null)",
          lock["catalog_number"] == "8204 LNMI" and lock["mfr"] == "Sargent"
          and lock["qty"] == 1,
          str((lock["catalog_number"], lock["mfr"])))
    check("JC Ryan: every conservation fallback is flagged, none silent",
          all("conservation_failed" in c["flags"]
              for c in comps if c["confidence"]["assembly"] == "low"))
    check("JC Ryan: conservation failures collapsed 239 -> 40 -> 5 (name "
          "vocabulary closed the ledger for the row-text makers, the zone "
          "round closes it for the hinge rows: a value delivered in a slot "
          "counts as covered)",
          sum(1 for c in comps if "conservation_failed" in c["flags"]) == 5,
          str(sum(1 for c in comps if "conservation_failed" in c["flags"])))

    _, val = load_jsonl(next((root / "Valor_Acres_Building_E")
                             .glob("*.sets.jsonl")))
    vcomps = [c for b in val for c in b["components"]]
    # 240 -> 234 on 2026-08-18: Rev 2 struck six component rows (ELECT
    # STRIKE / WALL STOP / POWER SUPPLY / CARD READER ...) clean off the page
    check("Valor 37 sets / 234 components (six fewer: the rows Rev 2 struck "
          "out); two-column book: finish carried, mfr genuinely absent",
          len(val) == 37 and len(vcomps) == 234
          and all(c["mfr"] is None for c in vcomps)
          and sum(1 for c in vcomps if c["finish"]) >= 150,
          str((len(val), len(vcomps),
               sum(1 for c in vcomps if c["finish"]))))
    check("Valor set 01 description is the bare title, joiner stripped: "
          "'(Door U1 - Interior Unit Entry Swing Doors)'",
          val[0]["description"]
          == "(Door U1 \u2013 Interior Unit Entry Swing Doors)",
          repr(val[0]["description"]))
    _, lyo = load_jsonl(next((root / "Lyons_Township_HS")
                             .glob("*.sets.jsonl")))
    check("Lyons Not-Used ghosts read 'Not Used', not '- Not Used'",
          all(b["description"] == "Not Used"
              for b in lyo if b["set_id"] in ("05", "16", "21", "22")))
    _, mvs = load_jsonl(next((root / "Market_View_Apartments")
                             .glob("*.sets.jsonl")))
    check("Market View set 03 description drops the joiner: "
          "'HARDWARE BY DOOR MFG.'",
          next(b for b in mvs if b["set_id"] == "03")["description"]
          == "HARDWARE BY DOOR MFG.")

    _, osw = load_jsonl(next(
        (root / "Village_of_Oswego_New_Public_Works_Facility__Copy_")
        .glob("*.sets.jsonl")))
    ocomps = [c for b in osw for c in b["components"]]
    check("Oswego 39 sets / 370 components == its 370 rule rows (clean "
          "generalisation of the HARDWARE GROUP NO. dialect)",
          len(osw) == 39 and len(ocomps) == 370,
          str((len(osw), len(ocomps))))
    check("Oswego mechanical fields survive assembly (mfr on 98%+ of rows)",
          sum(1 for c in ocomps if c["mfr"]) >= 360,
          str(sum(1 for c in ocomps if c["mfr"])))
    check("Woodridge assembled nothing (no blocks to assemble)",
          not list((root / "Woodridge_Public_Works").glob("*.sets.jsonl")))


def check_column_bands(root: Path) -> None:
    print("column bands (2026-08-18): the schedule's own column x0 admits "
          "what the vocabulary cannot know; off-band stays rejected")
    # Gerrard: the book's mfr legend is names-without-codes and DE appears
    # once in 466 rows -- no list can admit it; its x0 (535.68) is the mfr
    # column of every sibling row.  Before: silently cut by assign_slots'
    # rest trim, rescued only because the block was broken.
    _, _, sets = load_sets(root, "2353_Gerrard_Street_Shelter",
                           "Hdw_Spec_Sch-IFT_5-*.sets.jsonl")
    al = next(s for s in sets if s["set_id"] == "AL")
    c = comp_at(al, "p19-L07")
    check("Gerrard AL exit device: mfr DE from the column (was null)",
          c["mfr"] == "DE" and c["finish"] == "630"
          and any(RE_FROM_COLUMN.match(f) and f.endswith(":mfr=DE")
                  for f in c["flags"]),
          f"{c['mfr']}|{c['flags']}")
    meta, _ = load_jsonl(next((root / "2353_Gerrard_Street_Shelter")
                              .glob("Hdw_Spec_Sch-IFT_5-*.rules.jsonl")))
    check("Gerrard bands induced at one x0 each: finish 481.7 / mfr 535.7",
          meta["column_bands"]["finish"]["x0"] == 481.7
          and meta["column_bands"]["mfr"]["x0"] == 535.7
          and meta["column_bands"]["finish"]["support"] >= 300
          and meta["column_bands"]["mfr"]["support"] >= 350,
          str(meta["column_bands"]))
    # the duplicate binding prints an "S" finish cell on 11 rows per
    # stream; the vocabulary called every one garbage, the column calls
    # them the document's own value.
    n_s = 0
    for sf in sorted((root / "2353_Gerrard_Street_Shelter")
                     .glob("*.sets.jsonl")):
        _, ss = load_jsonl(sf)
        n_s += sum(1 for s in ss for c in s["components"]
                   if c["finish"] == "S"
                   and any(RE_FROM_COLUMN.match(f) for f in c["flags"]))
    # All 30 "S" cells deliver now: 18 the model claimed (validated at the
    # band), 12 it never claimed -- the zone round fills those straight
    # from the measured finish interval, so the delivery no longer depends
    # on whether the model happened to read the lone letter.  (History: 22
    # while the legend carried 7 bogus codes, 18 after the context was
    # corrected, 30 once position finished the job, 2026-08-19.)
    check("Gerrard 'S' finish cells: all 30 delivered (18 model-claimed at "
          "the band + 12 zone-filled), none left rejected",
          n_s == 30
          and not any("slot_recovery_rejected:finish=S" in f
                      for sf in (root / "2353_Gerrard_Street_Shelter")
                      .glob("*.sets.jsonl")
                      for _, ss in [load_jsonl(sf)]
                      for s in ss for c in s["components"]
                      for f in c["flags"]),
          str(n_s))

    # Village: no legend at all; 630-316 (hyphen compound, x4) and ANCLR
    # (five-letter code, x2) recur at the finish x0 but can never pass the
    # BHMA shape gate into the distribution.
    _, _, vsets = load_sets(
        root, "Village_of_Oswego_New_Public_Works_Facility__Copy_",
        "*.sets.jsonl")
    pulls = [c for s in vsets for c in s["components"]
             if (c["catalog_number"] or "").startswith("9264F")]
    check("Village LONG DOOR PULL x4: finish 630-316, catalog clean, "
          "conservation healed",
          len(pulls) == 4
          and all(c["finish"] == "630-316"
                  and "630-316" not in c["catalog_number"]
                  and "conservation_failed" not in c["flags"]
                  for c in pulls),
          str([(c["catalog_number"], c["finish"]) for c in pulls]))
    ops = [c for s in vsets for c in s["components"]
           if (c["description"] or "").startswith("SURF. AUTO OPERATOR")]
    check("Village auto operator x2: ANCLR back from total token loss",
          len(ops) == 2 and all(c["finish"] == "ANCLR" for c in ops),
          str([(c["finish"], c["flags"]) for c in ops]))

    # silently-cut tokens (inner slot accepted, outer failed): the 3b
    # column_filled path is the only way these can come back.
    _, _, lsets = load_sets(root,
                            "Livelle_Mulholland_-_Life_Plan_Community",
                            "*.sets.jsonl")
    c = next(c for s in lsets for c in s["components"]
             if "p697-L29" in c["anchors"])
    check("Livelle p697-L29: silently-cut WE reaches mfr via the column",
          c["mfr"] == "WE"
          and any(RE_FROM_COLUMN.match(f) and f.endswith(":mfr=WE")
                  for f in c["flags"]),
          f"{c['mfr']}|{c['flags']}")
    _, _, msets = load_sets(root, "Market_View_Apartments", "*.sets.jsonl")
    c = next(c for s in msets for c in s["components"]
             if "p777-L39" in c["anchors"])
    check("Market View p777-L39: TBD is what the mfr cell prints",
          c["mfr"] == "TBD"
          and any(RE_FROM_COLUMN.match(f) and f.endswith(":mfr=TBD")
                  for f in c["flags"]),
          f"{c['mfr']}|{c['flags']}")

    # negative controls: no usable band -> vocabulary-only, byte-stable.
    for proj, why in (("JC_Ryan_2", "name-type book, no slot"),
                      ("Roselle_Public_Library", "wide table, no rows")):
        m, _ = load_jsonl(next((root / proj).glob("*.rules.jsonl")))
        check(f"{proj}: no band ({why}) -> today's behaviour kept",
              not m.get("column_bands"), str(m.get("column_bands")))
    # HFH flipped from negative control to banded on 2026-08-18: the old
    # "multi-template, share 0.89" was a measurement artifact -- the icon
    # glyphs set the line bbox top 1.54pt above the text baseline, so
    # geometry_for's top-match dropped every text word on 1,828 of 1,940
    # rows and the band vote ran on the icon-only residue.  With words
    # owned by y-interval the same book votes 0.99 on one template.
    m, _ = load_jsonl(next((root / "HFH_DG_-_HOSPITAL").glob("*.rules.jsonl")))
    b = m.get("column_bands") or {}
    check("HFH bands exist after the geometry repair (one template, "
          "share ~0.99 -- the 0.89 was icon-only geometry, not the book)",
          b.get("finish", {}).get("x0") == 471.2
          and b.get("mfr", {}).get("x0") == 512.0
          and b["finish"]["support"] / b["finish"]["n"] >= 0.98
          and b["mfr"]["support"] / b["mfr"]["n"] >= 0.98,
          str(b))
    rej = [f for sf in (root / "Morris_Bank").glob("*.sets.jsonl")
           for _, ss in [load_jsonl(sf)] for s in ss
           for c in s["components"] for f in c["flags"]
           if f == "slot_recovery_rejected:finish=BL"]
    check("Morris off-band BL stays rejected (found 190pt left of the "
          "column: catalog territory, not the finish cell)",
          len(rej) >= 1, str(rej))

    # structure invariant, independently recomputed: every value a
    # *_from_column flag admitted must sit inside its column in that
    # component's own word geometry -- at the band x0 where a band exists,
    # inside the measured slot ZONE where the zone round admitted it (unit
    # fills verify against the unit zone the same way, 2026-08-19).
    n_col, bad = 0, []
    for proj in sorted(p.name for p in root.iterdir() if p.is_dir()):
        for sf in sorted((root / proj).glob("*.sets.jsonl")):
            rf = sf.with_name(sf.name.replace(".sets.", ".rules."))
            gf = sf.with_name(sf.name.replace(".sets.", ".geometry."))
            meta, _ = load_jsonl(rf)
            bands = meta.get("column_bands") or {}
            zones = meta.get("slot_zones") or {}
            geo = {}
            for line in gf.read_text("utf-8").splitlines():
                g = json.loads(line)
                geo[g["anchor"]] = g["words"]
            _, ss = load_jsonl(sf)
            for s in ss:
                for c in s.get("components", []):
                    for f in c["flags"]:
                        m = RE_FROM_COLUMN.match(f)
                        if not m:
                            continue
                        n_col += 1
                        role, value = m.group(1), m.group(2)
                        band = bands.get(role)
                        zone = zones.get(role)
                        vt = value.split()
                        ok = False
                        for a in c["anchors"]:
                            words = geo.get(a, [])
                            for i in range(len(words) - len(vt) + 1):
                                if not all(words[i + j]["text"] == vt[j]
                                           for j in range(len(vt))):
                                    continue
                                x = words[i]["x0"]
                                if (band and abs(x - band["x0"])
                                        <= band["tol"]):
                                    ok = True
                                if zone:
                                    hi = (zone["hi"] if zone["hi"] is not None
                                          else 1e9)
                                    if zone["lo"] <= x < hi:
                                        ok = True
                        if not ok and zone:
                            # per-occurrence semantics (screenshot round):
                            # Livelle's scrambled rows print "Dark" and
                            # "Bronze" on separate lines, both inside the
                            # finish zone -- mirror validate_slot's arm
                            hi = (zone["hi"] if zone["hi"] is not None
                                  else 1e9)
                            ok = all(
                                any(w["text"] == t
                                    and zone["lo"] <= w["x0"] < hi
                                    for a in c["anchors"]
                                    for w in geo.get(a, []))
                                for t in vt)
                        if not ok:
                            bad.append(f"{proj}:{f}")
    check("every *_from_column value re-verified inside its column "
          "(band x0 or slot zone, independent recompute over geometry)",
          n_col >= 100 and not bad, f"n={n_col} bad={bad[:5]}")


def check_zone_round(root: Path) -> None:
    print("zone round (2026-08-19): position owns column membership -- "
          "fills close the ledger, detaches stop the catalog carrying "
          "other columns' ink")
    # the user-facing case that started the round: HFH set 001's by-others
    # rows (no qty printed; unit EA at the unit column, B/O at the mfr
    # column, the responsibility note riding the catalog column)
    _, hfh = load_jsonl(next((root / "HFH_DG_-_HOSPITAL")
                             .glob("*.sets.jsonl")))
    s1 = next(b for b in hfh if b["set_id"] == "001")
    pair = [c for c in s1["components"]
            if c["description"] in ("AUTOMATIC OPERATOR", "WAVE ACTUATOR")]
    check("HFH 001 by-others pair: unit=EA and mfr=B/O measured out of "
          "their zones, BY RELATED SECTION stays a note, qty honestly null "
          "(the page prints none), conservation ledger closed",
          len(pair) == 2 and all(
              c["unit"] == "EA" and c["mfr"] == "B/O" and c["qty"] is None
              and c["notes"] == ["BY RELATED SECTION 08 71 13"]
              and "slot_filled_from_column:unit=EA" in c["flags"]
              and "slot_filled_from_column:mfr=B/O" in c["flags"]
              and "conservation_failed" not in c["flags"]
              for c in pair),
          str([(c["unit"], c["mfr"], c["flags"]) for c in pair])[:200])
    # JC Ryan set 5.0: the qty-less hinge row (McKinney lived on 28 such
    # rows, 0 ever entered the name-vocabulary sample) and the ABH row the
    # count>=3 floor refused -- both admitted by the measured column now,
    # both still carrying their refusal trace
    _, jc = load_jsonl(next((root / "JC_Ryan_2").glob("*.sets.jsonl")))
    s2 = next(b for b in jc if b["set_id"] == "2.0")
    hinge = next(c for c in s2["components"]
                 if c["description"] == "Hinge, Full Mortise")
    check("JC 2.0 hinge: mfr=McKinney from the column on a row the qty-led "
          "grammar never saw (component_without_row + fill flag)",
          hinge["mfr"] == "McKinney"
          and "component_without_row" in hinge["flags"]
          and "slot_filled_from_column:mfr=McKinney" in hinge["flags"],
          str(hinge["flags"]))
    # the measured zone starts at the cluster's own wobble edge (425);
    # four hinge rows print McKinney another 1-4pt further left and stay
    # null WITH their unvalidatable trace -- the boundary is measured, not
    # stretched to make the number pretty (25 of 29 deliver)
    shadow = {b["set_id"]: [f for f in c["flags"] if "McKinney" in f]
              for b in jc for c in b["components"]
              if c["description"] == "Hinge, Full Mortise"
              and c["mfr"] is None}
    check("JC wobble shadow: exactly sets 3.0/4.0/4.5/5.0 stay null -- "
          "4.0/4.5/5.0 with the refusal trace, 3.0 the model never even "
          "proposed (nothing to trace); no zone was stretched to make "
          "the number pretty",
          sorted(shadow) == ["3.0", "4.0", "4.5", "5.0"]
          and shadow["3.0"] == []
          and all(shadow[k] ==
                  ["slot_recovery_unvalidatable:mfr=McKinney"]
                  for k in ("4.0", "4.5", "5.0")),
          str(shadow))
    abh = [c for b in jc for c in b["components"]
           if c["mfr"] == "ABH"
           and "slot_recovery_rejected:mfr=ABH" in c["flags"]
           and "slot_filled_from_column:mfr=ABH" in c["flags"]]
    check("JC A500: one ABH sits inside the measured zone -- refused by "
          "the frequency floor AND admitted by the column, dual trace on "
          "one component",
          len(abh) == 1, str(len(abh)))
    # Forest Park: the detach flags behind the pinned five-tuple
    _, fp = load_jsonl(next((root / "Forest_Park_School")
                            .glob("*.sets.jsonl")))
    fpc = {c["description"]: c for b in fp for c in b["components"]}
    check("FP detach provenance: Surface Overhead Stop sheds US32D+ABH, "
          "Silencers sheds Gray -- each move flagged",
          "catalog_detached:finish=US32D" in fpc["Surface Overhead Stop"]["flags"]
          and "catalog_detached:mfr=ABH" in fpc["Surface Overhead Stop"]["flags"]
          and "catalog_detached:finish=Gray" in fpc["Silencers"]["flags"],
          str(fpc["Surface Overhead Stop"]["flags"]))
    # Bridgeport negative control: the book PRINTS the finish twice (inline
    # in the catalog cell + in the finish column); the inline occurrence is
    # outside the finish zone, so the catalog keeps its own ink
    _, bp = load_jsonl(next((root / "81-85_Bridgeport")
                            .glob("08-70-00-Hardware-Schedule-*.sets.jsonl")))
    keep = [c for b in bp for c in b["components"]
            if c["finish"] and (c["catalog_number"] or "")
            .endswith(" " + c["finish"])
            and not any(f.startswith("catalog_detached:")
                        for f in c["flags"])]
    check("Bridgeport inline-echo control: catalog cells that genuinely "
          "print the finish keep it (occurrence off-zone blocks the "
          "detach), 100+ such components untouched",
          len(keep) >= 100, str(len(keep)))
    # corpus totals for this round, exact
    n_fill = n_det = n_unit = 0
    for proj in sorted(pp.name for pp in root.iterdir() if pp.is_dir()):
        for sf in sorted((root / proj).glob("*.sets.jsonl")):
            _, ss = load_jsonl(sf)
            for s in ss:
                for c in s.get("components", []):
                    for f in c["flags"]:
                        if f.startswith("slot_filled_from_column:unit="):
                            n_unit += 1
                        if f.startswith("slot_filled_from_column:")                                 and "column_filled" not in f:
                            n_fill += 1
                        if f.startswith("catalog_detached:"):
                            n_det += 1
    # 173/67 -> 182/76 when the veto round's header-fallback zones opened
    # SAT's and Vantage's finish/mfr columns to the fill and detach arms
    # (the SAT finish band had been blocked by the very 28-poison the veto
    # kills -- the header pin routed around it).
    # 182/76 -> 255/80 when Roselle's header zones opened: 122 qty fills
    # join the ledger (qty is zone-verified by its own flag check above)
    # +87 when the 33 Roselle header rows split (33 mfr + 33 finish + 21
    # qty -- 12 header qty cells are genuinely blank)
    # 354/80 -> 351/77 in the screenshot round: SAT's three DOOR CONTACT
    # rows now validate WHT through the zone-interval arm up front
    # (slot_recovered_from_column), so the fill+detach repair of the
    # fallback-glued row never has to run for them
    check("zone-round corpus totals: 351 fills (17 unit, 155 qty) + 77 "
          "detaches, every one behind a flag",
          n_fill == 351 and n_unit == 17 and n_det == 77,
          f"fills={n_fill} unit={n_unit} detaches={n_det}")
    # prose stays prose: the guard keeps the rightmost rule out of
    # StarHardware's specification-paragraph stream
    _, star = load_jsonl(next((root / "StarHardware")
                              .glob("*p53-113.sets.jsonl")))
    dirt = [f for b in star for c in b["components"] for f in c["flags"]
            if f.startswith("slot_filled_from_column:")]
    check("Star prose stream: zero zone fills after the 10% guard (the "
          "ungated rule filled mfr='or'/'625' there -- prose has no "
          "columns to measure)",
          dirt == [], str(dirt[:4]))


def check_column_invariant(root: Path) -> None:
    print("round B (2026-08-19): the column invariant -- on v2 streams no "
          "delivered description/catalog holds a token whose every "
          "measured occurrence sits in the other field's column")
    RE_P = re.compile(r"[^\w]+", re.UNICODE)
    n_scanned = n_reroute = n_mismatch = 0
    leftovers = []
    reroute_kinds = Counter()
    for proj in sorted(p.name for p in root.iterdir() if p.is_dir()):
        for sf in sorted((root / proj).glob("*.sets.jsonl")):
            rf = sf.with_name(sf.name.replace(".sets.", ".rules."))
            gf = sf.with_name(sf.name.replace(".sets.", ".geometry."))
            meta, _ = load_jsonl(rf)
            zones = meta.get("slot_zones") or {}
            if "description" not in zones or "catalog" not in zones:
                continue
            geo = {}
            for line in gf.read_text("utf-8").splitlines():
                g = json.loads(line)
                geo[g["anchor"]] = g["words"]
            ztab = [(nm, zones[nm]) for nm in
                    ("description", "catalog", "finish", "mfr", "unit")
                    if zones.get(nm)]
            _, ss = load_jsonl(sf)
            for s in ss:
                for c in s.get("components", []):
                    n_scanned += 1
                    for f in c["flags"]:
                        if f.startswith("column_rerouted:"):
                            n_reroute += 1
                            reroute_kinds[f.split(":")[1]] += 1
                        if f.startswith("column_mismatch:"):
                            n_mismatch += 1
                    occ = {}
                    for a in c["anchors"]:
                        for w in geo.get(a, []):
                            for nm, zn in ztab:
                                hi = zn["hi"] if zn["hi"] is not None else 1e9
                                if zn["lo"] <= w["x0"] < hi:
                                    occ.setdefault(w["text"], set()).add(nm)
                                    break
                    echo = {RE_P.sub("", v).casefold()
                            for v in (c["finish"], c["mfr"]) if v}
                    if any(ff.startswith("column_mismatch:")
                           for ff in c["flags"]):
                        continue
                    for field, own in (("description", "description"),
                                       ("catalog_number", "catalog")):
                        other = ("catalog" if own == "description"
                                 else "description")
                        for tok in (c.get(field) or "").split():
                            zs = occ.get(tok)
                            nt = RE_P.sub("", tok).casefold()
                            if (zs and len(zs) == 1 and len(nt) >= 3
                                    and nt not in echo
                                    and next(iter(zs)) == other):
                                leftovers.append(
                                    (proj[:18], s["set_id"], field, tok))
    check("column invariant holds: zero provable cross-column tokens left "
          "in description/catalog on the v2 streams (independent recompute "
          "over word geometry; ambiguous, short, and slot-echo tokens are "
          "unprovable by definition and excluded)",
          n_scanned > 4000 and not leftovers,
          f"scanned={n_scanned} leftovers={leftovers[:6]}")
    # 28 -> 25 in the screenshot round: SAT's three DOOR CONTACT rows no
    # longer fall back to the glued row (WHT validates up front), so their
    # desc->notes reroute of "AS SPECIFIED IN DIVISION" never has to run
    # -- the LLM's own split already delivered the phrase whole
    check("round-B ledger: 25 reroutes (7 desc->catalog / 12 desc->notes / "
          "6 catalog->description) + 90 grid-mismatch rows kept on the "
          "record",
          n_reroute == 25 and n_mismatch == 90
          and reroute_kinds == Counter({"description->notes": 12,
                                        "description->catalog": 7,
                                        "catalog->description": 6}),
          f"{dict(reroute_kinds)} mismatch={n_mismatch}")
    # the phrase travels whole: the SAT by-others tail and its HFH sibling
    _, sat = load_jsonl(next((root / "SAT_TDP").glob("*.sets.jsonl")))
    trio = [c for b in sat if str(b["set_id"]) == "C201C"
            for c in b["components"]
            if any(f.startswith("column_rerouted:description->notes:")
                   for f in c["flags"])]
    check("SAT C201C: three by-others tails leave the description as ONE "
          "phrase each (AS SPECIFIED IN DIVISION -> notes; the ungapped "
          "splitter stranded desc='... AS IN', catalog='SPECIFIED "
          "DIVISION')",
          len(trio) == 3 and all(
              "AS SPECIFIED IN DIVISION" in " ".join(c["notes"])
              and c["catalog_number"] is None for c in trio),
          str([(c["description"], c["notes"]) for c in trio])[:180])
    _, door = load_jsonl(next((root / "The_Door_Company__Copy_")
                              .glob("*.sets.jsonl")))
    gask = [c for b in door for c in b["components"]
            if "column_mismatch:description_in_catalog_zone" in c["flags"]]
    check("Vantage grid-mismatch rows: 90 by-others rows whose description "
          "is PRINTED in the catalog column (GASKETING at 274.6) keep "
          "their description -- the book broke its own grid, the reroute "
          "must not finish the job",
          len(gask) == 90 and all(c["description"] for c in gask),
          str(len(gask)))
    # position veto (2026-08-19): the vocabulary cannot refuse a
    # shape-legal squatter ("BY DIVISION 28" made 28 a finish in three
    # books' distributions -- US28 exists); the coordinate can.
    n_veto = Counter()
    veto_vals = Counter()
    bad_veto = []
    for proj in sorted(p.name for p in root.iterdir() if p.is_dir()):
        for sf in sorted((root / proj).glob("*.sets.jsonl")):
            rf = sf.with_name(sf.name.replace(".sets.", ".rules."))
            gf = sf.with_name(sf.name.replace(".sets.", ".geometry."))
            meta, _ = load_jsonl(rf)
            zones = meta.get("slot_zones") or {}
            geo = {}
            for line in gf.read_text("utf-8").splitlines():
                g = json.loads(line)
                geo[g["anchor"]] = g["words"]
            _, ss = load_jsonl(sf)
            for s in ss:
                for c in s.get("components", []):
                    for f in c["flags"]:
                        if not f.startswith("slot_vetoed_off_column:"):
                            continue
                        role, val = f.split(":", 1)[1].split("=", 1)
                        n_veto[proj] += 1
                        veto_vals[val] += 1
                        zone = zones.get(role) or {}
                        if zone.get("from") not in ("band", "header"):
                            bad_veto.append(f"{proj}:low-grade-zone")
                            continue
                        hi = (zone["hi"] if zone.get("hi") is not None
                              else 1e9)
                        vt = val.split()
                        for a in c["anchors"]:
                            words = geo.get(a, [])
                            for i in range(len(words) - len(vt) + 1):
                                if all(words[i + j]["text"] == vt[j]
                                       for j in range(len(vt)))                                         and zone["lo"] - 10 <= words[i]["x0"]                                         < hi + 10:
                                    bad_veto.append(f"{proj}:{f}:in-zone")
                        refilled = any(
                            ff.startswith(f"slot_filled_from_column:{role}=")
                            for ff in c["flags"])
                        if c[role] is not None and not refilled:
                            bad_veto.append(f"{proj}:{f}:still-delivered")
    # Bridgeport sets 56/83: veto kills the inline 626 the text parse
    # grabbed, the fill then reads 626/622 off the actual finish column,
    # the detach clears the catalog echo -- three flagged steps, wrong
    # value replaced by the cell's own ink.  A refill on fresh in-zone
    # evidence is the system working, not the veto failing.
    check("position veto: 483 riders stripped (finish=28 x389 dead, plus "
          "71/08/13 section fragments, MFR header echoes, model numbers "
          "posing as finishes) -- every veto re-verified off-zone on a "
          "band/header-grade zone, none still delivered unrepaired",
          sum(n_veto.values()) == 483 and veto_vals["28"] == 389
          and not bad_veto,
          f"n={sum(n_veto.values())} 28={veto_vals['28']} "
          f"bad={bad_veto[:4]}")
    check("veto evidence gate: JC Ryan's rightmost-grade zone vetoes "
          "nothing (the wobble-shadow pages would amputate 9 correct "
          "names -- admission may ride a cluster, destruction may not)",
          n_veto.get("JC_Ryan_2", 0) == 0, str(dict(n_veto)))
    nf28 = sum(1 for proj in n_veto or [""] for _ in [0])  # placeholder
    left = 0
    for proj in sorted(p.name for p in root.iterdir() if p.is_dir()):
        for sf in sorted((root / proj).glob("*.sets.jsonl")):
            _, ss = load_jsonl(sf)
            left += sum(1 for s in ss for c in s.get("components", [])
                        if c.get("finish") == "28")
    check("corpus finish=='28' extinct (was 389 across HFH/SAT/Village, "
          "delivered flagless as legitimate data)",
          left == 0, str(left))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/out/step3")
    check_vantage(root)
    check_bridgeport(root)
    check_livelle(root)
    check_roselle(root)
    check_five_new_books(root)
    check_forest_park(root)
    check_ami(root)
    check_column_bands(root)
    check_zone_round(root)
    check_column_invariant(root)
    stream_invariants(root)
    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILURES:'} "
          f"({len(FAILURES)} failed)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
