"""Topic profile of an agenda item: a topic signature (Lin & Hovy 2000) built from the item's own texts.

A topic signature is a weighted list of lemmas that are typical of one topic. We build it in two steps:

  1. Seed lemmas from the item's topic text (title, Drucksache titles, Amtliches Protokoll text). They name the
     subject the Bundestag put on the agenda, so they get a high weight.
  2. Pseudo-relevance feedback: the lemmas of the item's whole debate are compared with a background corpus of
     other Bundestag speeches by Dunning's log-likelihood G² (Dunning 1993). The top K "key" lemmas are added.
     The debate tells us how the speakers actually talk about the topic ("Glasfaser", "Netzausbau" for a bill
     titled "Änderung des Telekommunikationsgesetzes").

Bundestag genre words ("Kollege", "Präsident", "Antrag", "heute") are removed from both lists: they occur evenly in
every debate (low Gries DP over agenda blocks, see scripts/build_background.py) and so say nothing about the topic.

The background statistics are read from data/resources/background_lemmas.tsv and bt_genre_words.tsv.
"""
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "data" / "resources"
MODEL = "de_core_news_md"

CONTENT_POS = {"NOUN", "PROPN", "ADJ", "VERB"}
ALPHA = re.compile(r"^[a-zäöüß][a-zäöüß\-]*[a-zäöüß]$")

# Function-like content words that spaCy tags as VERB/ADJ/NOUN but that carry no topic (modal and light verbs,
# vague nouns). Topic weight 0. The genre list from the background takes care of the Bundestag-specific rest.
LIGHT = set("""
sein haben werden können müssen sollen wollen dürfen mögen möchten lassen machen geben gehen kommen sagen stehen
sehen tun bringen nehmen finden halten stellen bleiben heißen meinen glauben wissen zeigen sprechen reden fragen
liegen setzen führen bekommen brauchen sorgen schaffen gelten
ganz groß klein neu alt gut schlecht viel wenig weit hoch richtig falsch wichtig klar einfach genau eigentlich
ander erst letzter nächst gleich weiter wirklich bisschen ehrlich besonders
ding art weise frage fall teil sache punkt ende jahr mal tag zeit seite stelle prozent euro beispiel thema
""".split())

# Words of the agenda text itself that describe the procedure, not the subject.
PROCEDURE = set("""
beratung erste zweite dritte entwurf entwurfs gesetz gesetzes gesetzentwurf antrag antrags drucksache bericht
berichts beschlussempfehlung ausschuss ausschusses fraktion fraktionen abgeordnete abgeordneter abgeordneten
bundesregierung eingebracht eingebrachten weiterer überweisungsbeschluss überweisungsvorschlag annahme ablehnung
aussprache vereinbart debatte vereinbarte stunde aktuelle verlangen anlage nummer buchstabe go-bt gemäß
mitberatend federführend änderung ändern unterrichtung stellungnahme beschluss beschließen findet statt
wahlperiode sitzung protokoll amtliches seite bundestag deutsch
""".split())


@lru_cache(maxsize=1)
def nlp():
    import spacy
    return spacy.load(MODEL)


def lemma_of(tok):
    """Normalised lemma of a spaCy token (lowercase), or '' if it is not a word."""
    lem = (tok.lemma_ or tok.text).lower()
    if lem == "--" or not ALPHA.match(lem):
        lem = tok.text.lower()
        if not ALPHA.match(lem):
            return ""
    return lem


def tok_record(tok):
    """Compact, JSON-serialisable token record: [lemma, pos, ent_type, text]."""
    return [lemma_of(tok), tok.pos_, tok.ent_type_, tok.text]


# ------------------------------------------------------------------ background statistics

@lru_cache(maxsize=1)
def background():
    """{"lemmas": {lemma: (freq, df, dp, range)}, "tokens": N, "speeches": N, "blocks": N}"""
    path = RES / "background_lemmas.tsv"
    out = {"lemmas": {}, "tokens": 1, "speeches": 1, "blocks": 1}
    if not path.exists():
        return out
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith("#"):
            m = re.match(r"#\s*(tokens|speeches|blocks)\s*=\s*(\d+)", ln)
            if m:
                out[m.group(1)] = int(m.group(2))
            continue
        p = ln.split("\t")
        if p[0] == "lemma" or len(p) < 6:
            continue
        out["lemmas"][p[0]] = (int(p[2]), int(p[3]), float(p[4]), float(p[5]))
    return out


@lru_cache(maxsize=1)
def genre_words():
    """Bundestag genre lemmas (from the background) plus the Münster genre stoplist (kind=content)."""
    out = set()
    path = RES / "bt_genre_words.tsv"
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            if ln.startswith("#") or ln.startswith("lemma\t"):
                continue
            out.add(ln.split("\t")[0])
    return frozenset(out)


PRUNED_FREQ = 2          # background frequency assumed for lemmas pruned from the TSV (they had freq < 5)


def idf(lemma):
    bg = background()
    df = bg["lemmas"].get(lemma, (0, 1, 1.0, 0.0))[1]
    return math.log((bg["speeches"] + 1) / (df + 1)) + 1.0


def g2(a, b, c, d):
    """Dunning log-likelihood for a word with freq a in a corpus of c tokens vs freq b in a reference of d tokens."""
    e1 = c * (a + b) / (c + d)
    e2 = d * (a + b) / (c + d)
    s = 0.0
    if a > 0:
        s += a * math.log(a / e1)
    if b > 0:
        s += b * math.log(b / e2)
    return 2 * s


# ------------------------------------------------------------------ topic signature

def is_topic_token(rec, genre, stop_names=frozenset()):
    lem, pos = rec[0], rec[1]
    if not lem or pos not in CONTENT_POS or len(lem) < 3:
        return False
    if lem in LIGHT or lem in genre or lem in stop_names:
        return False
    if rec[2] == "PER":
        return False
    return True


_AP_NOISE = re.compile(r"^\s*(?:Überweisungsbeschluss|Überweisungsvorschlag|A\. f\.|.*ausschuss\b|.*Ausschuss \(f\)|"
                       r"Haushaltsausschuss|Drucksache|Annahme|Ablehnung|Erledigt|Kenntnisnahme|Federführung|"
                       r"\d+\. Wahlperiode|Seite \d|.*Sitzung des Deutschen Bundestages|Es findet eine Aussprache|"
                       r"Namentliche|abgegebene|ja:|nein:|enthalten:|Berichterstattung:|.*\(f\)\s*$)", re.I)


def clean_topic_text(text):
    """Remove procedure lines (referrals, committees, votes, page heads) and the lists of motion signatories
    from an agenda/AP text; keep the subject."""
    lines = []
    for ln in text.splitlines():
        if _AP_NOISE.match(ln):
            continue
        lines.append(ln)
    t = " ".join(lines)
    # "Beratung des Antrags der Abgeordneten A, B, C, weiterer Abgeordneter und der Fraktion der AfD <Titel>"
    t = re.sub(r"der Abgeordneten\s.*?(?:weiterer Abgeordneter\s+)?und der Fraktion", "der Fraktion", t)
    t = re.sub(r"\b\d+/\d+\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def signature(topic_text, debate_records, cfg, stop_names=frozenset(), extra_seed_records=None):
    """Topic signature {lemma: weight} of one item.

    topic_text      the item's agenda/AP text (seed)
    debate_records  token records of all speech sentences of the item (pseudo-relevance feedback)
    cfg             weights: seed_weight, prf_k, prf_min_g2
    """
    genre = genre_words()
    bg = background()
    sig = Counter()
    seeds = []
    if topic_text:
        doc = nlp()(clean_topic_text(topic_text)[:5000])
        seeds = [tok_record(t) for t in doc]
    if extra_seed_records:
        seeds = seeds + list(extra_seed_records)
    for rec in seeds:
        if is_topic_token(rec, genre, stop_names) and rec[0] not in PROCEDURE:
            sig[rec[0]] = max(sig[rec[0]], cfg["seed_weight"])
    # pseudo-relevance feedback by G² keyness against the background
    toks = [r for r in debate_records if r[0]]
    counts = Counter(r[0] for r in toks if is_topic_token(r, genre, stop_names))
    c = max(1, len(toks))
    d = bg["tokens"]
    key = []
    for lem, a in counts.items():
        if a < cfg["prf_min_freq"]:
            continue
        b = bg["lemmas"].get(lem, (PRUNED_FREQ,))[0]
        if a / c <= b / d:
            continue
        g = g2(a, b, c, d)
        if g >= cfg["prf_min_g2"]:
            key.append((g, lem))
    key.sort(reverse=True)
    key = key[: cfg["prf_k"]]
    if key:
        top = key[0][0]
        for g, lem in key:
            w = cfg["prf_weight"] * (g / top) ** 0.5
            sig[lem] = max(sig[lem], w)
    return dict(sig)


def compound_parts(sig, min_len=5):
    """Signature lemmas long enough to be matched inside compounds ('Glasfaser' in 'Glasfaserausbau')."""
    return sorted((l for l in sig if len(l) >= min_len and "-" not in l), key=len, reverse=True)


def topic_score(records, sig, parts, cfg, genre=None, stop_names=frozenset()):
    """Topic relatedness of one sentence: summed signature weight of its content tokens, normalised by the square
    root of the number of content tokens (long sentences are not rewarded just for being long).
    Compounds: a token that contains a long signature lemma gets half of its weight."""
    genre = genre_words() if genre is None else genre
    content = [r for r in records if r[0] and r[1] in CONTENT_POS and len(r[0]) >= 3 and r[0] not in LIGHT]
    if not content:
        return 0.0, []
    total, hits = 0.0, []
    for r in content:
        lem = r[0]
        if lem in stop_names or r[2] == "PER":
            continue
        w = sig.get(lem, 0.0)
        if not w and cfg.get("compound", True) and len(lem) >= 8 and lem not in genre:
            for p in parts:
                if p != lem and p in lem:
                    w = 0.5 * sig[p]
                    break
        if w:
            total += w
            hits.append(lem)
    return total / math.sqrt(len(content)), hits
