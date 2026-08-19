"""Acceptance checks for step 1.5 (line roles): asserts hand-verified facts
from the sample corpus -- the 2026-08-17 furniture probe of all 9 step-1
streams.

Usage:  python pipeline/step1p5_checks.py [step1_root] [step1p5_root]
Defaults: data/out/step1  data/out/step1p5.
Prints PASS/FAIL per fact; exit code 1 if anything failed.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from step1_locate import RE_QTY_UNIT, RE_SET_HDR  # noqa: E402
from step1p5_roles import ROLES  # noqa: E402

FAILURES = []
KEEP = ("anchor", "page", "line", "text", "bbox")


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILURES.append(name)


def load(root: Path, project: str, prefix: str) -> tuple[dict, list[dict]]:
    path = next((root / project).glob(prefix + "*.jsonl"))
    recs = [json.loads(l) for l in path.read_text("utf-8").splitlines()]
    return recs[0], recs[1:]


def page_role_counts(lines: list[dict], role: str) -> Counter:
    c = Counter()
    for r in lines:
        if r["role"] == role:
            c[r["page"]] += 1
    return c


def every_page(lines: list[dict], role: str, n: int) -> bool:
    pages = {r["page"] for r in lines}
    c = page_role_counts(lines, role)
    return all(c.get(p, 0) == n for p in pages)


def role_of(lines: list[dict], pred) -> set[str]:
    return {r["role"] for r in lines if pred(r)}


STREAMS = [  # (project, stream prefix)
    ("81-85_Bridgeport", "08-70-00-Hardware-Schedule-p3-49"),
    ("81-85_Bridgeport", "08-70-00-Hardware-Schedule_Rev_0-p3-49"),
    ("Livelle_Mulholland_-_Life_Plan_Community",
     "2025-12-12_Livelle_Bid_Set_Project_Manual_Vol1_rev1-p643-700"),
    ("Lyons_Township_HS", "Project_Manual_1_-p285-294"),
    ("Market_View_Apartments", "S_251107_Market_View_Prelim_Project_Manual_pdf-p770-780"),
    ("Morris_Bank", "030f2d1d-Morris_Bank_Macon_-Spec_Manual_Issued_for_Const."
                    "_1-26-26_FULL_SPECS-p233-263"),
    ("Morris_Bank", "030f2d1d-Morris_Bank_Macon_-Spec_Manual_Issued_for_Const."
                    "_1-26-26_FULL_SPECS-p283-290"),
    ("National_Doors_and_Hardware", "15e2b8ac-FS17_Specs_V1-p406-412"),
    ("The_Door_Company__Copy_", "Vantage_TX-22_Div_01_08-p389-421"),
    ("Roselle_Public_Library", "087100_FL_-_Door_Hardware_IFB_REVISED-p15-17"),
    ("2353_Gerrard_Street_Shelter",
     "2.02_2535_Gerrard_Shelter-Issued_for_Tender_5-Architectural"
     "_Specifications-p165-182"),
    ("2353_Gerrard_Street_Shelter", "Hdw_Spec_Sch-IFT_5-p19-36"),
    ("AMI__Copy_",
     "c43c36d9-000_Full_Volume_ATC_Renovation_Bid_Specs_Volume_1__1_"
     "-p397-419"),
    ("Forest_Park_School", "Project_Manual_1_-p262-263"),
    # the rest of the corpus, so the table is every stream, not a sample
    ("HFH_DG_-_HOSPITAL", "08_71_00_-_DOOR_HARDWARE-p20-183"),
    ("JC_Ryan_2", "087100_-_Door_Hardware-6-p24-46"),
    ("Valor_Acres_Building_E", "087100-DOOR-HARDWARE_Rev_2-p7-18"),
    ("Village_of_Oswego_New_Public_Works_Facility__Copy_",
     "SPECIFICATIONS_VOLUME_1-p418-445"),
    ("SAT_TDP", "2025.12.19_-_SAT_TDP_-_Project_Manual-p715-836"),
    ("SJC_Well_Behavioral", "89671ede-20260218_SJC_BeWell_Bldg_B_85_"
                            "_DESIGN_UPDATE_-_SPECIFICATIONS-p711-748"),
    ("Shubie_Center", "e2231795-IFT_Specs-p174-175"),
    ("StarHardware", "9839d1a1-Division_8_Specs_-_Commons_Lane-p25-26"),
    ("StarHardware", "9839d1a1-Division_8_Specs_-_Commons_Lane-p53-113"),
]


def main() -> int:
    in_root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/out/step1")
    out_root = Path(sys.argv[2] if len(sys.argv) > 2 else "data/out/step1p5")

    print("all 23 streams (mark-don't-delete + guards):")
    ok_id = ok_role = ok_meta = ok_sethdr = ok_qty = ok_page = True
    for project, prefix in STREAMS:
        meta_in, lines_in = load(in_root, project, prefix)
        meta_out, lines_out = load(out_root, project, prefix)
        ok_id &= len(lines_in) == len(lines_out) and all(
            {k: a[k] for k in KEEP} == {k: b[k] for k in KEEP}
            for a, b in zip(lines_in, lines_out))
        ok_role &= all(r.get("role") in ROLES for r in lines_out)
        ok_meta &= (all(meta_out.get(k) == meta_in[k] for k in meta_in)
                    and "source_pdf" in meta_out
                    and meta_out["roles"]["counts"]
                    == dict(Counter(r["role"] for r in lines_out)
                            | {r: 0 for r in ROLES
                               if r not in {l["role"] for l in lines_out}}))
        # "struck" is a deliberate demotion (the source crossed the line
        # out), so it joins "content" as a legal home for a real set header
        # or component row; the guard still forbids furniture/noise eating one.
        live = {"content", "struck"}
        ok_sethdr &= role_of(lines_out,
                             lambda r: RE_SET_HDR.search(r["text"])) <= live
        ok_qty &= role_of(lines_out,
                          lambda r: RE_QTY_UNIT.match(r["text"])) <= live
        # a page may end up furniture-only, but only if it really is blank:
        # HFH keeps four running-header-only pages inside its region (p121,
        # p164-166 -- the transparent pages step 1 bridges).  Those carry 3
        # lines; the thinnest page that does have content carries 4.  So the
        # guard is "furniture ate a page" not "a page was empty".
        per_page = Counter(r["page"] for r in lines_out)
        kept_pages = {r["page"] for r in lines_out if r["role"] in live}
        ok_page &= all(p in kept_pages or per_page[p] <= 3
                       for p in per_page)
    check("anchors/text/bbox byte-identical, line counts preserved", ok_id)
    check("every line carries a role from the closed set", ok_role)
    check("meta preserved + source_pdf + counts consistent", ok_meta)
    check("no set-header line demoted off content (guard holds)", ok_sethdr)
    check("no qty-unit component row marked as furniture/noise", ok_qty)
    check("every page keeps a content/struck line unless it is blank "
          "(<=3 furniture lines)", ok_page)

    print("Vantage (2-line header + footer, PART shells, col-hdr reprints):")
    _, van = load(out_root, "The_Door_Company__Copy_", "Vantage_TX-22")
    check("every page exactly 2 page_header + 1 page_footer",
          every_page(van, "page_header", 2) and every_page(van, "page_footer", 1))
    foot = [r for r in van if r["role"] == "page_footer"]
    check("all 33 footers match 'DOOR HARDWARE 08 71 00 - N'",
          len(foot) == 33 and all(re.fullmatch(r"DOOR HARDWARE 08 71 00 - \d+",
                                               r["text"]) for r in foot))
    noise = [r for r in van if r["role"] == "noise"]
    check("44 noise lines, all 'PART <n> -' empty shells",
          len(noise) == 44 and all(re.fullmatch(r"PART \d+ -", r["text"].strip())
                                   for r in noise), str(len(noise)))
    check("prefixed set head stays content (PART 6 - HARDWARE GROUP NO. 103)",
          role_of(van, lambda r: "PART 6 - HARDWARE GROUP NO. 103" in r["text"])
          == {"content"})
    check("prefixed provide-each line stays content",
          role_of(van, lambda r: "PROVIDE EACH SGL DOOR(S)" in r["text"])
          == {"content"})
    cols = [r for r in van if r["role"] == "col_hdr"]
    check("42 col_hdr lines, incl. page-top reprint p390-L04",
          len(cols) == 42 and any(r["anchor"] == "p390-L04" for r in cols)
          and all(r["text"].startswith("QTY DESCRIPTION") for r in cols),
          str(len(cols)))
    check("section title '3.6 HARDWARE SETS:' (p389) is content",
          role_of(van, lambda r: r["page"] == 389 and "3.6 HARDWARE SETS" in r["text"])
          == {"content"})
    check("ghost set line (GROUP NO. 002, p421) is content",
          role_of(van, lambda r: r["page"] == 421 and "GROUP NO. 002" in r["text"])
          == {"content"})

    print("Bridgeport (footer-only furniture; dup twins):")
    _, b49 = load(out_root, "81-85_Bridgeport", "08-70-00-Hardware-Schedule-p3-49")
    _, brev = load(out_root, "81-85_Bridgeport", "08-70-00-Hardware-Schedule_Rev_0")
    for name, lines in (("49p", b49), ("Rev_0", brev)):
        foot = [r for r in lines if r["role"] == "page_footer"]
        check(f"{name}: zero page_header; every page exactly 1 'Page N of 238' footer",
              not any(r["role"] == "page_header" for r in lines)
              and every_page(lines, "page_footer", 1)
              and all(re.fullmatch(r"Page \d+ of 238", r["text"]) for r in foot))
    headings = {m.group(1) for r in b49 if r["role"] == "content"
                for m in [re.search(r"Heading\s*#(\d+)", r["text"])] if m}
    check("all 90 Heading numbers still visible in content view",
          len(headings) == 90, f"{len(headings)} found")
    check("schedule title on p3 is content",
          role_of(b49, lambda r: r["text"] == "Hardware Schedule") == {"content"})
    check("dup twins carry identical role sequences",
          [r["role"] for r in b49] == [r["role"] for r in brev])

    print("Morris (rigid one-set-per-page layout -- the trap book):")
    meta1, m1 = load(out_root, "Morris_Bank", "030f2d1d-Morris_Bank_Macon_-Spec_"
                     "Manual_Issued_for_Const._1-26-26_FULL_SPECS-p233-263")
    check("every page exactly 3 page_header + 1 page_footer",
          every_page(m1, "page_header", 3) and every_page(m1, "page_footer", 1))
    check("'Set #101' stays content (frac .97, dev 1.92 -- set-header guard)",
          role_of(m1, lambda r: r["text"].startswith("Set #101")) == {"content"})
    check("'4 Hinges MPB79 ...' stays content (frac .81, dev 0.00 -- band)",
          role_of(m1, lambda r: r["text"].startswith("4 Hinges MPB79")) == {"content"})
    check("'N/A' ghost markers stay content",
          role_of(m1, lambda r: r["text"].strip() == "N/A") == {"content"})
    check("zero col_hdr (Note-prose with 3 column words stays content)",
          not any(r["role"] == "col_hdr" for r in m1))
    sus = meta1["roles"]["suspect_furniture"]
    check("rigid rows surface as suspect_furniture (miss-visibility)",
          any(s["sample"].startswith("4 Hinges MPB79") for s in sus),
          f"{len(sus)} suspects")
    _, m2 = load(out_root, "Morris_Bank", "030f2d1d-Morris_Bank_Macon_-Spec_"
                 "Manual_Issued_for_Const._1-26-26_FULL_SPECS-p283-290")
    check("twin section p283-290: same furniture shape (3+1 per page)",
          every_page(m2, "page_header", 3) and every_page(m2, "page_footer", 1))
    check("alphanumeric set id (Set #PR38ICCL) stays content",
          role_of(m2, lambda r: "Set #PR38ICCL" in r["text"]) == {"content"})

    print("Lyons (no furniture at all; icon-glyph noise):")
    _, lyo = load(out_root, "Lyons_Township_HS", "Project_Manual_1_")
    check("zero page_header and zero page_footer",
          not any(r["role"] in ("page_header", "page_footer") for r in lyo))
    noise = [r for r in lyo if r["role"] == "noise"]
    check("131 noise lines, all without any alphanumeric char",
          len(noise) == 131 and all(not re.search(r"[0-9A-Za-z]", r["text"])
                                    for r in noise), str(len(noise)))
    check("25 col_hdr lines (per-set reprints, none at page top)",
          sum(1 for r in lyo if r["role"] == "col_hdr") == 25)
    check("'Hardware Group No. 05 - Not Used' stays content",
          role_of(lyo, lambda r: "No. 05 - Not Used" in r["text"]) == {"content"})
    check("legend page intro ('Legend:', p285) stays content",
          role_of(lyo, lambda r: r["text"] == "Legend:") == {"content"})

    print("Market View (3-line header + 2-line footer):")
    _, mv = load(out_root, "Market_View_Apartments", "S_251107")
    check("every page exactly 3 page_header + 2 page_footer",
          every_page(mv, "page_header", 3) and every_page(mv, "page_footer", 2))
    check("24 col_hdr lines",
          sum(1 for r in mv if r["role"] == "col_hdr") == 24)
    check("set head with description ('Group No. 02 - ...') stays content",
          role_of(mv, lambda r: re.search(r"Hardware Group No\. 02 - ", r["text"]))
          == {"content"})

    print("National (footer at 0.888 -- bottom-band binding constraint):")
    _, nat = load(out_root, "National_Doors_and_Hardware", "15e2b8ac")
    check("every page exactly 2 page_header + 2 page_footer",
          every_page(nat, "page_header", 2) and every_page(nat, "page_footer", 2))
    foot_texts = {r["text"] for r in nat if r["role"] == "page_footer"}
    check("both footer shapes caught (#2024814 ... and 08/29/25)",
          any(t.startswith("#2024814") for t in foot_texts) and "08/29/25" in foot_texts)
    check("15 col_hdr lines",
          sum(1 for r in nat if r["role"] == "col_hdr") == 15)

    print("Livelle (4-line footer; PE decoys):")
    _, liv = load(out_root, "Livelle_Mulholland_-_Life_Plan_Community", "2025-12-12")
    check("zero page_header; every page exactly 4 page_footer",
          not any(r["role"] == "page_header" for r in liv)
          and every_page(liv, "page_footer", 4))
    pe_foot = [r for r in liv if r["text"].startswith("PE Project 96050.00")]
    check("'PE Project ...' footer marked on all 58 pages "
          "(fake PE removed from content view)",
          len(pe_foot) == 58 and {r["role"] for r in pe_foot} == {"page_footer"})
    check("decimal set id ('Set: 87.1') stays content",
          role_of(liv, lambda r: r["text"].startswith("Set: 87.1")) == {"content"})
    check("PE-as-manufacturer row (Gasketing S88BL PE) stays content",
          role_of(liv, lambda r: "Gasketing S88BL PE" in r["text"]) == {"content"})
    check("'Description:' lines stay content",
          role_of(liv, lambda r: r["text"].startswith("Description:")) == {"content"})

    print("Roselle (wide table: letterhead+title+col-header all banded furniture):")
    _, ros = load(out_root, "Roselle_Public_Library",
                  "087100_FL_-_Door_Hardware_IFB_REVISED-p15-17")
    check("every page exactly 4 page_header + 2 page_footer",
          every_page(ros, "page_header", 4) and every_page(ros, "page_footer", 2))
    check("column-header line is furniture, never col_hdr (0.7 ratio gate) "
          "and never content (top band catches it)",
          role_of(ros, lambda r: r["text"].startswith("SET HARDWARE TYPE"))
          == {"page_header"})
    check("all 33 bare-dotted set heads stay content",
          sum(1 for r in ros if r["role"] == "content"
              and re.match(r"^\d{1,2}\.\d{1,2}\s+[A-Z(]", r["text"])) == 33)
    check("content view 256 lines, zero noise",
          sum(1 for r in ros if r["role"] == "content") == 256
          and sum(1 for r in ros if r["role"] == "noise") == 0)

    print("Forest Park (2-page region: furniture proven by 2/2 repetition, "
          "2026-08-18):")
    _, fp = load(out_root, "Forest_Park_School", "Project_Manual_1_-p262-263")
    check("counts: 2 page_header + 4 page_footer + 42 content, zero noise",
          sum(1 for r in fp if r["role"] == "page_header") == 2
          and sum(1 for r in fp if r["role"] == "page_footer") == 4
          and sum(1 for r in fp if r["role"] == "content") == 42
          and sum(1 for r in fp if r["role"] == "noise") == 0)
    check("'000053277 CHSD218 Phase 3' tops both pages as page_header",
          role_of(fp, lambda r: r["text"].startswith("000053277"))
          == {"page_header"})
    check("section footer + date footer banded out on both pages",
          role_of(fp, lambda r: r["text"].startswith("Door Hardware 087100"))
          == {"page_footer"}
          and role_of(fp, lambda r: r["text"].startswith("February 3,"))
          == {"page_footer"})
    check("the five decimal component rows stay content",
          sum(1 for r in fp if r["role"] == "content"
              and re.match(r"^\d\.0 ", r["text"])) == 5)

    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nall checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
