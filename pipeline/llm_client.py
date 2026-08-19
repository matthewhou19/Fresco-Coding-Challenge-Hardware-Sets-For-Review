"""Thin LLM client for step 3c: real API + disk cache + usage metering.

Design (design-notes section 5, decided 2026-08-18):
* The deliverable runs the real Anthropic API through the SDK -- never a
  `claude -p` subprocess.  This module is the single place the API is
  touched, so the engine stays swappable without being a second code path.
* Determinism comes from the cache, not the model: every request is
  fingerprinted (model + effort + max_tokens + system + user + schema) and
  the raw response is written to disk before being returned.  A re-run with
  an unchanged pipeline hits the cache for every block and reproduces the
  output byte for byte -- that is the "run twice, identical" contract, and
  it also means step3c_checks can run offline once the cache is populated.
* Responses are forced into JSON by structured outputs
  (output_config.format json_schema), not by prompt begging -- the API
  validates the shape, so there is no parse-and-retry loop here.
* Usage (input/output tokens) is recorded per call and aggregated by the
  caller for the report; the spend story stays auditable.

The API key resolves from ANTHROPIC_API_KEY, falling back to the repo-root
.env file (KEY=value lines, no dependency on python-dotenv).  A fully warm
cache needs no key at all.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class LLMUnavailable(RuntimeError):
    """Raised on a cache miss when no API key can be resolved."""


def resolve_api_key(env_file: str | Path | None = None) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    candidates = [Path(env_file)] if env_file else []
    candidates += [Path(".env"), Path(__file__).parent.parent / ".env"]
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text("utf-8").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return None


class LLMClient:
    def __init__(self, cache_dir: str | Path, model: str = "claude-opus-5",
                 effort: str = "low", max_tokens: int = 12000) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = None  # lazy: a warm cache never needs the SDK

    # -- cache ---------------------------------------------------------------

    def _fingerprint(self, system: str, user: str, schema: dict) -> str:
        req = {
            "model": self.model,
            "effort": self.effort,
            "max_tokens": self.max_tokens,
            "system": system,
            "user": user,
            "schema": schema,
        }
        blob = json.dumps(req, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def _cache_path(self, fp: str) -> Path:
        return self.cache_dir / f"{fp}.json"

    # -- call ----------------------------------------------------------------

    def complete(self, system: str, user: str, schema: dict) -> dict:
        """Return {"data", "cache_hit", "usage", "stop_reason", "fingerprint"}."""
        fp = self._fingerprint(system, user, schema)
        path = self._cache_path(fp)
        if path.is_file():
            rec = json.loads(path.read_text("utf-8"))
            return {"data": json.loads(rec["response_text"]),
                    "cache_hit": True, "usage": rec["usage"],
                    "stop_reason": rec["stop_reason"], "fingerprint": fp}

        if self._client is None:
            key = resolve_api_key()
            if not key:
                raise LLMUnavailable(
                    "cache miss and no ANTHROPIC_API_KEY (env or repo .env)")
            import anthropic  # deferred: offline runs never import it
            self._client = anthropic.Anthropic(api_key=key)

        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"model refused (fingerprint {fp})")
        if response.stop_reason == "max_tokens":
            raise RuntimeError(
                f"output truncated at {self.max_tokens} tokens "
                f"(fingerprint {fp}) -- raise --max-tokens")
        text = next(b.text for b in response.content if b.type == "text")
        json.loads(text)  # structured outputs guarantee this; assert anyway

        rec = {
            "fingerprint": fp,
            "model": self.model,
            "effort": self.effort,
            "max_tokens": self.max_tokens,
            "stop_reason": response.stop_reason,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            "response_text": text,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, sort_keys=True),
                       encoding="utf-8", newline="\n")
        os.replace(tmp, path)
        return {"data": json.loads(text), "cache_hit": False,
                "usage": rec["usage"], "stop_reason": response.stop_reason,
                "fingerprint": fp}
