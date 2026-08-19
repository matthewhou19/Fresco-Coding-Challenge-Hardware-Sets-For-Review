"""Acceptance checks for step 2 (set chunking): asserts hand-verified facts
from the sample corpus -- the 2026-08-17 desk run over all 9 streams: an
independent keyword-only recount (no id grammar) matched the strict cut 9/9,
id lists were eyeballed whole, and the C200C / Heading #8 targets were
hand-computed from the raw streams first (corrections found on the way:
Vantage has 44 group ids not 43, National runs 01-15 not 02-15, Morris's
second section holds 18 sets not 4, Lyons hides four Not-Used ghosts).

Usage:  python pipeline/step2_checks.py [step1p5_root] [step2_root]
Defaults: data/out/step1p5  data/out/step2.
Prints PASS/FAIL per fact; exit code 1 if anything failed.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILURES.append(name)


# (project, stream prefix, blocks, family, loose keyword-only recount rx)
EXPECTED_DUP_IDS = {
    "89671ede-20260218_SJC_BeWell_Bldg_B_85_"
    "_DESIGN_UPDATE_-_SPECIFICATIONS-p711-748": ["10C", "14B"],
}

STREAMS = [
    # SJC: 117 header lines on the page, 91 live sets -- the other 26 are
    # superseded definitions the source struck out (see step1_checks).  The
    # loose net is the raw "HW <id>" line shape, no id grammar.
    ("SJC_Well_Behavioral", "89671ede-20260218_SJC_BeWell_Bldg_B_85_"
                            "_DESIGN_UPDATE_-_SPECIFICATIONS-p711-748", 91,
     "hw_bare", re.compile(r"^HW\s+[A-Z]?\d", re.I)),
    ("SAT_TDP", "2025.12.19_-_SAT_TDP_-_Project_Manual-p715-836", 178,
     "group_no", re.compile(r"^Hardware\s+Group\s+No\.", re.I)),
    ("Shubie_Center", "e2231795-IFT_Specs-p174-175", 3, "group_no",
     re.compile(r"^Hardware\s+Group\s+No\.", re.I)),
    # StarHardware p25-26 opens each set inside a sentence, so the loose net
    # is the keyword+id anywhere in the line
    ("StarHardware", "9839d1a1-Division_8_Specs_-_Commons_Lane-p25-26", 2,
     "assigned_following",
     re.compile(r".*Hardware\s+Groups?/Sets?\s*#", re.I)),
    # same book pluralises both halves at will: "Hardware Groups/Set #10",
    # "Hardware Group/Sets #101", "Hardware Groups/Sets #18"
    ("StarHardware", "9839d1a1-Division_8_Specs_-_Commons_Lane-p53-113", 67,
     "group_no", re.compile(r"^Hardware\s+Groups?/Sets?\s*#", re.I)),
    ("81-85_Bridgeport", "08-70-00-Hardware-Schedule-p3-49", 90, "heading",
     re.compile(r"^Heading\s*#", re.I)),
    ("81-85_Bridgeport", "08-70-00-Hardware-Schedule_Rev_0-p3-49", 90, "heading",
     re.compile(r"^Heading\s*#", re.I)),
    ("Livelle_Mulholland_-_Life_Plan_Community",
     "2025-12-12_Livelle_Bid_Set_Project_Manual_Vol1_rev1-p643-700", 161,
     "set_colon", re.compile(r"^Set:", re.I)),
    ("Lyons_Township_HS", "Project_Manual_1_-p285-294", 29, "group_no",
     re.compile(r"^Hardware\s+Group\s+No\.", re.I)),
    ("Market_View_Apartments",
     "S_251107_Market_View_Prelim_Project_Manual_pdf-p770-780", 24, "group_no",
     re.compile(r"^Hardware\s+Group\s+No\.", re.I)),
    ("Morris_Bank", "030f2d1d-Morris_Bank_Macon_-Spec_Manual_Issued_for_Const."
                    "_1-26-26_FULL_SPECS-p233-263", 31, "set_hash",
     re.compile(r"^Set\s*#", re.I)),
    ("Morris_Bank", "030f2d1d-Morris_Bank_Macon_-Spec_Manual_Issued_for_Const."
                    "_1-26-26_FULL_SPECS-p283-290", 18, "set_hash",
     re.compile(r"^Set\s*#", re.I)),
    ("National_Doors_and_Hardware", "15e2b8ac-FS17_Specs_V1-p406-412", 15,
     "group_no", re.compile(r"^Hardware\s+Group\s+No\.", re.I)),
    ("The_Door_Company__Copy_", "Vantage_TX-22_Div_01_08-p389-421", 44,
     "group_no", re.compile(r"^(?:PART\s+\d+\s*-\s*)?HARDWARE\s+GROUP\s+NO\.", re.I)),
    # Roselle wide table has no keyword: the independent net is a DIFFERENT
    # grammar (dotted head AND mid-row qty+finish adjacency), not the id rule
    ("Roselle_Public_Library", "087100_FL_-_Door_Hardware_IFB_REVISED-p15-17",
     33, "bare_dotted",
     re.compile(r"^\d{1,2}\.\d{1,2}\s+.*(?:^|\s)(?:\d{1,3}|--)\s+(?:6\d{2}\b|BLACK\b)")),
    ("AMI__Copy_",
     "c43c36d9-000_Full_Volume_ATC_Renovation_Bid_Specs_Volume_1__1_-p397-419",
     44, "group_no", re.compile(r"^Hardware\s+Group\s+No\.", re.I)),
    ("Forest_Park_School", "Project_Manual_1_-p262-263", 1, "set_hash",
     re.compile(r"^Set\s*#", re.I)),
    ("2353_Gerrard_Street_Shelter",
     "2.02_2535_Gerrard_Shelter-Issued_for_Tender_5-Architectural"
     "_Specifications-p165-182", 35, "set_hash",
     re.compile(r"^Set\s*#", re.I)),
    ("2353_Gerrard_Street_Shelter", "Hdw_Spec_Sch-IFT_5-p19-36", 35,
     "set_hash", re.compile(r"^Set\s*#", re.I)),
    # five-new-book round 2026-08-18
    ("HFH_DG_-_HOSPITAL", "08_71_00_-_DOOR_HARDWARE-p20-183", 190, "group_no",
     re.compile(r"^Hardware\s+Group\s+No\.", re.I)),
    ("JC_Ryan_2", "087100_-_Door_Hardware-6-p24-46", 38, "set_colon",
     re.compile(r"^Set:", re.I)),
    ("Valor_Acres_Building_E", "087100-DOOR-HARDWARE_Rev_2-p7-18", 37,
     "group_no", re.compile(r"^Hardware\s+Group\s+No\.", re.I)),
    ("Village_of_Oswego_New_Public_Works_Facility__Copy_",
     "SPECIFICATIONS_VOLUME_1-p418-445", 39, "group_no",
     re.compile(r"^Hardware\s+Group\s+No\.", re.I)),
]

MORRIS1_IDS = ["101", "102", "103", "104", "105", "106", "107", "108", "109",
               "111", "112", "113", "115", "116", "117", "118", "119", "201",
               "203", "205", "207", "208", "210", "211", "212", "214", "215",
               "216", "217", "218", "MISC"]
MORRIS2_IDS = ["101.68", "103.68", "106.38", "119.38", "205.68", "CR38",
               "CR38CLHO", "MISC", "OF38", "211.3080", "PR38ICCL", "PR38INCL",
               "SR38", "SR38CL", "SR38CLCA", "SW38RPA", "SW38RXCA", "XSR48CL"]
C200C_SPANS = [
    {"page": 395, "lines": [29, 30], "bbox": [72.02, 525.23, 406.88, 572.99]},
    {"page": 396, "lines": [4, 32], "bbox": [72.02, 86.49, 540.04, 497.75]},
    {"page": 397, "lines": [3, 4], "bbox": [72.02, 73.62, 540.12, 97.26]},
]
HEADING8_SPANS = [{"page": 6, "lines": [16, 25],
                   "bbox": [41.4, 308.84, 548.03, 473.6]}]


def load_content(root: Path, project: str, prefix: str) -> list[dict]:
    path = next((root / project).glob(prefix + "*.jsonl"))
    recs = [json.loads(l) for l in path.read_text("utf-8").splitlines()]
    return [r for r in recs[1:] if r["role"] == "content"]


def load_blocks(root: Path, project: str, prefix: str) -> tuple[dict, list[dict]]:
    path = next((root / project).glob(prefix + "*.blocks.jsonl"))
    recs = [json.loads(l) for l in path.read_text("utf-8").splitlines()]
    return recs[0], recs[1:]


def load_report(root: Path, project: str) -> dict:
    return json.loads((root / project / "chunks_report.json").read_text("utf-8"))


def stream_summary(report: dict, prefix: str) -> dict:
    return next(s for s in report["streams"] if s["stream"].startswith(prefix))


def members_of(block: dict, content: list[dict]) -> list[dict]:
    return [r for r in content
            if any(s["page"] == r["page"]
                   and s["lines"][0] <= r["line"] <= s["lines"][1]
                   for s in block["spans"])]


def main() -> int:
    in_root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/out/step1p5")
    out_root = Path(sys.argv[2] if len(sys.argv) > 2 else "data/out/step2")

    print(f"all {len(STREAMS)} streams (partition + spans + independent "
          "recount + nets):")
    ok_part = ok_span = ok_order = ok_flag = ok_bbox = ok_meta = True
    ok_loose = ok_count = ok_family = ok_nets = True
    for project, prefix, n_expected, family, loose_rx in STREAMS:
        content = load_content(in_root, project, prefix)
        meta, blocks = load_blocks(out_root, project, prefix)
        rep = stream_summary(load_report(out_root, project), prefix)

        # exact partition: every content line in exactly one bucket
        member_anchors = [r["anchor"] for b in blocks
                         for r in members_of(b, content)]
        pre = [e["anchor"] for e in rep["preamble"]]
        post = [e["anchor"] for e in rep["postamble"]]
        every = member_anchors + pre + post
        ok_part &= (len(every) == len(content)
                    and set(every) == {r["anchor"] for r in content})
        # spans reconstruct each block exactly
        for b in blocks:
            m = members_of(b, content)
            ok_span &= (len(m) == b["n_lines"]
                        and m[0]["anchor"] == b["anchor_first"] == b["header"]["anchor"]
                        and m[-1]["anchor"] == b["anchor_last"])
        # blocks in document order, no overlap
        firsts = [(b["spans"][0]["page"], b["spans"][0]["lines"][0]) for b in blocks]
        lasts = [(b["spans"][-1]["page"], b["spans"][-1]["lines"][1]) for b in blocks]
        ok_order &= all(lasts[i] < firsts[i + 1] for i in range(len(blocks) - 1))
        ok_flag &= all(b["empty"] == (b["n_lines"] == 1)
                       and b["seq"] == i + 1 and b["type"] == "block"
                       for i, b in enumerate(blocks))
        # bbox unions inside page geometry
        for b in blocks:
            for s in b["spans"]:
                w, h = meta["pages"][str(s["page"])]
                x0, top, x1, bot = s["bbox"]
                ok_bbox &= 0 <= x0 < x1 <= w and 0 <= top < bot <= h
        ok_meta &= (meta["chunks"]["n_blocks"] == len(blocks)
                    and meta["chunks"]["n_content_lines"] == len(content)
                    and meta["chunks"]["n_preamble_lines"] == len(pre)
                    and meta["chunks"]["n_postamble_lines"] == len(post))
        # independent net: keyword-only recount, no id grammar
        ok_loose &= sum(1 for r in content
                        if loose_rx.match(r["text"].strip())) == len(blocks)
        ok_count &= len(blocks) == n_expected
        ok_family &= all(b["family"] == family for b in blocks)
        # duplicate ids inside one stream are a loud report, never a silent
        # merge.  SJC really prints two blocks for 10C and for 14B: a one-line
        # marker ("HW 14B NOT USED", "HW 10C Exterior doors only moved to ...")
        # and the live set under the same id.  Both are delivered.
        ok_nets &= (not rep["suspect_headers"] and rep["preamble_qty_rows"] == 0
                    and rep["duplicate_ids_in_stream"]
                    == EXPECTED_DUP_IDS.get(prefix, [])
                    and "alarm" not in rep)
    check("every content line in exactly one of preamble/block/postamble", ok_part)
    check("spans reconstruct every block exactly (count + first/last anchor)", ok_span)
    check("blocks in document order, spans never overlap", ok_order)
    check("seq/type/empty flags consistent", ok_flag)
    check("every span bbox inside its page geometry", ok_bbox)
    check("meta chunk counts match the records", ok_meta)
    check("independent recount == block count on all 14", ok_loose)
    check("block counts: 90/90 161 29 24 31/18 15 44 33 + 44 1 35/35",
          ok_count)
    check("single header family per stream, as expected", ok_family)
    check("nets quiet: no suspects, no preamble qty rows, no in-stream dups, no alarm",
          ok_nets)

    print("Vantage (PART prefix, dotted id, ghosts, terminator):")
    _, van = load_blocks(out_root, "The_Door_Company__Copy_", "Vantage")
    rep_v = stream_summary(load_report(out_root, "The_Door_Company__Copy_"), "Vantage")
    ids = [b["set_id"] for b in van]
    check("44 blocks; first is 103 from 'PART 6 - ...' (prefix stripped), p389-L06",
          len(van) == 44 and van[0]["set_id"] == "103"
          and van[0]["header"]["anchor"] == "p389-L06"
          and van[0]["header"]["text"] == "PART 6 - HARDWARE GROUP NO. 103")
    check("preamble is exactly the section title '3.6 HARDWARE SETS:' (p389-L03)",
          rep_v["preamble"] == [{"anchor": "p389-L03", "text": "3.6 HARDWARE SETS:"}])
    check("terminator: postamble is exactly 'END OF SECTION' (p421-L04)",
          rep_v["postamble"] == [{"anchor": "p421-L04", "text": "END OF SECTION"}])
    c200c = next(b for b in van if b["set_id"] == "C200C")
    check("C200C: 33 lines across 3 pages, spans == hand-computed target",
          c200c["n_lines"] == 33 and c200c["anchor_first"] == "p395-L29"
          and c200c["anchor_last"] == "p397-L04"
          and c200c["spans"] == C200C_SPANS)
    check("subtle-change pair C200C / C200CZ both cut (C200CZ at p397-L06)",
          any(b["set_id"] == "C200CZ" and b["header"]["anchor"] == "p397-L06"
              for b in van))
    check("dotted id: C00 and C00.EXT are two distinct blocks",
          "C00" in ids and "C00.EXT" in ids)
    ghosts = [b for b in van if b["set_id"] in ("001", "002")]
    check("ghosts 001+002: empty blocks, instruction kept as trailer",
          len(ghosts) == 2 and all(
              g["empty"] and g["trailer"] == "DO NOT APPLY DOOR NUMBERS TO SETS"
              for g in ghosts)
          and [g["spans"][0]["page"] for g in ghosts] == [420, 421])
    check("no other empty block in Vantage", rep_v["empty_blocks"] == ["001", "002"])

    print("Bridgeport (twins; items-share-components blocks):")
    _, b49 = load_blocks(out_root, "81-85_Bridgeport", "08-70-00-Hardware-Schedule-p3-49")
    _, brev = load_blocks(out_root, "81-85_Bridgeport", "08-70-00-Hardware-Schedule_Rev_0")
    rep_b = load_report(out_root, "81-85_Bridgeport")
    check("ids are 1..90 in document order",
          [b["set_id"] for b in b49] == [str(i) for i in range(1, 91)])
    strip = lambda bs: [{k: b[k] for k in ("set_id", "n_lines", "spans", "empty",
                                           "trailer")} for b in bs]
    check("dup twins cut into identical block sequences", strip(b49) == strip(brev))
    check("all 90 ids shared across the twin streams (marked, not merged)",
          len(rep_b["duplicate_ids_across_streams"]) == 90)
    b8 = next(b for b in b49 if b["set_id"] == "8")
    check("Heading #8: 10 lines, one span p6 L16-L25, bbox == target",
          b8["n_lines"] == 10 and b8["spans"] == HEADING8_SPANS)
    b17 = next(b for b in b49 if b["set_id"] == "17")
    check("Heading #17 (68 doors, one component list): 76 lines over p11-p12",
          b17["n_lines"] == 76 and [s["page"] for s in b17["spans"]] == [11, 12])
    check("no ghost sets in Bridgeport (1-90 all non-empty)",
          not any(b["empty"] for b in b49))
    check("preamble is the p3 schedule title",
          stream_summary(rep_b, "08-70-00-Hardware-Schedule-p3-49")["preamble"]
          == [{"anchor": "p3-L01", "text": "Hardware Schedule"}])

    print("Morris (one set per page; two sections, MISC in both):")
    _, m1 = load_blocks(out_root, "Morris_Bank", "030f2d1d-Morris_Bank_Macon_-Spec_"
                        "Manual_Issued_for_Const._1-26-26_FULL_SPECS-p233-263")
    _, m2 = load_blocks(out_root, "Morris_Bank", "030f2d1d-Morris_Bank_Macon_-Spec_"
                        "Manual_Issued_for_Const._1-26-26_FULL_SPECS-p283-290")
    rep_m = load_report(out_root, "Morris_Bank")
    check("section 1: ids 101..218-with-gaps + MISC (31 blocks)",
          [b["set_id"] for b in m1] == MORRIS1_IDS)
    check("one set per page: 31 single-page blocks covering p233-263 exactly",
          all(len(b["spans"]) == 1 for b in m1)
          and [b["spans"][0]["page"] for b in m1] == list(range(233, 264)))
    check("Set #103 cut at p235-L04",
          next(b for b in m1 if b["set_id"] == "103")["header"]["anchor"] == "p235-L04")
    check("section 2: 18 ids incl. dotted (211.3080) and long alnum (XSR48CL)",
          [b["set_id"] for b in m2] == MORRIS2_IDS)
    check("subtle-change pair PR38ICCL / PR38INCL both cut",
          "PR38ICCL" in MORRIS2_IDS and any(b["set_id"] == "PR38INCL" for b in m2))
    misc1 = next(b for b in m1 if b["set_id"] == "MISC")
    misc2 = next(b for b in m2 if b["set_id"] == "MISC")
    check("MISC exists in BOTH sections as two different blocks (not merged)",
          rep_m["duplicate_ids_across_streams"].keys() == {"MISC"}
          and misc1["spans"][0]["page"] == 263
          and misc2["spans"] != misc1["spans"])

    print("Lyons (four Not-Used ghosts; legend preamble):")
    _, lyo = load_blocks(out_root, "Lyons_Township_HS", "Project_Manual_1_")
    rep_l = stream_summary(load_report(out_root, "Lyons_Township_HS"), "Project_Manual_1_")
    check("ids are 01..29 in order",
          [b["set_id"] for b in lyo] == [f"{i:02d}" for i in range(1, 30)])
    check("empty blocks are exactly 05/16/21/22, all trailed 'Not Used' "
          "(joiner stripped 2026-08-18)",
          rep_l["empty_blocks"] == ["05", "16", "21", "22"]
          and all(b["trailer"] == "Not Used" for b in lyo if b["empty"]))
    check("preamble = 3 legend lines starting 'Legend:'",
          len(rep_l["preamble"]) == 3 and rep_l["preamble"][0]["text"] == "Legend:")

    print("Market View (inline descriptions in trailers):")
    _, mv = load_blocks(out_root, "Market_View_Apartments", "S_251107")
    rep_mv = stream_summary(load_report(out_root, "Market_View_Apartments"), "S_251107")
    check("ids are 01..24 in order",
          [b["set_id"] for b in mv] == [f"{i:02d}" for i in range(1, 25)])
    check("block 01 trailer carries the inline description (A/G EXT ...)",
          "A/G EXT PR CR NL-OP-PANIC" in mv[0]["trailer"])
    check("block 03 trailer: 'HARDWARE BY DOOR MFG.' (joiner stripped)",
          next(b for b in mv if b["set_id"] == "03")["trailer"]
          == "HARDWARE BY DOOR MFG.")
    check("terminator caught title-case 'End of Section' (p780-L27)",
          rep_mv["postamble"] == [{"anchor": "p780-L27", "text": "End of Section"}])

    print("National (01-15, corpus said 02-15; legend sits in preamble):")
    _, nat = load_blocks(out_root, "National_Doors_and_Hardware", "15e2b8ac")
    rep_n = stream_summary(load_report(out_root, "National_Doors_and_Hardware"),
                           "15e2b8ac")
    check("ids are 01..15 (the 01 corpus-notes missed, p406-L17)",
          [b["set_id"] for b in nat] == [f"{i:02d}" for i in range(1, 16)]
          and nat[0]["header"]["anchor"] == "p406-L17")
    check("preamble = 14 lines: 'D. Hardware Sets:' + manufacturer legend",
          len(rep_n["preamble"]) == 14
          and rep_n["preamble"][0]["text"] == "D. Hardware Sets:"
          and any(e["text"] == "SCH Schlage Lock Company" for e in rep_n["preamble"]))
    check("terminator: 'END OF SECTION 08 71 00' (p412-L08)",
          rep_n["postamble"] == [{"anchor": "p412-L08",
                                 "text": "END OF SECTION 08 71 00"}])

    print("Livelle (161 decimal ids + MISC; qty-semantics note in preamble):")
    _, liv = load_blocks(out_root, "Livelle_Mulholland_-_Life_Plan_Community",
                         "2025-12-12")
    rep_liv = stream_summary(load_report(
        out_root, "Livelle_Mulholland_-_Life_Plan_Community"), "2025-12-12")
    ids = [b["set_id"] for b in liv]
    check("161 blocks, 1.0 first, MISC last, MISC is the only non-decimal id",
          len(ids) == 161 and ids[0] == "1.0" and ids[-1] == "MISC"
          and [i for i in ids if not re.fullmatch(r"\d+\.\d+", i)] == ["MISC"])
    check("preamble (21 lines) ends on the 'Hardware Sets' title and carries "
          "the qty-semantics note",
          len(rep_liv["preamble"]) == 21
          and rep_liv["preamble"][-1]["text"] == "Hardware Sets"
          and any(e["text"].startswith("1. Quantities listed are for each pair")
                  for e in rep_liv["preamble"]))
    check("terminator: 'END OF SECTION 08 71 00' (p700-L34)",
          rep_liv["postamble"] == [{"anchor": "p700-L34",
                                    "text": "END OF SECTION 08 71 00"}])

    print("Roselle (bare_dotted family, gated on the wide column header):")
    rep_ros = stream_summary(load_report(out_root, "Roselle_Public_Library"),
                             "087100_FL_-_Door_Hardware_IFB_REVISED-p15-17")
    _, ros_blocks = load_blocks(out_root, "Roselle_Public_Library",
                                "087100_FL_-_Door_Hardware_IFB_REVISED-p15-17")
    check("ids 1.1..8.3 complete (33, document order)",
          [b["set_id"] for b in ros_blocks] == [
              "1.1", "1.2", "1.3", "1.4", "1.5", "2.1", "2.2", "2.3", "2.4",
              "3.1", "3.2", "3.3", "3.4", "3.5", "4.1", "4.2", "4.3", "4.4",
              "4.5", "4.6", "4.7", "5.1", "6.1", "6.2", "7.1", "7.2", "7.3",
              "7.4", "7.5", "7.6", "8.1", "8.2", "8.3"])
    check("preamble empty (furniture peeled upstream by 1.5 bands)",
          rep_ros["preamble"] == [])
    check("postamble == the PART-prefixed terminator (4th observed form)",
          rep_ros["postamble"] == [{"anchor": "p17-L86",
                                    "text": "PART 2 - END OF SECTION 087100"}])
    check("no empty blocks (head line IS the first component row)",
          not any(b["empty"] for b in ros_blocks))

    print("Gerrard (Set # dialect; hyphenated U-ids recognised 2026-08-18 "
          "-- the suspects net that flagged them is now quiet):")
    ger_ids = (["AL"] + [f"{i:02d}" for i in range(1, 33) if i != 15]
               + ["U-01", "U-02", "U-03"])
    for prefix, pg in (("Hdw_Spec_Sch-IFT_5-p19-36", ""),
                       ("2.02_2535_Gerrard_Shelter-Issued_for_Tender_5"
                        "-Architectural_Specifications-p165-182", " (copy)")):
        _, g_blocks = load_blocks(out_root, "2353_Gerrard_Street_Shelter",
                                  prefix)
        check(f"35 blocks{pg}: AL + 01..32 minus 15 (the book's own gap) "
              "+ U-01..03", [b["set_id"] for b in g_blocks] == ger_ids)
    check("the two streams carry identical set ids (duplicate binding)",
          [b["set_id"] for b in load_blocks(
              out_root, "2353_Gerrard_Street_Shelter",
              "Hdw_Spec_Sch-IFT_5-p19-36")[1]]
          == [b["set_id"] for b in load_blocks(
              out_root, "2353_Gerrard_Street_Shelter", "2.02_2535")[1]])

    print("Forest Park (tail-net page joins its set's block, 2026-08-18):")
    rep_fp = stream_summary(load_report(out_root, "Forest_Park_School"),
                            "Project_Manual_1_-p262-263")
    _, fp_blocks = load_blocks(out_root, "Forest_Park_School",
                               "Project_Manual_1_-p262-263")
    check("single block 'Set #1' spans p262 L36-39 + p263 L2-4",
          fp_blocks[0]["set_id"] == "1"
          and [[s["page"]] + s["lines"] for s in fp_blocks[0]["spans"]]
          == [[262, 36, 39], [263, 2, 4]])
    check("preamble = the 34 legend lines (manufacturer/option/finish "
          "lists live in-region, above the sets title)",
          len(rep_fp["preamble"]) == 34
          and any(e["text"] == "FINISH LIST:" for e in rep_fp["preamble"]))
    check("postamble is exactly 'END OF SECTION' (p263-L05)",
          rep_fp["postamble"] == [{"anchor": "p263-L05",
                                   "text": "END OF SECTION"}])

    print("five new books (2026-08-18): HFH cross-blank continuation, "
          "JC Ryan prefixed ids, Valor id split:")
    _, hfh = load_blocks(out_root, "HFH_DG_-_HOSPITAL",
                         "08_71_00_-_DOOR_HARDWARE-p20-183")
    b197 = next(b for b in hfh if b["set_id"] == "197")
    check("HFH group 197 keeps its rows: header p163-L33, rows p167-L01..05 "
          "across three furniture-only pages",
          [[s["page"]] + s["lines"] for s in b197["spans"]]
          == [[163, 33, 33], [167, 1, 5]] and not b197["empty"],
          str([[s["page"]] + s["lines"] for s in b197["spans"]]))
    check("HFH 190 blocks, none empty", len(hfh) == 190
          and not any(b["empty"] for b in hfh))
    _, jc = load_blocks(out_root, "JC_Ryan_2",
                        "087100_-_Door_Hardware-6-p24-46")
    check("JC Ryan ids stay whole: EX-1.0..EX-4.0 ahead of 1.0 (not cut to "
          "'EX-1' with '.0' left in the trailer)",
          [b["set_id"] for b in jc[:5]]
          == ["EX-1.0", "EX-2.0", "EX-3.0", "EX-4.0", "1.0"],
          str([b["set_id"] for b in jc[:5]]))
    ex2 = next(b for b in jc if b["set_id"] == "EX-2.0")
    check("EX-2.0 keeps the single row stranded on sparse p25",
          [[s["page"]] + s["lines"] for s in ex2["spans"]]
          == [[24, 29, 32], [25, 3, 3]])
    _, val = load_blocks(out_root, "Valor_Acres_Building_E",
                         "087100-DOOR-HARDWARE_Rev_2-p7-18")
    check("Valor 37 blocks: 01..36 with the book's own 21A/21B split",
          [b["set_id"] for b in val]
          == [f"{i:02d}" for i in range(1, 21)] + ["21A", "21B"]
          + [f"{i:02d}" for i in range(22, 37)])
    check("Woodridge has no stream to chunk (its region was rejected at "
          "step 1) -- no phantom blocks invented here",
          not list((out_root / "Woodridge_Public_Works").glob("*.blocks.jsonl")))

    print("header-trailer hygiene + split-schedule net (2026-08-18):")
    bad = []
    for bf in sorted(out_root.rglob("*.blocks.jsonl")):
        for line in bf.read_text("utf-8").splitlines()[1:]:
            rec = json.loads(line)
            if rec.get("trailer") and re.match(r"^[:,\-\u2013\u2014]",
                                               rec["trailer"]):
                bad.append((bf.parent.name, rec.get("set_id"),
                            rec["trailer"][:30]))
    check("no block trailer begins with an id-joiner (: - ,) anywhere "
          "(the joiner is header syntax, not description content)",
          not bad, str(bad[:4]))
    susp = [(rp.parent.name, s["tail_set_id"])
            for rp in sorted(out_root.rglob("chunks_report.json"))
            for s in json.loads(rp.read_text("utf-8"))
            .get("split_schedule_suspects", [])]
    check("split-schedule net quiet corpus-wide (validated LOUD on the "
          "stashed pre-fix HFH streams: fires on empty tail set 197)",
          not susp, str(susp[:3]))

    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nall checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
