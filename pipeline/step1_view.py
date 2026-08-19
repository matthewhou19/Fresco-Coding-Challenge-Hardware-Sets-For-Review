"""Human-inspection views for step-1 output.

  dump    stream.jsonl ...          -> sibling view/<stem>.txt  (anchor | text, per page)
  overlay stream.jsonl PAGE ...     -> sibling view/<stem>-pNNN.png (page render + line boxes)

The overlay is the honest test of the coordinate chain: boxes are drawn from
the stream's bbox values only -- if they hug the printed lines, the
anchor->bbox trade works; any systematic offset would be visible instantly.
PDF paths come from the region_report.json next to the stream file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCALE = 2.0  # 144 dpi


def load_stream(path: Path):
    recs = [json.loads(l) for l in path.read_text("utf-8").splitlines()]
    return recs[0], recs[1:]


def pdf_path_for(stream: Path, file_name: str) -> Path:
    report = json.loads((stream.parent / "region_report.json").read_text("utf-8"))
    for f in report["files"]:
        if f["file"] == file_name:
            return Path(f["path"])
    raise SystemExit(f"{file_name} not in {stream.parent}/region_report.json")


def view_dir(stream: Path) -> Path:
    d = stream.parent / "view"
    d.mkdir(exist_ok=True)
    return d


def dump(stream: Path) -> Path:
    meta, lines = load_stream(stream)
    out = view_dir(stream) / (stream.stem + ".txt")
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"source file : {meta['file']}\n")
        fh.write(f"region      : p{meta['region'][0]}-p{meta['region'][1]}\n")
        fh.write(f"lines       : {len(lines)}\n")
        fh.write("format      : anchor | text   (bbox lives in the .jsonl)\n")
        cur = None
        for r in lines:
            if r["page"] != cur:
                cur = r["page"]
                w, h = meta["pages"][str(cur)]
                fh.write(f"\n===== p{cur}  ({w} x {h} pt) =====\n")
            fh.write(f"{r['anchor']:>10} | {r['text']}\n")
    return out


def overlay(stream: Path, pages: list[int]) -> list[Path]:
    import pypdfium2 as pdfium
    from PIL import ImageDraw

    meta, lines = load_stream(stream)
    pdf = pdfium.PdfDocument(str(pdf_path_for(stream, meta["file"])))
    outs = []
    for pno in pages:
        img = pdf[pno - 1].render(scale=SCALE).to_pil().convert("RGB")
        draw = ImageDraw.Draw(img)
        for i, r in enumerate(l for l in lines if l["page"] == pno):
            x0, top, x1, bottom = (v * SCALE for v in r["bbox"])
            color = (220, 40, 40) if i % 2 == 0 else (40, 90, 220)
            draw.rectangle([x0 - 2, top - 2, x1 + 2, bottom + 2],
                           outline=color, width=2)
        out = view_dir(stream) / f"{stream.stem}-p{pno}.png"
        img.save(out)
        outs.append(out)
    pdf.close()
    return outs


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in ("dump", "overlay"):
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "dump":
        for arg in sys.argv[2:]:
            print(dump(Path(arg)))
    else:
        stream, pages = Path(sys.argv[2]), [int(p) for p in sys.argv[3:]]
        for out in overlay(stream, pages):
            print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
