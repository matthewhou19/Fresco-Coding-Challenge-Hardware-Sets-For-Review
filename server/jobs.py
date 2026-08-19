"""Upload channel: run the whole funnel on a PDF the user drops in.

One upload = one job = its own data root under `data/out/uploads/<job_id>/`,
so a job can never write into the canonical `data/out/step*` tree (which the
pipeline's byte-for-byte re-run discipline depends on).

The six stages are the exact commands documented in the README, run as
subprocesses with `cwd` = the data root. That means the upload path exercises
the same code path a reviewer would run by hand, and the API key resolves the
same way (ANTHROPIC_API_KEY, else the repo-root .env).

Jobs run one at a time on a single worker thread -- a queue, not a pool: the
LLM stage already fans out 8 ways inside itself.

Honest failure is a first-class outcome: if stage 1 finds no hardware-set
region (the Woodridge case), the job stops there and reports the alarm the
pipeline raised instead of handing back an empty viewer.
"""
from __future__ import annotations

import json
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

UPLOADS = Path("data/out/uploads")
LOG_TAIL = 40  # lines kept in memory for the UI; the full log is on disk

STAGES = [
    ("locate", "① locate the hardware-set region"),
    ("roles", "①.5 label line roles (strip page furniture)"),
    ("chunk", "② cut sets into blocks (location)"),
    ("dossier", "③a book dossier (legend + column schema)"),
    ("rules", "③b rule side (qty / mfr / finish, stitching)"),
    ("assemble", "③c LLM assembly (reads every block)"),
]


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")[:48] or "upload"


@dataclass
class Job:
    id: str
    filename: str
    project: str          # project directory name inside the job (unique)
    dir: Path
    created: float
    status: str = "queued"   # queued | running | done | no_sets | error
    stage: str | None = None
    stages: list = field(default_factory=list)
    log: list = field(default_factory=list)
    result: dict = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "project": self.project,
            "created": self.created,
            "status": self.status,
            "stage": self.stage,
            "stages": self.stages,
            "log": self.log[-LOG_TAIL:],
            "result": self.result,
            "error": self.error,
        }

    def save(self) -> None:
        (self.dir / "job.json").write_text(
            json.dumps({**self.to_dict(), "dir": str(self.dir)}, indent=1), "utf-8")


class JobRunner:
    """Queues uploads and runs the pipeline on them, one at a time."""

    def __init__(self, root: Path, pipeline_dir: Path, on_done=None) -> None:
        self.root = Path(root)
        self.pipeline = Path(pipeline_dir)
        self.on_done = on_done
        self.jobs: dict[str, Job] = {}
        self._q: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._run_loop, daemon=True)
        self._worker.start()
        self._load_previous()

    # -- public ------------------------------------------------------------

    def submit(self, pdf_bytes_path: Path, filename: str) -> Job:
        """Take a PDF already streamed to disk and queue the pipeline on it."""
        job_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        stem = slug(Path(filename).stem)
        project = f"{stem}__{job_id[-6:]}"       # unique across the catalog
        job_dir = self.root / UPLOADS / job_id
        (job_dir / "pdfs" / project).mkdir(parents=True, exist_ok=True)
        target = job_dir / "pdfs" / project / f"{stem}.pdf"
        pdf_bytes_path.replace(target)

        job = Job(id=job_id, filename=filename, project=project, dir=job_dir,
                  created=time.time(),
                  stages=[{"key": k, "title": t, "state": "waiting", "seconds": None}
                          for k, t in STAGES])
        job.save()
        self.jobs[job_id] = job
        self._q.put(job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def delete(self, job_id: str) -> Job:
        """Throw one upload away -- its PDF, its output, its LLM cache.

        A job owns its whole sub-tree, so re-uploading the same PDF afterwards
        is a genuinely cold run with nothing left to hit in cache. That is the
        point of being able to delete one: a live re-run, not a replay.
        """
        job = self.jobs[job_id]
        if job.status in ("queued", "running"):
            raise RuntimeError(f"job {job_id} is still {job.status}")
        shutil.rmtree(job.dir, ignore_errors=True)
        return self.jobs.pop(job_id)

    def list(self) -> list[dict]:
        return [j.to_dict() for j in
                sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)]

    # -- worker ------------------------------------------------------------

    def _run_loop(self) -> None:
        while True:
            job = self.jobs.get(self._q.get())
            if job is None:
                continue
            try:
                self._run(job)
            except Exception as exc:                      # never kill the worker
                job.status, job.error = "error", f"{type(exc).__name__}: {exc}"
            job.stage = None
            job.save()
            if self.on_done:
                self.on_done(job)

    def _run(self, job: Job) -> None:
        job.status = "running"
        for i, (key, title) in enumerate(STAGES):
            job.stage = key
            job.stages[i]["state"] = "running"
            job.save()
            started = time.time()
            code = self._stage(job, key)
            job.stages[i]["seconds"] = round(time.time() - started, 1)

            if code != 0:
                job.stages[i]["state"] = "failed"
                job.status = "error"
                job.error = f"{key} exited {code} -- see the log"
                return
            job.stages[i]["state"] = "done"

            if key == "locate" and not self._streams_found(job):
                for rest in job.stages[i + 1:]:
                    rest["state"] = "skipped"
                job.status = "no_sets"
                return

        job.status = "done"
        job.result.update(self._summarise(job))

    def _stage(self, job: Job, key: str) -> int:
        j = job.dir
        argv = {
            "locate": [self.pipeline / "step1_locate.py", j / "pdfs" / job.project,
                       "--out", j / "step1"],
            "roles": [self.pipeline / "step1p5_roles.py", j / "step1",
                      "--out", j / "step1p5"],
            "chunk": [self.pipeline / "step2_chunk.py", j / "step1p5",
                      "--out", j / "step2"],
            "dossier": [self.pipeline / "step3_dossier.py", j / "step2",
                        "--out", j / "step3"],
            "rules": [self.pipeline / "step3_rules.py", j / "step2",
                      "--dossiers", j / "step3", "--out", j / "step3"],
            "assemble": [self.pipeline / "step3c_assemble.py", j / "step3",
                         "--blocks", j / "step2", "--out", j / "step3"],
        }[key]
        cmd = [sys.executable] + [str(a) for a in argv]

        self._log(job, f"$ {' '.join(Path(c).name if i else 'python' for i, c in enumerate(cmd))}")
        proc = subprocess.Popen(cmd, cwd=self.root, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", bufsize=1)
        for line in proc.stdout:
            self._log(job, line.rstrip())
        return proc.wait()

    def _log(self, job: Job, line: str) -> None:
        job.log.append(line)
        with (job.dir / "log.txt").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # -- reading the pipeline's own reports --------------------------------

    def _streams_found(self, job: Job) -> bool:
        report = job.dir / "step1" / job.project / "region_report.json"
        if not report.is_file():
            job.result["locate"] = {"streams": 0, "alarm": "step 1 wrote no report"}
            return False
        d = json.loads(report.read_text("utf-8"))
        job.result["locate"] = {
            "streams": len(d.get("streams", [])),
            "alarm": d.get("alarm"),
            "files": [{"file": f["file"], "pages": f["pages"], "verdict": f["verdict"],
                       "image_only_pages": f.get("image_only_pages", 0),
                       "warning": f.get("warning"),
                       "rejected_regions": len(f.get("rejected_regions") or [])}
                      for f in d.get("files", [])],
        }
        return bool(d.get("streams"))

    def _summarise(self, job: Job) -> dict:
        report = job.dir / "step3" / job.project / "assembly_report.json"
        if not report.is_file():
            return {}
        d = json.loads(report.read_text("utf-8"))
        streams = d.get("streams", [])
        return {
            "streams": [{"id": f"{job.project}/{s['stream']}", "file": s["file"],
                         "n_sets": s["n_blocks"], "n_components": s["n_components"],
                         "n_demoted": s["n_demoted"],
                         "confidence": s.get("assembly_confidence", {}),
                         "llm_calls": s.get("llm_calls", 0),
                         "usage": s.get("usage", {})} for s in streams],
            "n_sets": sum(s["n_blocks"] for s in streams),
            "n_components": sum(s["n_components"] for s in streams),
            "input_tokens": sum(s.get("usage", {}).get("input_tokens", 0) for s in streams),
            "output_tokens": sum(s.get("usage", {}).get("output_tokens", 0) for s in streams),
        }

    def _load_previous(self) -> None:
        """Past jobs survive a restart -- their results are on disk anyway."""
        for path in sorted((self.root / UPLOADS).glob("*/job.json")):
            try:
                d = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            job = Job(id=d["id"], filename=d["filename"], project=d["project"],
                      dir=path.parent, created=d["created"],
                      status=d["status"] if d["status"] not in ("queued", "running")
                      else "error",
                      stages=d.get("stages", []), log=d.get("log", []),
                      result=d.get("result", {}),
                      error=d.get("error") or ("interrupted by a restart"
                                               if d["status"] in ("queued", "running")
                                               else None))
            self.jobs[job.id] = job
