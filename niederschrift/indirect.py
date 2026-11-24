"""Two more ways to read a speech, by general German grammar and sentence extraction:

  indirect_mode   the excerpt's sentences in indirect speech (Konjunktiv I), without "dass"
  summary_mode    the two or three most important sentences of the speech

Indirect speech (Duden grammar, "Indirekte Rede"): the finite verbs move to Konjunktiv I. Where that form equals the
indicative (most plurals), Konjunktiv II or "würde" + infinitive is used. Past tense becomes the perfect: "hatte
geändert" -> "habe geändert", "wurde beschlossen" -> "sei beschlossen worden".

A sentence is moved only when this is safe. It is refused when it needs its speaker or its context (first and second
person, "Sie", pronoun subjects, "deshalb", "dafür", …), when it is a question, an imperative or a form of address,
when it holds a quotation, a colon or an insertion between dashes, and when one of its finite verbs is not in a form
the rules know for certain (for example "stieg").
"""
import re

from .kurz import ANAPHORIC, FIRST, SUBORD_TAGS, lower_first

AUX = {  # surface form -> Konjunktiv; the plural forms take Konjunktiv II because Konjunktiv I equals the indicative
    "ist": "sei", "sind": "seien", "hat": "habe", "haben": "hätten", "wird": "werde", "werden": "würden",
    "kann": "könne", "können": "könnten", "muss": "müsse", "müssen": "müssten", "soll": "solle", "sollen": "sollten",
    "will": "wolle", "wollen": "wollten", "darf": "dürfe", "dürfen": "dürften", "weiß": "wisse", "wissen": "wüssten"}
PAST = {"hatte": "habe", "hatten": "hätten", "war": "sei", "waren": "seien"}      # only with a participle
PASSIVE = {"wurde": "sei", "wurden": "seien"}                                     # + participle + "worden"
SUBJUNCTIVE = re.compile(r"^(?:würden?|wären?|hätten?|könnten?|müssten?|sollten?|dürften?|möchten?|bräuchten?|gäbe)$")
STRONG = {"gibt": "geben", "nimmt": "nehmen", "sieht": "sehen", "spricht": "sprechen", "hilft": "helfen", "hält": "halten",
          "lässt": "lassen", "läuft": "laufen", "fällt": "fallen", "trägt": "tragen", "schlägt": "schlagen",
          "fährt": "fahren", "wächst": "wachsen", "tritt": "treten", "trifft": "treffen", "gilt": "gelten",
          "wirft": "werfen", "bricht": "brechen", "liest": "lesen", "stößt": "stoßen", "rät": "raten", "tut": "tun",
          "geschieht": "geschehen", "fängt": "fangen", "schafft": "schaffen", "empfiehlt": "empfehlen"}
PREFIX = re.compile(r"^(?:be|ge|er|ver|zer|ent|miss|über|unter|wider|wieder|durch|um|voll|hinter)?$")
ADDRESS = re.compile(r"\b(?:Kolleg(?:e|en|in|innen)|Damen|Herren|Präsident(?:in)?|Vizepräsident(?:in)?)\b|\b(?:Herr|Frau|[Ll]iebe[rs]?)\s+[A-ZÄÖÜ]")
POINTS_BACK = re.compile(r"\b(?:dafür|dazu|darüber|davon|daran|darauf|dagegen|damit|dabei|dadurch|daraus|darin|dort|deswegen|"
                         r"deshalb|daher|darum|denn|nämlich|also|ebenfalls|beides|letzteres?)\b", re.I)
RELATIVE = {"der", "die", "das", "den", "dem", "denen", "dessen", "deren", "wer", "wo", "was", "wie", "wobei", "womit"}
IMPERSONAL = {"geben", "gehen", "brauchen", "fehlen", "sein", "heißen", "handeln", "bedürfen"}     # "es gibt", "es geht um"
SECOND = {"du", "dich", "dir", "dein", "deine", "euch", "euer", "eure"}
STOP_OVERLAP = 0.4
MIN_SPEECH, MAX_CORE, MAX_WORDS = 120, 3, 80


def _present(tok):
    """Konjunktiv I of a full verb in the 3rd person singular, or None if the form is not certain."""
    form, lemma = tok.lower_, tok.lemma_.lower()
    for strong, inf in STRONG.items():
        if form.endswith(strong) and PREFIX.match(form[: -len(strong)]):
            return form[: -len(strong)] + (inf[:-1] if inf == "tun" else inf[:-2]) + ("e" if inf != "tun" else "e")
    stem = lemma[:-2] if lemma.endswith("en") else lemma[:-1] if lemma.endswith(("eln", "ern")) else None
    if stem and form in (stem + "t", stem + "et"):
        return stem + "e"
    return None


def _verb_final(tok):
    """The finite verb closes a subordinate clause: punctuation follows, and a subordinator opens the clause."""
    doc = tok.doc
    if tok.i + 1 < len(doc) and not doc[tok.i + 1].is_punct:
        return False
    k = tok.i - 1
    while k >= 0 and doc[k].text not in (",", ";", ":"):
        if doc[k].tag_ in SUBORD_TAGS:
            return True
        k -= 1
    return False


def _clauses_are_complete(doc, fin):
    """Every clause (between commas) has the finite verbs it needs, so no verb was missed by the tagger: one main clause
    with a finite verb; a clause that opens like a subordinate clause has one too; two finite verbs in one clause only
    when a conjunction joins them."""
    ids, parts, cur = {t.i for t in fin}, [], []
    for t in doc:
        if t.text == ",":
            parts.append(cur)
            cur = []
        elif not t.is_punct and not t.is_space:
            cur.append(t)
    parts = [p for p in parts + [cur] if p]
    main = False
    for n, part in enumerate(parts):
        k = [t for t in part if t.i in ids]
        first = part[0]
        sub = any(t.tag_ in SUBORD_TAGS - {"KOUI"} for t in part[:3])
        if (sub or (n and first.lower_ in RELATIVE)) and not k:
            return False
        if len(k) > 1 and not any(t.tag_ == "KON" for t in doc[k[0].i:k[-1].i]):
            return False
        main = main or bool(k and not sub)
        if n == 0 and not k and not sub:
            return False
    return main


def reported(doc):
    """One sentence -> the same sentence in indirect speech (first word in lower case where German allows), or None."""
    text = doc.text.strip()
    words = [t for t in doc if not t.is_punct and not t.is_space]
    if not 4 <= len(words) <= 45 or not text.endswith(".") or re.search(r"[„“”\"»«:;()…?!]|\s[–—-]\s", text):
        return None
    low = {t.lower_ for t in doc}
    if low & FIRST or low & SECOND or ANAPHORIC.search(text) or POINTS_BACK.search(text) or ADDRESS.search(text):
        return None
    if any(t.lower_ in ("sie", "ihnen", "ihr", "ihre", "ihren", "ihrem", "ihrer", "ihres") and t.text[0].isupper() for t in doc):
        return None
    if doc[0].tag_ in ("KON", "KOUS", "KOUI") or "Fin" in doc[0].morph.get("VerbForm") or doc[0].tag_ in ("VVIMP", "PTKANT", "ITJ"):
        return None
    subject = False
    for t in doc:
        if t.dep_ in ("sb", "ep"):
            if t.pos_ == "PRON" and t.tag_ not in ("PRELS", "PIS", "PWS") and t.lower_ != "man" and not (t.lower_ == "es" and t.head.lemma_ in IMPERSONAL):
                return None
            subject = True
    fin = [t for t in doc if "Fin" in t.morph.get("VerbForm") and t.pos_ in ("VERB", "AUX")]
    if not subject or not fin:
        return None
    if not _clauses_are_complete(doc, fin):
        return None
    edits = []                                              # (start, end, replacement)
    for t in fin:
        form, person = t.lower_, t.morph.get("Person")
        if person and person != ["3"]:
            return None
        participle = next((c for c in t.children if c.tag_ == "VVPP" and c.dep_ in ("oc", "pd")), None)
        if SUBJUNCTIVE.match(form):
            continue
        if form in AUX:
            new = AUX[form]
        elif form in PAST and participle is not None:
            new = PAST[form]
        elif form in PASSIVE and participle is not None:
            new = PASSIVE[form]
            edits.append((participle.idx + len(participle), participle.idx + len(participle), " worden"))
        elif t.tag_ == "VVFIN" and t.morph.get("Tense") == ["Pres"] and t.morph.get("Number") == ["Sing"]:
            new = _present(t)
        elif t.tag_ == "VVFIN" and t.morph.get("Tense") == ["Pres"] and t.morph.get("Number") == ["Plur"] and form == t.lemma_.lower():
            if _verb_final(t):
                new = f"{form} würden"
            elif "," not in text and not any(c.dep_ == "svp" for c in t.children) and len(fin) == 1:
                new = "würden"
                end = len(doc.text.rstrip()) - 1
                edits.append((end, end, f" {form}"))
            else:
                new = None
        else:
            new = None
        if not new:
            return None
        edits.append((t.idx, t.idx + len(t), new))
    if fin[0].i > 12 and not any(t.tag_ in SUBORD_TAGS for t in doc[:fin[0].i]):
        return None                                         # no early main verb: the parse is doubtful
    out = doc.text
    for a, b, new in sorted(edits, reverse=True):
        out = out[:a] + new + out[b:]
    out = out.strip()
    first = lower_first(doc[0].text, doc[0])
    return first + out[len(doc[0].text):]


def _capital(s):
    return s[0].upper() + s[1:]


def indirect_mode(speaker, core, nlp):
    """The excerpt in indirect speech: "X erklärt, …". Sentences that cannot be moved are left out and marked […]."""
    out, last = [], None
    for s in core:
        r = reported(nlp(s["text"].strip()))
        if not r:
            continue
        gap = last is not None and s["k"] != last + 1
        out.append((f"{speaker} erklärt, {r}" if not out else ("[…] " if gap else "") + _capital(r)))
        last = s["k"]
    return " ".join(out) or None


def _content(text):
    return {w for w in re.findall(r"[a-zäöüß]{4,}", text.lower())}


def summary_mode(speaker, core, nlp, words_of_speech):
    """The most important sentences (topic + information score), at most three and about 80 words, without
    repetition, in spoken order: in indirect speech where that is safe, else word for word."""
    if words_of_speech < MIN_SPEECH:
        return None
    chosen, words = [], 0
    for s in sorted(core, key=lambda s: -s["score"]):
        text, n = s["text"].strip(), len(s["text"].split())
        if len(chosen) == MAX_CORE or n > 45 or n < 4 or not text.endswith((".", "!")) or words + n > MAX_WORDS:
            continue
        if ADDRESS.search(text) or ANAPHORIC.search(text) or POINTS_BACK.search(text) or re.search(r"[„“”\"»«]|\bSie\b|\bIhnen\b|\bIhre?[nmrs]?\b", text):
            continue                                        # needs its context, or speaks to someone in the room
        c = _content(text)
        if any(len(c & _content(o["text"])) / max(1, min(len(c), len(_content(o["text"])))) > STOP_OVERLAP for o in chosen):
            continue
        chosen.append(s)
        words += n
    if len(chosen) < 2:
        return None
    out, quoted = [], None                                  # quoted = index of the last quoted sentence
    for s in sorted(chosen, key=lambda s: s["k"]):
        text = s["text"].strip()
        r = reported(nlp(text))
        if r:
            out.append(f"{speaker} erklärt, {r}" if not out else _capital(r))
            quoted = None
        elif quoted is not None:
            out[-1] = out[-1][:-1] + (" " if s["k"] == quoted + 1 else " […] ") + text + "“"
            quoted = s["k"]
        else:
            out.append((f"{speaker} erklärt: " if not out else "Wörtlich: ") + f"„{text}“")
            quoted = s["k"]
    return " ".join(out)
