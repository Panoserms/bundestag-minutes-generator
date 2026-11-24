"""Sentence labels (QUOTE / REPORT / DROP) and the choice of direct quotes (MMR under a word budget).

Score of a sentence = topic relatedness (topic.topic_score against the item's topic signature) + information cues:
numbers and named entities in on-topic sentences, Drucksache/§ references, deontic and stance markers, and a
following "(Beifall …)" (applause marks the rhetorical high points, Atkinson 1984). Penalties: formulas
(salutations, thanks, "Ich komme zum Schluss"), meta-discourse, replies to heckling, second-person attacks without
topic words, fragments under 5 words.
"""
import math
import re

from . import topic

FORMULA = re.compile(
    r"^(?:(?:Sehr )?(?:geehrte[rs]?|verehrte[rs]?|liebe[rs]?|werte[rs]?)\s|Meine (?:sehr )?(?:verehrten |geehrten )?Damen|"
    r"(?:Herr|Frau) (?:Präsident|Präsidentin|Vizepräsident|Vizepräsidentin)\b|Liebe Kolleg|Werte Kolleg|"
    r"(?:Vielen|Herzlichen|Recht herzlichen|Schönen) Dank|Danke\b|Ich danke|Ich bedanke mich|"
    r"Gerne?\.|Ja\.|Nein\.|Bitte\.|Ich komme zum Schluss|Ich komme zum Ende|Lassen Sie mich zum Schluss|"
    r"Herzlichen Glückwunsch|Glück auf|Wo waren wir)", re.I)
META = re.compile(r"^(?:Erstens|Zweitens|Drittens|Viertens|Fünftens|Letztens|Zum Schluss|Abschließend)\.?$|"
                  r"\b(?:lassen Sie mich|ich möchte (?:noch )?(?:kurz )?(?:auf|zu) .{0,30}(?:eingehen|kommen|sagen)|"
                  r"meine Redezeit|ich habe (?:nur )?noch \w+ (?:Minuten|Sekunden)|zu meinem Vorredner)", re.I)
HECKLE = re.compile(r"^–|\b(?:Hören Sie (?:doch )?(?:mal )?zu|Sie können gleich|Regen Sie sich|Zwischenruf|"
                    r"rufen Sie (?:doch )?nicht)", re.I)
SECOND = re.compile(r"(?<!^)\b(?:Sie|Ihnen|Ihr|Ihre|Ihren|Ihrem|Ihrer|Ihres)\b")
DEONTIC = re.compile(r"\b(?:müssen|muss|müsste[n]?|brauchen|braucht|fordern|fordert|fordere|sollten|sollen|"
                     r"dürfen nicht|darf nicht|notwendig|erforderlich|ablehnen|lehnen|zustimmen|stimmen\b|"
                     r"unterstützen|wollen|Ziel)\b", re.I)
REFS = re.compile(r"\b(?:Drucksache|§\s*\d+|Artikel \d+|Art\. \d+|\d+/\d{3,5})")
ZUSTIMMUNG = re.compile(r"\b(?:bitte(?:n)? (?:Sie )?(?:um (?:Ihre )?Zustimmung|um Unterstützung)|"
                        r"(?:Stimmen|Stimmen Sie) (?:Sie )?(?:unserem|diesem|dem) (?:Antrag|Gesetzentwurf) zu)", re.I)


def question_signature(q_speech, sig, cfg, stop_names=frozenset()):
    """Adjacency pair: the answer's topic is its question's topic (question lemmas, idf-weighted), plus the item."""
    genre = topic.genre_words()
    out = {k: 0.5 * v for k, v in sig.items()}
    lems = [r[0] for s in q_speech["sentences"] for r in s["tokens"] if topic.is_topic_token(r, genre, stop_names)]
    if lems:
        mx = max(topic.idf(l) for l in lems)
        for l in lems:
            out[l] = max(out.get(l, 0), cfg["seed_weight"] * topic.idf(l) / mx)
    return out


def label_sentences(sp, sig, cfg, stop_names=frozenset()):
    genre = topic.genre_words()
    parts = topic.compound_parts(sig)
    out = []
    for s in sp["sentences"]:
        t = s["text"]
        words = len(t.split())
        ts, hits = topic.topic_score(s["tokens"], sig, parts, cfg, genre, stop_names)
        info = 0.0
        if any(r[1] == "NUM" for r in s["tokens"]) and ts > 0:
            info += 0.3
        if any(r[2] in ("ORG", "LOC", "MISC") for r in s["tokens"]) and ts > 0:
            info += 0.1
        if REFS.search(t):
            info += 0.3
        if DEONTIC.search(t):
            info += 0.2
        if any(r.startswith("(Beifall") for r in s["reactions"]):
            info += 0.2
        flags = []
        label = None
        if FORMULA.match(t) and words <= 14:
            flags.append("formula")
            label = "DROP"
        elif words < cfg["min_words"] and not t.endswith("?"):
            flags.append("fragment")
            label = "DROP"
        if ZUSTIMMUNG.search(t):
            flags.append("zustimmung")
            label = "DROP"
        pen = 0.0
        if META.search(t):
            pen += 0.3
            flags.append("meta")
        if HECKLE.search(t):
            pen += 0.4
            flags.append("heckle")
        if SECOND.search(t) and not hits:
            pen += 0.3
            flags.append("attack")
        score = ts + info - pen
        if label is None:
            if ts >= cfg["quote_min_topic"] and score >= cfg["quote_min_topic"] and "heckle" not in flags:
                label = "QUOTE"
            elif words >= cfg["report_min_words"]:
                label = "REPORT"
            else:
                label = "DROP"
        if sp["kind"] in ("Zwischenfrage", "Frage") and t.endswith("?") and label != "DROP":
            label = "QUOTE"
        out.append({"k": s["k"], "topic": ts, "info": info, "score": score, "label": label, "words": words,
                    "hits": hits, "flags": flags, "lemmas": {r[0] for r in s["tokens"] if r[0] and r[1] in topic.CONTENT_POS}})
    return out


def _sim(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / math.sqrt(len(a) * len(b))


def _paragraphs(sp, labels, budget, cfg):
    """v3: the speaker's paragraphs (the <p> units of the XML) are the units of selection, so the excerpt reads as
    passages of the speech (topical segments, cf. Hearst 1997 TextTiling). A paragraph is a candidate when at least
    one of its sentences is on topic (QUOTE); its value is the mean score of its sentences. MMR over paragraphs
    under the word budget; inside a chosen paragraph every sentence is kept except formulas and heckle replies.
    -> (picked labels, words used); ([], 0) when no paragraph fits."""
    by_unit = {}
    for l in labels:
        by_unit.setdefault(sp["sentences"][l["k"]]["unit"], []).append(l)
    paras = []
    order = {u: n for n, u in enumerate(sp["units"])}
    for u, ls in by_unit.items():
        # a short sentence inside a kept passage stays ("Genau darum geht es."); formulas and heckle replies go
        keep = [l for l in ls if (l["label"] != "DROP" or l["flags"] == ["fragment"]) and "heckle" not in l["flags"]]
        if not keep or not any(l["label"] == "QUOTE" for l in keep):
            continue
        paras.append({"keep": keep, "n": order.get(u, -9), "words": sum(l["words"] for l in keep),
                      "score": sum(l["score"] for l in keep) / len(keep),
                      "lemmas": set().union(*(l["lemmas"] for l in keep))})
    picked, used, chosen = [], 0, []
    pool = list(paras)
    while pool:
        near = {c["n"] + d for c in chosen for d in (-1, 1)}
        best = max(pool, key=lambda p: cfg["mmr_lambda"] * p["score"] - (1 - cfg["mmr_lambda"]) *
                   max((_sim(p["lemmas"], c["lemmas"]) for c in chosen), default=0.0) +
                   (cfg.get("adjacency_bonus", 0.0) if p["n"] in near else 0.0))
        pool.remove(best)
        if used + best["words"] <= budget:
            chosen.append(best)
            picked += best["keep"]
            used += best["words"]
    return picked, used


def choose(sp, labels, cfg):
    """MMR (Carbonell & Goldstein 1998) over the QUOTE sentences under the word budget of the speech.
    -> {"quotes": [[k, ...], ...], "formula", "formula_sent", "report", "dropped"}"""
    words = sp["words"]
    budget = min(cfg["max_quote_words"], max(0, round(cfg["ratio"] * words)))
    cands = [l for l in labels if l["label"] == "QUOTE"]
    if sp["kind"] in ("Zwischenfrage", "Frage"):
        qs = [l for l in cands if sp["sentences"][l["k"]]["text"].endswith("?")]
        cands = qs or cands
        budget = max(budget, cfg["question_max_words"])
    if words < cfg["min_speech_words"]:
        # a short contribution is kept whole, without formulas and replies to heckling
        cands = [l for l in labels if l["label"] != "DROP" and "heckle" not in l["flags"]]
        budget = words
    picked, used = [], 0
    if cfg.get("unit") == "paragraph" and words >= cfg["min_speech_words"] and sp["kind"] not in ("Zwischenfrage", "Frage"):
        picked, used = _paragraphs(sp, labels, budget, cfg)
    pool = [] if picked else list(cands)
    while pool:
        best, bv = None, -1e9
        near = {p["k"] + d for p in picked for d in (-1, 1)}
        for l in pool:
            red = max((_sim(l["lemmas"], p["lemmas"]) for p in picked), default=0.0)
            v = cfg["mmr_lambda"] * l["score"] - (1 - cfg["mmr_lambda"]) * red
            # v3: a neighbour of a sentence already taken is preferred, so the excerpt reads as passages of the
            # speech and not as a chain of single sentences with […] between them (cohesion, Halliday & Hasan)
            if l["k"] in near:
                v += cfg.get("adjacency_bonus", 0.0)
            if v > bv:
                best, bv = l, v
        pool.remove(best)
        if used + best["words"] <= budget:
            picked.append(best)
            used += best["words"]
    # the questions of a question turn are always kept in the speaker's words (a question is the first part of an
    # adjacency pair: a paraphrase loses what exactly was asked; a rhetorical question inside a speech is not kept), and so is a short request for approval
    asks = sp["kind"] in ("Zwischenfrage", "Frage", "Kurzintervention")
    for l in labels:
        t = sp["sentences"][l["k"]]["text"]
        if asks and l not in picked and l["label"] != "DROP" and "heckle" not in l["flags"] and t.endswith("?") \
                and l["words"] <= 40:
            picked.append(l)
        elif l not in picked and "zustimmung" in l["flags"] and l["words"] <= 25:
            picked.append(l)
    # a gap of one short sentence between two kept passages is filled: the reader gets one passage, not two
    # passages and an omission mark for a few words
    have = {l["k"] for l in picked}
    for l in labels:
        if l["k"] not in have and l["k"] - 1 in have and l["k"] + 1 in have and l["label"] != "DROP" \
                and "heckle" not in l["flags"] and l["words"] <= cfg.get("gap_fill_words", 0):
            picked.append(l)
    # every speech of 60+ words with a candidate gets at least one quote
    if not picked and cands and words >= cfg["min_speech_words"]:
        short = [l for l in cands if l["words"] <= cfg["first_quote_max"]]
        if short:
            picked = [max(short, key=lambda l: l["score"])]
    ks = sorted(l["k"] for l in picked)
    groups = []
    for k in ks:
        if groups and groups[-1][-1] == k - 1 and sp["sentences"][k]["unit"] == sp["sentences"][k - 1]["unit"]:
            groups[-1].append(k)
        else:
            groups.append([k])
    formula, fsent = None, []
    z = [l["k"] for l in labels if "zustimmung" in l["flags"] and l["k"] not in ks]
    if z:
        formula, fsent = "und bat um Zustimmung", z
    chosen = set(ks)
    report = [l["k"] for l in labels if l["label"] == "REPORT" or (l["label"] == "QUOTE" and l["k"] not in chosen)]
    report = [k for k in report if labels[k]["label"] == "REPORT" and k not in chosen]
    dropped = [l["k"] for l in labels if l["label"] == "DROP" and l["k"] not in fsent and l["k"] not in chosen]
    return {"quotes": groups, "formula": formula, "formula_sent": fsent, "report": report, "dropped": dropped}
