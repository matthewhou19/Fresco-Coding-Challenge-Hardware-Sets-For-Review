"""Render a PDF page to PNG, with a disk cache.

This is the only part of the server that touches the source PDFs. The cache
means a page costs one render for the life of the deployment, and it is what
makes the deploy story small: a bundle of cached page images plus the JSONL
output can ship without the (235 MB) specbooks -- point `FRESCO_DATA_ROOT` at
it and the same server runs with `pdf_available` false.

Bytes are deterministic for a given (pdf, page, scale), so re-running the
checks twice compares equal, same discipline as the pipeline steps.
"""
from __future__ import annotations

import atexit
import io
import threading
from pathlib import Path

import pypdfium2 as pdfium

# pypdfium2 documents are not safe to use from several threads at once, and
# FastAPI runs sync endpoints in a threadpool -- so renders are serialised.
_LOCK = threading.Lock()
_DOCS: dict[str, object] = {}


def cache_path(cache_dir: Path, sid: str, page: int, scale: float) -> Path:
    return cache_dir / sid / f"p{page:04d}@{scale:g}x.png"


def render(pdf_path: Path, page: int, out: Path, scale: float) -> bytes:
    """Return PNG bytes for a 1-based page number, writing through the cache."""
    if out.is_file():
        return out.read_bytes()

    key = str(pdf_path)
    with _LOCK:
        doc = _DOCS.get(key)
        if doc is None:
            doc = _DOCS[key] = pdfium.PdfDocument(pdf_path)
        image = doc[page - 1].render(scale=scale).to_pil().convert("RGB")

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    data = buf.getvalue()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return data


@atexit.register
def close_all() -> None:
    """Documents stay open for the process lifetime; close them on the way out."""
    with _LOCK:
        for doc in _DOCS.values():
            doc.close()
        _DOCS.clear()
