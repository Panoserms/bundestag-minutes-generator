"""Part D: what the speakers said, as direct quotes, a short reported part and dropped formulas.

    speeches = group_speeches(sitting["units"], item["units"])
    digests  = digest_item(item_id, item["topic_text"], speeches, sitting["units"])
    sentence = render_reported_rule(digest)              # system C: rule template for the reported part

Method (rules T1–E7 with their linguistic reasoning: docs/RULES.md):
  1. group_speeches: one Speech per <rede> of its main speaker; another speaker inside the rede is a Zwischenfrage
     (the main speaker's answer stays in the speech); Kurzintervention / Erwiderung from the chair's turn before;
     Frage / Antwort in question time. Gender only from the chair's grammatical reference to the speaker.
     Sentences by spaCy (de_core_news_md); each paragraph's reactions go to its last sentence.
  2. topic.signature: topic signature of the item (seed lemmas from the agenda text + G² key lemmas of the debate).
  3. select.label_sentences: every sentence gets a topic score and an information score -> QUOTE / REPORT / DROP.
  4. select.choose: MMR over the QUOTE sentences under a word budget; adjacent picks merge into one quote.
  5. frames: a speech-act verb and a lead for every quote ("Claudia Roth (BÜNDNIS 90/DIE GRÜNEN) forderte:").
"""
import re
from collections import Counter

from . import frames, select, topic

CFG = {
    # topic signature
    "seed_weight": 1.0, "prf_weight": 1.0, "prf_k": 40, "prf_min_g2": 10.83, "prf_min_freq": 3,
    "compound": True,
    # sentence labels
    "quote_min_topic": 0.15, "report_min_words": 6, "min_words": 5,
    # selection. v3 (23.10.2026, user: "more of the transcript, closer to the words, wortnah"): about half
    # of each speech in the speaker's own words (v2: 12 %, at most 110 words)
    "ratio": 0.50, "max_quote_words": 450, "min_speech_words": 60, "mmr_lambda": 0.7, "first_quote_max": 60,
    "question_max_words": 120, "adjacency_bonus": 0.4, "gap_fill_words": 25, "unit": "paragraph",
}

KINDS = ("Rede", "Zwischenfrage", "Kurzintervention", "Erwiderung", "Frage", "Antwort")


# ------------------------------------------------------------------ gender from the chair's reference

_ABBR = {"Dr.", "Prof.", "Abg.", "St.", "h.", "c."}
_CUE = [  # (regex on one token or two-token phrase, gender)
    (r"Herr|Herrn|Kollege|Redner|Abgeordneter|Fragesteller|Bundesminister|Minister|Ministers|Staatssekretär|"
     r"Staatssekretärs|Staatsminister|Bundeskanzler|Berichterstatter|Bundesministers", "m"),
    (r"Frau|Kollegin|Rednerin|Fragestellerin|Bundesministerin|Ministerin|Staatssekretärin|Staatsministerin|"
     r"Bundeskanzlerin|Berichterstatterin|Abgeordnetenkollegin", "f"),
]
_AFTER = re.compile(r"^(?:ist|war)$")
_AFTER_NOUN = {"Redner": "m", "Rednerin": "f", "Fragesteller": "m", "Fragestellerin": "f"}
_ALS = {"Nächster": "m", "Nächste": "f", "Erster": "m", "Erste": "f", "Letzter": "m", "Letzte": "f"}
_CUE_RX = [(re.compile(r"^(?:" + rx + r")$"), g) for rx, g in _CUE]
_ART_ABG = re.compile(r"^(dem|des|den|der|die)$")
_ABG = re.compile(r"^(?:[\w]+-)?(Abgeordneten|Abgeordnete|Kollegen)$")
_TOK = re.compile(r"[\w][\w\-’']*\.?|[–!?;:]")
_ADDRESS = re.compile(r"(?:^|[,!.]\s*)(Herr|Frau)\s+(Kollege|Kollegin|Minister|Ministerin|Bundesminister|"
                      r"Bundesministerin|Staatssekretär|Staatssekretärin|Staatsminister|Staatsministerin|"
                      r"Abgeordneter|Abgeordnete|Bundeskanzler|Bundeskanzlerin)\b")
_THANKS = re.compile(r"^\s*(?:Vielen|Herzlichen|Schönen)?\s*Dank|^\s*Danke")


def surname(name):
    parts = [p for p in re.split(r"\s+", name or "") if p and not p.endswith(".")]
    return parts[-1] if parts else ""


def _cue_gender(tokens, k):
    """Gender of the cue right before token k (nearest preceding cue within 10 tokens, same clause)."""
    for j in range(k - 1, max(-1, k - 11), -1):
        t = tokens[j]
        if t in ("–", "!", "?", ";", ":") or (t.endswith(".") and t not in _ABBR and len(t) > 2):
            return None
        bare = t.rstrip(".")
        for rx, g in _CUE_RX:
            if rx.match(bare):
                return g
        m = _ABG.match(bare)
        if m:
            if m.group(1) == "Abgeordnete" and j > 0 and tokens[j - 1] == "die":
                return "f"
            if m.group(1) == "Abgeordnete" and j > 0 and tokens[j - 1] == "der":
                return "m"
            if j > 0 and _ART_ABG.match(tokens[j - 1]):
                art = tokens[j - 1]
                if m.group(1) in ("Abgeordneten", "Kollegen"):
                    if art == "der" and m.group(1) == "Abgeordneten":
                        return "f"
                    if art in ("dem", "des", "den"):
                        return "m"
            continue            # no determining article ('Frau Abgeordnete X'): look further back
    return None


def gender_cues(chair_text, name):
    """Gender cues the chair attaches to `name` in one chair turn: every occurrence of the surname with its nearest
    preceding agreement cue ('Die nächste Rednerin ist Anne-Mieke Bremer', 'dem Abgeordneten Max Lucks',
    'Herr Görke, möchten Sie erwidern?'). Returns a Counter {'m': n, 'f': n}."""
    out = Counter()
    sn = surname(name)
    if not sn or not chair_text:
        return out
    toks = _TOK.findall(chair_text)
    for k, t in enumerate(toks):
        if t.rstrip(".,") == sn:
            g = _cue_gender(toks, k)
            if not g:
                g = _als_gender(toks, k) or _postposed_gender(toks, k)
            if g:
                out[g] += 1
    return out


def _als_gender(toks, k):
    """'Als Nächster spricht Joachim Ebmeyer' / 'Als Nächste hat Frau X das Wort'."""
    for j in range(k - 1, max(-1, k - 8), -1):
        if toks[j] in ("–", "!", "?", ";", ":") or (toks[j].endswith(".") and toks[j] not in _ABBR):
            return None
        if toks[j] in _ALS and j > 0 and toks[j - 1] == "Als":
            return _ALS[toks[j]]
    return None


def _postposed_gender(toks, k):
    """'Karoline Otte ist die nächste Rednerin'."""
    if k + 1 < len(toks) and _AFTER.match(toks[k + 1]):
        for j in range(k + 2, min(len(toks), k + 6)):
            bare = toks[j].rstrip(".")
            if bare in _AFTER_NOUN:
                return _AFTER_NOUN[bare]
            if toks[j] in ("–", "!", "?", ";", ":"):
                return None
    return None


def address_gender(segment):
    """Unbound address with a role noun: 'Frau Ministerin, bitte.', 'Vielen Dank, Herr Kollege.'"""
    m = _ADDRESS.search(segment)
    if not m:
        return None
    return "m" if m.group(1) == "Herr" else "f"


def _segments(text):
    return [s.strip() for s in re.split(r"\s+–\s+|(?<=[.!?])\s+", text) if s.strip()]


# ------------------------------------------------------------------ grouping

_QA_HINT = re.compile(r"Befragung der Bundesregierung|Fragestunde|mündlichen Fragen|Frage \d+ de[rs] Abgeordneten|"
                      r"für die heutige Befragung")
_CALL_Q = re.compile(r"(?:Frage|Fragen)\s+\d+\s*(?:\w\s*)?(?:und\s+\d+\s*)?de[rs]\s+(?:\w+-)?(?:Abgeordneten|Kollegen|"
                     r"Kollegin)\s+(?:Herrn\s+|Frau\s+)?(.+?)(?:\s+(?:von der|aus der|der Fraktion|für die|,)|\s+auf\b|:|$)")
_KURZ = re.compile(r"Kurzintervention")
_ERW = re.compile(r"erwidern|Erwiderung")
_ZWF = re.compile(r"Zwischenfrage|Zwischenbemerkung|Frage")


def _chair_before(by_i, first_i, max_back=8):
    """Chair units right before unit first_i (stops at the first non-chair unit)."""
    out = []
    j = first_i - 1
    while j in by_i and by_i[j]["role"] == "chair" and len(out) < max_back:
        out.insert(0, by_i[j])
        j -= 1
    return out


def _chair_after(by_i, last_i, max_fwd=4):
    out = []
    j = last_i + 1
    while j in by_i and by_i[j]["role"] == "chair" and len(out) < max_fwd:
        out.append(by_i[j])
        j += 1
    return out


def _is_qa(by_i, ids):
    chair = " ".join(by_i[i]["text"] for i in ids[:12] if by_i[i]["role"] == "chair")
    return bool(_QA_HINT.search(chair))


def _new_speech(u, kind, rede=None, parent=None):
    return {"rede": rede if rede is not None else u["rede"], "speaker": u["speaker"], "group": u["group"],
            "role": u["role"], "kind": kind, "gender": None, "units": [u["i"]], "parent": parent,
            "read_by_chair": False, "sentences": [], "words": 0}


def _group_of(name, by_i):
    sn = surname(name)
    for u in by_i.values():
        if u["role"] != "chair" and (u["speaker"] == name or surname(u["speaker"]) == sn) and u["group"]:
            return u["group"], u["role"], u["speaker"]
    return "", "member", name


def _read_questions(by_i, ids):
    """Fragestunde: the chair reads the written question ('Ich rufe Frage 1 des Abgeordneten Bernd Schattner auf:'
    + question text). -> {first_question_unit_i: (asker_name, [unit ids])}"""
    out = {}
    for n, i in enumerate(ids):
        u = by_i[i]
        if u["role"] != "chair":
            continue
        m = _CALL_Q.search(u["text"])
        if not m or not u["text"].rstrip().endswith(":"):
            continue
        name = m.group(1).strip(" ,:")
        qs = []
        for j in ids[n + 1:]:
            v = by_i[j]
            if v["role"] != "chair":
                break
            if len(v["text"].split()) <= 8 and re.search(r"[Bb]itte|Wort", v["text"]):
                break
            qs.append(j)
        if qs:
            out[qs[0]] = (name, qs)
    return out


def group_speeches(units, item_unit_ids, kind=None):
    """-> list[Speech] in spoken order (by first unit).

    units          all units of the sitting (sources.load_sitting(sid)["units"]); chair turns around the item are
                   used for the kind and the gender cue
    item_unit_ids  the unit indices filed under the item
    kind           optional item kind ("qa" for Befragung/Fragestunde); detected from the chair's text if None
    """
    by_i = {u["i"]: u for u in units}
    ids = sorted(i for i in set(item_unit_ids) if i in by_i)
    qa = (kind == "qa") if kind else _is_qa(by_i, ids)
    read_q = _read_questions(by_i, ids) if qa else {}
    read_units = {j for _, qs in read_q.values() for j in qs}

    speeches = []
    main_of_rede = {}              # rede id -> index of its main speech
    open_run = None                # index of the current speech (for continuation across chair turns)
    for i in ids:
        u = by_i[i]
        if i in read_q:
            name, qs = read_q[i]
            group, role, full = _group_of(name, by_i)
            sp = _new_speech({"rede": None, "speaker": full, "group": group, "role": role, "i": qs[0]}, "Frage")
            sp["units"] = list(qs)
            sp["read_by_chair"] = True
            speeches.append(sp)
            open_run = None
            continue
        if i in read_units:
            continue
        if u["role"] == "chair":
            if qa and re.search(r"\bFrage\s+\d+", u["text"]):
                open_run = None            # a new question is called: the next answer is a new speech
            continue
        rede = u["rede"]
        if rede is not None:
            if rede not in main_of_rede:
                main_of_rede[rede] = len(speeches)
                speeches.append(_new_speech(u, "Rede"))
                open_run = len(speeches) - 1
                continue
            m = main_of_rede[rede]
            if u["speaker"] == speeches[m]["speaker"]:
                speeches[m]["units"].append(i)
                open_run = m
                continue
            # another speaker inside the rede: a Zwischenfrage (continues while the same asker speaks)
            if open_run is not None and speeches[open_run]["kind"] == "Zwischenfrage" and \
                    speeches[open_run]["speaker"] == u["speaker"] and speeches[open_run]["rede"] == rede:
                speeches[open_run]["units"].append(i)
                continue
            speeches.append(_new_speech(u, "Zwischenfrage", rede=rede, parent=m))
            open_run = len(speeches) - 1
            continue
        # no rede id (Fragestunde, some 'other' speakers): runs of one speaker
        if open_run is not None and speeches[open_run]["rede"] is None and \
                speeches[open_run]["speaker"] == u["speaker"] and not speeches[open_run]["read_by_chair"]:
            speeches[open_run]["units"].append(i)
            continue
        speeches.append(_new_speech(u, "Rede", rede=None))
        open_run = len(speeches) - 1

    # kinds
    seen_question = False
    for sp in speeches:
        before = " ".join(c["text"] for c in _chair_before(by_i, sp["units"][0]))
        if qa:
            if sp["role"] == "government":
                sp["kind"] = "Antwort" if seen_question else "Rede"     # opening reports come before questions
            else:
                sp["kind"] = "Frage"
                seen_question = True
        elif sp["kind"] == "Rede":
            if _KURZ.search(before):
                sp["kind"] = "Kurzintervention"
            elif _ERW.search(before):
                sp["kind"] = "Erwiderung"
        sp["chair_before"] = before

    # gender: the chair's grammatical reference to the speaker, pooled over the speaker's speeches in the item
    item_chair = [by_i[i]["text"] for i in ids if by_i[i]["role"] == "chair"]
    distinct = Counter(surname(n) for n in {sp["speaker"] for sp in speeches})
    by_name = {}
    for sp in speeches:
        by_name.setdefault(sp["speaker"], []).append(sp)
    for name, sps in by_name.items():
        cues, source = Counter(), None
        for sp in sps:
            before = _chair_before(by_i, sp["units"][0])
            for c in before:
                cues.update(gender_cues(c["text"], name))
            if before:                             # 'Frau Ministerin, bitte.' as the last segment before the speech
                seg = _segments(before[-1]["text"])
                if seg and re.search(r"[Bb]itte|das Wort|Sie haben", seg[-1]) and not _THANKS.search(seg[-1]):
                    g = address_gender(seg[-1])
                    if g:
                        cues[g] += 1
        if cues:
            source = "before"
        else:
            if distinct[surname(name)] == 1:       # other chair references in the item bound to this surname
                for t in item_chair:
                    cues.update(gender_cues(t, name))
            for sp in sps:                         # 'Vielen Dank, Frau Kollegin.' right after the speech
                after = _chair_after(by_i, sp["units"][-1])
                if after:
                    seg = _segments(after[0]["text"])
                    if seg and _THANKS.search(seg[0]):
                        g = address_gender(seg[0])
                        if g:
                            cues[g] += 1
            source = "item" if cues else None
        gender = next(iter(cues)) if len(cues) == 1 else None
        if len(cues) > 1:
            source = "conflict"
        for sp in sps:
            sp["gender"], sp["gender_source"] = gender, source
    _split_sentences(speeches, by_i)
    return speeches


# ------------------------------------------------------------------ sentences

def _split_sentences(speeches, by_i):
    need = sorted({i for sp in speeches for i in sp["units"]})
    nlp = topic.nlp()
    docs = dict(zip(need, nlp.pipe((by_i[i]["text"] for i in need), batch_size=64)))
    for sp in speeches:
        sents, parts, off = [], [], 0
        for i in sp["units"]:
            u = by_i[i]
            doc = docs[i]
            unit_sents = []
            for s in doc.sents:
                text = s.text.strip()
                if not text:
                    continue
                st = off + s.start_char
                unit_sents.append({"k": 0, "text": text, "unit": i, "reactions": [], "start": st,
                                   "end": st + len(text), "tokens": [topic.tok_record(t) for t in s]})
            unit_sents = _merge_tiny(unit_sents, u["text"], off)
            if unit_sents:
                unit_sents[-1]["reactions"] = list(u["reactions"])
            parts.append(u["text"])
            off += len(u["text"]) + 1
            if sents and unit_sents and _continues(sents[-1]["text"], unit_sents[0]["text"]):
                # a heckle or applause in mid-sentence splits one sentence over two XML paragraphs
                # ('Die AfD ist eine demokratische Partei, (Lachen) die …'): join it again across the paragraphs
                p, s = sents[-1], unit_sents.pop(0)
                p["end"] = s["end"]
                p["text"] = " ".join(parts)[p["start"]: s["end"]]
                p["tokens"] = p["tokens"] + s["tokens"]
                p["reactions"] = p["reactions"] + s["reactions"]
            sents.extend(unit_sents)
        for k, s in enumerate(sents):
            s["k"] = k
        sp["text"] = " ".join(parts)
        sp["sentences"] = sents
        sp["words"] = len(sp["text"].split())


_GLUE_PREV = re.compile(r"(?:\b\d{1,2}\.|\b(?:Nr|Art|Abs|Abg|Dr|Prof|bzw|ca|vgl|ggf|Ziff|Kap)\.|\bz\. B\.|\bu\. a\.)$")
_MONTH = re.compile(r"^(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\b")


def _merge_tiny(sents, unit_text, off):
    """spaCy sometimes splits after an ordinal or an abbreviation ('am 5. Juni', 'Art. 3', 'Dr. Weidel').
    Glue such a piece back to its predecessor, so that every quote stays a whole sentence."""
    out = []
    for s in sents:
        if out:
            prev = out[-1]["text"]
            glue = bool(re.search(r"\b(?:Nr|Art|Abs|Abg|Dr|Prof|bzw|ca|vgl|ggf|Ziff|Kap)\.$|\bz\. B\.$|\bu\. a\.$", prev))
            glue = glue or (re.search(r"\b\d{1,2}\.$", prev) and (_MONTH.match(s["text"]) or s["text"][:1].islower()))
            glue = glue or s["text"][:1].islower()
            if glue:
                p = out[-1]
                p["end"] = s["end"]
                p["text"] = unit_text[p["start"] - off: s["end"] - off]
                p["tokens"] = p["tokens"] + s["tokens"]
                continue
        out.append(s)
    return out


_OPEN_END = re.compile(r"[,;:–-]$|[^.!?…“”\"')]$")


def _continues(prev, nxt):
    """True when the next paragraph's first sentence continues the previous paragraph's last sentence: the previous
    one has no sentence-final punctuation, or the next one starts in lower case."""
    return bool(_OPEN_END.search(prev.rstrip())) or nxt[:1].islower()


def speech_text_norm(text):
    return " ".join(text.split())


# ------------------------------------------------------------------ digest

def digest_item(item_id, topic_text, speeches, sitting_units, cfg=None, kind=None, title=None):
    """-> list[Digest], one per speech in speech order (see docs/ARCHITECTURE.md).

    item_id      e.g. "TOP 18"
    topic_text   the item's title + agenda text + AP text (seed of the topic signature)
    speeches     output of group_speeches
    sitting_units  all units of the sitting (speaker names of the sitting are removed from the topic seeds)
    kind, title  optional: item kind ("qa") and title (for "stellte den Gesetzentwurf vor" / "begründete den Antrag")
    """
    c = dict(CFG, **(cfg or {}))
    if not speeches:
        return []
    qa = kind == "qa" or any(sp["kind"] in ("Frage", "Antwort") for sp in speeches)
    stop_names = _name_lemmas(sitting_units)
    all_recs = [r for sp in speeches for s in sp["sentences"] for r in s["tokens"]]
    sig = topic.signature(topic_text, all_recs, c, stop_names=stop_names)
    sigs = []
    for n, sp in enumerate(speeches):
        s_sig = sig
        if qa and sp["kind"] == "Antwort":
            q = _question_for(speeches, n)
            if q is not None:                      # adjacency pair: the answer's topic is its question's topic
                s_sig = select.question_signature(q, sig, c, stop_names)
        sigs.append(s_sig)
    digests = []
    for n, sp in enumerate(speeches):
        labels = select.label_sentences(sp, sigs[n], c, stop_names=stop_names)
        picks = select.choose(sp, labels, c)
        digests.append(_digest(sp, labels, picks))
    frames.assign(digests, speeches, item_id=item_id, title=title or topic_text, qa=qa)
    return digests


def _question_for(speeches, n):
    for m in range(n - 1, -1, -1):
        if speeches[m]["kind"] == "Frage":
            return speeches[m]
        if speeches[m]["kind"] == "Antwort" and speeches[m]["speaker"] != speeches[n]["speaker"]:
            return None
    return None


def _name_lemmas(units):
    out = set()
    for u in units or []:
        if u["role"] == "chair":
            continue
        for p in re.split(r"[\s\-]+", u["speaker"] or ""):
            if len(p) > 2 and not p.endswith("."):
                out.add(p.lower())
    return frozenset(out)


def _digest(sp, labels, picks):
    text = sp["text"]
    parts = []
    for grp in picks["quotes"]:
        s0, s1 = sp["sentences"][grp[0]], sp["sentences"][grp[-1]]
        parts.append({"type": "quote", "text": text[s0["start"]: s1["end"]], "sent": list(grp), "verb": None,
                      "lead": None})
    if picks.get("formula"):
        parts.append({"type": "formula", "text": picks["formula"], "sent": picks.get("formula_sent", [])})
    return {"speaker": sp["speaker"], "group": sp["group"], "role": sp["role"], "kind": sp["kind"],
            "gender": sp["gender"], "rede": sp["rede"], "words_source": sp["words"], "parts": parts,
            "report_sents": picks["report"], "report_hint": {}, "dropped": picks["dropped"],
            "scores": [{"k": l["k"], "topic": round(l["topic"], 3), "info": round(l["info"], 3),
                        "label": l["label"]} for l in labels]}


# ------------------------------------------------------------------ system C: rule template for the reported part

def render_reported_rule(digest):
    """One short sentence in reported form, built only from digest["report_hint"]
    ('Zudem kritisierte sie die Fraktion der AfD.'). None when nothing reliable can be said."""
    return frames.render_reported(digest)
