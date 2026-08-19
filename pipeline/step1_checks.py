"""Acceptance checks for step 1: asserts hand-verified facts from the sample
corpus (Bridgeport) and the 2026-08-17 Vantage probe.

Usage:  python pipeline/step1_checks.py <out_root>
where <out_root> is the --out passed to step1_locate.py (default data/out/step1).
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


def load_report(root: Path, project: str) -> dict:
    return json.loads((root / project / "region_report.json").read_text("utf-8"))


def by_file(report: dict, name: str) -> dict:
    return next(f for f in report["files"] if f["file"] == name)


def stream_lines(root: Path, project: str, stem_prefix: str) -> list[dict]:
    path = next((root / project).glob(stem_prefix + "*.jsonl"))
    recs = [json.loads(l) for l in path.read_text("utf-8").splitlines()]
    return recs


def bbox_sane(rec: dict, pages_meta: dict) -> bool:
    x0, top, x1, bottom = rec["bbox"]
    w, h = pages_meta[str(rec["page"])]
    return 0 <= x0 < x1 <= w and 0 <= top < bottom <= h


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/out/step1")

    print("Bridgeport:")
    rep = load_report(root, "81-85_Bridgeport")
    for name in ("08-10-00-Hollow-Metal-Doors-Frames_Rev_0.pdf",
                 "08-20-00-Wood-Doors_Rev_0.pdf"):
        f = by_file(rep, name)
        check(f"{name} -> no_sets", f["verdict"] == "no_sets", f["verdict"])
    sched = by_file(rep, "08-70-00-Hardware-Schedule.pdf")
    check("schedule region == p3-49",
          [[r["start"], r["end"]] for r in sched["regions"]] == [[3, 49]],
          str(sched["regions"] and [[r["start"], r["end"]] for r in sched["regions"]]))
    rev0 = by_file(rep, "08-70-00-Hardware-Schedule_Rev_0.pdf")
    check("Rev_0 region == p3-49",
          [[r["start"], r["end"]] for r in rev0["regions"]] == [[3, 49]])
    check("Rev_0 scanned tail excluded (189 image-only pages)",
          rev0["image_only_pages"] == 189, str(rev0["image_only_pages"]))
    dup = rev0["regions"][0].get("duplicate_of") if rev0["regions"] else None
    check("Rev_0 region flagged as duplicate of 49p schedule",
          bool(dup) and dup["file"] == "08-70-00-Hardware-Schedule.pdf"
          and dup["pages"] == [3, 49], str(dup))

    recs = stream_lines(root, "81-85_Bridgeport", "08-70-00-Hardware-Schedule-p3-49")
    meta, lines = recs[0], recs[1:]
    check("stream meta region p3-49", meta["region"] == [3, 49])
    h1 = [r for r in lines if r["page"] == 3 and re.search(r"Heading\s*#1\b", r["text"])]
    check("Heading #1 on p3 with sane bbox",
          bool(h1) and bbox_sane(h1[0], meta["pages"]),
          h1[0]["anchor"] if h1 else "not found")
    h17 = [r for r in lines if re.search(r"Heading\s*#17\b", r["text"])]
    check("Heading #17 (multi-page set) present on p11",
          bool(h17) and h17[0]["page"] == 11,
          h17[0]["anchor"] if h17 else "not found")
    headings = {m.group(1) for r in lines
                for m in [re.search(r"Heading\s*#(\d+)", r["text"])] if m}
    check("90 distinct Heading numbers in stream", len(headings) == 90,
          f"{len(headings)} found")

    print("Vantage:")
    rep = load_report(root, "The_Door_Company__Copy_")
    van = by_file(rep, "Vantage TX-22 Div 01, 08.pdf")
    check("region == p389-421 (3.6 HARDWARE SETS -> END OF SECTION)",
          [[r["start"], r["end"]] for r in van["regions"]] == [[389, 421]],
          str([[r["start"], r["end"]] for r in van["regions"]]))

    recs = stream_lines(root, "The_Door_Company__Copy_", "Vantage_TX-22_Div_01_08-p389-421")
    meta, lines = recs[0], recs[1:]
    hinge = [r for r in lines if r["page"] == 396 and "HINGE 5BB1HW" in r["text"]]
    check("p396 HINGE 5BB1HW row anchored with sane bbox",
          bool(hinge) and bbox_sane(hinge[0], meta["pages"]),
          f"{hinge[0]['anchor']} bbox={hinge[0]['bbox']}" if hinge else "not found")
    ghost = [r for r in lines if r["page"] == 421
             and "HARDWARE GROUP NO. 002" in r["text"].upper()]
    check("ghost set (GROUP NO. 002, p421) inside region", bool(ghost),
          ghost[0]["anchor"] if ghost else "not found")
    groups = {m.group(1).upper() for r in lines
              for m in [re.search(r"HARDWARE GROUP NO\.\s*([A-Z]?\w+)",
                                  r["text"], re.I)] if m}
    print(f"  info  {len(groups)} distinct HARDWARE GROUP ids in stream")

    # ---- round 2 (2026-08-17): five unseen projects, facts verified by
    # region digests + line-anchored Set-line sweeps (see corpus-notes.md)
    def regions_of(rep, name):
        return [[r["start"], r["end"]] for r in by_file(rep, name)["regions"]]

    def rejected_of(rep, name):
        return [[r["start"], r["end"]]
                for r in by_file(rep, name).get("rejected_regions", [])]

    print("Lyons Township HS:")
    rep = load_report(root, "Lyons_Township_HS")
    check("region == p285-294", regions_of(rep, "Project Manual (1).pdf") == [[285, 294]])
    recs = stream_lines(root, "Lyons_Township_HS", "Project_Manual_1_-p285-294")
    check("legend page (p285) inside region",
          any(r["page"] == 285 and r["text"].startswith("Legend") for r in recs[1:]))

    print("Morris Bank:")
    rep = load_report(root, "Morris_Bank")
    name = ("030f2d1d-Morris_Bank_Macon_-Spec_Manual_Issued_for_Const."
            "_1-26-26_FULL_SPECS.pdf")
    check("two set regions (twin 087100 sections)",
          regions_of(rep, name) == [[233, 263], [283, 290]], str(regions_of(rep, name)))
    check("cabinet-hardware narrative page rejected (p109)",
          rejected_of(rep, name) == [[109, 109]])
    recs = stream_lines(root, "Morris_Bank", "030f2d1d-Morris_Bank_Macon_"
                        "-Spec_Manual_Issued_for_Const._1-26-26_FULL_SPECS-p233-263")
    check("alpha-only set id (Set #MISC, p263) inside region -- regression for "
          "the miss the suspect sweep caught",
          any(r["page"] == 263 and r["text"].startswith("Set #MISC")
              for r in recs[1:]))
    recs = stream_lines(root, "Morris_Bank", "030f2d1d-Morris_Bank_Macon_"
                        "-Spec_Manual_Issued_for_Const._1-26-26_FULL_SPECS-p283-290")
    check("alphanumeric set id (Set #PR38ICCL) in second region",
          any("Set #PR38ICCL" in r["text"] for r in recs[1:]))

    print("National Doors and Hardware:")
    rep = load_report(root, "National_Doors_and_Hardware")
    check("region == p406-412", regions_of(rep, "15e2b8ac-FS17_Specs_V1.pdf") == [[406, 412]])

    print("Market View Apartments:")
    rep = load_report(root, "Market_View_Apartments")
    name = "S_251107 Market View Prelim Project Manual_pdf.pdf"
    check("region == p770-780", regions_of(rep, name) == [[770, 780]])
    check("paint-schedule page rejected (p888)", rejected_of(rep, name) == [[888, 888]])
    recs = stream_lines(root, "Market_View_Apartments",
                        "S_251107_Market_View_Prelim_Project_Manual_pdf-p770-780")
    check("set header carries description (Group No. 02 - ...)",
          any(re.search(r"Hardware Group No\. 02 - ", r["text"]) for r in recs[1:]))

    print("Livelle Mulholland:")
    rep = load_report(root, "Livelle_Mulholland_-_Life_Plan_Community")
    check("Vol1 region == p643-700 (Set-line sweep: min 643, max 700)",
          regions_of(rep, "2025-12-12_Livelle_Bid_Set_Project_Manual_Vol1_rev1.pdf")
          == [[643, 700]])
    for vol in ("Vol2", "Vol4"):
        f = by_file(rep, f"2025-12-12_Livelle_Bid_Set_Project_Manual_{vol}.pdf")
        check(f"{vol} -> no_sets", f["verdict"] == "no_sets")
    v3 = by_file(rep, "2025-12-12_Livelle_Bid_Set_Project_Manual_Vol3_rev1.pdf")
    check("Vol3 (kitchen-equipment cut sheets): 0 regions, 9 rejected",
          v3["verdict"] == "no_sets" and not v3["regions"]
          and len(v3.get("rejected_regions", [])) == 9)
    recs = stream_lines(root, "Livelle_Mulholland_-_Life_Plan_Community",
                        "2025-12-12_Livelle_Bid_Set_Project_Manual_Vol1_rev1-p643-700")
    check("decimal set id (Set: 87.1) in stream",
          any(r["text"].startswith("Set: 87.1") for r in recs[1:]))
    check("PE-as-manufacturer row (Gasketing S88BL PE) in stream",
          any("Gasketing S88BL PE" in r["text"] for r in recs[1:]))

    print("Roselle Public Library (wide-table dialect, 2026-08-18):")
    rep = load_report(root, "Roselle_Public_Library")
    check("087100 region == p15-17",
          regions_of(rep, "087100_FL_-_Door_Hardware_IFB_REVISED.pdf")
          == [[15, 17]])
    check("region tagged dialect=wide_table (carried by the wide rule alone)",
          by_file(rep, "087100_FL_-_Door_Hardware_IFB_REVISED.pdf")
          ["regions"][0].get("dialect") == "wide_table")
    for name in ("081113_FL_-_Hollow_Metal_Doors_and_Frames.pdf",
                 "081416_FL_-_Flush_Wood_Doors.pdf",
                 "083113_FL_-_Access_Doors_and_Frames.pdf"):
        check(f"{name} -> no_sets", by_file(rep, name)["verdict"] == "no_sets")
    recs = stream_lines(root, "Roselle_Public_Library",
                        "087100_FL_-_Door_Hardware_IFB_REVISED-p15-17")
    check("head-as-first-component-row in stream (1.1 CYLINDER / CORE ...)",
          any(r["text"].startswith("1.1 CYLINDER / CORE SCHLAGE")
              for r in recs[1:]))

    print("Forest Park School (decimal-qty book; structural tail net round "
          "2026-08-18):")
    rep = load_report(root, "Forest_Park_School")
    fp = by_file(rep, "Project Manual (1).pdf")
    check("region == p262-263 (p263 pulled by the tail net, not by signals)",
          [[r["start"], r["end"]] for r in fp["regions"]] == [[262, 263]],
          str([[r["start"], r["end"]] for r in fp["regions"]]))
    tail = fp["regions"][0].get("tail")
    check("tail evidence on record: csi footer 087100, EOS on p263",
          tail == {"pages_added": [263],
                   "evidence": {"csi_footer": "087100", "eos_page": 263}},
          str(tail))
    check("30 image-only pages counted", fp["image_only_pages"] == 30,
          str(fp["image_only_pages"]))

    print("AMI (Hardware Group No. dialect, clean tail):")
    rep = load_report(root, "AMI__Copy_")
    ami = by_file(rep, "c43c36d9-000_Full_Volume_ATC_Renovation_Bid_Specs"
                       "_Volume_1__1_.pdf")
    check("region == p397-419", [[r["start"], r["end"]]
                                 for r in ami["regions"]] == [[397, 419]])
    check("no tail extension recorded (EOS on the tail page itself)",
          "tail" not in ami["regions"][0])

    print("Gerrard (same schedule bound twice: standalone + inside arch "
          "spec):")
    rep = load_report(root, "2353_Gerrard_Street_Shelter")
    hdw = by_file(rep, "Hdw Spec & Sch-IFT_5.pdf")
    check("hardware book region == p19-36",
          [[r["start"], r["end"]] for r in hdw["regions"]] == [[19, 36]])
    arch = by_file(rep, "2.02 2535 Gerrard Shelter-Issued for Tender_5-"
                        "Architectural Specifications.pdf")
    check("arch-spec copy region == p165-182",
          [[r["start"], r["end"]] for r in arch["regions"]] == [[165, 182]])
    dup = arch["regions"][0].get("duplicate_of")
    check("arch-spec copy flagged duplicate_of the hardware book p19-36",
          dup == {"file": "Hdw Spec & Sch-IFT_5.pdf", "pages": [19, 36]},
          str(dup))

    print("five new books (2026-08-18 round): HFH / JC Ryan / Valor / "
          "Oswego / Woodridge:")
    rep = load_report(root, "HFH_DG_-_HOSPITAL")
    hfh = by_file(rep, "08 71 00 - DOOR HARDWARE.pdf")
    check("HFH is ONE region p20-183 -- three furniture-only pages "
          "(p164-166) are transparent, not a region break",
          [[r["start"], r["end"]] for r in hfh["regions"]] == [[20, 183]],
          str([[r["start"], r["end"]] for r in hfh["regions"]]))
    idx = by_file(rep, "08 71 00.01 - DOOR HARDWARE INDEX cut up.pdf")
    check("HFH door-index PDF: no_sets, and the filename hint is flagged",
          idx["verdict"] == "no_sets" and "warning" in idx)

    rep = load_report(root, "JC_Ryan_2")
    jc = by_file(rep, "087100 - Door Hardware-6.pdf")
    check("JC Ryan region == p24-46 (the EX-x.0 exterior sets are inside)",
          [[r["start"], r["end"]] for r in jc["regions"]] == [[24, 46]],
          str([[r["start"], r["end"]] for r in jc["regions"]]))
    check("JC Ryan suspects net quiet now (it is what found EX-1.0/3.0/4.0)",
          "suspect_pages" not in jc, str(jc.get("suspect_pages")))
    check("JC Ryan door/frame PDFs -> no_sets",
          all(by_file(rep, f)["verdict"] == "no_sets" for f in
              ("08 11 13 - HOLLOW METAL DOORS AND FRAMES.pdf",
               "08 14 16 - FLUSH WOOD DOORS.pdf")))

    rep = load_report(root, "Valor_Acres_Building_E")
    val = by_file(rep, "087100-DOOR-HARDWARE_Rev_2.pdf")
    check("Valor region == p7-18",
          [[r["start"], r["end"]] for r in val["regions"]] == [[7, 18]])
    check("Valor: the other 9 PDFs are all no_sets (one hardware file wins)",
          sum(1 for f in rep["files"] if f["verdict"] == "no_sets") == 9)
    sf = by_file(rep, "084113-ALUMINUM-FRAMED-ENTRANCES-AND-STOREFRONTS"
                      "_Rev_1.pdf")
    check("Valor storefront 'DOOR HARDWARE SCHEDULE' (p13) filed as a "
          "refer-elsewhere stub",
          any(s["page"] == 13 for s in sf.get("set_title_stubs", [])))

    osw = by_file(load_report(
        root, "Village_of_Oswego_New_Public_Works_Facility__Copy_"),
        "SPECIFICATIONS VOLUME 1.pdf")
    check("Oswego region == p418-445",
          [[r["start"], r["end"]] for r in osw["regions"]] == [[418, 445]])

    print("Woodridge (a book with NO hardware schedule -- honest empty):")
    rep = load_report(root, "Woodridge_Public_Works")
    wd = by_file(rep, "1548 - Specs - VOW - New Public Works Facility  "
                      "- B&P.pdf")
    check("no region accepted (08 71 00 runs p631-647 and is all narrative: "
          "the schedule is a contractor submittal)",
          wd["regions"] == [] and wd["verdict"] == "no_sets")
    rej = wd.get("rejected_regions", [])
    check("the vehicle-lubrication equipment table p812-821 is rejected, "
          "not silently kept",
          [[r["start"], r["end"]] for r in rej] == [[812, 821]], str(rej))
    check("rejection reason names the missing dialect (column headers "
          "without wide-table qty+finish)",
          bool(rej) and "wide-table" in rej[0]["reason"]
          and rej[0]["signals"]["set_hdr"] == 0,
          rej[0]["reason"] if rej else "")
    check("project-level ALARM fires (zero regions = eyeball this book)",
          "alarm" in rep)
    check("file-level warning points at rejected_regions", "warning" in wd)

    print("four new books (2026-08-18 second round): SAT TDP / SJC / "
          "Shubie / StarHardware:")
    sat = by_file(load_report(root, "SAT_TDP"),
                  "2025.12.19 - SAT TDP - Project Manual.pdf")
    check("SAT TDP: the schedule is p715-836 of a 3,930-page manual "
          "(3.6 HARDWARE SETS: ends p715, next section starts p837)",
          [[r["start"], r["end"]] for r in sat["regions"]] == [[715, 836]],
          str([[r["start"], r["end"]] for r in sat["regions"]]))

    rep = load_report(root, "SJC_Well_Behavioral")
    sjc = by_file(rep, "89671ede-20260218_SJC_BeWell_Bldg_B_85_"
                       "_DESIGN_UPDATE_-_SPECIFICATIONS.pdf")
    check("SJC region == p711-748 -- the 'HW 01' header form (no SET/GROUP "
          "word, no separator) used to score set_hdr=0 and lose 38 pages",
          [[r["start"], r["end"]] for r in sjc["regions"]] == [[711, 748]],
          str([[r["start"], r["end"]] for r in sjc["regions"]]))
    rej = {(r["start"], r["end"]): r for r in sjc.get("rejected_regions", [])}
    check("SJC p161 (a commissioning activity schedule: 'Mechanical equipment "
          "set 1 day') is rejected -- set header, zero hardware evidence",
          (161, 161) in rej
          and "hardware evidence" in rej[(161, 161)]["reason"],
          str(rej.get((161, 161), {}).get("reason")))
    check("SJC no longer files its p710 sets title as a refer-elsewhere stub "
          "(the region below it is real)",
          not any(s["page"] == 710 for s in sjc.get("set_title_stubs", [])))
    check("SJC dense-rejection net silent now (it fired on p711-748 before "
          "the HW fix and is what pointed at the miss)",
          "dense_rejections" not in sjc, str(sjc.get("dense_rejections")))

    shu = by_file(load_report(root, "Shubie_Center"), "e2231795-IFT_Specs.pdf")
    check("Shubie region == p174-175 (3 groups, next section p176)",
          [[r["start"], r["end"]] for r in shu["regions"]] == [[174, 175]],
          str([[r["start"], r["end"]] for r in shu["regions"]]))

    star = by_file(load_report(root, "StarHardware"),
                   "9839d1a1-Division_8_Specs_-_Commons_Lane.pdf")
    check("StarHardware keeps both real regions -- p25-26 (sets 103/104 "
          "inside section 08 17 13) and p53-113 (67 sets in 08 71 00)",
          [[r["start"], r["end"]] for r in star["regions"]]
          == [[25, 26], [53, 113]],
          str([[r["start"], r["end"]] for r in star["regions"]]))
    check("StarHardware p31 (a 'vertical schedule format sample' printed in "
          "the submittal requirements) is NOT a region",
          not any(r["start"] <= 31 <= r["end"] for r in star["regions"]))

    print("revision strikethrough (three books ship struck-out text):")
    strk = {}
    for proj in sorted(pp.name for pp in root.iterdir() if pp.is_dir()):
        for sp in (root / proj).glob("*.jsonl"):
            n = sum(1 for line in sp.read_text("utf-8").splitlines()
                    if '"struck"' in line)
            if n:
                strk[proj] = strk.get(proj, 0) + n
    check("exactly the three books with revision markup carry struck lines",
          set(strk) == {"SJC_Well_Behavioral", "HFH_DG_-_HOSPITAL",
                        "Valor_Acres_Building_E"}, str(strk))
    sjc_stream = next((root / "SJC_Well_Behavioral").glob("*.jsonl"))
    recs = [json.loads(x) for x in
            sjc_stream.read_text("utf-8").splitlines()[1:]]
    hw15 = [r for r in recs if r["text"].startswith("HW 15 ")]
    # the book keeps three "HW 15" lines: the live marker that the set is
    # withdrawn, and the two definitions it withdrew (struck end to end).
    check("SJC 'HW 15': the two superseded definitions are struck, the live "
          "'HW 15 Not Used' marker and the replacement 'HW 15A' are not",
          len(hw15) == 3
          and [r.get("struck", 0) >= 0.95 for r in hw15] == [False, True, True]
          and all(r.get("struck", 0) == 0 for r in recs
                  if r["text"].startswith("HW 15A ")),
          str([(r["anchor"], r.get("struck")) for r in hw15]))
    check("SJC drops 26 superseded set blocks: 117 headers on the page, 91 "
          "live sets after the struck ones leave the content view",
          sum(1 for r in recs if r.get("struck", 0) >= 0.95) == 273,
          str(sum(1 for r in recs if r.get("struck", 0) >= 0.95)))

    print("miss-detection nets (19 books with schedules):")
    all_projects = ["81-85_Bridgeport", "The_Door_Company__Copy_",
                    "Lyons_Township_HS", "Morris_Bank",
                    "National_Doors_and_Hardware", "Market_View_Apartments",
                    "Livelle_Mulholland_-_Life_Plan_Community",
                    "Roselle_Public_Library", "2353_Gerrard_Street_Shelter",
                    "AMI__Copy_", "Forest_Park_School", "HFH_DG_-_HOSPITAL",
                    "JC_Ryan_2", "Valor_Acres_Building_E",
                    "Village_of_Oswego_New_Public_Works_Facility__Copy_",
                    "SAT_TDP", "SJC_Well_Behavioral", "Shubie_Center",
                    "StarHardware"]
    # Woodridge is deliberately absent: its alarm IS the right answer for a
    # book whose 08 71 00 carries no schedule at all (asserted just above).
    alarms = [p for p in all_projects if "alarm" in load_report(root, p)]
    check("no project-level miss alarm", not alarms, str(alarms))
    suspects = [(p, f["file"], f["suspect_pages"]["count"])
                for p in all_projects for f in load_report(root, p)["files"]
                if f.get("suspect_pages")]
    check("no suspect pages left outside accepted regions", not suspects,
          str(suspects))
    v3 = by_file(load_report(root, "Livelle_Mulholland_-_Life_Plan_Community"),
                 "2025-12-12_Livelle_Bid_Set_Project_Manual_Vol3_rev1.pdf")
    check("all-rejected file carries an explicit warning (Vol3)",
          "warning" in v3)
    unrec = [(p, f["file"], t) for p in all_projects
             for f in load_report(root, p)["files"]
             for t in f.get("unreconciled_set_titles", [])]
    check("every HARDWARE-SETS-style title reconciles (region within 6p "
          "or refer-stub)", not unrec, str(unrec))
    v1 = by_file(load_report(root, "Livelle_Mulholland_-_Life_Plan_Community"),
                 "2025-12-12_Livelle_Bid_Set_Project_Manual_Vol1_rev1.pdf")
    check("Livelle 08 17 00 title (p541) classified as refer-elsewhere stub",
          any(s["page"] == 541 for s in v1.get("set_title_stubs", [])))

    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nall checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
