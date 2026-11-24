"""One place for all LLM calls, through a command-line client set in the environment:

    NIEDERSCHRIFT_LLM_CMD     the command, e.g. "my-llm --system {system}" ({system}, {model} and {effort} are filled
                              in after splitting). The prompt goes to standard input; the reply is JSON with "result"
                              and, if known, "usage" (input_tokens, output_tokens), "modelUsage" and "total_cost_usd".
    NIEDERSCHRIFT_LLM_MODEL   the model name passed on as {model} (default: "default")

Every call is logged with its token usage, wall time and the CLI's API-equivalent cost in results/llm_log.jsonl,
so the efficiency comparison uses measured numbers, not estimates. The call runs in an empty temporary folder with
a fixed system prompt, and the client is run without tools or settings, so nothing but the prompt reaches the model.

Replies are cached by a hash of (model, effort, system, prompt) in work/llm_cache/, so re-rendering a Niederschrift
never pays for the same call twice. A cached reply is logged with cached=true and is not counted as cost.
"""
import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "results" / "llm_log.jsonl"
CACHE = ROOT / "work" / "llm_cache"
MODEL = os.environ.get("NIEDERSCHRIFT_LLM_MODEL", "default")


def call(prompt, system, tag, model=MODEL, effort=None, timeout=3600, use_cache=True):
    """-> (reply text, log record)."""
    key = hashlib.sha256(json.dumps([model, effort, system, prompt]).encode()).hexdigest()[:24]
    cached = CACHE / f"{key}.json"
    if use_cache and cached.exists():
        d = json.loads(cached.read_text(encoding="utf-8"))
        rec = dict(d["rec"], tag=tag, cached=True)
        _log(rec)
        return d["result"], rec
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run(
            _command(model, system, effort),
            input=prompt, capture_output=True, text=True, cwd=tmp, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"LLM command failed ({p.returncode}): {p.stderr[:500]} {p.stdout[:500]}")
    d = json.loads(p.stdout)
    u = d.get("usage", {})
    rec = {"tag": tag, "model": list((d.get("modelUsage") or {}).keys()), "effort": effort or "default",
           "seconds": round(time.time() - t0, 1),
           "input_tokens": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
           + u.get("cache_read_input_tokens", 0),
           "output_tokens": u.get("output_tokens", 0), "cost_usd": d.get("total_cost_usd"),
           "prompt_chars": len(prompt) + len(system), "cached": False}
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps({"rec": rec, "result": d.get("result", "")}, ensure_ascii=False), encoding="utf-8")
    _log(rec)
    return d.get("result", ""), rec


def _command(model, system, effort):
    """The client call as an argument list. Values are filled in after splitting, so they need no quoting."""
    template = os.environ.get("NIEDERSCHRIFT_LLM_CMD")
    if not template:
        raise RuntimeError("no cached reply, and NIEDERSCHRIFT_LLM_CMD is not set (see niederschrift/llm.py)")
    values = {"{model}": model, "{system}": system, "{effort}": effort or "default"}
    return [values.get(part, part) for part in shlex.split(template)]


def _log(rec):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def usage(prefix):
    """Sum of the non-cached calls whose tag starts with `prefix`."""
    tot = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "seconds": 0.0}
    if not LOG.exists():
        return tot
    for line in LOG.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["tag"].startswith(prefix) and not r.get("cached"):
            tot["calls"] += 1
            for k in ("input_tokens", "output_tokens", "seconds"):
                tot[k] += r.get(k) or 0
            tot["cost_usd"] += r.get("cost_usd") or 0
    return tot
