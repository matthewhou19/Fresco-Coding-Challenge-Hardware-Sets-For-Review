"""Viewer + JSON API over the extracted hardware sets.

    python server/app.py                      # http://127.0.0.1:8000
    uvicorn app:app --app-dir server --reload # same thing, with reload

Reads only what the pipeline already wrote (see catalog.py), so it needs no
API key and no network. Deployment is the same command with `--host 0.0.0.0
--port $PORT`; `FRESCO_DATA_ROOT` points it at the data.

Routes
    GET  /                                        viewer (static, no build step)
    GET  /api/health                              root, counts, render scale
    GET  /api/streams                             one row per extracted region
    GET  /api/streams/{project}/{stream}          sets + components + boxes
    GET  /api/streams/{project}/{stream}/export.json    deliverable JSON
    GET  /api/streams/{project}/{stream}/pages/{page}.png   rendered page
    POST /api/uploads                             new PDF -> run the whole funnel
    GET  /api/jobs, /api/jobs/{job_id}            upload progress
    DELETE /api/jobs/{job_id}                     throw an upload away (cold re-run)
"""
from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import catalog
import jobs
import pageimg

STATIC = Path(__file__).resolve().parent / "static"
PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
MAX_UPLOAD = 400 * 1024 * 1024  # biggest specbook in the corpus is 57 MB

CATALOG = catalog.Catalog()
CACHE_DIR = Path(os.environ.get("FRESCO_PAGE_CACHE")
                 or CATALOG.root / "data" / "out" / "server_cache")
RUNNER = jobs.JobRunner(CATALOG.root, PIPELINE, on_done=lambda job: CATALOG.refresh())

app = FastAPI(
    title="Hardware set extractor",
    description="Division 08 specbooks -> hardware sets, with page locations.",
    version="0.1",
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


def _stream(project: str, stream: str) -> str:
    sid = f"{project}/{stream}"
    if sid not in CATALOG.streams:
        raise HTTPException(404, f"unknown stream: {sid}")
    return sid


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, **CATALOG.stats(), "page_cache": str(CACHE_DIR)}


@app.get("/api/streams")
def streams() -> list[dict]:
    """Uploads first (newest on top), then the corpus grouped by project."""
    rows = [s.summary() for s in CATALOG.streams.values()]
    uploaded = sorted((r for r in rows if r["source"] == "upload"),
                      key=lambda r: r["job_id"], reverse=True)
    corpus = sorted((r for r in rows if r["source"] != "upload"),
                    key=lambda r: (r["project"], bool(r["duplicate_of"]), r["file"]))
    return uploaded + corpus


@app.get("/api/streams/{project}/{stream}")
def stream_detail(project: str, stream: str) -> dict:
    return CATALOG.payload(_stream(project, stream))


@app.get("/api/streams/{project}/{stream}/export.json")
def stream_export(project: str, stream: str) -> Response:
    sid = _stream(project, stream)
    return JSONResponse(
        CATALOG.export(sid),
        headers={"Content-Disposition":
                 f'attachment; filename="{CATALOG.streams[sid].stem}.sets.json"'},
    )


@app.get("/api/streams/{project}/{stream}/pages/{page}.png")
def stream_page(project: str, stream: str, page: int) -> Response:
    sid = _stream(project, stream)
    if page not in CATALOG.page_numbers(sid):
        raise HTTPException(404, f"page {page} is outside the extracted region")
    pdf = CATALOG.streams[sid].pdf_path
    if not pdf.is_file():
        raise HTTPException(404, f"source PDF not available here: {pdf.name}")
    out = pageimg.cache_path(CACHE_DIR, sid, page, catalog.RENDER_SCALE)
    data = pageimg.render(pdf, page, out, catalog.RENDER_SCALE)
    return Response(data, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/uploads", status_code=202)
async def upload(file: UploadFile = File(...)) -> dict:
    """Take a PDF and queue the whole funnel on it (steps 1 through 3c)."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "only .pdf files are accepted")

    incoming = CATALOG.root / jobs.UPLOADS / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    tmp = incoming / f"{secrets.token_hex(8)}.pdf"
    size = 0
    try:  # the file must be closed before it can be removed (Windows)
        with tmp.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                if not size and not chunk.startswith(b"%PDF-"):
                    raise HTTPException(400, "that file is not a PDF (no %PDF- header)")
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, f"larger than {MAX_UPLOAD // 2**20} MB")
                fh.write(chunk)
        if not size:
            raise HTTPException(400, "empty file")
    except HTTPException:
        tmp.unlink(missing_ok=True)
        raise

    job = RUNNER.submit(tmp, Path(file.filename).name)
    return {"job": job.to_dict(), "size": size}


@app.get("/api/jobs")
def job_list() -> list[dict]:
    return RUNNER.list()


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    job = RUNNER.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job: {job_id}")
    return job.to_dict()


@app.delete("/api/jobs/{job_id}")
def job_delete(job_id: str) -> dict:
    """Remove an uploaded book and everything derived from it.

    Only uploads can be deleted: the corpus tree under data/out/step* is the
    pipeline's own output, and the server has no business erasing it.
    """
    if RUNNER.get(job_id) is None:
        raise HTTPException(404, f"unknown job: {job_id}")
    gone = [sid for sid, s in CATALOG.streams.items() if s.job_id == job_id]
    try:
        job = RUNNER.delete(job_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    for sid in gone:
        shutil.rmtree(CACHE_DIR / sid.split("/")[0], ignore_errors=True)
    CATALOG.refresh()
    return {"deleted": job_id, "filename": job.filename, "streams_removed": gone,
            **CATALOG.stats()}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")))
