"""Human-inspection views for step-1.5 output (role-annotated streams).

  dump    stream.jsonl ...          -> sibling view/<stem>.txt  (role | anchor | text)
  overlay stream.jsonl PAGE ...     -> sibling view/<stem>-pNNN.png (boxes colored by role)

The overlay is the honest test of the role layer: page furniture red, column
headers blue, noise orange, content green -- one glance at a rendered page
shows whether the furniture detector grabbed real furniture and nothing else.
PDF paths come from the stream meta ("source_pdf", written by step1p5_roles).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCALE = 2.0  # 144 dpi

COLORS = {
    "page_header": (220, 40, 40),
    "page_footer": (220, 40, 40),
    "col_hdr": (40, 90, 220),
    "noise": (240, 150, 30),
    "content": (30, 160, 70),
}


def load_stream(path: Path):
    recs = [json.loads(l) for l in path.read_text("utf-8").splitlines()]
    return recs[0], recs[1:]


def view_dir(stream: Path) -> Path:
    d = stream.parent / "view"
    d.mkdir(exist_ok=True)
    return d


def dump(stream: Path) -> Path:
    meta, lines = load_stream(stream)
    out = view_dir(stream) / (stream.stem + ".txt")
    counts = meta["roles"]["counts"]
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"source file : {meta['file']}\n")
        fh.write(f"region      : p{meta['region'][0]}-p{meta['region'][1]}\n")
        fh.write(f"roles       : " + ", ".join(
            f"{k} {v}" for k, v in sorted(counts.items())) + "\n")
        fh.write("format      : role | anchor | text\n")
        cur = None
        for r in lines:
            if r["page"] != cur:
                cur = r["page"]
                fh.write(f"\n===== p{cur} =====\n")
            tag = {"page_header": "HDR", "page_footer": "FTR", "col_hdr": "COL",
                   "noise": "NSE", "content": "   "}[r["role"]]
            fh.write(f"{tag} {r['anchor']:>10} | {r['text']}\n")
    return out


def overlay(stream: Path, pages: list[int]) -> list[Path]:
    import pypdfium2 as pdfium
    from PIL import ImageDraw

    meta, lines = load_stream(stream)
    pdf = pdfium.PdfDocument(meta["source_pdf"])
    outs = []
    for pno in pages:
        img = pdf[pno - 1].render(scale=SCALE).to_pil().convert("RGB")
        draw = ImageDraw.Draw(img)
        for r in (l for l in lines if l["page"] == pno):
            x0, top, x1, bottom = (v * SCALE for v in r["bbox"])
            draw.rectangle([x0 - 2, top - 2, x1 + 2, bottom + 2],
                           outline=COLORS[r["role"]], width=3)
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
