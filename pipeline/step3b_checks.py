"""Acceptance checks for step 3b (rule-side block extraction): asserts
hand-verified facts from the sample corpus (2026-08-18):
the four hand-walked target blocks, the Livelle scrambled three-line rows
whose word-level x0 was pulled from the raw PDF, the raw schedule lines
behind every pinned mfr/finish call, and the tail-token tallies.

Structural invariants are NOT read back from the rules report -- partition,
anchor census, body counts, and geometry coverage are recomputed here from
the step-2 block indexes and step-1.5 line streams (found through each rules
file's own meta record), so a bookkeeping bug in step3_rules cannot vouch
for itself.

Usage:  python pipeline/step3b_checks.py [step3_root]
Default: data/out/step3; run from the repo root like every other step.
Prints PASS/FAIL per fact; exit 1 on any failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILURES.append(name)


def load_rules(root: Path, project: str, glob: str):
    path = next((root / project).glob(glob))
    recs = [json.loads(l) for l in path.read_text("utf-8").splitlines()]
    return path, recs[0], recs[1:]


def load_sources(meta: dict):
    """Step-2 blocks and step-1.5 content lines, through the rules meta."""
    recs = [json.loads(l) for l in
            Path(meta["source_blocks"]).read_text("utf-8").splitlines()]
    s2meta, s2blocks = recs[0], recs[1:]
    stream = [json.loads(l) for l in
              Path(s2meta["source_stream"]).read_text("utf-8").splitlines()][1:]
    content = {(r["page"], r["line"]): r for r in stream
               if r["role"] == "content"}
    return s2meta, s2blocks, content


def member_positions(b2: dict, content: dict) -> list:
    """A block's member (page, line) positions, header first."""
    return [(s["page"], ln) for s in b2["spans"]
            for ln in range(s["lines"][0], s["lines"][1] + 1)
            if (s["page"], ln) in content]


BUCKETS = ("rows", "door_lines", "note_lines", "unresolved", "properties")


def census(rb: dict):
    """(recorded anchors, implicitly-consumed line count) for one block.

    A stitched line was consumed through the unresolved bucket and then
    moved into its row's stitch ledger -- its anchor lives there now."""
    anchors = [x["anchor"] for k in BUCKETS for x in rb[k]]
    anchors += [s["anchor"] for r in rb["rows"] for s in r["stitched"]]
    implicit = ((rb["description"] is not None)
                + ("trailer_continuation" in rb["flags"]))
    return anchors, implicit


def block_by_header(blocks: list, anchor: str) -> dict:
    return next(b for b in blocks if b["header_anchor"] == anchor)


def row_at(blocks: list, anchor: str) -> dict:
    return next(r for b in blocks for r in b["rows"] if r["anchor"] == anchor)


def geometry(path: Path) -> dict:
    gp = path.with_name(path.name.replace(".rules.jsonl", ".geometry.jsonl"))
    return {g["anchor"]: g for g in
            (json.loads(l) for l in gp.read_text("utf-8").splitlines())}


def report_stream(root: Path, project: str, meta: dict) -> dict:
    name = Path(meta["source_blocks"]).name.replace(".blocks.jsonl", "")
    rep = json.loads((root / project / "rules_report.json").read_text("utf-8"))
    return next(s for s in rep["streams"] if s["stream"] == name)


# project, rules glob, (rows, mfr, finish, stitched), broken blocks
STREAMS = [
    ("81-85_Bridgeport", "08-70-00-Hardware-Schedule-p3-49.rules.jsonl",
     (734, 0, 557, 0), 1),
    ("81-85_Bridgeport", "08-70-00-Hardware-Schedule_Rev_0-p3-49.rules.jsonl",
     (734, 0, 557, 0), 1),
    ("Livelle_Mulholland_-_Life_Plan_Community", "*.rules.jsonl",
     (1292, 1287, 825, 0), 29),
    ("Lyons_Township_HS", "*.rules.jsonl", (142, 142, 142, 131), 6),
    ("Market_View_Apartments", "*.rules.jsonl", (204, 199, 159, 1), 10),
    ("Morris_Bank", "*p233-263.rules.jsonl", (230, 229, 147, 0), 30),
    ("Morris_Bank", "*p283-290.rules.jsonl", (138, 138, 86, 0), 1),
    ("National_Doors_and_Hardware", "*.rules.jsonl", (88, 86, 82, 1), 5),
    ("The_Door_Company__Copy_", "*.rules.jsonl", (463, 371, 348, 0), 35),
    # Roselle wide table: 3b row grammar is documented-blind (no rows yet);
    # the honest-degradation shape is pinned here as a fact.
    ("Roselle_Public_Library", "*.rules.jsonl", (0, 0, 0, 0), 6),
    # three-book round 2026-08-18 (totals after the doors/U-id/by-others
    # round: +18 operator rows on Gerrard, AMI door-number fake rows freed)
    ("2353_Gerrard_Street_Shelter", "2.02_*.rules.jsonl",
     (466, 386, 326, 0), 28),
    ("2353_Gerrard_Street_Shelter", "Hdw_*.rules.jsonl",
     (466, 386, 326, 0), 28),
    ("AMI__Copy_", "*.rules.jsonl", (398, 377, 349, 0), 29),
    ("Forest_Park_School", "*.rules.jsonl", (5, 4, 3, 0), 0),
]

LIVELLE_CATALOG_TAILS = {"2006M", "2113AV", "2221APK", "312CR", "332CS",
                         "355CS"}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/out/step3")

    print("[all 13 streams: outputs exist; partition, census, and body "
          "counts recomputed against step 2 / step 1.5]")
    loaded = {}
    n_blocks_total = 0
    bad_partition = bad_census = bad_bodies = bad_headers = 0
    bad_geo_sets = bad_geo_sort = 0
    stop_leak = catalog_leak = 0
    for project, glob, totals, n_broken in STREAMS:
        path, meta, blocks = load_rules(root, project, glob)
        loaded[(project, glob)] = (path, meta, blocks)
        s2meta, s2blocks, content = load_sources(meta)
        n_blocks_total += len(blocks)
        check(f"{path.name}: block count matches step 2",
              len(blocks) == s2meta["chunks"]["n_blocks"] == len(s2blocks))

        by_seq = {b["seq"]: b for b in s2blocks}
        broken_members = set()
        for rb in blocks:
            b2 = by_seq[rb["seq"]]
            pos = member_positions(b2, content)
            anchors, implicit = census(rb)
            if rb["partition"]["n_body_lines"] != rb["partition"]["consumed"]:
                bad_partition += 1
            if len(anchors) != len(set(anchors)) \
                    or len(anchors) + implicit != rb["partition"]["consumed"]:
                bad_census += 1
            if rb["partition"]["n_body_lines"] != len(pos) - 1:
                bad_bodies += 1
            if rb["header_anchor"] != b2["header"]["anchor"]:
                bad_headers += 1
            broken_members.update(content[p]["anchor"] for p in pos)

        geo = geometry(path)
        # zone round 2026-08-19: geometry covers every member line of every
        # block (was: broken blocks + rows + stitch sources) -- 3c's zone
        # fill needs word x0 on the row-less note/unresolved lines too
        # (HFH's unit-led by-others rows, JC Ryan's qty-less hinge rows).
        expected_geo = set(broken_members)
        for b in blocks:
            for r in b["rows"]:
                expected_geo.add(r["anchor"])
                expected_geo.update(s["anchor"] for s in r["stitched"])
        if set(geo) != expected_geo:
            bad_geo_sets += 1
        if any([w["x0"] for w in g["words"]]
               != sorted(w["x0"] for w in g["words"]) for g in geo.values()):
            bad_geo_sort += 1

        vocab = meta["vocabulary"]
        if "BY" in vocab["mfr"]["distribution"] \
                or "BY" in vocab["mfr"]["legend"]:
            stop_leak += 1
        if LIVELLE_CATALOG_TAILS & set(vocab["finish"]["distribution"]):
            catalog_leak += 1

        got = (sum(len(b["rows"]) for b in blocks),
               sum(1 for b in blocks for r in b["rows"] if r["mfr"]),
               sum(1 for b in blocks for r in b["rows"] if r["finish"]),
               sum(len(r["stitched"]) for b in blocks for r in b["rows"]))
        check(f"{path.name}: rows/mfr/finish/stitched = {totals}",
              got == totals, f"got {got}")
        check(f"{path.name}: {n_broken} broken blocks, report agrees",
              sum(b["broken"] for b in blocks) == n_broken
              and len(report_stream(root, project, meta)["broken_blocks"])
              == n_broken)

    check("650 blocks across the 13 streams (step 2's partition carried "
          "over: 535 + 35/35 Gerrard + 44 AMI + 1 Forest Park)",
          n_blocks_total == 650, f"got {n_blocks_total}")
    check("every block: n_body_lines == consumed (partition identity)",
          bad_partition == 0, f"{bad_partition} violations")
    check("every block: bucket anchors unique, census == consumed",
          bad_census == 0, f"{bad_census} violations")
    check("every block: body count == step-2 span content lines minus header",
          bad_bodies == 0, f"{bad_bodies} violations")
    check("every block: header anchor == step-2 header anchor",
          bad_headers == 0, f"{bad_headers} violations")
    check("geometry files carry exactly every block's member lines "
          "(zone round 2026-08-19; was broken+rows+stitched -- the zone "
          "fill reads x0 on row-less lines too)",
          bad_geo_sets == 0, f"{bad_geo_sets} streams off")
    check("geometry words sorted by x0 (reading order preserved)",
          bad_geo_sort == 0)
    print("[slot zones: position-only column intervals, 2026-08-19]")

    def zones_of(project: str, glob: str) -> dict:
        m = json.loads(next((root / project).glob(glob))
                       .read_text("utf-8").splitlines()[0])
        return m.get("slot_zones") or {}

    hz = zones_of("HFH_DG_-_HOSPITAL", "*.rules.jsonl")
    check("HFH slot zones measured: unit[107,140) finish[469,510) "
          "mfr[510,inf) -- the book that had NO position data before the "
          "geometry repair now carries all three intervals",
          round(hz["unit"]["lo"]) == 107 and round(hz["unit"]["hi"]) == 140
          and round(hz["finish"]["lo"]) == 469
          and round(hz["finish"]["hi"]) == 510
          and round(hz["mfr"]["lo"]) == 510 and hz["mfr"]["hi"] is None,
          str(hz))
    jz = zones_of("JC_Ryan_2", "*.rules.jsonl")
    check("JC Ryan: mfr zone from the rightmost cell-start cluster "
          "(no band ever forms -- 3b assigns no mfr, so the value-seeded "
          "vote has zero sample); no finish/unit zone invented",
          jz.get("mfr", {}).get("from") == "rightmost"
          and jz["mfr"]["hi"] is None and "finish" not in jz
          and "unit" not in jz, str(jz))
    sz = zones_of("StarHardware", "*p53-113.rules.jsonl")
    check("StarHardware prose stream: the 10%-of-lines guard keeps the "
          "rightmost rule OUT (ungated it filled mfr='or'/'625' from prose "
          "tails, measured 2026-08-19)",
          "mfr" not in sz, str(sz))
    check("STOP token 'BY' in no mfr vocabulary "
          "(Vantage's wrapped 'SWITCH BY' tail recurs, must not enter)",
          stop_leak == 0)
    check("Pemko catalog tails (2113AV family) in no finish vocabulary "
          "(BHMA value gate)", catalog_leak == 0)

    print("[confidence semantics: legend books say high, "
          "distribution-only books can never]")
    for project, glob, all_high in (
            ("Morris_Bank", "*p233-263.rules.jsonl", 229),
            ("Morris_Bank", "*p283-290.rules.jsonl", 138),
            ("National_Doors_and_Hardware", "*.rules.jsonl", 86)):
        _, _, blocks = loaded[(project, glob)]
        highs = sum(1 for b in blocks for r in b["rows"]
                    if r["mfr"] and r["confidence"]["mfr"] == "high")
        check(f"{project.split('_')[0]} {glob[-22:]}: "
              f"all {all_high} assigned mfr high (closed legend set)",
              highs == all_high, f"got {highs}")
    for project, glob in (
            ("Livelle_Mulholland_-_Life_Plan_Community", "*.rules.jsonl"),
            ("Lyons_Township_HS", "*.rules.jsonl"),
            ("Market_View_Apartments", "*.rules.jsonl"),
            ("The_Door_Company__Copy_", "*.rules.jsonl"),
            ("Roselle_Public_Library", "*.rules.jsonl")):
        _, _, blocks = loaded[(project, glob)]
        confs = {c for b in blocks for r in b["rows"]
                 for c in r["confidence"].values()}
        check(f"{project.split('_')[0]}: no-legend book never claims high",
              "high" not in confs, str(confs))

    print("[Vantage set 103 -- clean five-column target block]")
    path, meta, blocks = loaded[("The_Door_Company__Copy_", "*.rules.jsonl")]
    check("schema mfr@-1 finish@-2, no re-induction",
          meta["column_schema"] == {"mfr_slot": -1, "finish_slot": -2}
          and meta["reinduced"] is None)
    b = block_by_header(blocks, "p389-L06")
    check("5 component rows at p389-L09..L13",
          [r["anchor"] for r in b["rows"]]
          == [f"p389-L{n}" for n in ("09", "10", "11", "12", "13")])
    r = b["rows"][0]
    check("hinge row: qty 3 EA, 652 IVE",
          (r["qty"], r["unit"], r["finish"], r["mfr"])
          == (3, "EA", "652", "IVE"))
    r = row_at([b], "p389-L11")
    check("PERMANENT CORE row: no digit token -> split hint honestly null, "
          "codes still read (626 SCH)",
          r["rest"] == "PERMANENT CORE COORDINATE WITH OWNER"
          and r["split_hint"] is None
          and (r["finish"], r["mfr"]) == ("626", "SCH"))
    r = row_at([b], "p389-L13")
    check("SILENCER row: alpha finish GRY via distribution",
          (r["finish"], r["mfr"]) == ("GRY", "IVE"))
    check("hinge-count boilerplate -> note prose; block clean, partition 8/8",
          [n["anchor"] for n in b["note_lines"] if n["kind"] == "prose"]
          == ["p389-L14", "p389-L15"] and not b["broken"]
          and b["partition"] == {"n_body_lines": 8, "consumed": 8})

    print("[Vantage C200C -- the broken electrical block]")
    b = block_by_header(blocks, "p395-L29")
    r = row_at([b], "p396-L20")
    check("WIRE HARNESS row: mfr SCH, finish null (no valid token at -2)",
          (r["mfr"], r["finish"]) == ("SCH", None))
    check("broken electrical rows keep all codes null for 3c",
          all((row_at([b], a)["mfr"], row_at([b], a)["finish"]) == (None, None)
              for a in ("p396-L21", "p396-L23")))
    check("orphan fragments held unresolved, exactly the hand-walked five",
          {(u["anchor"], u["text"], u["kind"]) for u in b["unresolved"]}
          == {("p396-L09", "VDC", "orphan"),
              ("p396-L10", "(FAIL SECURE)", "orphan"),
              ("p396-L14", "(TO SUIT FRAME)", "orphan"),
              ("p396-L22", "BY DIV 28", "orphan"),
              ("p396-L24", "DIV 28", "orphan")})
    kinds = {n["anchor"]: n["kind"] for n in b["note_lines"]}
    check("OPERATION: opens an end-scope note run that crosses the page",
          kinds.get("p396-L28") == "note_header"
          and kinds.get("p397-L03") == "note"
          and kinds.get("p397-L04") == "note")
    check("block flagged broken (orphan_fragment) -> geometry attached",
          b["broken"] and "orphan_fragment" in b["flags"])

    print("[Bridgeport Heading #2 -- finish@-1 only, no mfr column exists]")
    path, meta, blocks = loaded[
        ("81-85_Bridgeport", "08-70-00-Hardware-Schedule-p3-49.rules.jsonl")]
    check("schema finish@-1, mfr absent (book-level induction result)",
          meta["column_schema"] == {"finish_slot": -1, "mfr_slot": None})
    check("finish vocabulary: 11 legend codes + exactly "
          "{626/626, 626/630, CA, GRY} from distribution (no fire ratings)",
          len(meta["vocabulary"]["finish"]["legend"]) == 11
          and meta["vocabulary"]["finish"]["distribution"]
          == ["626/626", "626/630", "CA", "GRY"])
    b = block_by_header(blocks, "p4-L01")
    check("Item and dimension lines -> door side",
          {d["anchor"] for d in b["door_lines"]} == {"p4-L02", "p4-L03"})
    r = row_at([b], "p4-L15")
    check("C32D-316 fails the closed+distribution sets -> honest null, "
          "text preserved in rest",
          r["finish"] is None and r["rest"].endswith("C32D-316 C32D-316"))
    r = row_at([b], "p4-L19")
    check("Auto Operator row: finish US28 high (legend), mfr null "
          "(Horton lives in catalog text; 3c/bonus territory)",
          (r["finish"], r["mfr"]) == ("US28", None)
          and r["confidence"]["finish"] == "high")
    r = row_at([b], "p4-L22")
    check("Weatherstrip Set row: CA medium (the 63-row closed-set gap, "
          "saved by distribution)",
          r["finish"] == "CA" and r["confidence"]["finish"] == "medium")
    check("qty-led pseudo-components stay rows with null codes "
          "(reading them is 3c's judgment)",
          all(row_at([b], a)["finish"] is None and row_at([b], a)["mfr"] is None
              for a in ("p4-L16", "p4-L25", "p4-L27")))
    check("'@ 42\" Top Down' fragment -> note prose (attachment is 3c's)",
          any(n["anchor"] == "p4-L07" and n["kind"] == "prose"
              for n in b["note_lines"]))
    check("partition 29/29",
          b["partition"] == {"n_body_lines": 29, "consumed": 29})

    print("[Bridgeport corpus-wide: dimensions never eaten, "
          "double-leaf finishes are]")
    all_finish = [r["finish"] for b in blocks for r in b["rows"] if r["finish"]]
    check("no row finish == '2/2134' (slash-dimension never validates)",
          "2/2134" not in all_finish)
    r = row_at(blocks, "p6-L13")
    check("'Gasketing W-22AL 1/965 x 2/2134': finish null, dims end the rest",
          r["finish"] is None and r["rest"].endswith("x 2/2134"))
    r = row_at(blocks, "p13-L12")
    check("'...W-16S 1/965 x 2/2134 CA': tail CA sliced, dims stay in rest",
          r["finish"] == "CA" and r["rest"].endswith("x 2/2134"))
    check("66 double-leaf 626/626 rows", all_finish.count("626/626") == 66)
    r = row_at(blocks, "p4-L35")
    check("mid-row '626/626' twin is NOT re-eaten (only the induced slot "
          "is read)", r["finish"] == "626/626" and "626/626" in r["rest"])
    check("top finish tallies: C26D 115, C32D 86 (hand-tallied)",
          all_finish.count("C26D") == 115 and all_finish.count("C32D") == 86)
    hi = {r["finish"] for b in blocks for r in b["rows"]
          if r["finish"] and r["confidence"]["finish"] == "high"}
    med = {r["finish"] for b in blocks for r in b["rows"]
           if r["finish"] and r["confidence"]["finish"] == "medium"}
    check("high == the 11 legend codes exactly; medium == the 4 "
          "distribution values exactly",
          hi == set(meta["vocabulary"]["finish"]["legend"])
          and med == {"626/626", "626/630", "CA", "GRY"})
    check("single broken block: set 19, two '*Confirm Door Thickness' orphans",
          [(b["set_id"], [u["text"] for u in b["unresolved"]])
           for b in blocks if b["broken"]]
          == [("19", ["*Confirm Door Thickness", "*Confirm Door Thickness"])])
    _, _, blocks2 = loaded[("81-85_Bridgeport",
                            "08-70-00-Hardware-Schedule_Rev_0-p3-49.rules.jsonl")]
    check("twin PDFs -> byte-identical block records",
          json.dumps(blocks, sort_keys=True)
          == json.dumps(blocks2, sort_keys=True))

    print("[Livelle Set 4.0 -- PE and NO disambiguated by column, "
          "zero legend in the book]")
    path, meta, blocks = loaded[
        ("Livelle_Mulholland_-_Life_Plan_Community", "*.rules.jsonl")]
    check("schema mfr@-1 finish@-2 from distribution alone",
          meta["column_schema"] == {"mfr_slot": -1, "finish_slot": -2})
    b = block_by_header(blocks, "p644-L29")
    check("Description: -> set description 'Unit Garage Door U1F'",
          b["description"] == "Unit Garage Door U1F")
    check("8 component rows, partition 9/9 (8 rows + description)",
          len(b["rows"]) == 8
          and b["partition"] == {"n_body_lines": 9, "consumed": 9})
    r = row_at([b], "p644-L34")
    check("Surface Closer row: 689 NO -- 'NO' read as maker by column "
          "position, parenthetical kept in rest",
          (r["finish"], r["mfr"]) == ("689", "NO")
          and r["rest"].endswith("(mount inside room)"))
    r = row_at([b], "p645-L01")
    check("Gasketing S88BL PE: S-led catalog fails finish shape -> null",
          (r["mfr"], r["finish"]) == ("PE", None))
    r = row_at([b], "p645-L02")
    check("Door Bottom 2113AV PE: 4-digit catalog fails the BHMA value "
          "gate -> null (the hand-walk's pinned fact)",
          (r["mfr"], r["finish"]) == ("PE", None))
    r = row_at([b], "p645-L03")
    check("Threshold 'Per Detail / Type as Req' PE: prose row, hint null",
          (r["mfr"], r["finish"], r["split_hint"]) == ("PE", None, None))
    check("no row in the stream carries a catalog tail as finish",
          not any(r["finish"] in LIVELLE_CATALOG_TAILS
                  for b in blocks for r in b["rows"]))

    print("[Livelle scrambled rows -- text lies, geometry travels with "
          "the block]")
    b = next(bb for bb in blocks if bb["seq"] == 114)
    check("set 108.0 flagged row_without_description, broken",
          b["set_id"] == "108.0" and b["broken"]
          and "row_without_description" in b["flags"])
    r = row_at([b], "p681-L30")
    check("'1 US32D SA': codes read (SA), rest is the bare finish shard",
          r["mfr"] == "SA" and r["finish"] is None and r["rest"] == "US32D")
    kinds = {n["anchor"]: n["kind"] for n in b["note_lines"]}
    check("its sibling shards held as prose, not guessed into the row",
          kinds.get("p681-L29") == "prose" and kinds.get("p681-L31") == "prose")
    geo = geometry(path)
    check("geometry p681-L30: qty at x0 74.3, US32D at 441.67, SA at 490.78 "
          "(one visual row split into three lines)",
          [(w["text"], w["x0"]) for w in geo["p681-L30"]["words"]]
          == [("1", 74.3), ("US32D", 441.67), ("SA", 490.78)])
    w31 = {w["text"]: w["x0"] for w in geo["p681-L31"]["words"]}
    check("geometry p681-L31: 'Exit' in the desc column (88.34), 'NEMW' in "
          "the catalog column (262.61) -- the line text alone misfiles NEMW",
          w31.get("Exit") == 88.34 and w31.get("NEMW") == 262.61)
    check("p683-L35 is the same break again ('1 US32D SA')",
          [w["text"] for w in geo["p683-L35"]["words"]] == ["1", "US32D", "SA"])

    print("[Lyons -- the dropped-row book: repair, then re-induce]")
    path, meta, blocks = loaded[("Lyons_Township_HS", "*.rules.jsonl")]
    check("schema re-induced after repair: null/null -> mfr@-1 finish@-2",
          meta["reinduced"] == {
              "from": {"mfr_slot": None, "finish_slot": None},
              "to": {"mfr_slot": -1, "finish_slot": -2}}
          and meta["column_schema"] == {"mfr_slot": -1, "finish_slot": -2})
    r = row_at(blocks, "p285-L12")
    check("'691 LCN' tail stitched back: finish 691, mfr LCN, ledger says "
          "dropped_tail from p285-L13",
          (r["finish"], r["mfr"]) == ("691", "LCN")
          and r["stitched"] == [{"anchor": "p285-L13", "how": "dropped_tail"}])
    check("every stitched row ends complete (finish and mfr both set)",
          all(r["finish"] and r["mfr"] for b in blocks for r in b["rows"]
              if r["stitched"]))
    check("no dropped tail left unattached",
          not any(u["kind"] == "dropped_tail"
                  for b in blocks for u in b["unresolved"]))
    b = blocks[0]
    check("door-number grids stay on the door side of the opener",
          all(any(d["anchor"] == a and d["kind"] == "door"
                  for d in b["door_lines"])
              for a in ("p285-L06", "p285-L07"))
          and any(d["kind"] == "opener" for d in b["door_lines"]))
    check("91 door lines under For-use-on-Door heads",
          sum(len(b["door_lines"]) for b in blocks) == 91)
    check("7 orphans held, all wrapped description shards "
          "(INSIDE INDICATOR / OUTSWING LOCKING DOORS)",
          sorted(u["text"] for b in blocks for u in b["unresolved"])
          == ["INSIDE INDICATOR"] * 5 + ["OUTSWING LOCKING DOORS)"] * 2)

    print("[National -- hyphen-split finish stitched, door grid fenced]")
    path, meta, blocks = loaded[("National_Doors_and_Hardware",
                                 "*.rules.jsonl")]
    r = row_at(blocks, "p406-L25")
    check("'630-' + next-line '316' -> finish 630-316 (medium), mfr IVE "
          "high, ledger says hyphen_finish",
          (r["finish"], r["mfr"]) == ("630-316", "IVE")
          and r["confidence"] == {"finish": "medium", "mfr": "high"}
          and r["stitched"] == [{"anchor": "p406-L26",
                                 "how": "hyphen_finish"}])
    check("door grid '2 5 25 33' held on the door side (head region)",
          any(d["anchor"] == "p409-L16" and d["kind"] == "door"
              and d["text"].strip().startswith("2 5 25 33")
              for b in blocks for d in b["door_lines"]))

    print("[Morris -- richest labels; the legend's one outsider stays null]")
    path, meta, blocks = loaded[("Morris_Bank", "*p233-263.rules.jsonl")]
    check("schema mfr@-1 finish@-2", meta["column_schema"]
          == {"mfr_slot": -1, "finish_slot": -2})
    nulls = [r for b in blocks for r in b["rows"] if r["mfr"] is None]
    check("exactly one mfr-null row: 'Other Door #MISC NH' p263-L05 "
          "(NH is outside the p230 legend)",
          len(nulls) == 1 and nulls[0]["anchor"] == "p263-L05"
          and nulls[0]["rest"].endswith("NH"))
    r = row_at(blocks, "p233-L25")
    check("SRI closer row: 10BE SA both high, option code SRI kept in rest "
          "for 3c's lookup",
          (r["finish"], r["mfr"]) == ("10BE", "SA")
          and r["confidence"] == {"finish": "high", "mfr": "high"}
          and r["rest"] == "Closer SRI 268 OB RH")
    check("36 Properties / Opening Description labels routed",
          sum(len(b["properties"]) for b in blocks) == 36)
    check("'Note: -' lines open note runs",
          any(n["kind"] == "note_header" and n["text"].startswith("Note:")
              for b in blocks for n in b["note_lines"]))
    _, _, rear = loaded[("Morris_Bank", "*p283-290.rules.jsonl")]
    check("rear section: 18 'Doors: N' header lines -> door side",
          sum(1 for b in rear for d in b["door_lines"]
              if d["kind"] == "door_header") == 18
          and all(d["text"].startswith("Doors:")
                  for b in rear for d in b["door_lines"]
                  if d["kind"] == "door_header"))

    print("[Market View -- wrapped set headers stitched at the trailer]")
    path, meta, blocks = loaded[("Market_View_Apartments", "*.rules.jsonl")]
    conts = [b for b in blocks if "trailer_continuation" in b["flags"]]
    check("7 trailer continuations", len(conts) == 7)
    t = next(b["trailer"] for b in blocks if b["set_id"] == "01")
    check("set 01 trailer closes its parenthesis after the stitch "
          "(...ZENTRA PLATFORM))",
          t.endswith("ZENTRA PLATFORM)") and t.count("(") == t.count(")"))
    check("its stitch is the same Ives pull as National's: 630- + 316",
          row_at(blocks, "p770-L24")["finish"] == "630-316"
          and row_at(blocks, "p770-L24")["stitched"][0]["how"]
          == "hyphen_finish")
    check("10 B/O rows (By Others rides the maker column, National's "
          "legend vouches for the reading)",
          sum(1 for b in blocks for r in b["rows"] if r["mfr"] == "B/O") == 10)

    print("[Forest Park -- decimal quantities ('3.0 Hinge ...') read as "
          "rows; qty normalised to int]")
    path, meta, blocks = loaded[("Forest_Park_School", "*.rules.jsonl")]
    b = blocks[0]
    got = [(r["anchor"], r["qty"], r["mfr"], r["finish"]) for r in b["rows"]]
    check("the 5 rows, hand-checked: qty 3/1/1/1/3, all ints",
          got == [("p262-L38", 3, "BES", "26D"),
                  ("p262-L39", 1, "BES", "626"),
                  ("p263-L02", 1, "C-R", "626"),
                  ("p263-L03", 1, None, None),
                  ("p263-L04", 3, "BRN", None)]
          and all(isinstance(r["qty"], int) for r in b["rows"]), str(got))
    check("hyphenated legend code C-R assigned from the book's own list",
          b["rows"][2]["mfr"] == "C-R")
    check("honest nulls keep their tokens in rest (US32D ABH / Gray)",
          b["rows"][3]["rest"].endswith("US32D ABH")
          and b["rows"][4]["rest"].endswith("Gray"))
    check("clean partition: no notes, no unresolved, block not broken",
          not b["note_lines"] and not b["unresolved"] and not b["broken"])
    check("this book has no units: unit None on all rows",
          all(r["unit"] is None for r in b["rows"]))

    print("[AMI -- bare door_header + numbers on following lines; "
          "wrapped by-phrase]")
    path, meta, blocks = loaded[("AMI__Copy_", "*.rules.jsonl")]
    check("every block has a door_header AND >=1 door-number line",
          all(any(d["kind"] == "door_header" for d in b["door_lines"])
              and any(d["kind"] == "door" for d in b["door_lines"])
              for b in blocks))
    check("46 door-number lines captured ('134' / '137A 210A 212B')",
          sum(1 for b in blocks for d in b["door_lines"]
              if d["kind"] == "door") == 46)
    b01a = next(b for b in blocks if b["set_id"] == "01A")
    check("01A: doors == ['134'], 'Each to have:' filed as note, "
          "zero unresolved",
          [d["text"] for d in b01a["door_lines"]
           if d["kind"] == "door"] == ["134"]
          and any(n["text"] == "Each to have:" for n in b01a["note_lines"])
          and not b01a["unresolved"])
    byo = [r for b in blocks for r in b["rows"] if r.get("by_others")]
    check("6 by_others rows: wrapped 'BY ALUMINUM DOOR |MANUFACTURER' "
          "prose word no longer reads as mfr",
          len(byo) == 6 and all(r["mfr"] is None and r["finish"] is None
                                and "BY ALUMINUM DOOR" in r["text"]
                                for r in byo))

    print("[Gerrard -- U-ids split into their own blocks; Single Operator "
          "is a component, not a door leaf]")
    for glob in ("2.02_*.rules.jsonl", "Hdw_*.rules.jsonl"):
        _, _, blocks = loaded[("2353_Gerrard_Street_Shelter", glob)]
        ops = [r for b in blocks for r in b["rows"]
               if r["rest"].startswith("Single Operator")]
        check(f"{glob}: 18 Single Operator rows with mfr HA / finish ALM",
              len(ops) == 18 and all(r["mfr"] == "HA" and r["finish"] == "ALM"
                                     for r in ops))
        u01 = next(b for b in blocks if b["set_id"] == "U-01")
        check(f"{glob}: U-01 owns 6 rows and its own door list",
              len(u01["rows"]) == 6
              and any(d["text"].startswith("Doors: D135B")
                      for d in u01["door_lines"]))
    check("Livelle 'Furnished by Security Contractor HD' keeps its printed "
          "maker (closed by-phrase; role-noun fence)",
          row_at(loaded[("Livelle_Mulholland_-_Life_Plan_Community",
                         "*.rules.jsonl")][2], "p654-L07")["mfr"] == "HD")

    print("JC Ryan (name-type mfr book: vocabulary induced from the book's "
          "own row tails, 2026-08-18):")
    _, jc_meta, _ = load_rules(root, "JC_Ryan_2", "*.rules.jsonl")
    check("mfr vocabulary == Norton/Pemko/Rockwood/Sargent/Securitron "
          "(no legend, no slot; >=3-token rows, >=3 occurrences, name "
          "shape -- 'Door/Frame Harness' x11 excluded by the row-length "
          "fence)",
          jc_meta["vocabulary"]["mfr"]
          == {"slot": None, "legend": [],
              "distribution": ["Norton", "Pemko", "Rockwood", "Sargent",
                               "Securitron"]},
          str(jc_meta["vocabulary"]["mfr"]))
    check("vocabulary does not assign: rules-side mfr stays 0 (assignment "
          "needs an induced slot; admission happens in 3c's gate)",
          jc_meta["rules"]["n_mfr"] == 0)
    check("finish vocabulary stays empty (book has no finish column)",
          jc_meta["vocabulary"]["finish"]["legend"] == []
          and jc_meta["vocabulary"]["finish"]["distribution"] == [])

    print("four new books (2026-08-18): title-case unit column")
    for proj, glob_pat, n_rows in (
            ("SAT_TDP", "*.rules.jsonl", 1925),
            ("SJC_Well_Behavioral", "*.rules.jsonl", 510),
            ("Shubie_Center", "*.rules.jsonl", 31),
            ("StarHardware", "*p53-113.rules.jsonl", 588)):
        _, _, blocks = load_rules(root, proj, glob_pat)
        rows = [r for b in blocks for r in b.get("rows", [])]
        check(f"{proj[:12]}: {n_rows} component rows parsed",
              len(rows) == n_rows, str(len(rows)))

    # SJC and StarHardware write the unit in title case ("1 Set", "8 Ea.");
    # the qty splitter only knew the all-caps spellings, so the unit used to
    # ride into the description and break token conservation on ~1,100 rows
    _, _, sjc = load_rules(root, "SJC_Well_Behavioral", "*.rules.jsonl")
    units = {r["unit"] for b in sjc for r in b.get("rows", [])}
    check("SJC units split off the row ('Set' / 'Ea.'), not folded into the "
          "description",
          {"Set", "Ea."} <= units
          and all(not r["rest"].startswith(("Set ", "Ea."))
                  for b in sjc for r in b.get("rows", [])),
          str(sorted(u for u in units if u)))
    _, _, star = load_rules(root, "StarHardware", "*p53-113.rules.jsonl")
    check("StarHardware likewise ('Ea.'), and the four non-manufacturer "
          "legend codes reach no row",
          "Ea." in {r["unit"] for b in star for r in b.get("rows", [])}
          and not ({"IFC", "OCI", "PART", "ADA"}
                   & {r["mfr"] for b in star for r in b.get("rows", [])
                      if r.get("mfr")}))

    # Shubie p174 L20/L21 read off the PDF 2026-08-18: two rows carry the
    # revision-marked unit "EA-R" in a column of plain "1 EA ..." rows.
    # The hyphen used to stop the unit matching, so the whole token rode
    # into the description ("EA-R AUTO OPERATOR"). These 2 rows are the
    # only ones in the corpus's 26,290 content rows that the -[A-Z] tail
    # changes; every other book's rules file is byte-identical.
    _, _, shubie = load_rules(root, "Shubie_Center", "*.rules.jsonl")
    srows = {r["anchor"]: r for b in shubie for r in b.get("rows", [])}
    check("Shubie: both EA-R rows split the unit off, qty kept, description "
          "starts at the real first word",
          srows["p174-L20"]["unit"] == "EA-R"
          and srows["p174-L20"]["qty"] == 1
          and srows["p174-L20"]["rest"].startswith("AUTO OPERATOR")
          and srows["p174-L21"]["unit"] == "EA-R"
          and srows["p174-L21"]["qty"] == 4
          and srows["p174-L21"]["rest"].startswith("ACTUATOR, TOUCH"),
          f'{srows["p174-L20"]["unit"]!r} {srows["p174-L21"]["unit"]!r}')
    check("...and the plain EA rows around them are unchanged (the tail is "
          "optional, not a new requirement)",
          srows["p174-L19"]["unit"] == "EA"
          and srows["p174-L22"]["unit"] == "EA",
          f'{srows["p174-L19"]["unit"]!r} {srows["p174-L22"]["unit"]!r}')
    check("...EA-R appears nowhere else in the corpus's rule files",
          {r["unit"] for p in sorted(root.glob("*/*.rules.jsonl"))
           if p.parent.name != "Shubie_Center"
           for line in p.read_text("utf-8").splitlines()[1:]
           for r in json.loads(line).get("rows", [])} .isdisjoint({"EA-R"}))

    print(f"\n{'ALL PASS' if not FAILURES else 'FAILURES: ' + str(FAILURES)}"
          f"  ({(len(FAILURES) and '!') or 'ok'})")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
