"""Acceptance checks for the viewer server.

The server adds no extraction logic -- it re-serves what steps 1.5 and 3c
wrote -- so these checks are about the serving contract, not about accuracy:

  * discovery finds every stream the pipeline produced, nothing invented;
  * counts match an independent recount straight off the JSONL files, not the
    meta headers the server itself reads;
  * the API surface carries the challenge's field names (set_number,
    description, location, components[qty/description/catalog_number/mfr/
    finish/notes]) and export.json drops the viewer-only drawing aids;
  * every location is inside its page, and every component anchor resolves to
    a real line box on a page the set claims -- the location promise the demo
    draws on screen;
  * page PNGs render, cache, and come back byte-identical on a second call
    (same determinism rule as every other step);
  * an upload can be deleted whole -- pdf, output and LLM cache -- so the same
    book can be re-run live instead of replayed, and the corpus tree is never
    touched by any of it.

Usage:  python pipeline/server_checks.py
Prints PASS/FAIL per fact; exit code 1 if anything failed.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILURES.append(name)


SPEC_SET_FIELDS = ("set_number", "description", "location", "components")
SPEC_COMPONENT_FIELDS = ("qty", "description", "catalog_number", "mfr", "finish", "notes")

LYONS = "Lyons_Township_HS/Project_Manual_1_-p285-294"
BRIDGEPORT = "81-85_Bridgeport/08-70-00-Hardware-Schedule-p3-49"


def recount(root: Path) -> tuple[int, int, int]:
    """Independent net: count streams/sets/components off the files.

    Both trees the server serves -- the corpus one and any upload job's own.
    """
    streams = sorted((root / "data/out/step3").glob("*/*.sets.jsonl")) + \
        sorted((root / "data/out/uploads").glob("*/step3/*/*.sets.jsonl"))
    sets = comps = 0
    for path in streams:
        recs = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l]
        sets += len(recs) - 1  # first record is the meta header
        comps += sum(len(r["components"]) for r in recs[1:])
    return len(streams), sets, comps


def main() -> int:
    cache = tempfile.TemporaryDirectory()
    os.environ["FRESCO_PAGE_CACHE"] = cache.name
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

    import app as server  # noqa: E402  (env must be set first)
    from fastapi.testclient import TestClient  # noqa: E402

    client = TestClient(server.app)
    root = server.CATALOG.root
    n_streams, n_sets, n_comps = recount(root)

    print(f"data root: {root}")

    print("\ndiscovery (against a recount off the JSONL files):")
    health = client.get("/api/health").json()
    check(f"health reports {n_streams} streams / {n_sets} sets / {n_comps} components",
          (health["n_streams"], health["n_sets"], health["n_components"])
          == (n_streams, n_sets, n_comps),
          f"{health['n_streams']}/{health['n_sets']}/{health['n_components']}")
    rows = client.get("/api/streams").json()
    check("one /api/streams row per stream on disk", len(rows) == n_streams)
    check("every row resolves its source PDF and its line stream",
          all(r["pdf_available"] for r in rows)
          and all(s.lines_path.is_file() for s in server.CATALOG.streams.values()))
    check("the two known copy pairs are flagged duplicate, nothing else is",
          sorted(r["id"] for r in rows if r["duplicate_of"]) == [
              "2353_Gerrard_Street_Shelter/2.02_2535_Gerrard_Shelter-Issued_for"
              "_Tender_5-Architectural_Specifications-p165-182",
              "81-85_Bridgeport/08-70-00-Hardware-Schedule_Rev_0-p3-49"])
    check("unknown stream is a 404, not a traversal",
          client.get("/api/streams/../../etc/passwd").status_code == 404
          and client.get("/api/streams/nope/nope").status_code == 404)

    print(f"\nspec field contract (all {n_streams} streams):")
    missing_set, missing_comp, wrong_id = [], [], []
    for sid in server.CATALOG.streams:
        payload = client.get(f"/api/streams/{sid}").json()
        raw = [json.loads(l) for l in
               server.CATALOG.streams[sid].sets_path.read_text("utf-8").splitlines() if l][1:]
        for s, r in zip(payload["sets"], raw):
            missing_set += [f for f in SPEC_SET_FIELDS if f not in s]
            if s["set_number"] != r["set_id"]:
                wrong_id.append(sid)
            for c in s["components"]:
                missing_comp += [f for f in SPEC_COMPONENT_FIELDS if f not in c]
    check("every set carries set_number / description / location / components",
          not missing_set, f"{len(missing_set)} missing")
    check("every component carries qty/description/catalog_number/mfr/finish/notes",
          not missing_comp, f"{len(missing_comp)} missing")
    check("set_number is the pipeline's set_id, unchanged", not wrong_id)

    print("\nlocation integrity (what the viewer draws):")
    bad_page, bad_lines, outside, unresolved, off_set = 0, 0, 0, 0, 0
    for sid in server.CATALOG.streams:
        p = client.get(f"/api/streams/{sid}").json()
        pages = {int(k): v for k, v in p["pages"].items()}
        lo, hi = p["region"]
        for s in p["sets"]:
            claimed = {l["page"] for l in s["location"]}
            for l in s["location"]:
                if not lo <= l["page"] <= hi or l["page"] not in pages:
                    bad_page += 1
                    continue
                if l["lines"][0] > l["lines"][1]:
                    bad_lines += 1
                x0, top, x1, bottom = l["bbox"]
                dim = pages[l["page"]]
                if x0 < 0 or top < 0 or x1 > dim["width"] or bottom > dim["height"] \
                        or x0 > x1 or top > bottom:
                    outside += 1
            for c in s["components"]:
                if len(c["boxes"]) != len(c["anchors"]):
                    unresolved += 1
                off_set += sum(1 for b in c["boxes"] if b["page"] not in claimed)
    check("every set location sits on a page inside its own region", bad_page == 0)
    check("every line range runs forwards", bad_lines == 0)
    check("every bbox sits inside the page box", outside == 0)
    check("every component anchor resolves to a line box", unresolved == 0)
    check("every component box lands on a page its set claims", off_set == 0)

    print("\nhand-checked values (Lyons p285, from the step-3 desk run):")
    lyons = client.get(f"/api/streams/{LYONS}").json()
    s1 = lyons["sets"][0]
    c1 = s1["components"][0]
    check("set 01 location = p285 lines 4-13, bbox [72.0, 123.13, 534.88, 237.99]",
          s1["location"] == [{"page": 285, "lines": [4, 13],
                              "bbox": [72.0, 123.13, 534.88, 237.99]}])
    check("its only component = 1 EA SURFACE CLOSER / 4040XP SCUSH / LCN / 691",
          (c1["qty"], c1["unit"], c1["description"], c1["catalog_number"],
           c1["mfr"], c1["finish"]) ==
          (1, "EA", "SURFACE CLOSER", "4040XP SCUSH", "LCN", "691"))
    check("its two anchors (broken line) give two boxes on p285",
          c1["anchors"] == ["p285-L12", "p285-L13"]
          and [b["page"] for b in c1["boxes"]] == [285, 285])
    empty = [s for s in lyons["sets"] if s["empty"]]
    check("the four Not-Used ghosts survive extraction and reach the API",
          len(empty) == 4 and empty[0]["set_number"] == "05")
    check("their header line is served as evidence, not dropped",
          empty[0]["header_text"] == "Hardware Group No. 05 - Not Used")
    check("doors ride along with the set (5 door lines on set 01)",
          len(s1["doors"]) == 5 and s1["doors"][0]["text"].startswith("For use on Door"))

    print("\nconfidence + demoted lines (bonus surfaces):")
    bp = client.get(f"/api/streams/{BRIDGEPORT}").json()
    counts = {}
    for s in bp["sets"]:
        for c in s["components"]:
            counts[c["confidence"]["assembly"]] = counts.get(c["confidence"]["assembly"], 0) + 1
    # 2026-08-18: the column-band round accepts 5 values per binding on
    # column evidence, which lands them at medium -- 1 high and 4 low moved
    # in, total unchanged (723). Every mover carries a *_from_column flag.
    # 2026-08-19 zone round: 18 hyphen-led finish cells (-626E family) fill
    # from the measured finish zone (fill flag => medium), and 3 lows climb
    # to medium once the moved value closes the conservation ledger.
    check("Bridgeport assembly confidence = 530 high / 190 medium / 3 low",
          counts == {"high": 530, "medium": 190, "low": 3}, str(counts))
    check("the stream row carries the same counts for the picker",
          bp["confidence"] == counts)
    demoted = [d for s in bp["sets"] for d in s["demoted"]]
    check("11 demoted lines are served with their reason, not deleted",
          len(demoted) == 11 and all(d.get("reason") for d in demoted), str(len(demoted)))


    print("\nexport.json (the deliverable shape):")
    exp = client.get(f"/api/streams/{LYONS}/export.json")
    body = exp.json()
    check("served as a download attachment",
          "attachment" in exp.headers.get("content-disposition", ""))
    check("29 sets / 142 components, bbox convention stated",
          (body["n_sets"], body["n_components"]) == (29, 142)
          and "top-left" in body["bbox_convention"])
    check("keeps spec fields, drops the viewer's drawing boxes",
          all(f in body["sets"][0] for f in SPEC_SET_FIELDS)
          and all("boxes" not in c for c in body["sets"][0]["components"])
          and body["sets"][0]["components"][0]["anchors"] == ["p285-L12", "p285-L13"])

    print("\npage images (render, cache, determinism):")
    r1 = client.get(f"/api/streams/{LYONS}/pages/285.png")
    r2 = client.get(f"/api/streams/{LYONS}/pages/285.png")
    cached = list(Path(cache.name).rglob("*.png"))
    check("page 285 renders as PNG", r1.status_code == 200
          and r1.content[:8] == b"\x89PNG\r\n\x1a\n"
          and r1.headers["content-type"] == "image/png")
    check("it lands in the page cache", len(cached) == 1, str(len(cached)))
    check("second call is byte-identical (cache hit)", r1.content == r2.content,
          f"{len(r1.content)} bytes")
    check("a page outside the region is a 404",
          client.get(f"/api/streams/{LYONS}/pages/1.png").status_code == 404)

    print("\nupload channel (validation, honest failure, delete for a live re-run):")
    corpus_files = sorted((root / "data/out/step3").glob("*/*.sets.jsonl"))
    check("a non-pdf filename is refused",
          client.post("/api/uploads", files={"file": ("notes.md", b"# hi", "text/markdown")})
          .status_code == 400)
    check("a .pdf without the %PDF- header is refused",
          client.post("/api/uploads", files={"file": ("fake.pdf", b"nope", "application/pdf")})
          .status_code == 400)
    check("an empty file is refused",
          client.post("/api/uploads", files={"file": ("empty.pdf", b"", "application/pdf")})
          .status_code == 400)
    check("nothing was left behind in _incoming",
          not list((root / "data/out/uploads/_incoming").glob("*.pdf"))
          if (root / "data/out/uploads/_incoming").is_dir() else True)

    # A blank page is a real PDF with no text layer: step 1 must stop the job
    # and say so, instead of running the paid stage on nothing.
    from PIL import Image  # noqa: E402  (already a page-render dependency)
    blank = Path(cache.name) / "blank_scan.pdf"
    Image.new("RGB", (612, 792), "white").save(blank)
    posted = client.post("/api/uploads",
                         files={"file": ("blank_scan.pdf", blank.read_bytes(),
                                         "application/pdf")})
    job_id = posted.json()["job"]["id"]
    deadline = time.time() + 60
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.5)
    check("upload is accepted and queued", posted.status_code == 202)
    check("the job stops at no_sets instead of reporting an empty book",
          job.get("status") == "no_sets", str(job.get("status")))
    check("step 1 ran, the five later stages (incl. the paid one) were skipped",
          [s["state"] for s in job["stages"]] == ["done"] + ["skipped"] * 5)
    check("the pipeline's own alarm is handed to the caller",
          "no set region accepted" in (job["result"]["locate"]["alarm"] or ""))
    check("the file is diagnosed as image-only, not silently empty",
          job["result"]["locate"]["files"][0]["image_only_pages"] == 1
          and job["result"]["locate"]["files"][0]["verdict"] == "image_only_no_text")
    check("a failed job adds no stream to the catalog",
          len(client.get("/api/streams").json()) == n_streams)
    check("uploads and corpus rows are labelled by source",
          {r["source"] for r in client.get("/api/streams").json()} <= {"corpus", "upload"})

    # Deleting is what makes a live re-run possible: the job owns its own LLM
    # cache, so the same PDF uploaded again really runs again.
    job_dir = root / "data/out/uploads" / job_id
    deleted = client.delete(f"/api/jobs/{job_id}")
    check("delete removes the job and reports what went with it",
          deleted.status_code == 200
          and deleted.json()["deleted"] == job_id
          and deleted.json()["n_streams"] == n_streams)
    check("its whole sub-tree is gone from disk (pdf, output, llm cache)",
          not job_dir.exists(), str(job_dir))
    check("the job itself is a 404 afterwards",
          client.get(f"/api/jobs/{job_id}").status_code == 404)
    check("an unknown id or a traversal is refused, not a stray rmtree",
          client.delete("/api/jobs/nope").status_code == 404
          and client.delete("/api/jobs/../../step3").status_code in (404, 405))
    check("the corpus tree is untouched by all of this",
          sorted((root / "data/out/step3").glob("*/*.sets.jsonl")) == corpus_files,
          f"{len(corpus_files)} stream files before")

    print("\nstatic viewer:")
    home = client.get("/")
    check("GET / serves the viewer html",
          home.status_code == 200 and "<title>Hardware set extractor</title>" in home.text)
    check("its js and css are served too",
          client.get("/app.js").status_code == 200
          and client.get("/style.css").status_code == 200)

    cache.cleanup()
    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nall checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
