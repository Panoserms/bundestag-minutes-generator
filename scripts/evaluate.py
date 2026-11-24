"""Score Niederschriften (NDS-JSON) of systems A/B/C on one sitting.

    .venv/bin/python scripts/evaluate.py 21032 [21030 21085]   -> results/eval_<sid>.json, printed table

Metrics (all checkable against the sources; no judgement calls):
  quotes      every quoted segment (split at "[…]") must be an exact, whitespace-normalised substring of the
              attributed speaker's own words ("exact"); else of any speaker ("misattributed"); else the best
              word-level difflib ratio decides "near" (>= 0.9) or "not found". Segments under 4 words are skipped.
  fragments   quotes that start in lower case or end in a comma: a sentence split error, not a misquote.
  placement   the unit a quote comes from lies in an XML agenda block that covers the output item (gold markup,
              used only here) or one of its joint partners.
  order       printed agenda items that appear in the output: pairs in the wrong order (Kendall discordant pairs).
  length      words of debate text / words of the speeches; share of quoted words.
  monotony    the verb that opens each speech: the first word of the Kurzfassung (v3: "fordert", "kritisiert"),
              else the first word after the speaker label ("erklärte", "führte"): share of the most frequent one,
              and the number of different ones.
  gaps        omission marks […] per speech in the excerpt (v3: fewer = longer coherent passages).
  compute     LLM calls, tokens, cost, seconds (from the NDS compute block; A from its run record).
"""
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from niederschrift.sources import load_sitting  # noqa: E402

OUT = ROOT / "outputs"              # new runs
SCORED = ROOT / "results" / "nds"   # the runs behind docs/RESULTS.md


def norm(t):
    t = t.replace("„", '"').replace("“", '"').replace("”", '"').replace("–", "-").replace("’", "'")
    return " ".join(t.split())


def intro_word(paragraph, speaker):
    """First word after the speaker label: 'Claudia Roth (GRÜNE) forderte: …' -> 'forderte'."""
    t = paragraph
    k = t.find(speaker)
    if k == -1 or k > 40:
        return None
    t = t[k + len(speaker):].lstrip()
    t = re.sub(r"^\([^)]*\)\s*", "", t)
    t = re.sub(r"^,[^,]*,\s*", "", t)                     # ", Bundesministerin für …,"
    m = re.match(r"([a-zäöüß]+)", t)
    return m.group(1) if m else None


def score(nds, sitting):
    units = sitting["units"]
    by_speaker = {}
    for u in units:
        by_speaker.setdefault(u["speaker"], []).append(u)
    all_text = norm(" ".join(u["text"] for u in units))
    blocks = sitting["gold"]["blocks"]
    unit_block = sitting["gold"]["unit_block"]
    q = Counter()
    frag = placed = placed_n = 0
    words = quoted = src_words = 0
    intros = []
    n_kurz = gaps = 0
    for it in nds["items"]:
        for sp in it.get("speeches", []):
            src_words += sp.get("words_source") or 0
            para = " ".join(p.get("display") or p["text"] for p in sp["parts"])
            words += len(para.split())
            summ = next((p for p in sp["parts"] if p["type"] == "summary"), None)
            w = summ["text"].split()[0].strip(",") if summ else intro_word(para, sp["speaker"])
            if w:
                intros.append(w)
            n_kurz += summ is not None
            gaps += sum(p["text"].count("[…]") for p in sp["parts"] if p["type"] == "quote")
            own = by_speaker.get(sp["speaker"], [])
            own_text = norm(" ".join(u["text"] for u in own))
            for p in sp["parts"]:
                if p["type"] != "quote":
                    continue
                quoted += len(p["text"].split())
                if re.match(r"^[a-zäöüß]", p["text"]) or p["text"].rstrip().endswith(","):
                    frag += 1
                for seg in re.split(r"\s*\[…\]\s*", p["text"]):
                    s = norm(seg).strip(" .")
                    if len(s.split()) < 4:
                        continue
                    if s in own_text:
                        q["exact"] += 1
                        src = next((u for u in own if s[:60] in norm(u["text"])), None)
                        if src is not None:
                            b = unit_block.get(src["i"])
                            items = set(blocks[b]["items"]) if b is not None else set()
                            ok = it["id"] in items or any(n.split()[-1] in it["id"] for n in items if False)
                            ok = ok or any(x in items for x in re.findall(r"(?:TOP|ZP) \d+", " ".join(it.get("notes", []))))
                            placed += ok
                            placed_n += 1
                    elif s in all_text:
                        q["misattributed"] += 1
                    else:
                        sw = s.split()
                        best = 0.0
                        for u in own or units:
                            uw = norm(u["text"]).split()
                            if not uw:
                                continue
                            m = difflib.SequenceMatcher(None, sw, uw, autojunk=False)
                            best = max(best, sum(b.size for b in m.get_matching_blocks()) / len(sw))
                        q["near" if best >= 0.9 else "not found"] += 1
    # order: printed items present in the output must keep printed order
    printed = [a["id"] for a in sitting["agenda"] if not a["id"].startswith("TO-")]
    printed = list(dict.fromkeys(printed))
    out_order = [it["id"] for it in nds["items"]]
    pos = [out_order.index(x) for x in printed if x in out_order]
    discordant = sum(1 for i in range(len(pos)) for j in range(i + 1, len(pos)) if pos[i] > pos[j])
    ic = Counter(intros)
    top = ic.most_common(1)[0] if ic else (None, 0)
    nq = sum(q.values())
    return {
        "items": len(nds["items"]), "speeches": sum(len(it.get("speeches", [])) for it in nds["items"]),
        "quote_segments": nq, "quotes_exact": q["exact"], "quotes_misattributed": q["misattributed"],
        "quotes_near": q["near"], "quotes_not_found": q["not found"],
        "exact_rate": round(q["exact"] / nq, 3) if nq else None, "fragment_quotes": frag,
        "quote_placement_ok": f"{placed}/{placed_n}",
        "printed_items_in_output": len(pos), "order_discordant_pairs": discordant,
        "debate_words": words, "speech_words": src_words,
        "length_ratio": round(words / src_words, 3) if src_words else None,
        "quoted_share": round(quoted / words, 3) if words else None,
        "top_intro": top[0], "top_intro_share": round(top[1] / len(intros), 3) if intros else None,
        "distinct_intros": len(ic), "intros": dict(ic.most_common(8)),
        "speeches_with_kurzfassung": n_kurz, "gaps_per_speech": round(gaps / max(1, sum(len(it.get("speeches", [])) for it in nds["items"])), 2),
        "compute": nds.get("compute", {}),
    }


def main():
    for sid in sys.argv[1:] or ["21032"]:
        sitting = load_sitting(sid)
        res = {}
        for system in "ABC":
            f = next((d / f"{sid}_{system}.json" for d in (OUT, SCORED) if (d / f"{sid}_{system}.json").exists()), None)
            if f:
                res[system] = score(json.loads(f.read_text()), sitting)
        (ROOT / "results").mkdir(exist_ok=True)
        (ROOT / "results" / f"eval_{sid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
        print(f"== {sid}")
        for k, v in res.items():
            print(k, json.dumps({x: y for x, y in v.items() if x not in ("intros",)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
