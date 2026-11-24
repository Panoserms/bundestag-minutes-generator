"""System A: the whole Niederschrift written by the LLM in ONE call per sitting.

    .venv/bin/python -m baseline.llm_only SID [--dry-run] [--no-parse]
        -> outputs/<SID>_A.md, outputs/<SID>_A.json (parsed NDS-JSON), work/eval/runs/<SID>_A.json (run record)

Inputs given to the model,
the same information B and C get:
  - the transcript in spoken order WITHOUT agenda markup, "Speaker (group): text", chair turns included, and the
    reactions printed after each paragraph ("(Beifall bei der SPD)") in brackets, as in the printed record;
  - the printed Tagesordnung of the day, numbers already mapped to the final numbering (sitting["agenda"]);
    items without a final partner keep their printed number with the prefix "TO-", explained in the prompt;
  - the Amtliches Protokoll text;
  - docs/style_guide_scored.md as the rules of the system prompt.
Default effort, as in the earlier experiment. One call, tag "<SID>:A".

This file is the DEVELOPMENT version. The version used on the test sittings is the frozen copy
baseline/llm_only_frozen.py (frozen before the test run).
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from niederschrift import llm  # noqa: E402
from niederschrift.sources import load_sitting  # noqa: E402

OUT = ROOT / "outputs"
RUNS = ROOT / "work" / "eval" / "runs"
STYLE = (ROOT / "docs" / "style_guide_scored.md").read_text(encoding="utf-8")   # frozen: the rules A was scored with
PROMPT_VERSION = "A-frozen-2026-10-23"
SYSTEM = "Du bist Schriftführer und schreibst die Niederschrift einer Parlamentssitzung.\n\n" + STYLE


def transcript_text(units):
    """Spoken order, no agenda markup. A new speaker turn starts a new paragraph with "Speaker (group): ";
    the reactions printed after a paragraph follow on their own line, in brackets."""
    lines, prev = [], None
    for u in units:
        who = u["speaker"] + (f" ({u['group']})" if u["group"] else "")
        if who != prev:
            if lines:
                lines.append("")
            lines.append(f"{who}: {u['text']}")
        else:
            lines.append(u["text"])
        for r in u.get("reactions") or []:
            r = r.strip()
            lines.append(r if r.startswith("(") else f"({r})")
        prev = who
    return "\n".join(lines).strip()


def agenda_text(agenda):
    """Printed Tagesordnung in printed order, numbers mapped to the final numbering ("TO-" = no final partner)."""
    out, seen = [], set()
    for it in agenda:
        line = f"{it['id']}: {it['title']}" + (f" (Drucksachen {', '.join(it['drucksachen'])})"
                                               if it.get("drucksachen") else "")
        if line not in seen:                      # the same printed entry mapped twice is listed once
            seen.add(line)
            out.append(line)
    return "\n".join(out)


def build_prompt(sitting):
    """-> (prompt, system). Only the inputs listed in the module docstring reach the model."""
    prompt = ("Erstelle die Niederschrift der Sitzung nach den Stilvorgaben.\n"
              "Format: Markdown, eine Überschrift „### TOP N – Titel“ bzw. „### ZP N – Titel“ je Punkt. "
              "Jeder Redebeitrag (auch Zwischenfragen, Kurzinterventionen und Antworten) steht in einem eigenen "
              "Absatz, der mit dem Redner beginnt: „Vorname Nachname (Fraktion)“ bzw. „Vorname Nachname "
              "(Funktion)“.\n"
              "Die Nummern der Punkte stehen in der Tagesordnung unten und im Amtlichen Protokoll; das Transkript "
              "enthält keine Gliederung. Punkte mit der Vorsilbe „TO-“ stehen in der gedruckten Tagesordnung, wurden "
              "aber in dieser Sitzung nicht unter dieser Nummer behandelt.\n"
              "Im Transkript stehen Beifall, Zurufe und andere Reaktionen in Klammern nach dem Absatz, wie im "
              "gedruckten Plenarprotokoll.\n\n"
              f"=== TAGESORDNUNG (gedruckte Reihenfolge) ===\n{agenda_text(sitting['agenda'])}\n\n"
              f"=== AMTLICHES PROTOKOLL ===\n{sitting['amtprot_text']}\n\n"
              f"=== TRANSKRIPT (Reihenfolge der Behandlung) ===\n{transcript_text(sitting['units'])}")
    return prompt, SYSTEM


OUTPUT_CAPS = (8192, 16384, 32000, 64000, 128000)     # known CLI / API output-token ceilings


def truncation_check(text, rec):
    """Heuristic: is the reply cut off? It looks at the reply only (no comparison with the sources), so it can be
    run on test outputs without looking at their content.
      cli_error   the CLI put an error message ('exceeded ... output token maximum', 'API Error') into the result
      empty       no text
      mid_sentence the last non-blank character is not a sentence end, closing quote or bracket
      near_cap    output tokens within 2 % of a known output ceiling (output tokens include thinking tokens)
      no_heading  no '### TOP/ZP' heading at all"""
    reasons = []
    t = (text or "").rstrip()
    if not t:
        reasons.append("empty")
    if re.search(r"(exceeded[^\n]{0,80}output token|API Error|max_tokens|Prompt is too long)", t[-600:], re.I):
        reasons.append("cli_error")
    if t and t[-1] not in ".!?“\"”»«)]:*_-–…":
        reasons.append("mid_sentence")
    out_tok = (rec or {}).get("output_tokens") or 0
    for cap in OUTPUT_CAPS:
        if abs(out_tok - cap) <= 0.02 * cap:
            reasons.append(f"near_cap_{cap}")
    if t and not re.search(r"^#{1,6}\s*(?:\*\*)?\s*(TOP|ZP)\s*\d+", t, re.M):
        reasons.append("no_heading")
    return {"truncated": bool(set(reasons) & {"empty", "cli_error", "mid_sentence"}) or
            any(r.startswith("near_cap") for r in reasons), "reasons": reasons}


def run(sid, parse=True, dry=False):
    t0 = time.time()
    sitting = load_sitting(sid)
    prompt, system = build_prompt(sitting)
    rule_seconds = round(time.time() - t0, 3)
    info = {"sid": sid, "prompt_version": PROMPT_VERSION, "script": Path(__file__).name,
            "prompt_chars": len(prompt), "system_chars": len(system),
            "prompt_sha": hashlib.sha256((system + "\n\n" + prompt).encode()).hexdigest()[:16],
            "transcript_units": len(sitting["units"]), "rule_seconds": rule_seconds}
    if dry:
        print(json.dumps(info, ensure_ascii=False))
        return info
    # The client inherits our environment. A *_EFFORT variable set by the calling shell (e.g. 'xhigh') would
    # silently change the 'default effort' condition of system A.
    for name in [n for n in os.environ if n.endswith("_EFFORT")]:
        os.environ.pop(name)
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    text, rec = llm.call(prompt, system, tag=f"{sid}:A")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{sid}_A.md").write_text(text, encoding="utf-8")
    trunc = truncation_check(text, rec)
    info.update({"started": started, "llm": rec, "truncation": trunc, "reply_chars": len(text)})
    if parse:
        from baseline.parse_md import parse_markdown
        compute = {"rule_seconds": rule_seconds, "llm_calls": 1, "input_tokens": rec.get("input_tokens"),
                   "output_tokens": rec.get("output_tokens"), "cost_usd": rec.get("cost_usd"),
                   "llm_seconds": rec.get("seconds")}
        try:
            nds = parse_markdown(text, sid, compute=compute)
            nds["truncation"] = trunc
            (OUT / f"{sid}_A.json").write_text(json.dumps(nds, ensure_ascii=False, indent=1), encoding="utf-8")
            info["parse"] = {"ok": True, **nds["parse"]["counts"]} if sitting["meta"]["split"] != "test" \
                else {"ok": True}
        except Exception as e:                        # report, do not hide
            info["parse"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{sid}_A.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: info[k] for k in ("sid", "prompt_version", "prompt_chars", "llm", "truncation", "parse")
                      if k in info}, ensure_ascii=False))
    return info


def main():
    a = argparse.ArgumentParser()
    a.add_argument("sid")
    a.add_argument("--dry-run", action="store_true", help="build the prompt and print its size only (no call)")
    a.add_argument("--no-parse", action="store_true")
    x = a.parse_args()
    run(x.sid, parse=not x.no_parse, dry=x.dry_run)


if __name__ == "__main__":
    main()
