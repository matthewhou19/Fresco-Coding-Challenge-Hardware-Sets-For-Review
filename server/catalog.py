"""Read-only catalog over the pipeline's output -- what the viewer serves.

The server never runs extraction. It reads what `step3c_assemble.py` already
wrote, so it needs no API key, no network, and starts instantly. Two files per
stream:

    data/out/step3/<project>/<stream>.sets.jsonl   sets (delivery records)
    data/out/step1p5/<project>/<stream>.jsonl      lines (anchor -> bbox)

The line stream is what turns a component's anchors ("p285-L12") back into
rectangles on the page, so the viewer can highlight one component and not just
the whole set -- the anchor mechanism the extractor is built on, made visible.

Field naming follows the challenge spec at the surface (`set_number`,
`description`, `location`, `components[]` with qty / description /
catalog_number / mfr / finish / notes). Everything the pipeline knows beyond
the spec is kept alongside under its own name, never folded into a spec field.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

RENDER_SCALE = 2.0  # page PNGs render at 144 dpi; bbox_px = bbox_pt * RENDER_SCALE

STEP1 = Path("data/out/step1")
STEP1P5 = Path("data/out/step1p5")
STEP3 = Path("data/out/step3")
UPLOADS = Path("data/out/uploads")  # one sub-tree per uploaded PDF (jobs.py)


def find_data_root() -> Path:
    """Repo root holding `data/out/` and `pdfs/`.

    `FRESCO_DATA_ROOT` wins -- that is the deploy knob: point it at whatever
    directory the image ships. Otherwise walk up from this file until the
    output tree appears, falling back to the repo root.
    """
    env = os.environ.get("FRESCO_DATA_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for cand in here.parents:
        if (cand / STEP3).is_dir():
            return cand
    return here.parents[1]


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def _read_meta(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.loads(fh.readline())


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text("utf-8")) if path.is_file() else {}


@dataclass(frozen=True)
class Stream:
    """One extracted document region -- the unit the viewer switches between."""

    id: str  # "<project_dir>/<stem>", also the URL path
    project_dir: str
    project: str
    stem: str
    file: str
    region: list
    n_sets: int
    n_components: int
    n_demoted: int
    model: str
    sets_path: Path
    lines_path: Path
    pdf_path: Path
    duplicate_of: str | None = None
    confidence: dict = field(default_factory=dict)
    source: str = "corpus"          # "corpus" | "upload"
    job_id: str | None = None

    def summary(self) -> dict:
        return {
            "id": self.id,
            "project": self.project,
            "file": self.file,
            "region": self.region,
            "n_sets": self.n_sets,
            "n_components": self.n_components,
            "n_demoted": self.n_demoted,
            "model": self.model,
            "confidence": self.confidence,
            "duplicate_of": self.duplicate_of,
            "source": self.source,
            "job_id": self.job_id,
            "pdf_available": self.pdf_path.is_file(),
        }


class Catalog:
    """Discovers streams on disk and serves them in the shapes the API needs."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root).resolve() if root else find_data_root()
        self.streams: dict[str, Stream] = {}
        self._payloads: dict[str, dict] = {}
        self._discover()

    # -- discovery ---------------------------------------------------------

    def _discover(self) -> None:
        self._scan(self.root / STEP3, self.root / STEP1P5, self.root / STEP1,
                   "corpus", None)
        for job_dir in sorted((self.root / UPLOADS).glob("*")):
            if (job_dir / "step3").is_dir():
                self._scan(job_dir / "step3", job_dir / "step1p5", job_dir / "step1",
                           "upload", job_dir.name)

    def _scan(self, step3_root: Path, step1p5_root: Path, step1_root: Path,
              source: str, job_id: str | None) -> None:
        """Read one output tree -- the corpus one, or one upload job's own.

        Project name, duplicate flag and confidence counts all come from
        reports the pipeline already writes; nothing here recomputes anything.
        """
        uploaded_name = None
        if job_id:
            uploaded_name = _load_json(step3_root.parent / "job.json").get("filename")

        for project_path in sorted(p for p in step3_root.glob("*") if p.is_dir()):
            sets_paths = sorted(project_path.glob("*.sets.jsonl"))
            if not sets_paths:
                continue
            project_dir = project_path.name
            region = _load_json(step1_root / project_dir / "region_report.json")
            report = _load_json(step3_root / project_dir / "assembly_report.json")
            conf = {e.get("stream", ""): e.get("assembly_confidence", {})
                    for e in report.get("streams", [])}
            dup: dict[tuple, str] = {}
            for entry in region.get("files", []):
                for reg in entry.get("regions", []):
                    other = reg.get("duplicate_of")
                    if other:
                        pages = other.get("pages", ["?", "?"])
                        dup[(entry["file"], reg["start"], reg["end"])] = (
                            f"{other.get('file')} p{pages[0]}-{pages[1]}")

            for sets_path in sets_paths:
                stem = sets_path.name[: -len(".sets.jsonl")]
                meta = _read_meta(sets_path)
                asm = meta.get("assembly", {})
                sid = f"{project_dir}/{stem}"
                key = (meta.get("file"), *meta.get("region", [])) \
                    if len(meta.get("region", [])) == 2 else ()
                self.streams[sid] = Stream(
                    id=sid,
                    project_dir=project_dir,
                    project=(uploaded_name or region.get("project")
                             or project_dir.replace("_", " ").strip()),
                    stem=stem,
                    file=meta.get("file", stem),
                    region=list(meta.get("region", [])),
                    n_sets=asm.get("n_blocks", 0),
                    n_components=asm.get("n_components", 0),
                    n_demoted=asm.get("n_demoted", 0),
                    model=meta.get("model", ""),
                    sets_path=sets_path,
                    lines_path=step1p5_root / project_dir / f"{stem}.jsonl",
                    pdf_path=self.root / str(meta.get("source_pdf", "")).replace("\\", "/"),
                    duplicate_of=dup.get(key),
                    confidence=conf.get(stem, {}),
                    source=source,
                    job_id=job_id,
                )

    def refresh(self) -> None:
        """Re-read the disk -- called when an upload job finishes."""
        self.streams.clear()
        self._payloads.clear()
        self._discover()

    # -- payloads ----------------------------------------------------------

    def stats(self) -> dict:
        return {
            "root": str(self.root),
            "n_streams": len(self.streams),
            "n_sets": sum(s.n_sets for s in self.streams.values()),
            "n_components": sum(s.n_components for s in self.streams.values()),
            "render_scale": RENDER_SCALE,
        }

    def payload(self, sid: str) -> dict:
        """Everything the viewer needs for one stream (cached in memory)."""
        if sid in self._payloads:
            return self._payloads[sid]

        s = self.streams[sid]
        sets_recs = _read_jsonl(s.sets_path)
        meta, raw_sets = sets_recs[0], sets_recs[1:]
        line_recs = _read_jsonl(s.lines_path)
        line_meta, lines = line_recs[0], line_recs[1:]

        boxes = {r["anchor"]: {"page": r["page"], "bbox": r["bbox"]} for r in lines}
        texts = {r["anchor"]: r["text"] for r in lines}
        pages = {int(page): {"width": size[0], "height": size[1]}
                 for page, size in line_meta.get("pages", {}).items()}

        payload = {
            **s.summary(),
            "source_pdf": str(meta.get("source_pdf", "")),
            "bbox_convention": line_meta.get("bbox_convention", ""),
            "render_scale": RENDER_SCALE,
            "pages": pages,
            "sets": [self._view_set(r, boxes, texts) for r in raw_sets],
        }
        self._payloads[sid] = payload
        return payload

    @staticmethod
    def _view_set(rec: dict, boxes: dict, texts: dict) -> dict:
        components = []
        for c in rec.get("components", []):
            anchors = c.get("anchors", [])
            components.append({
                "qty": c.get("qty"),
                "unit": c.get("unit"),
                "description": c.get("description"),
                "catalog_number": c.get("catalog_number"),
                "mfr": c.get("mfr"),
                "finish": c.get("finish"),
                "notes": c.get("notes", []),
                "confidence": c.get("confidence", {}),
                "flags": c.get("flags", []),
                "anchors": anchors,
                "boxes": [boxes[a] for a in anchors if a in boxes],
            })
        return {
            "seq": rec.get("seq"),
            "set_number": rec.get("set_id"),
            "description": rec.get("description"),
            "location": rec.get("location", []),
            "components": components,
            "n_components": len(components),
            "doors": rec.get("doors", []),
            "set_notes": rec.get("set_notes", []),
            "properties": rec.get("properties", []),
            "demoted": rec.get("demoted", []),
            "flags": rec.get("flags", []),
            "empty": rec.get("empty", False),
            "family": rec.get("family"),
            "header_anchor": rec.get("header_anchor"),
            "header_text": texts.get(rec.get("header_anchor", "")),
        }

    def export(self, sid: str) -> dict:
        """The deliverable JSON: spec shape, minus the viewer's drawing aids."""
        p = self.payload(sid)
        sets = [{
            "set_number": s["set_number"],
            "description": s["description"],
            "location": s["location"],
            "components": [{k: v for k, v in c.items() if k != "boxes"}
                           for c in s["components"]],
            "doors": s["doors"],
            "set_notes": s["set_notes"],
            "properties": s["properties"],
            "demoted": s["demoted"],
            "empty": s["empty"],
            "flags": s["flags"],
        } for s in p["sets"]]
        return {
            "project": p["project"],
            "source_file": p["file"],
            "pages": p["region"],
            "model": p["model"],
            "bbox_convention": p["bbox_convention"],
            "n_sets": len(sets),
            "n_components": sum(len(s["components"]) for s in sets),
            "sets": sets,
        }

    def page_numbers(self, sid: str) -> set[int]:
        """Pages the region covers -- the only pages the image route renders."""
        return set(self.payload(sid)["pages"])
