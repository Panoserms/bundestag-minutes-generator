"""Rule-based segmentation of a Plenarprotokoll transcript into agenda items, plus phases inside an item.

    from niederschrift.segment import segment, score
    seg = segment(sitting["units"], sitting["agenda"], sitting["amtprot"])
    score(seg["units"], sitting["units"], sitting["gold"], sid)      # dev only

The transcript has no agenda markup (sources.py removes it), so the item boundaries are found from the language of
the chair. Developed on 53 earlier dev sittings (98% unit accuracy) and the dev sittings in data/split.json.
Linguistic background for every rule: README.md (How it works) and docs/RULES.md.

Rules
  S1 Topic continuity. The turn (paragraph) is the unit; only chair turns can shift the topic. Every other unit
     inherits the current item (Grosz & Sidner 1986: a segment lasts until it is explicitly closed or shifted).
  S2 Call cue. A discourse marker that opens a segment: "Ich rufe … auf", "Wir kommen zu Tagesordnungspunkt 5",
     "Damit kommen wir jetzt zum Zusatzpunkt 4" (verb second, subject after the verb), a paragraph that starts
     with the item number. The items named in the call become the current item set (joint calls). Items in the
     scope of "nach / vor / im Anschluss an" are announcements of order, not calls.
  S3 Return cue. "Ich komme zurück zu Tagesordnungspunkt 14" is an embedded segment (push): the chair turn goes to
     the named item, the next speech pops back to the interrupted item. A return that resumes the debate
     ("Wir setzen die Aussprache zu TOP 9 fort") replaces the current item instead.
  S4 Reference by Drucksache. A Drucksache number refers to one item: calls without a number are resolved by it,
     and a vote paragraph that names a Drucksache of another item is filed under that item.
  S5 Lexical cohesion. A call without number or Drucksache goes to the not-yet-handled item with the most similar
     title (tf-idf cosine >= 0.2, genre words removed; Hearst 1997).
  S2b Cataphoric colon. A chair turn that ends in "… Tagesordnungspunkt 2:" is a call, whatever the verb
     ("Wir … steigen jetzt ein in Tagesordnungspunkt 2:"): the colon points forward to the item.
  S6 Announcements before the first call (abgesetzt, vorgezogen, erweitert, …) are kept as notes, not topics.
  S6b Items in a sentence whose predicate withdraws them ("Tagesordnungspunkt 8 … werden abgesetzt", "vertagt",
     "von der Tagesordnung abgesetzt") are not called, even when the sentence opens with the item number.
  Sub-item headers ("Tagesordnungspunkt 43a:") keep the item and record the letter.
Phases (inside an item): Aufruf, Aussprache-Rahmen, Rede, Zwischenfrage, Kurzintervention, Abstimmung, Ergebnis,
Überweisung, Leitung.
"""
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------------ item references

ITEM_NOUN = r"(?:Tagesordnungspunkt|Zusatzpunkt)(?:e|en|es|s)?"
ITEM_LIST = re.compile(r"(Tagesordnungspunkt|Zusatzpunkt)(?:e|en|es|s)?\s+"
                       r"((?:\d+(?:\s?[a-z]\b)?(?:\s*(?:,|und|bis|sowie)\s*)?)+)")
DS = re.compile(r"\b(?:21|20)/\d{2,5}\b")


def named_items(text):
    """Every agenda item a text names, in order of mention, without duplicates.

    'Tagesordnungspunkte 7a und 7b sowie Zusatzpunkt 2' -> ['TOP 7', 'ZP 2'];
    'Tagesordnungspunkte 13 bis 15' -> ['TOP 13', 'TOP 14', 'TOP 15'] (a range of bare numbers is expanded;
    '42a bis 42f' is a range of letters inside one item)."""
    out = []
    for m in ITEM_LIST.finditer(text):
        kind = "TOP " if m.group(1) == "Tagesordnungspunkt" else "ZP "
        toks = re.findall(r"(\d+)(\s?[a-z]\b)?|(bis)", m.group(2))
        nums, prev_bare, pending_range = [], None, False
        for num, letter, bis in toks:
            if bis:
                pending_range = True
                continue
            n = int(num)
            if pending_range and prev_bare is not None and not letter and 0 < n - prev_bare <= 20:
                nums += list(range(prev_bare + 1, n + 1))
            else:
                nums.append(n)
            prev_bare = n if not letter else None
            pending_range = False
        for n in nums:
            iid = kind + str(n)
            if iid not in out:
                out.append(iid)
    return out


def sub_letters(text):
    """Letters of sub-items named in a text: 'Tagesordnungspunkte 8a, 8d und 8f' -> ['a', 'd', 'f']."""
    return re.findall(r"\b\d+\s?([a-z])\b", " ".join(m.group(0) for m in ITEM_LIST.finditer(text)))


# ------------------------------------------------------------------ cue phrases (S2, S3, S6)

# Adverbs and pronouns that can stand between the finite verb and its complement (the German "Mittelfeld"), so the
# same pattern covers subject-first order ("Wir kommen jetzt zu …") and verb-second order after a fronted adverb
# ("Damit kommen wir jetzt zu …", "Jetzt rufe ich … auf").
MF = r"(?:ich|wir|jetzt|nun|nunmehr|damit|dann|somit|also|zunächst|gleich|sogleich|noch|endlich|abschließend|" \
     r"heute|hier|direkt|unmittelbar|sofort|weiter|wieder|kurz|als\s+N[äa]chstes|als\s+Letztes|noch\s+einmal|" \
     r"noch\s+mal)"

CALL_PATTERNS = {
    # separable verb "aufrufen" in a main clause: the particle "auf" closes the clause (right bracket), the item
    # stands before or after it: "Ich rufe den TOP 5 auf", "Ich rufe auf die Tagesordnungspunkte 7a und 7b"
    "rufe-auf": r"\b(?:rufe|rufen)\b[^.:;?!]{0,160}?\bauf\b",
    # verb-final clause or infinitive: "während ich den Zusatzpunkt 4 aufrufe", "möchte ich … aufrufen"
    "aufrufen": r"\baufrufen?\b",
    # "kommen zu": subject before (V1 study) or after (V2) the finite verb, adverbs anywhere in the Mittelfeld
    "kommen-zu": rf"\b(?:komme|kommen)\s+(?:{MF}\s+){{0,4}}zu(?:m|r)?\s+(?:dem\s+|den\s+)?{ITEM_NOUN}\s+\d",
    # other verbs of motion through the agenda: "Wir gehen weiter zu TOP 14", "leite über zu"
    "gehen-zu": rf"\b(?:gehen|geht|leite|leiten)\s+(?:{MF}\s+){{0,4}}(?:über\s+)?zu(?:m|r)?\s+(?:dem\s+|den\s+)?"
                rf"{ITEM_NOUN}\s+\d",
    "ueberleiten": r"\büberleiten\s+zu",
    "aufmerksamkeit": rf"\bAufmerksamkeit\s+für\s+(?:den\s+|die\s+)?{ITEM_NOUN}\s+\d",
    # the item number opens the paragraph ("Tagesordnungspunkt 43a:", "Zusatzpunkt 3. Interfraktionell …")
    "header": rf"^\s*{ITEM_NOUN}\s+\d+",
    # S2b cataphoric colon: a chair turn that ENDS in "… Tagesordnungspunkt 2:" announces the item whatever the verb
    # ("steigen jetzt ein in Tagesordnungspunkt 2:"); the colon points forward to the item's title
    "colon": rf"\b{ITEM_NOUN}\s+\d+\s?[a-z]?\s*:\s*$",
}
CALL = re.compile("|".join(f"(?:{p})" for p in CALL_PATTERNS.values()), re.I | re.M)

# calls without an item number: resolved by Drucksache (S4) or by lexical cohesion (S5)
# ("Wir kommen zur Abstimmung über den Antrag …" is a vote cue, not a call)
CALL_NO_NUM = re.compile(rf"^\s*(?:(?:Wir|Damit|Dann|Jetzt|Nun|So)\s+)?(?:kommen|rufe)\s+"
                         rf"(?!(?:{MF}\s+){{0,3}}(?:zur\s+Abstimmung|zu\s+den\s+Abstimmungen|zur\s+Überweisung))"
                         rf"(?:{MF}\s+){{0,3}}(?:zu\w*\s+|auf\s+)?[^.]{{0,80}}(Beratung|Antrag|Gesetzentwurf|Drucksache|Aktuellen?\s+Stunde|"
                         r"Fragestunde|Befragung|Wahl\b|Vereinbarten?\s+Debatte|Regierungserklärung|"
                         r"Beschlussempfehlung)", re.I)

# S3: "Ich komme zurück zu Tagesordnungspunkt 14", "Damit komme ich zurück zu …" (V2), "Zurück zu Zusatzpunkt 6".
# The complement of "zurück zu" must be the item itself: "komme ich zurück zum gestrigen Tag" is no return.
RETURN = re.compile(rf"(?:\b(?:komme|kommen)\s+(?:{MF}\s+){{0,4}}|^\s*)zurück\s+zu(?:m|r)?\s+"
                    rf"(?:dem\s+|den\s+|unserem\s+|unseren\s+)?(?={ITEM_NOUN}\s+\d)", re.I | re.M)
# a return that resumes the debate: the item number is named and the debate is continued
RESUME_NUM = re.compile(rf"\b(?:setzen|fahren)\s+(?:{MF}\s+){{0,3}}(?:die|unsere|der|mit\s+der)?\s*"
                        r"(?:\w*[Bb]eratung(?:en)?|Aussprache|Debatte)\b[^.]{0,120}\bfort\b", re.I)
# resumption without a number, after a temporary return: pop the focus stack
# (turn allocation ends the embedded segment: "Das Wort hat …", "Als Nächstes hat das Wort …", "Der nächste Redner …")
RESUME = re.compile(r"Aussprache|erteile\s+(?:\w+\s+){0,3}das\s+Wort|\bhat\s+(?:\w+\s+){0,2}das\s+Wort|"
                    r"Das\s+Wort\s+hat|(?:nächste|letzte)[nr]?\s+Red(?:e|ner|nerin)\b|Als\s+Nächste|"
                    r"fahren\s+(?:in\s+der\s+(?:Debatte|Aussprache)\s+)?fort|setzen\s+(?:\w+\s+){0,3}fort", re.I)
# items in the scope of these prepositions are not called, only placed ("TOP 9 wird nach TOP 10 aufgerufen")
NOT_CALLED = re.compile(rf"\b(?:nach|vor|im\s+Anschluss\s+an|zusammen\s+mit\s+den\s+bereits)\s+(?:dem\s+|den\s+)?"
                        rf"{ITEM_NOUN}\s+\d+(?:\s?[a-z]\b)?(?:\s*(?:,|und|bis|sowie)\s*\d+(?:\s?[a-z]\b)?)*", re.I)
SUB_HEADER = re.compile(r"^\s*(?:Tagesordnungspunkt|Zusatzpunkt)\s+\d+\s?([a-z])\b")
ANNOUNCE = re.compile(r"abgesetzt|vorgezogen|erweitert|Zusatzpunkteliste|getauscht|verschoben|"
                      r"nach\s+(?:Tagesordnungspunkt|Zusatzpunkt)\s+\d+\s+aufgerufen|abgewichen|An\s+der\s+Stelle", re.I)
VOTE_WORDS = re.compile(r"Abstimmung|Wer\s+stimmt|Beschlussempfehlung|angenommen|abgelehnt|Überweisung", re.I)
CHAIR_START = re.compile(r"^\s*(?:Ich\s+rufe|(?:Nun|Jetzt|Damit|Dann)\s+(?:rufe|komme|kommen)|Wir\s+kommen\s+zu)",
                         re.I)


def is_call(text):
    """True if the text contains a call cue (S2). It opens an item only if it also names an item."""
    return bool(CALL.search(text))


def call_cue(text):
    """Name of the first call pattern that matches, or None (for diagnostics and tests)."""
    for name, p in CALL_PATTERNS.items():
        if re.search(p, text, re.I | re.M):
            return name
    return None


WITHDRAWN = re.compile(r"\babgesetzt\b|\bvertagt\b|von\s+der\s+Tagesordnung", re.I)


def not_called(text):
    """Items named only as reference points of an order announcement ('nach Tagesordnungspunkt 10'), and items that
    are the subject of a withdrawal ('Tagesordnungspunkt 8 … wird abgesetzt': S6b, the predicate removes them)."""
    out = set()
    for m in NOT_CALLED.finditer(text):
        span = re.sub(r"^\D*?(?=Tagesordnungs|Zusatz)", "", m.group(0))
        out.update(named_items(span))
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if WITHDRAWN.search(sent):
            out.update(named_items(sent))
    return out


def call_targets(text):
    """Items a call opens: all named items minus the order-announcement scope (S2)."""
    nc = not_called(text)
    return [x for x in named_items(text) if x not in nc]


def is_return(text):
    return bool(RETURN.search(text))


def return_targets(text):
    """Items named as the complement of a return cue ('… zurück zu den Tagesordnungspunkten 8a, 8d und 8f')."""
    m = RETURN.search(text)
    if not m:
        return []
    clause = re.split(r"[.;:!?](?:\s|$)", text[m.end():], maxsplit=1)[0]
    return named_items(clause)


# ------------------------------------------------------------------ phases (sub-topics inside an item)

PHASES = [
    ("Ergebnis", re.compile(r"(ist|sind)\s+(damit\s+|somit\s+)?(\w+\s+){0,6}(angenommen|abgelehnt)|"
                            r"Ergebnis\s+der\s+namentlichen|abgegebene\s+Stimm|mit\s+den\s+Stimmen\s+der", re.I)),
    ("Abstimmung", re.compile(r"Wer\s+stimmt|Gegenprobe|Enthaltungen\?|Wir\s+kommen\s+zur\s+Abstimmung|"
                              r"stimmen\s+(wir\s+)?ab|namentliche\s+Abstimmung|Abstimmung\s+über|Schlussabstimmung|"
                              r"dritten?\s+Beratung|zweiten?\s+Beratung|zustimmen\s+wollen|Wer\s+ist\s+(?:da)?(?:für|gegen)",
                              re.I)),
    ("Überweisung", re.compile(r"Überweisung|überwiesen|aufgeführten\s+Ausschüsse", re.I)),
    ("Aufruf", re.compile(r"\brufe\b|\baufrufen\b|Aussprache\s+(von|mit)\s+\d+|vereinbart", re.I)),
    ("Aussprache-Rahmen", re.compile(r"eröffne\s+die\s+Aussprache|schließe\s+die\s+Aussprache|"
                                     r"keine\s+weiteren\s+Wortmeldungen|Das\s+Wort\s+hat|erteile\s+.*Wort", re.I)),
    ("Zwischenfrage", re.compile(r"Zwischenfrage|Zwischenbemerkung", re.I)),
    ("Kurzintervention", re.compile(r"Kurzintervention", re.I)),
]


def phase_of(u):
    if u["role"] != "chair":
        return "Rede"
    for name, rx in PHASES:
        if rx.search(u["text"]):
            return name
    return "Leitung"


# ------------------------------------------------------------------ lexical cohesion (S5)

def _genre_words():
    words = set()
    f = ROOT / "data" / "resources" / "genre_stoplist_muenster.txt"
    if f.exists():
        words = {l.split("\t")[0] for l in f.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")}
    return words | {"beratung", "antrags", "abgeordneten", "fraktion", "drucksache", "entwurfs", "gesetzes",
                    "bundesregierung", "eingebrachten", "beschlussempfehlung", "berichts", "ausschusses", "erste",
                    "zweite", "dritte", "weiterer"}


GENRE = _genre_words()


def tokenize(t):
    return [w for w in re.findall(r"[a-zäöüß]{3,}", t.lower()) if w not in GENRE]


class TfIdf:
    def __init__(self, docs):
        self.df = Counter()
        for d in docs:
            self.df.update(set(tokenize(d)))
        self.n = len(docs)

    def vec(self, text):
        tf = Counter(tokenize(text))
        return {w: c * math.log((self.n + 1) / (self.df.get(w, 0) + 1)) for w, c in tf.items()}

    @staticmethod
    def cos(a, b):
        num = sum(v * b.get(w, 0) for w, v in a.items())
        den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
        return num / den if den else 0.0


def item_catalogue(agenda, ap):
    """All known items with text and Drucksachen: printed agenda (final ids) first, then items only in the AP."""
    cat = {}
    for it in agenda:
        if it["id"].startswith("TO-"):
            continue                                  # printed but not handled under that number this day
        c = cat.setdefault(it["id"], {"title": it["title"], "ds": set(), "planned": True, "text": ""})
        c["ds"] |= set(it.get("drucksachen", []))
        c["text"] += "\n" + it["raw"]
    for it in ap:
        c = cat.setdefault(it["id"], {"title": "", "ds": set(), "planned": False, "text": ""})
        c["ds"] |= set(it["drucksachen"])
        c["text"] += "\n" + it["text"]
        if not c["title"]:
            c["title"] = re.sub(r"\s+", " ", it["text"][:300])
    ds_map = {}
    for iid, c in cat.items():
        for d in c["ds"]:
            ds_map.setdefault(d, set()).add(iid)
    return cat, ds_map


# ------------------------------------------------------------------ the segmenter

def segment(units, agenda, ap):
    """-> {"units": [{i, items, rule, phase, sub}], "events": [...], "announcements": [...], "handled_order": [...]}

    items = the item ids a unit is filed under (["ERÖFFNUNG"] before the first call)."""
    cat, ds_map = item_catalogue(agenda, ap)
    tfidf = TfIdf([c["text"] for c in cat.values()] or [""])
    vecs = {iid: tfidf.vec(c["title"] + " " + c["text"][:800]) for iid, c in cat.items()}
    current, stack, handled = ["ERÖFFNUNG"], None, []
    out, events, announcements = [], [], []
    started = False

    def mark_handled(items):
        for x in items:
            if x not in handled:
                handled.append(x)

    for k, u in enumerate(units):
        t = u["text"]
        rule, assigned, sub = "S1 inherit", list(current), None
        named = set(named_items(t))
        # only the chair calls items; a short paragraph that opens with a call and names an item is a chair act
        # even when the XML attributes it to someone else (markup errors: missing <name>, ",Vizepräsident …")
        chair_like = u["role"] == "chair" or (CHAIR_START.match(t) and named and len(t) < 400)
        if chair_like:
            targets = call_targets(t)
            sm = SUB_HEADER.match(t)
            if RESUME_NUM.search(t) and named and not CALL.search(t[:RESUME_NUM.search(t).start()]):
                current, stack = sorted(named), None                     # S3: the debate on the named item goes on
                assigned, rule = list(current), "S3 resume (number)"
                events.append({"i": u["i"], "type": "resume", "items": list(current)})
            elif return_targets(t):
                target = return_targets(t)
                target = [x for x in target if x in cat] or target
                after = t[RETURN.search(t).end():]
                if stack is not None and set(target) <= set(stack):
                    current, stack, rule = list(stack), None, "S3 return to the interrupted item"   # pop
                elif RESUME.search(after) and re.search(r"Aussprache|Debatte|Beratung", after[:200]):
                    current, stack, rule = target, None, "S3 return+resume"
                else:                                                     # S3: embedded segment (push)
                    stack = stack or list(current)
                    current, rule = target, "S3 return (temporary)"
                assigned = list(current)
                events.append({"i": u["i"], "type": "return", "items": target})
            elif (CALL.search(t) and targets) or (sm and not (named & set(current))):
                target = targets or sorted(named)
                if sm and set(target) & set(current):
                    sub, rule = sm.group(1), "S2 sub-item header"
                elif re.search(r"Abstimmung", t) and current != ["ERÖFFNUNG"] \
                        and (stack is not None or not (set(target) & set(current))) \
                        and not re.search(r"\bAussprache\b|\bWort\b", t):
                    # a call that only opens votes on another item inside a running debate (or inside a temporary
                    # return): temporary, the interrupted debate is restored at the next speech (S3 push)
                    stack = stack or list(current)
                    current, rule = target, "S2 call (vote only, temporary)"
                    events.append({"i": u["i"], "type": "vote-call", "items": target, "text": t[:200]})
                else:
                    current, stack = target, None
                    rule = "S2 call (number)"
                    started = True
                    mark_handled(target)
                    events.append({"i": u["i"], "type": "call", "items": target, "text": t[:200],
                                   "cue": call_cue(t)})
                if sm and not sub:
                    sub = sm.group(1)
                assigned = list(current)
            elif sm and named & set(current):
                sub, rule = sm.group(1), "S2 sub-item header"
            elif CALL_NO_NUM.search(t) and started:
                ds = set(DS.findall(" ".join(x["text"] for x in units[k:k + 4] if x["role"] == "chair")))
                cand = set().union(*[ds_map.get(d, set()) for d in ds]) if ds else set()
                if cand:
                    current, rule = sorted(cand), "S4 call by Drucksache"
                else:
                    ctx = t + " " + " ".join(x["text"] for x in units[k + 1:k + 6])[:2000]
                    v = tfidf.vec(ctx)
                    best = max(((tfidf.cos(v, vecs[i]), i) for i in cat if i not in handled), default=(0, None))
                    if best[1] and best[0] >= 0.2:
                        current, rule = [best[1]], f"S5 lexical call ({best[0]:.2f})"
                if rule.startswith(("S4", "S5")):
                    stack = None
                    mark_handled(current)
                    events.append({"i": u["i"], "type": "call", "items": list(current), "text": t[:200],
                                   "cue": "no-number"})
                    assigned = list(current)
            elif VOTE_WORDS.search(t):
                # S4 for decisions: a vote paragraph naming a Drucksache of exactly one other item
                ds = set(DS.findall(t))
                cand = set().union(*[ds_map.get(d, set()) for d in ds]) if ds else set()
                if cand and not (cand & set(current)) and len(cand) == 1:
                    assigned, rule = sorted(cand), "S4 decision filed under its item"
            if stack is not None and rule == "S1 inherit" and RESUME.search(t) and (not named or named <= set(stack)):
                current, stack = stack, None             # "Wir setzen die Aussprache fort. Der nächste Redner …"
                assigned, rule = list(current), "S3 restore (resume cue)"
            elif stack is not None and rule == "S1 inherit":
                rule = "S3 embedded (temporary)"         # chair turns inside the embedded segment (vote routine)
            if not started and ANNOUNCE.search(t):
                announcements.append({"i": u["i"], "text": t})
        elif stack is not None:                           # a speech after a temporary return: pop the stack
            current, stack = stack, None
            assigned, rule = list(current), "S3 restore after return"
        out.append({"i": u["i"], "items": assigned, "rule": rule, "phase": phase_of(u), "sub": sub})
    return {"units": out, "events": events, "announcements": announcements, "handled_order": handled}


# ------------------------------------------------------------------ evaluation against the XML blocks (dev only)

# Gold repairs, checked by hand in the XML (dev sittings only). sources.py takes a block's items from its top-id
# and the agenda paragraphs; that misses (a) a top-id without number and (b) the partners of a joint call, which the
# XML files as ONE block under the first item. Each fix below was read off the block's opening paragraph and the
# Amtliches Protokoll. score(..., fix_gold=False) gives the raw numbers.
GOLD_FIX = {
    ("21099", 9): ["TOP 13"],                  # top-id "Tagesordnungspunkt"; "Ich rufe auf Tagesordnungspunkt 13:"
    ("21099", 0): ["TOP 7", "ZP 2"],           # "Tagesordnungspunkte 7a und 7b sowie Zusatzpunkt 2"
    ("21099", 1): ["ZP 3", "TOP 8"],           # "Zusatzpunkt 3 sowie die Tagesordnungspunkte 8a bis 8g"
    ("21099", 4): ["TOP 42", "ZP 4"],          # "Tagesordnungspunkte 42a bis 42f … sowie die Zusatzpunkte 4a bis 4c"
    ("21099", 15): ["TOP 19", "ZP 6"],         # "Tagesordnungspunkte 19a bis 19d sowie Zusatzpunkt 6"
    ("21099", 26): ["TOP 29", "ZP 8"],         # "Tagesordnungspunkt 29 sowie den Zusatzpunkt 8"
    ("21100", 2): ["TOP 37", "ZP 9"],          # "Tagesordnungspunkt 37 sowie Zusatzpunkt 9"
    ("21100", 5): ["ZP 10", "ZP 11"],          # top-id "Zusatzpunkt 10 und 11"
}
MOVED = ("S4 decision", "S3 return (temporary)", "S2 call (vote only", "S3 embedded")


def _bounds(labels):
    return [1 if i and labels[i] != labels[i - 1] else 0 for i in range(len(labels))]


def pk_windowdiff(ref, hyp, k):
    """Pk (Beeferman et al. 1999) and WindowDiff (Pevzner & Hearst 2002) for two label sequences."""
    rb, hb = _bounds(ref), _bounds(hyp)
    m = len(ref) - k
    if m <= 0:
        return 0.0, 0.0
    pk = wd = 0
    for i in range(m):
        r, h = sum(rb[i + 1:i + k + 1]), sum(hb[i + 1:i + k + 1])
        pk += (r > 0) != (h > 0)
        wd += r != h
    return pk / m, wd / m


def score(seg_units, units, gold, sid=None, n_errors=10, fix_gold=True):
    """Unit accuracy (predicted item set overlaps the gold block's items), by role, Pk and WindowDiff.

    Units that a rule deliberately files under another item than the block they were spoken in (S4 decisions,
    S3 temporary returns, vote-only calls) and that miss the block are counted as 'decisions_filed_elsewhere' and
    left out: the XML files a vote where it was spoken, the Niederschrift under its own item."""
    ub = gold["unit_block"]
    fixes = GOLD_FIX if fix_gold else {}
    blocks = {b["block"]: set(fixes.get((sid, b["block"]), b["items"])) for b in gold["blocks"]}
    role = {u["i"]: ("chair" if u["role"] == "chair" else "speech") for u in units}
    ok = n = elsewhere = strict_ok = 0
    by_role, errors, ref, hyp = {}, [], [], []
    for s in seg_units:
        b = ub.get(s["i"])
        g = blocks.get(b, set()) if b is not None else {"ERÖFFNUNG"}
        hit = bool(set(s["items"]) & g)
        strict_ok += hit
        if s["rule"].startswith(MOVED) and not hit:
            elsewhere += 1
            continue
        n += 1
        ok += hit
        r = by_role.setdefault(role[s["i"]], [0, 0])
        r[0] += hit
        r[1] += 1
        if not hit and len(errors) < n_errors:
            errors.append({"i": s["i"], "pred": s["items"], "gold": sorted(g), "rule": s["rule"]})
        ref.append(b)
        hyp.append(tuple(s["items"]))
    lens, cur, ln = [], object(), 0
    for x in ref:
        if x != cur and ln:
            lens.append(ln)
            ln = 0
        cur = x
        ln += 1
    lens.append(ln)
    k = max(2, int(round(sum(lens) / len(lens) / 2)))
    pk, wd = pk_windowdiff(ref, hyp, k)
    return {"sid": sid, "units": n, "accuracy": round(ok / n, 4) if n else None,
            "strict_accuracy": round(strict_ok / len(seg_units), 4) if seg_units else None,
            "chair_acc": round(by_role["chair"][0] / by_role["chair"][1], 4) if "chair" in by_role else None,
            "speech_acc": round(by_role["speech"][0] / by_role["speech"][1], 4) if "speech" in by_role else None,
            "Pk": round(pk, 4), "WindowDiff": round(wd, 4), "k": k, "decisions_filed_elsewhere": elsewhere,
            "errors": errors}
