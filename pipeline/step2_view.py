"""Human-inspection views for step-2 output (set blocks).

  dump    blocks.jsonl ...       -> sibling view/<stem>.txt  (blocks + their lines)
  overlay blocks.jsonl PAGE ...  -> sibling view/<stem>-pNNN.png (one box per
                                    block span, labeled with the set id)

The overlay is the honest test of the location layer: the challenge asks for
"meaningful location data", and the proof is a rectangle that frames exactly
one set on the rendered page -- which is also what the demo shows. Line
texts come from the step-1.5 stream recorded in the meta ("source_stream");
membership is reconstructed from the spans alone, the same way a consumer
would use them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCALE = 2.0  # 144 dpi

PALETTE = [  # cycled per block so neighbors differ
    (220, 40, 40), (40, 90, 220), (30, 150, 60), (200, 120, 20),
    (140, 50, 190), (0, 150, 160),
]


def load_blocks(path: Path):
    recs = [json.loads(l) for l in path.read_text("utf-8").splitlines()]
    return recs[0], recs[1:]


def load_content(meta: dict) -> list[dict]:
    recs = [json.loads(l) for l in
            Path(meta["source_stream"]).read_text("utf-8").splitlines()]
    return [r for r in recs[1:] if r["role"] == "content"]


def member_map(blocks: list[dict]) -> dict:
    """(page, line) -> block record, from the spans."""
    owner = {}
    for b in blocks:
        for s in b["spans"]:
            for line in range(s["lines"][0], s["lines"][1] + 1):
                owner[(s["page"], line)] = b
    return owner


def view_dir(path: Path) -> Path:
    d = path.parent / "view"
    d.mkdir(exist_ok=True)
    return d


def dump(path: Path) -> Path:
    meta, blocks = load_blocks(path)
    content = load_content(meta)
    owner = member_map(blocks)
    out = view_dir(path) / (path.stem + ".txt")
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"source file : {meta['file']}\n")
        fh.write(f"region      : p{meta['region'][0]}-p{meta['region'][1]}\n")
        c = meta["chunks"]
        fh.write(f"blocks      : {c['n_blocks']}  (content {c['n_content_lines']}"
                 f" = pre {c['n_preamble_lines']} + blocks"
                 f" + post {c['n_postamble_lines']})\n")
        cur = None
        for r in content:
            b = owner.get((r["page"], r["line"]))
            if b is not cur:
                cur = b
                if b:
                    pages = "+".join(f"p{s['page']}" for s in b["spans"])
                    fh.write(f"\n#{b['seq']:>3} set {b['set_id']}"
                             f"  [{b['family']}, {pages}, {b['n_lines']} lines"
                             f"{', EMPTY' if b['empty'] else ''}]"
                             + (f"  trailer: {b['trailer']}" if b["trailer"] else "")
                             + "\n")
            tag = "    " if b else ("PRE " if cur is None else "POST")
            fh.write(f"{tag} {r['anchor']:>10} | {r['text']}\n")
    return out


def overlay(path: Path, pages: list[int]) -> list[Path]:
    import pypdfium2 as pdfium
    from PIL import ImageDraw

    meta, blocks = load_blocks(path)
    pdf = pdfium.PdfDocument(meta["source_pdf"])
    outs = []
    for pno in pages:
        img = pdf[pno - 1].render(scale=SCALE).to_pil().convert("RGB")
        draw = ImageDraw.Draw(img)
        for b in blocks:
            color = PALETTE[(b["seq"] - 1) % len(PALETTE)]
            for s in b["spans"]:
                if s["page"] != pno:
                    continue
                x0, top, x1, bottom = (v * SCALE for v in s["bbox"])
                draw.rectangle([x0 - 3, top - 3, x1 + 3, bottom + 3],
                               outline=color, width=3)
                draw.text((x0 + 2, max(top - 16, 2)),
                          f"set {b['set_id']}", fill=color)
        out = view_dir(path) / f"{path.stem}-p{pno}.png"
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
        path, pages = Path(sys.argv[2]), [int(p) for p in sys.argv[3:]]
        for out in overlay(path, pages):
            print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
