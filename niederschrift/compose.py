"""Build the Niederschrift of one sitting with system B (hybrid) or C (rules only).

    .venv/bin/python -m niederschrift.compose SID --system B|C [--pdf]
    -> outputs/<sid>_<system>.json (NDS-JSON), .html, (.pdf)

Steps (see docs/ARCHITECTURE.md):
  1. sources.load_sitting      the three public sources, agenda markup removed
  2. structure.build_structure segmentation into agenda items, plan order, votes, decisions   (rules)
  3. debate.digest_item        per speech: the excerpt in the speaker's own words (about half)   (rules)
  4. Kurzfassung               B: one LLM call per item (reported_llm), checked; C: kurz.rule
  5. render                    speaker line, Kurzfassung, the speaker's words; HTML -> PDF
"""
import argparse
import json
import time
from pathlib import Path

from . import debate, kurz, reported_llm, render, structure, topic
from .sources import load_sitting

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
KURZ_WORDS = 180      # the LLM sees the best-scored excerpt sentences up to this many words ...
REPORT_MAX = 4        # ... and at most this many off-topic sentences of the speech
GAP = " […] "


def body_of(d, s):
    """The speaker's own words as ONE text in spoken order (wortnah style: the speaker line, then the
    contribution without quotation marks or reporting verb). Omitted sentences are marked […]; the speaker's own
    paragraph breaks (the <p> of the XML) are kept as line breaks."""
    out, last = "", None
    for q in (p for p in d["parts"] if p["type"] == "quote"):
        if out:
            new_par = s["sentences"][q["sent"][0]]["unit"] != s["sentences"][last]["unit"]
            gap = q["sent"][0] != last + 1
            if new_par:
                out += "\n" + ("[…] " if gap else "")
            else:
                out += GAP if gap else " "
        out += q["text"]
        last = q["sent"][-1]
    return out


def kurz_input(d, s):
    """Sentences for the Kurzfassung: the best-scored excerpt sentences (topic + information), plus the most
    informative off-topic ones, in spoken order."""
    by_k = {x["k"]: x["text"] for x in s["sentences"]}
    score = {x["k"]: x["topic"] + x["info"] for x in d.get("scores", [])}
    info = {x["k"]: x["info"] for x in d.get("scores", [])}
    take, words = [], 0
    for k in sorted((k for p in d["parts"] if p["type"] == "quote" for k in p["sent"]), key=lambda k: -score.get(k, 0)):
        w = len(by_k[k].split())
        if take and words + w > KURZ_WORDS:
            continue
        take.append(k)
        words += w
    rep = sorted(d.get("report_sents", []), key=lambda k: -info.get(k, 0))[:REPORT_MAX]
    return [by_k[k] for k in sorted(set(take) | set(rep)) if k in by_k]


def nds_speech(d, s, kf):
    """One Digest -> NDS speech: [Kurzfassung] + the excerpt in the speaker's words."""
    parts = []
    if kf:
        parts.append({"type": "summary", "text": kf["text"], "verb": kf["text"].split()[0], "source": kf["source"],
                      "display": kf["text"]})
    body = body_of(d, s)
    if body:
        parts.append({"type": "quote", "text": body, "verb": None, "source": "rule", "display": body,
                      "sent": [k for p in d["parts"] if p["type"] == "quote" for k in p["sent"]]})
    return {"speaker": d["speaker"], "group": d.get("group"), "role": d.get("role"), "kind": d.get("kind"),
            "gender": d.get("gender"), "rede": d.get("rede"), "units": s["units"],
            "words_source": d.get("words_source"), "parts": parts}


def compose(sid, system, tag_prefix=None):
    t_wall = time.time()
    t_cpu = time.process_time()
    sitting = load_sitting(sid)
    st = structure.build_structure(sitting)
    nds = structure.nds_skeleton(st, sitting)
    nds["system"] = system
    nds["style"] = "v3 wortnah + Kurzfassung"
    llm_recs, checks = [], []
    nlp = topic.nlp()
    for item in nds["items"]:
        it = st["items"][item["id"]]
        if it.get("debate_owner") not in (None, item["id"]) or not it.get("units"):
            item.setdefault("speeches", [])
            continue
        speeches = debate.group_speeches(sitting["units"], it["units"])
        if not speeches:
            item.setdefault("speeches", [])
            continue
        digests = debate.digest_item(item["id"], it["topic_text"], speeches, sitting["units"], kind=it.get("kind"),
                                     title=item.get("title"))
        for d, s in zip(digests, speeches):
            d["kurz_sents"] = kurz_input(d, s)
        kurz_of = {}
        if system == "B":
            replies, rec = reported_llm.kurz_for_item(item["id"], item.get("title"), digests,
                                                      tag=f"{tag_prefix or sid}:B3:{item['id']}", nlp=nlp)
            if rec:
                llm_recs.append(rec)
            for k, r in replies.items():
                checks.append({"item": item["id"], "speech": k, "ok": r["ok"], "problems": r["problems"]})
                if r["ok"]:
                    kurz_of[k] = {"text": r["text"], "source": "llm"}
        used = {}
        for k, (d, s) in enumerate(zip(digests, speeches)):
            if k not in kurz_of and d["kurz_sents"]:
                t = kurz.rule(d, s, nlp, used)
                if t:
                    kurz_of[k] = {"text": t, "source": "rule" if system == "C" else "rule-fallback"}
        item["speeches"] = [nds_speech(d, s, kurz_of.get(k)) for k, (d, s) in enumerate(zip(digests, speeches))]
    fresh = [r for r in llm_recs if not r.get("cached")]
    nds["compute"] = {
        "rule_seconds": round(time.process_time() - t_cpu, 2),
        "wall_seconds": round(time.time() - t_wall, 1),
        "llm_calls": len(llm_recs), "llm_calls_cached": len(llm_recs) - len(fresh),
        "input_tokens": sum(r["input_tokens"] for r in llm_recs),
        "output_tokens": sum(r["output_tokens"] for r in llm_recs),
        "cost_usd": round(sum(r.get("cost_usd") or 0 for r in llm_recs), 4),
        "llm_seconds": round(sum(r["seconds"] for r in llm_recs), 1)}
    nds["kurz_checks"] = checks
    return nds, st, sitting


def write(nds, st, sitting, pdf=False, out=OUT):
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"{nds['sid']}_{nds['system']}"
    stem.with_suffix(".json").write_text(json.dumps(nds, ensure_ascii=False, indent=1), encoding="utf-8")
    html = render.render_html(nds, st, sitting["meta"])
    stem.with_suffix(".html").write_text(html, encoding="utf-8")
    if pdf:
        render.to_pdf(stem.with_suffix(".html"), stem.with_suffix(".pdf"))
    return stem


def main():
    a = argparse.ArgumentParser()
    a.add_argument("sid")
    a.add_argument("--system", choices=["B", "C"], required=True)
    a.add_argument("--pdf", action="store_true")
    x = a.parse_args()
    nds, st, sitting = compose(x.sid, x.system)
    stem = write(nds, st, sitting, pdf=x.pdf)
    c = nds["compute"]
    print(f"{x.sid} {x.system}: {len(nds['items'])} items, rules {c['rule_seconds']} s CPU, "
          f"LLM {c['llm_calls']} calls ({c['llm_calls_cached']} cached), {c['input_tokens']} in / "
          f"{c['output_tokens']} out tokens, ${c['cost_usd']} -> {stem}.html")


if __name__ == "__main__":
    main()
