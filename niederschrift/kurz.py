"""The Kurzfassung: one sentence on top of each speech, before the speaker's own words.

Form: the speaker line carries the attribution, so the sentence
has no subject and starts with a speech-act verb in the present tense: "fordert, dass …", "fragt, ob …",
"kritisiert, dass …". System B lets the LLM write it (reported_llm.py); this module is the rule version for
system C and the fallback when B's reply fails the rule check.

Rule (verb-final transformation):
  1. the speech's best-scored excerpt sentences (topic + information) are tried in order, at most 6;
  2. a declarative main clause becomes a dass-clause: the finite verb (and its separable particle) moves to the
     end; a question becomes "ob …" or keeps its wh-word. Sentences that need context are skipped: anaphoric
     openers ("Deshalb …", "Das …"), pronoun subjects, first or second person (whom "wir" means is unclear),
     commas, colons or dashes inside the main clause, more than one finite verb;
  3. the frame verb comes from the speech-act class of that sentence (frames.act), in the present tense; a plain
     statement gets the least used of "führt aus / erklärt / betont / stellt fest / …" within the item;
  4. if no sentence passes: "stellt den Gesetzentwurf vor." / "begründet den Antrag." for the opening speech of a
     bill or motion debate, else no Kurzfassung.
"""
import re

from . import frames

SUBORD_TAGS = {"KOUS", "KOUI", "PRELS", "PRELAT", "PWAV", "PWS", "PWAT"}   # STTS: subordinator, relative, wh-word
ADJ_SUFFIX = re.compile(r"(?:lich|ig|isch|weise|bar|los|sam|haft|mäßig|ens)$")
FIRST = {"ich", "wir", "mich", "mir", "uns", "unser", "unsere", "unseren", "unserem", "unserer", "unseres", "mein",
         "meine", "meinen", "meinem", "meiner", "meines"}
WH = {"warum", "wieso", "weshalb", "wie", "was", "wann", "wo", "wer", "welche", "welcher", "welches", "welchen",
      "woher", "wohin", "inwiefern", "wofür", "womit"}
ANAPHORIC = re.compile(r"^(?:und |aber )?(?:dann|deshalb|deswegen|daher|dazu|darum|das|dies|so|da|denn|doch|also|"
                       r"nun|ja|trotzdem|dennoch|außerdem)\b", re.I)
PRONOUN_SUBJ = {"er", "sie", "es", "das", "dies", "diese", "dieser", "dieses", "die", "der", "man"}
FRAME = {"fragte": "fragt", "forderte": "fordert", "warnte": "warnt", "kritisierte": "kritisiert",
         "begrüßte": "begrüßt", "kündigte an": "kündigt an", "antwortete": "antwortet"}
OPENING = {"stellte den Gesetzentwurf vor": "stellt den Gesetzentwurf vor.",
           "erläuterte den Gesetzentwurf": "stellt den Gesetzentwurf vor.",
           "begründete den Antrag": "begründet den Antrag.", "erläuterte den Antrag": "begründet den Antrag."}
MAX_WORDS = 30
STATE = ["führt aus", "erklärt", "betont", "stellt fest", "hebt hervor", "unterstreicht"]


def main_clause_span(doc):
    """Tokens of the main clause: up to the first comma that opens an extraposed subordinate clause."""
    toks = list(doc)
    for k, t in enumerate(toks):
        if t.text == "," and k + 1 < len(toks) and toks[k + 1].tag_ in SUBORD_TAGS:
            return toks[:k], toks[k:]
    return toks, []


def lower_first(text, tok):
    """After "dass" the first word is lower case unless it is a noun (adjective/adverb suffixes count against the
    tagger's noun reading of a capitalised sentence-initial word)."""
    if tok.tag_ in ("NN", "NE") and not ADJ_SUFFIX.search(text.lower()):
        return text
    return text[0].lower() + text[1:]


def to_verb_final(doc, intro):
    """Main clause -> subordinate clause after `intro` ("dass", "ob", or "wh" for a wh-question).
    Returns the clause without final punctuation, or None when the rule does not apply safely."""
    if doc[0].tag_ in ("KOUS", "KOUI", "KON") or ANAPHORIC.search(doc.text):
        return None
    main, rest = main_clause_span(doc)
    words = [t for t in main if not t.is_punct]
    fin = [t for t in words if "Fin" in t.morph.get("VerbForm") and t.pos_ in ("VERB", "AUX")]
    if len(fin) != 1 or len(words) > 28 or not words:
        return None
    v = fin[0]
    if any(t.text in (",", ":", ";", "–", "—", "-") for t in main[:-1]):
        return None
    if any(len(t.text) > 1 and t.text.endswith(".") for t in main[:-1]):
        return None
    if intro != "wh" and not any(t.dep_ == "sb" for t in words):
        return None
    if any(t.dep_ == "sb" and t.pos_ == "PRON" and t.lower_ in PRONOUN_SUBJ for t in words):
        return None
    if v.tag_ == "VMFIN" and not any(t.head.i == v.i and t.dep_ == "oc" for t in words):
        return None
    fin_ids = {w.i for w in words if "Fin" in w.morph.get("VerbForm")}
    for t in rest:      # the main clause goes on after the subordinate clause
        if t.head.i in fin_ids and t.dep_ in ("oc", "svp", "pd", "oa", "da", "sb") and t.tag_ not in SUBORD_TAGS \
                and not any(c.tag_ in SUBORD_TAGS for c in t.children):
            return None
    if {t.lower_ for t in doc} & FIRST:
        return None
    if any(t.lower_ in ("sie", "ihnen", "ihr", "ihre", "ihren", "ihrem", "ihrer", "ihres") and t.text[0].isupper()
           and t.i > 0 for t in doc) or re.search(r"\b(?:du|dich|dir|euch)\b", doc.text, re.I):
        return None
    part = next((c for c in v.children if c.dep_ == "svp"), None)
    skip = {v.i} | ({part.i} if part is not None else set())
    body = [t.text for t in words if t.i not in skip]
    if not body:
        return None
    form = (part.text.lower() if part is not None else "") + v.text.lower()
    if intro in ("dass", "ob"):
        first = next(t for t in words if t.i not in skip)
        body[0] = lower_first(body[0], first)
        clause = intro + " " + " ".join(body + [form])
    else:
        body[0] = body[0][0].lower() + body[0][1:]
        clause = " ".join(body + [form])
    if rest:
        clause += " " + "".join((" " if (t.whitespace_ or k) else "") + t.text for k, t in enumerate(rest)).strip()
    return re.sub(r"\s+([,.?!])", r"\1", clause).rstrip(".?! ")


def rule(digest, speech, nlp, used=None):
    """-> one sentence without subject ("fordert, dass …") or None. `used` counts the frame verbs of the item: a
    plain statement gets the least used verb of STATE, so the item does not read "führt aus" again and again."""
    used = used if used is not None else {}
    body = [k for p in digest["parts"] if p["type"] == "quote" for k in p.get("sent", [])]
    score = {s["k"]: s["topic"] + s["info"] for s in digest.get("scores", [])}
    for k in sorted(body, key=lambda k: -score.get(k, 0))[:6]:
        text = speech["sentences"][k]["text"]
        if re.search(r"\b(?:dafür|dazu|darüber|davon|daran|darauf|dagegen)\b", text, re.I):
            continue        # pronominal adverbs point back to the previous sentence
        doc = nlp(text)
        if text.rstrip().endswith("?"):
            wh = doc[0].lower_ in WH
            if not wh and not ("Fin" in doc[0].morph.get("VerbForm") and doc[0].pos_ in ("VERB", "AUX")):
                continue
            clause = to_verb_final(doc, "wh" if wh else "ob")
            frame = "fragt"
        else:
            clause = to_verb_final(doc, "dass")
            frame = FRAME.get(frames.act(text, "Rede", digest.get("role")))
            if frame in (None, "fragt"):
                frame = min(STATE, key=lambda v: (used.get(v, 0), STATE.index(v)))
        if clause and len(clause.split()) + len(frame.split()) <= MAX_WORDS:
            used[frame] = used.get(frame, 0) + 1
            return f"{frame}, {clause}."
    first = next((p.get("verb") for p in digest["parts"] if p["type"] == "quote"), None)
    return OPENING.get(first)
