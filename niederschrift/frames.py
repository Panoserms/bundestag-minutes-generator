"""Speech-act frames for the quotes: a reporting verb chosen by rule, and the lead ("X (Fraktion) forderte:").

Speech-act classes (Searle 1976) recognised from surface cues of the quoted sentences:
  question -> fragte; directive/deontic -> forderte; warning -> warnte; negative evaluation of others ->
  kritisierte; positive evaluation -> begrüßte; commissive ("wir werden") -> kündigte an; a government member
  opening a bill debate -> stellte den Gesetzentwurf vor; answer in question time -> antwortete;
  default -> erklärte / betonte.
The palette follows the discussion verbs of the Münster minutes (data/resources/muenster_section_verbs.tsv,
section "Verlauf/Diskussion": begründen, einbringen, vorstellen, erläutern, beantworten, erklären, hinweisen).
Variation: the same verb never twice in a row within an item (the next verb of the class ladder is used).
"""
import re

LADDER = {
    "fragte": ["fragte", "wollte wissen", "erkundigte sich"],
    "forderte": ["forderte", "verlangte", "mahnte"],
    "warnte": ["warnte", "mahnte", "gab zu bedenken"],
    "kritisierte": ["kritisierte", "bemängelte", "beanstandete"],
    "begrüßte": ["begrüßte", "lobte", "würdigte"],
    "kündigte an": ["kündigte an", "sagte zu", "stellte in Aussicht"],
    "stellte den Gesetzentwurf vor": ["stellte den Gesetzentwurf vor", "erläuterte den Gesetzentwurf"],
    "begründete den Antrag": ["begründete den Antrag", "erläuterte den Antrag"],
    "antwortete": ["antwortete", "entgegnete", "erwiderte"],
    "erklärte": ["erklärte", "betonte", "sagte", "hob hervor", "stellte fest", "unterstrich", "führte aus"],
}

Q = re.compile(r"\?\s*$")
DEMAND = re.compile(r"\b(?:fordern|fordere|fordert|müssen|muss|brauchen|braucht|sollten|endlich)\b|!\s*$", re.I)
WARN = re.compile(r"\b(?:warne|warnen|Gefahr|gefährlich|gefährdet|droht|drohen|Risiko|riskieren)\b", re.I)
NEG = re.compile(r"\b(?:falsch|Versagen|versagt|gescheitert|scheitern|Skandal|skandalös|Chaos|schlecht|verfehlt|"
                 r"ungerecht|kritisieren|kritisiere|nichts|Unsinn|Problem|Fehler|verantwortungslos|ignorieren)\b", re.I)
POS = re.compile(r"\b(?:begrüßen|begrüße|freue mich|freuen uns|gut|richtig|wichtiger Schritt|Erfolg|erfolgreich|"
                 r"gelungen|stolz|endlich)\b", re.I)
COMMIT = re.compile(r"\b(?:wir werden|werden wir|ich werde|werde ich|wir sorgen dafür|künftig)\b", re.I)
OTHERS = re.compile(r"\b(?:Sie|Ihr|Ihre|Ihren|AfD|Union|CDU|CSU|SPD|Grünen|Linke[n]?|Bundesregierung|Koalition)\b")

GROUP_ACC = [("AfD", r"\bAfD\b", "die Fraktion der AfD"), ("CDU/CSU", r"\bUnion\b|CDU/CSU|\bCDU\b|\bCSU\b",
             "die Fraktion der CDU/CSU"), ("SPD", r"\bSPD\b", "die SPD-Fraktion"),
             ("Grüne", r"\bGrünen\b|BÜNDNIS 90", "die Fraktion BÜNDNIS 90/DIE GRÜNEN"),
             ("Linke", r"\bLinke[n]?\b", "die Fraktion Die Linke"),
             ("Bundesregierung", r"\bBundesregierung\b|\bRegierung\b", "die Bundesregierung"),
             ("Koalition", r"\bKoalition\b", "die Koalition")]
MINISTRY = {"AA": "Bundesminister des Auswärtigen", "BMDS": "Bundesminister für Digitales und Staatsmodernisierung",
            "BMLEH": "Bundesminister für Landwirtschaft, Ernährung und Heimat", "BMAS": "Bundesminister für Arbeit und Soziales",
            "BMF": "Bundesminister der Finanzen", "BMI": "Bundesminister des Innern", "BMJV": "Bundesminister der Justiz und für Verbraucherschutz",
            "BMVg": "Bundesminister der Verteidigung", "BMWE": "Bundesminister für Wirtschaft und Energie",
            "BMBFSFJ": "Bundesminister für Bildung, Familie, Senioren, Frauen und Jugend", "BMG": "Bundesminister für Gesundheit",
            "BMV": "Bundesminister für Verkehr", "BMUKN": "Bundesminister für Umwelt, Klimaschutz, Naturschutz und nukleare Sicherheit",
            "BMWSB": "Bundesminister für Wohnen, Stadtentwicklung und Bauwesen", "BMZ": "Bundesminister für wirtschaftliche Zusammenarbeit und Entwicklung",
            "BMFTR": "Bundesminister für Forschung, Technologie und Raumfahrt"}


def act(text, kind, role, first_gov_bill=False, first_motion=False):
    if kind in ("Zwischenfrage", "Frage") or Q.search(text):
        return "fragte"
    if first_gov_bill:
        return "stellte den Gesetzentwurf vor"
    if first_motion:
        return "begründete den Antrag"
    if kind == "Antwort":
        return "antwortete"
    if COMMIT.search(text):
        return "kündigte an"
    if WARN.search(text):
        return "warnte"
    if NEG.search(text) and OTHERS.search(text):
        return "kritisierte"
    if DEMAND.search(text):
        return "forderte"
    if POS.search(text) and not NEG.search(text):
        return "begrüßte"
    return "erklärte"


def function_of(group, gender):
    """'Bundesminister BMDS' -> 'Bundesminister für Digitales und Staatsmodernisierung' (feminine form if known)."""
    m = re.match(r"^(Bundesministerin|Bundesminister|Parl\. Staatssekretärin|Parl\. Staatssekretär|Staatsministerin|"
                 r"Staatsminister|Bundeskanzlerin|Bundeskanzler)\s*(\S*)", group or "")
    if not m:
        return group
    title, abbr = m.group(1).replace("Parl.", "Parlamentarische" if m.group(1).endswith("in") else "Parlamentarischer"), m.group(2)
    if title.startswith("Bundesminister") and abbr in MINISTRY:
        return MINISTRY[abbr].replace("Bundesminister", title, 1)
    return f"{title} ({abbr})" if abbr else title


def name_of(speaker):
    return speaker


def lead_name(d):
    if d["role"] == "government":
        return f"{name_of(d['speaker'])}, {function_of(d['group'], d['gender'])},"
    return f"{name_of(d['speaker'])} ({d['group']})" if d["group"] else name_of(d["speaker"])


def surname(speaker):
    p = [x for x in (speaker or "").split() if not x.endswith(".")]
    return p[-1] if p else speaker


def pronoun(d, cap=False):
    if d["gender"] == "f":
        return "Sie" if cap else "sie"
    if d["gender"] == "m":
        return "Er" if cap else "er"
    return surname(d["speaker"])


def _vary(verb_class, prev, used=None):
    """The least used verb of the class ladder within the item that differs from the previous one
    (v2.1, 23.10.2026: before, only the previous verb was avoided, so "erklärte" came back every second quote)."""
    used = used if used is not None else {}
    cands = [v for v in LADDER.get(verb_class, [verb_class]) if v != prev] or [LADDER["erklärte"][1]]
    return min(cands, key=lambda v: (used.get(v, 0), cands.index(v)))


def _lead(d, verb, first):
    if first:
        if verb in ("kündigte an",):
            return f"{lead_name(d)} kündigte an:".replace(",, ", ", ")
        return f"{lead_name(d)} {verb}:".replace(",, ", ", ")
    p = pronoun(d)
    if verb == "erklärte":
        return f"{pronoun(d, cap=True)} fügte hinzu:" if d["gender"] else f"{p} fügte hinzu:"
    if " " in verb and verb not in ("wollte wissen",):
        head, rest = verb.split(" ", 1)
        return f"Weiter {head} {p} {rest}:"
    if verb == "wollte wissen":
        return f"Weiter wollte {p} wissen:"
    return f"Weiter {verb} {p}:"


def assign(digests, speeches, item_id=None, title="", qa=False):
    """Set verb and lead of every quote part and the report_hint of every digest (in place)."""
    title = title or ""
    bill = bool(re.search(r"Entwurfs? eines .*Gesetzes|Gesetzentwurf", title[:400]))
    motion_group = None
    m = re.search(r"Antrags? der Fraktion(?:en)? (?:der )?([A-ZÄÖÜ][\w/ ]+?)\s", title[:400])
    if m:
        motion_group = m.group(1).strip()
    prev = None
    used = {}
    first_main = True
    for d, sp in zip(digests, speeches):
        main = sp["kind"] == "Rede"
        first_gov_bill = bill and first_main and main and d["role"] == "government" and not qa
        first_motion = (not bill and motion_group and first_main and main and d["group"]
                        and motion_group.split()[0].upper()[:3] in d["group"].upper())
        if main:
            first_main = False
        n = 0
        for part in d["parts"]:
            if part["type"] != "quote":
                continue
            cls = act(part["text"], d["kind"], d["role"], first_gov_bill and n == 0, bool(first_motion) and n == 0)
            verb = _vary(cls, prev, used)
            used[verb] = used.get(verb, 0) + 1
            part["verb"] = verb
            part["lead"] = _lead(d, verb, n == 0)
            prev = verb
            n += 1
        d["report_hint"] = hint(d, sp)


def hint(d, sp):
    sents = [sp["sentences"][k]["text"] for k in d["report_sents"]]
    text = " ".join(sents)
    addressees = []
    own = (d["group"] or "").upper()
    for name, rx, _ in GROUP_ACC:
        if re.search(rx, text) and not (name.upper()[:3] in own):
            addressees.append(name)
    neg = bool(NEG.search(text))
    verb = "kritisierte" if addressees and neg else ("wandte sich an" if addressees else None)
    kw = []
    return {"verb": verb, "addressees": addressees[:2], "keywords": kw}


def render_reported(d):
    """System C: 'Zudem kritisierte sie die Fraktion der AfD.' -- or None."""
    h = d.get("report_hint") or {}
    if not h.get("verb") or not h.get("addressees"):
        return None
    acc = {n: a for n, _, a in GROUP_ACC}
    objs = [acc[a] for a in h["addressees"] if a in acc]
    if not objs:
        return None
    obj = " und ".join(objs)
    has_quote = any(p["type"] == "quote" for p in d["parts"])
    subj = pronoun(d) if has_quote else lead_name(d).rstrip(",")
    if h["verb"] == "kritisierte":
        return f"Zudem kritisierte {subj} {obj}." if has_quote else f"{subj} kritisierte {obj}."
    if has_quote:
        return f"Zudem wandte {subj} sich an {obj}."
    return f"{subj} wandte sich an {obj}."
