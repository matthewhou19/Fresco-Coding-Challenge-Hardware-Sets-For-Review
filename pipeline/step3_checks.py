"""Acceptance checks for step 3a (per-book dossier): asserts hand-verified
facts from the sample corpus (2026-08-18): Morris's p230-232 legend tables
and Bridgeport's p2 tables were pulled from the raw PDFs and read whole;
Livelle was swept for legends and has none; the trailing-slot tallies were
cross-checked against an independent probe over the same streams. Slot
identities are per-book INDUCTION results --
the checks pin the induced schema, never a positional assumption.

Usage:  python pipeline/step3_checks.py [step3_root]
Default: data/out/step3.  Prints PASS/FAIL per fact; exit 1 on any failure.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILURES.append(name)


def dossier(root: Path, project: str) -> dict:
    return json.loads((root / project / "dossier.json").read_text("utf-8"))


def pdf_entry(d: dict, suffix: str) -> dict:
    return next(v for k, v in d["pdfs"].items() if k.endswith(suffix))


def stream(d: dict, prefix: str) -> dict:
    return next(v for k, v in d["streams"].items() if k.startswith(prefix))


def top_dict(slot: dict) -> dict:
    return {t: c for t, c in slot["top"]}


def legend_traces(legend: dict) -> set[str]:
    """Code of every row the harvest refused, from all three trace bins.
    Mark-don't-delete: a rejected row must still be findable by its code."""
    out = set()
    for key in ("suspect_rows", "furniture_rows", "isolated_rows"):
        for entry in legend.get(key, []):
            head = entry["text"].split()
            if head:
                out.add(head[0])
    return out


BRIDGEPORT_FINISH = {"26D", "32D", "626", "628", "630", "689",
                     "C26D", "C28", "C32D", "US15", "US28"}
MORRIS_MFR = {"AD", "BE", "HS", "MC", "MED1", "PE", "RO", "SA", "VA01"}
MORRIS_FINISH = {"10BE", "313", "613E", "630", "BLK", "EB"}
NATIONAL_MFR = {"ADA", "B/O", "IVE", "JOH", "LCN", "SCH", "TRI", "VON", "ZER"}
# StarHardware's two abbreviation tables, transcribed from p24 (L09-L23) and
# p51 (L47-L72) of 9839d1a1-Division_8_Specs_-_Commons_Lane.pdf, 2026-08-18
STAR_MFR = {"AD", "BA", "CA", "CO", "CR", "DO", "FR", "HE", "IV", "JA",
            "LC", "LO", "MA", "PE", "RX", "SC", "SN", "SY", "TR", "VO"}
STAR_NON_MFR = {"IFC", "OCI", "PART", "ADA"}
# Gerrard's Reference Standards article (p13-p18) + its running page header
GERRARD_NON_MFR = {"2535", "90", "BHMA", "DHI", "ICC/ANSI", "NFPA", "UL"}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/out/step3")

    print("[dossiers exist, one per project]")
    projects = ["81-85_Bridgeport", "Livelle_Mulholland_-_Life_Plan_Community",
                "Lyons_Township_HS", "Market_View_Apartments", "Morris_Bank",
                "National_Doors_and_Hardware", "The_Door_Company__Copy_",
                "Roselle_Public_Library"]
    for p in projects:
        check(f"{p}/dossier.json", (root / p / "dossier.json").exists())
    n_streams = sum(len(dossier(root, p)["streams"]) for p in projects)
    check("10 streams covered", n_streams == 10, f"got {n_streams}")

    print("[Bridgeport: p2 legend = 11 finish codes, maker NAMES only; "
          "induced schema = finish@-1, no mfr column]")
    d = dossier(root, "81-85_Bridgeport")
    for suffix in ("08-70-00-Hardware-Schedule.pdf",
                   "08-70-00-Hardware-Schedule_Rev_0.pdf"):
        pe = pdf_entry(d, suffix)
        check(f"{suffix}: finish legend exact 11",
              set(pe["legend"]["finish"]) == BRIDGEPORT_FINISH)
        check(f"{suffix}: zero mfr codes", pe["legend"]["mfr"] == {})
        check(f"{suffix}: >=12 maker names",
              len(pe["legend"]["mfr_names_without_codes"]) >= 12)
    s1 = stream(d, "08-70-00-Hardware-Schedule-")
    s2 = stream(d, "08-70-00-Hardware-Schedule_Rev_0-")
    check("twin streams induce identically",
          json.dumps(s1["slots"], sort_keys=True)
          == json.dumps(s2["slots"], sort_keys=True))
    check("slot -1 = finish", s1["slots"]["-1"]["identity"] == "finish")
    check("schema: finish@-1, mfr absent",
          s1["column_schema"] == {"finish_slot": -1, "mfr_slot": None})
    check("CA/GRY used in schedule but NOT in p2 legend (closed-set gap)",
          top_dict(s1["slots"]["-1"]).get("CA") == 63
          and "CA" not in BRIDGEPORT_FINISH)

    print("[Morris: p230 mfr 9 codes (PE=Pemko), p232 finish 6, options; "
          "both 087100 sections inherit the same PDF dossier]")
    d = dossier(root, "Morris_Bank")
    pe = pdf_entry(d, "FULL_SPECS.pdf")
    check("mfr legend exact 9", set(pe["legend"]["mfr"]) == MORRIS_MFR)
    check("PE resolves to Pemko", "Pemko" in pe["legend"]["mfr"]["PE"])
    check("BE/MED1 are makers (BEST/Medeco), not finishes",
          "BEST" in pe["legend"]["mfr"]["BE"]
          and "Medeco" in pe["legend"]["mfr"]["MED1"])
    check("finish legend exact 6", set(pe["legend"]["finish"]) == MORRIS_FINISH)
    check("option codes harvested (70/7P/2004M...)",
          {"70", "7P", "2004M"} <= set(pe["legend"]["option"]))
    for pre, n_cand, mfr_share in (
            ("030f2d1d-Morris_Bank_Macon_-Spec_Manual_Issued_for_Const."
             "_1-26-26_FULL_SPECS-p233-263", 230, 0.99),
            ("030f2d1d-Morris_Bank_Macon_-Spec_Manual_Issued_for_Const."
             "_1-26-26_FULL_SPECS-p283-290", 138, 0.99)):
        st = stream(d, pre)
        check(f"...{pre[-9:]}: schema mfr@-1 finish@-2",
              st["column_schema"] == {"mfr_slot": -1, "finish_slot": -2})
        check(f"...{pre[-9:]}: {n_cand} candidates",
              st["n_component_candidates"] == n_cand)
        check(f"...{pre[-9:]}: slot -1 legend share >= {mfr_share}",
              st["slots"]["-1"]["legend_mfr_share"] >= mfr_share,
              str(st["slots"]["-1"]["legend_mfr_share"]))
    check("qty note found in window prose",
          any("each pair of doors" in q["text"]
              for q in pe["qty_notes"]))

    print("[National: preamble abbreviation table = 9 mfr codes exactly]")
    d = dossier(root, "National_Doors_and_Hardware")
    pe = pdf_entry(d, "15e2b8ac-FS17_Specs_V1.pdf")
    check("mfr legend exact 9 (ANSI prose junk gated out)",
          set(pe["legend"]["mfr"]) == NATIONAL_MFR)
    check("ADA -> Adams Rite", "Adams Rite" in pe["legend"]["mfr"]["ADA"])
    st = stream(d, "15e2b8ac")
    check("schema mfr@-1 finish@-2",
          st["column_schema"] == {"mfr_slot": -1, "finish_slot": -2})
    check("slot -1 mfr high", st["slots"]["-1"]["confidence"] == "high")

    print("[Livelle: NO legend anywhere -> distribution-only dossier; "
          "the PE page-footer fake must not leak in as a code]")
    d = dossier(root, "Livelle_Mulholland_-_Life_Plan_Community")
    pe = pdf_entry(d, "Vol1_rev1.pdf")
    check("legend_missing flag", pe["legend_missing"] is True)
    check("no PE fake from footer", "PE" not in pe["legend"]["mfr"])
    st = stream(d, "2025-12-12_Livelle")
    t1, t2 = top_dict(st["slots"]["-1"]), top_dict(st["slots"]["-2"])
    check("slot -1 = mfr (high) on distribution alone",
          st["slots"]["-1"]["identity"] == "mfr"
          and st["slots"]["-1"]["confidence"] == "high")
    check("slot -1 top: SA 325 / PE 281 / RO 250 / MK 184",
          (t1.get("SA"), t1.get("PE"), t1.get("RO"), t1.get("MK"))
          == (325, 281, 250, 184))
    check("NO rides the mfr column (23 rows)", t1.get("NO") == 23)
    check("slot -2 = finish; US15 310 / BSP 106 (SPEC's own example token)",
          st["slots"]["-2"]["identity"] == "finish"
          and t2.get("US15") == 310 and t2.get("BSP") == 106)
    check("schema mfr@-1 finish@-2",
          st["column_schema"] == {"mfr_slot": -1, "finish_slot": -2})
    check("qty semantics note captured",
          any("each pair of doors" in q["text"] for q in pe["qty_notes"]))

    print("[Vantage: icon glyphs stripped before tokenizing; "
          "prose window yields no fake legend]")
    d = dossier(root, "The_Door_Company__Copy_")
    pe = pdf_entry(d, "Vantage TX-22 Div 01, 08.pdf")
    check("legend_missing flag", pe["legend_missing"] is True)
    st = stream(d, "Vantage_TX-22")
    t1, t2 = top_dict(st["slots"]["-1"]), top_dict(st["slots"]["-2"])
    check("slot -1 = mfr (high); IVE 110 top", t1.get("IVE") == 110
          and st["slots"]["-1"]["identity"] == "mfr"
          and st["slots"]["-1"]["confidence"] == "high")
    check("slot -2 = finish; 626 95",
          st["slots"]["-2"]["identity"] == "finish" and t2.get("626") == 95)
    check("no private-use icon char survives in slot tokens",
          all(ord(ch) < 0xE000 for slot in st["slots"].values()
              for tok, _ in slot["top"] for ch in tok))

    print("[Market View: distribution-only; B/O rides the mfr column]")
    d = dossier(root, "Market_View_Apartments")
    pe = pdf_entry(d, "S_251107 Market View Prelim Project Manual_pdf.pdf")
    check("legend_missing flag", pe["legend_missing"] is True)
    st = stream(d, "S_251107")
    t1 = top_dict(st["slots"]["-1"])
    check("slot -1 = mfr (high); IVE 58 / FAL 40",
          st["slots"]["-1"]["identity"] == "mfr"
          and t1.get("IVE") == 58 and t1.get("FAL") == 40)
    check("B/O in mfr column top", "B/O" in t1)
    check("schema mfr@-1 finish@-2",
          st["column_schema"] == {"mfr_slot": -1, "finish_slot": -2})

    print("[Lyons: the dropped-row book -- induction must degrade LOUDLY, "
          "not guess: both slots unclear, no schema, pseudo rows excluded]")
    d = dossier(root, "Lyons_Township_HS")
    pe = pdf_entry(d, "Project Manual (1).pdf")
    check("legend_missing flag", pe["legend_missing"] is True)
    st = stream(d, "Project_Manual_1_")
    check("both slots unclear/low",
          all(st["slots"][s]["identity"] == "unclear"
              and st["slots"][s]["confidence"] == "low" for s in ("-1", "-2")))
    check("no column schema induced",
          st["column_schema"] == {"mfr_slot": None, "finish_slot": None})
    check("140 candidates ('691 LCN' finish+mfr fragments NOT sampled)",
          st["n_component_candidates"] == 140,
          str(st["n_component_candidates"]))

    print("[Roselle: wide-table book -- no legend, sampler blind by design "
          "(qty sits mid-row): distribution-only dossier, no schema guessed]")
    d = dossier(root, "Roselle_Public_Library")
    pe = pdf_entry(d, "087100_FL_-_Door_Hardware_IFB_REVISED.pdf")
    check("legend_missing flag", pe["legend_missing"] is True)
    check("inline '613 (OIL RUBBED BRONZE)' glosses NOT harvested as legend "
          "(>=4-distinct-codes table gate holds)",
          pe["legend"]["finish"] == {} and pe["legend"]["mfr"] == {})
    st = stream(d, "087100_FL")
    check("zero component candidates (RE_QTY_LED blind to wide rows)",
          st["n_component_candidates"] == 0)
    check("no column schema induced (honest null/null)",
          st["column_schema"] == {"mfr_slot": None, "finish_slot": None})

    print("[Forest Park: legends printed IN-region above the sets title; "
          "header-shadow + hyphen-code + sets-title-close round 2026-08-18]")
    d = dossier(root, "Forest_Park_School")
    pe = pdf_entry(d, "Project Manual (1).pdf")
    check("mfr legend holds the book's own codes incl. hyphenated C-R "
          "(NFPA/PART left via the table-shape net 2026-08-18)",
          sorted(pe["legend"]["mfr"]) == ["BES", "BRN", "BYO", "C-R", "DKA",
                                          "DKC"],
          str(sorted(pe["legend"]["mfr"])))
    check("finish legend == the 7 parseable FINISH LIST codes ('HARDWARE "
          "SETS:' no longer reads as a code row)",
          sorted(pe["legend"]["finish"]) == ["26D", "32D", "626", "630",
                                             "689", "AL", "CAS"],
          str(sorted(pe["legend"]["finish"])))
    check("13 option codes (OPTION LIST parses once the header routes)",
          len(pe["legend"]["option"]) == 13, str(len(pe["legend"]["option"])))
    st = stream(d, "Project_Manual_1_")
    check("5 decimal-qty candidates -> schema induced mfr@-1 / finish@-2",
          st["n_component_candidates"] == 5
          and st["column_schema"] == {"mfr_slot": -1, "finish_slot": -2})
    check("mfr slot high on legend share 0.8 (BES/C-R/BRN vouched)",
          st["slots"]["-1"]["identity"] == "mfr"
          and st["slots"]["-1"]["confidence"] == "high"
          and st["slots"]["-1"]["legend_mfr_share"] == 0.8)

    print("[AMI: no legend anywhere in the book -- honest distribution-only "
          "dossier, schema still induced from 392 rows]")
    d = dossier(root, "AMI__Copy_")
    pe = pdf_entry(d, "c43c36d9-000_Full_Volume_ATC_Renovation_Bid_Specs"
                      "_Volume_1__1_.pdf")
    check("legend_missing flag with empty mfr/finish tables",
          pe["legend_missing"] is True and pe["legend"]["mfr"] == {}
          and pe["legend"]["finish"] == {})
    st = stream(d, "c43c36d9")
    check("392 candidates -> mfr@-1 / finish@-2",
          st["n_component_candidates"] == 392
          and st["column_schema"] == {"mfr_slot": -1, "finish_slot": -2})

    print("[Gerrard: same legend harvested from both bindings]")
    d = dossier(root, "2353_Gerrard_Street_Shelter")
    for suffix in ("Hdw Spec & Sch-IFT_5.pdf",
                   "Architectural Specifications.pdf"):
        pe = pdf_entry(d, suffix)
        leg = pe["legend"]
        check(f"0 mfr / 13 finish / 10 option codes ({suffix[:20]}...)",
              (len(leg["mfr"]), len(leg["finish"]),
               len(leg["option"])) == (0, 13, 10),
              f'{len(leg["mfr"])}/{len(leg["finish"])}/{len(leg["option"])}')
        # p13-p18 of both bindings, read whole 2026-08-18: what the harvest
        # used to file as this book's manufacturer legend is the Reference
        # Standards article -- "1. UL Listed Miscellaneous Fire Door
        # Accessories.", "2. BHMA certified for door sweeps...", "4. 90 min.
        # fire rating.", "NFPA 80", "ICC/ANSI A117.1", "DHI Publication - ..."
        # -- plus the running page header "2535 Gerrard Shelter 08 71 00 -
        # Door Hardware". Not one manufacturer among the seven.
        check(f"...and the 7 rows that used to sit there are standards prose "
              f"and a page header, every one still visible in a trace bin "
              f"({suffix[:20]}...)",
              not (GERRARD_NON_MFR & set(leg["mfr"]))
              and GERRARD_NON_MFR <= legend_traces(leg),
              str(sorted(GERRARD_NON_MFR - legend_traces(leg))))
        check(f"...the real finish/option tables are untouched by both nets "
              f"({suffix[:20]}...)",
              leg["finish"].get("US26D") == "Chromium Plated, Dull"
              and leg["option"].get("NRP") == "Non-Removable Pin",
              f'{leg["finish"].get("US26D")!r} {leg["option"].get("NRP")!r}')
        # ...and the emptiness is honest but NOT complete. Read off p164
        # (2026-08-18): between "B. Finish List" and "D. Option List" sits a
        # real "C. Manufacturer List" -- with exactly three rows. That is
        # under MIN_TABLE_CODES, so the run degrades to suspect_rows, the
        # same before this change as after (the old 7 came from the p159
        # prose run, a different run entirely). Pinned so the next round
        # sees it: lowering the floor to 3 is a corpus-wide decision.
        check(f"...the book's REAL 3-code manufacturer list is still below "
              f"the >=4 table floor, and all 3 stay traceable "
              f"({suffix[:20]}...)",
              not ({"CMND", "DE", "HA"} & set(leg["mfr"]))
              and {"CMND Command Access", "DE Detex", "HA Hager Companies"}
              <= {e["text"] for e in leg["suspect_rows"]
                  if e.get("run_below_floor") == "mfr"},
              str(sorted({"CMND", "DE", "HA"} & set(leg["mfr"]))))
    # the delivered field survives that miss because the slot distribution
    # carries it: 772 of Gerrard's components are HA (764) or CMND (8)
    gerrard_mfr = Counter()
    for path in sorted((root / "2353_Gerrard_Street_Shelter").glob("*.sets.jsonl")):
        for line in path.read_text("utf-8").splitlines()[1:]:
            for comp in json.loads(line)["components"]:
                if comp.get("mfr"):
                    gerrard_mfr[comp["mfr"]] += 1
    check("...and the induced slot plus the column band still deliver those "
          "codes on 774 components, so the miss costs book context, not "
          "fields (DE arrives only via the column arm)",
          gerrard_mfr == Counter({"HA": 764, "CMND": 8, "DE": 2}),
          str(dict(gerrard_mfr)))

    print("[Forest Park: two rows of spec prose left the mfr table]")
    d = dossier(root, "Forest_Park_School")
    leg = next(iter(d["pdfs"].values()))["legend"]
    # p260-L05 "NFPA 80." and p260-L32 "PART 3 EXECUTION", both read off the
    # PDF 2026-08-18: a standard reference and an article heading, each
    # sitting 27 lines away from the nearest real legend row.
    check("Forest Park legend: 6 manufacturer codes, none of them NFPA/PART",
          len(leg["mfr"]) == 6 and not ({"NFPA", "PART"} & set(leg["mfr"])),
          str(sorted(leg["mfr"])))
    check("...both rejects recorded as isolated (too far from the table)",
          {"NFPA", "PART"} <= {e["text"].split()[0]
                               for e in leg.get("isolated_rows", [])},
          str(leg.get("isolated_rows")))

    print("[National keeps its real ADA code: the nets reject by position, "
          "not by a blacklist of words]")
    leg = next(iter(dossier(root, "National_Doors_and_Hardware")
                    ["pdfs"].values()))["legend"]
    check("National mfr legend still holds ADA (a real maker code there) "
          "while StarHardware's ADA row is gone",
          set(leg["mfr"]) == NATIONAL_MFR, str(sorted(leg["mfr"])))

    print("[four new books, 2026-08-18: three write no legend at all, "
          "StarHardware writes one -- and two nets take four rows out of it]")
    for proj, prefix, n_cand, conf in (
            ("SAT_TDP", "2025.12.19_-_SAT_TDP_-_Project_Manual-p715-836",
             1796, "high"),
            ("SJC_Well_Behavioral",
             "89671ede-20260218_SJC_BeWell_Bldg_B_85_"
             "_DESIGN_UPDATE_-_SPECIFICATIONS-p711-748", 482, "high"),
            ("Shubie_Center", "e2231795-IFT_Specs-p174-175", 28, "medium")):
        d = dossier(root, proj)
        pe = next(iter(d["pdfs"].values()))
        st = stream(d, prefix)
        check(f"{proj[:12]}: no legend anywhere -> distribution-only dossier, "
              f"schema still induced mfr@-1 / finish@-2 ({conf})",
              pe.get("legend_missing") is True
              and not pe["legend"]["mfr"] and not pe["legend"]["finish"]
              and st["column_schema"] == {"mfr_slot": -1, "finish_slot": -2}
              and st["slots"]["-1"]["n"] == n_cand
              and st["slots"]["-1"]["confidence"] == conf,
              f'{pe.get("legend_missing")} {st["column_schema"]} '
              f'{st["slots"]["-1"]["n"]}')

    d = dossier(root, "StarHardware")
    pe = next(iter(d["pdfs"].values()))
    leg = pe["legend"]
    mfr = leg["mfr"]
    check("StarHardware legend: exactly the 20 codes of the book's own two "
          "tables (p24 L09-L23 and p51 L47-L72, read off the PDF)",
          set(mfr) == STAR_MFR, str(sorted(set(mfr) ^ STAR_MFR)))
    check("...values come from the first table to define each code "
          "(PE=Pemko or equal, SC=Schlage Manufacturing)",
          mfr.get("PE") == "Pemko or equal"
          and mfr.get("SC") == "Schlage Manufacturing",
          f'{mfr.get("PE")!r} {mfr.get("SC")!r}')
    # p19-p24 / p47-p52 read off the PDF 2026-08-18: the running two-line
    # page header "IFC 12.12.2025" / "OCI Design The Commons Lane" repeats
    # on all 12 window pages at a fixed y in the top 6% of the sheet, and
    # both lines parse as code rows. "PART 3 EXECUTION" (p22-L45, p49-L08)
    # and "ADA - Americans with Disabilities Act..." (p23-L71) are one-offs
    # 16 and 105 lines from the nearest real legend row.
    check("...the page header is out via the role view, not the word list",
          {"IFC", "OCI"} <= {e["text"].split()[0]
                             for e in leg.get("furniture_rows", [])}
          and not ({"IFC", "OCI"} & set(mfr)),
          str(sorted({"IFC", "OCI"} & set(mfr))))
    check("...the two one-off prose rows are out via the table-shape net",
          {"PART", "ADA"} <= {e["text"].split()[0]
                              for e in leg.get("isolated_rows", [])}
          and not ({"PART", "ADA"} & set(mfr)),
          str(sorted({"PART", "ADA"} & set(mfr))))
    assigned = set()
    for path in sorted((root / "StarHardware").glob("*.rules.jsonl")):
        for line in path.read_text("utf-8").splitlines():
            rec = json.loads(line)
            for row in rec.get("rows", []):
                if row.get("mfr"):
                    assigned.add(row["mfr"])
    check("...dropping them changed no component: they never reached one "
          "(that is why this was precision, not a wrong delivery). The "
          "assigned set beyond the legend is the induced distribution -- "
          "AB/FH/SE/WA sit at row tails and predate this change",
          not (STAR_NON_MFR & assigned) and {"AB", "FH", "SE", "WA"} <= assigned,
          str(sorted(STAR_NON_MFR & assigned)))

    print(f"\n{'ALL PASS' if not FAILURES else 'FAILURES: ' + str(FAILURES)}"
          f"  ({(len(FAILURES) and '!') or 'ok'})")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
