"""Votes and decisions: the chair's question-answer pairs and the decision lines of the Amtliches Protokoll.

A vote is an adjacency pair: the chair asks ("Wer stimmt dafür?", "Gegenprobe!", "Enthaltungen?") and the answer
names the voters ("Das sind CDU/CSU und SPD."); the closing third states the result ("… ist angenommen").
Rules V1–V6 in docs/RULES.md.
"""
import re

ALL5 = ["CDU/CSU", "AfD", "SPD", "BÜNDNIS 90/DIE GRÜNEN", "Die Linke"]
GROUPS = [("CDU/CSU", r"CDU/CSU|\bUnion(?:sfraktion)?\b"), ("SPD", r"\bSPD\b"), ("AfD", r"\bAfD\b"),
          ("BÜNDNIS 90/DIE GRÜNEN", r"Grünen|BÜNDNIS 90|Bündnis 90"), ("Die Linke", r"\bLinke[n]?\b|Linksfraktion"),
          ("fraktionslos", r"fraktionslos")]

YES = (r"Wer\s+stimmt\s+(?:dafür|für\b[^?]*|zu)\?|Wer\s+ist\s+(?:dafür|für\b[^?]*)\?|"
       r"[^.?!–]*zustimmen\s+(?:wollen|möchten)[^.?!]*(?:erheben|Handzeichen|aufzustehen)[^.?!]*[.?!]|"
       r"Wer\s+möchte\s+[^?]*zustimmen[^?]*\?|Wer\s+möchte\s+[^?]*Zustimmung\s+geben\?")
NO = (r"Gegenprobe!?\s*(?::\s*)?(?:Wer\s+stimmt\s+dagegen\?)?|Wer\s+stimmt\s+(?:dagegen|gegen\b[^?]*)\?|"
      r"Wer\s+ist\s+(?:dagegen|gegen\b[^?]*)\?|(?:Gibt\s+es\s+)?Gegenstimmen\?|(?:Stimmt|Ist)\s+jemand\s+dagegen\?|"
      r"Jemand\s+dagegen\?|(?:Wer|Möchte\s+jemand)\s+(?:möchte\s+|will\s+)?dagegenstimmen\?|Neinstimmen\?|"
      r"Dagegen\s+stimmt\?|Gibt\s+es\s+jemanden,\s+der\s+dagegenstimmt\?")
ABST = (r"(?:Und\s+|Oder\s+)?[Ww]er\s+enthält\s+sich(?:\s+der\s+Stimme)?\?|(?:Gibt\s+es\s+)?(?:Stimm)?[Ee]nthaltungen\?|"
        r"(?:Oder\s+)?[Ee]nthält\s+sich\s+jemand\?|Wer\s+möchte\s+sich\s+enthalten\?")
Q = re.compile(f"({YES})|({NO})|({ABST})")
RESULT = re.compile(r"\b(angenommen|abgelehnt)\b")


def groups_in(answer, taken=()):
    """Parliamentary groups named in an answer. 'alle' / 'alle übrigen' are resolved against the five groups."""
    t = answer
    if re.search(r"\b(?:niemand|keine[rn]?)\b|nicht der Fall|[Ss]ehe ich nicht|nicht erkennen|Dem ist nicht so", t) \
            and not any(re.search(rx, t) for _, rx in GROUPS):
        return ["niemand"]
    if re.search(r"übrigen|anderen\s+Fraktionen|restlichen|Rest des Hauses", t):
        named = [n for n, rx in GROUPS if re.search(rx, t)]
        return [g for g in ALL5 if g not in taken] + [g for g in named if g not in ALL5]
    if re.search(r"(?:gesamte|ganze)\s+Haus|\balle\b|aller\s+Fraktionen", t, re.I):
        return ["alle Fraktionen"]
    out = []
    if re.search(r"Koalitionsfraktionen|Koalition\b", t):
        out += ["CDU/CSU", "SPD"]
    for name, rx in GROUPS:
        if re.search(rx, t) and name not in out:
            out.append(name)
    return out


def _slot(m):
    return "dafür" if m.group(1) else "dagegen" if m.group(2) else "Enthaltung"


def vote_episodes(text):
    """Every vote in a chair paragraph: [{positions, result, reading, drucksachen, text}].
    A new episode starts at a yes-question that follows a stated result or an already filled yes-slot."""
    eps, cur = [], None
    qs = list(Q.finditer(text))
    for k, m in enumerate(qs):
        slot = _slot(m)
        end = qs[k + 1].start() if k + 1 < len(qs) else len(text)
        ans = text[m.end():end].strip(" –-")
        ans = re.split(r"(?<=[.!])\s+(?=(?:Damit|Somit|Dann|Der|Die|Das\s+ist\s+damit|Auch|Ich|Wir)\b)", ans)[0]
        if cur is None or (slot == "dafür" and ("dafür" in cur["positions"] or cur["result"])):
            cur = {"positions": {}, "result": None, "start": m.start()}
            eps.append(cur)
        taken = [g for v in cur["positions"].values() for g in v]
        g = groups_in(ans, taken) if ans and not Q.match(ans) else []
        if g:
            cur["positions"][slot] = g
        r = RESULT.search(text[m.end():end])
        if r:
            cur["result"] = r.group(1)
    for k, e in enumerate(eps):
        seg = text[e["start"]: eps[k + 1]["start"] if k + 1 < len(eps) else len(text)]
        head = text[:e["start"]] if k == 0 else ""
        e["text"] = (head + seg).strip()
        e["drucksachen"] = re.findall(r"\b21/\d+\b", e["text"])
        e["reading"] = ("dritte" if re.search(r"Schlussabstimmung|dritte[nr]?\s+Beratung", e["text"]) else
                        "zweite" if re.search(r"zweite[nr]?\s+Beratung", e["text"]) else None)
        del e["start"]
    if not eps and RESULT.search(text):                          # result without question-answer pairs
        m = re.search(r"mit\s+den\s+Stimmen\s+(?:der\s+|von\s+)?([^.]*?)(?:\s+gegen\s+die\s+Stimmen\s+(?:der\s+)?"
                      r"([^.]*?))?(?:\s+(?:bei|unter)\s+Enthaltung\s+(?:der\s+)?([^.]*?))?\s+(?:angenommen|abgelehnt)",
                      text)
        pos = {}
        if m:
            for slot, grp in (("dafür", 1), ("dagegen", 2), ("Enthaltung", 3)):
                if m.group(grp):
                    pos[slot] = groups_in(m.group(grp))
        eps.append({"positions": pos, "result": RESULT.search(text).group(1), "text": text,
                    "drucksachen": re.findall(r"\b21/\d+\b", text), "reading": None})
    return eps


def vote_positions(text):
    """(positions of the first vote, its result): 'Wer stimmt dafür? – CDU/CSU und SPD. …' -> ({dafür: [...]}, …)."""
    eps = vote_episodes(text)
    return (eps[0]["positions"], eps[0]["result"]) if eps else ({}, None)


# ------------------------------------------------------------------ result sentence (style guide rule 7)

def _g(gs):
    return ", ".join(g for g in gs if g != "niemand")


def result_sentence(positions, result, reading=None, ap_line=None):
    """Münster Rat formula, result first, ending in a colon:
    'Der Bundestag beschließt mit Mehrheit (CDU/CSU, SPD) bei Gegenstimmen (AfD):' /
    'Der Bundestag lehnt mit Mehrheit (…) bei Fürstimmen (…) ab:'. The verb follows the Amtliches Protokoll line
    when given (Annahme / Ablehnung), else the chair's result. A fronted reading keeps the verb second."""
    if ap_line:
        if ap_line.startswith("Ablehnung"):
            result = "abgelehnt"
        elif re.match(r"(Annahme|Zustimmung|Wahl|Kenntnisnahme|Erledigterklärung)", ap_line):
            result = "angenommen"
    yes = [g for g in positions.get("dafür", []) if g != "niemand"]
    no = [g for g in positions.get("dagegen", []) if g != "niemand"]
    abst = [g for g in positions.get("Enthaltung", []) if g != "niemand"]
    reject = result == "abgelehnt"
    verb, part = ("lehnt", " ab") if reject else ("beschließt", "")   # v3: present-tense frame (wortnah style)
    label = {"dritte": "In der Schlussabstimmung", "zweite": "In der zweiten Beratung"}.get(reading or "")
    head = f"{label} {verb} der Bundestag" if label else f"Der Bundestag {verb}"
    major, minor, minor_word = (no, yes, "Fürstimmen") if reject else (yes, no, "Gegenstimmen")
    if ("alle Fraktionen" in major or set(major) >= set(ALL5)) and not minor and not abst:
        return f"{head} einstimmig{part}:"
    mods = []
    if major and (minor or abst):
        mods.append(f"mit Mehrheit ({_g(major)})")
    elif major:
        mods.append(f"mit den Stimmen ({_g(major)})")
    if minor:
        mods.append(f"bei {minor_word} ({_g(minor)})")
    if abst:
        mods.append(f"{'und' if minor else 'bei'} Stimmenthaltungen ({_g(abst)})")
    return f"{head}{' ' + ' '.join(mods) if mods else ''}{part}:"


# ------------------------------------------------------------------ Amtliches Protokoll

DECISION_START = re.compile(r"^(Annahme|Ablehnung|Überweisungsbeschluss|wird abgesetzt|Das bedeutet|Erledigterklärung|"
                            r"Kenntnisnahme|Wahl|gewählt|Zustimmung|Absetzung)")


def clean(t):
    t = re.sub(r"(\w)-\s*\n\s*([a-zäöüß])", r"\1\2", t)
    return re.sub(r"[ \t]+", " ", t)


def ap_decisions(ap_text):
    """Decision lines of one Amtliches Protokoll item (outcome, referral, 'wird abgesetzt', roll-call counts)."""
    out, cur = [], None
    for l in (x.strip() for x in clean(ap_text).splitlines()):
        if DECISION_START.match(l):
            cur = l
            out.append(cur)
        elif cur is not None and l and not re.match(r"^(\d+\. Wahlperiode|Seite \d|[a-z]\)|Tagesordnungspunkt|"
                                                    r"Zusatzpunkt|Beratung|Erste Beratung|Zweite|Amtliches Protokoll|"
                                                    r"\d+\. Sitzung|Die nächste Sitzung)", l):
            out[-1] += " " + l
        else:
            cur = None
    return [re.sub(r"\s+", " ", x).strip() for x in out]


def decisions(ap_lines):
    """-> (decision lines with 'Das bedeutet:' joined, referral lines)."""
    out, refs, k = [], [], 0
    while k < len(ap_lines):
        l = ap_lines[k]
        if l.startswith("Überweisungsbeschluss"):
            refs.append(l)
        elif l.startswith("Das bedeutet") and out:
            rest = l.split(":", 1)[1].strip() if ":" in l else ""
            if not rest and k + 1 < len(ap_lines):
                rest, k = ap_lines[k + 1], k + 1
            out[-1] += "; das bedeutet: " + rest
        else:
            out.append(l)
        k += 1
    return out, refs


def committees(ref_line):
    """'Überweisungsbeschluss: A. f. Digitales (f) Innenausschuss …' -> ['Ausschuss für Digitales (federführend)', …]."""
    t = ref_line.split(":", 1)[1].strip() if ":" in ref_line else ref_line
    t = re.sub(r"\s+mitberatend und gemäß\s+§\s*96 der GO", " (mitberatend und gemäß § 96 GO-BT)", t)
    t = t.replace("gemäß § 96 der GO", "(gemäß § 96 GO-BT)")
    parts = re.split(r"\s+(?=A\. f\. |Auswärtiger Ausschuss|Ausschuss für |[A-ZÄÖÜ][a-zäöüß]*ausschuss\b)", t)
    return [p.replace("A. f. ", "Ausschuss für ").replace("(f)", "(federführend)").strip() for p in parts if p.strip()]


def flat(t):
    t = re.sub(r"(\w)-\s*\n\s*([a-zäöüß])", r"\1\2", t)
    t = re.sub(r"\b([A-ZÄÖÜ]{2,})-\s*\n\s*([A-ZÄÖÜ]{2,})\b", r"\1\2", t)
    t = re.sub(r"(\w)-\s*\n\s*([A-ZÄÖÜ])", r"\1-\2", t)
    return re.sub(r"\s+", " ", t).strip()


def ap_parts(ap_text):
    """Split an AP item into lettered sub-items: (head, [{letter, title, drucksachen, lines}])."""
    if not ap_text:
        return "", []
    chunks = re.split(r"\n\s*(?=[a-z]\)\s)", "\n" + ap_text)
    head = flat(chunks[0]) if chunks[0].strip() and len(chunks) > 1 else ""
    subs = []
    for c in (chunks[1:] if len(chunks) > 1 else [ap_text]):
        m = re.match(r"\s*([a-z])\)\s+", c)
        body = c[m.end():] if m else c
        title = flat(re.split(r"\n\s*Drucksachen? \d|\n\s*(?:Annahme|Ablehnung|Überweisungsbeschluss|wird abgesetzt|"
                              r"Wahl|Erledigterklärung|Kenntnisnahme|Es findet)", body)[0])
        title = re.sub(r"\s*21\. Wahlperiode.*$", "", title)
        subs.append({"letter": m.group(1) if m else None, "title": title,
                     "drucksachen": sorted(set(re.findall(r"\b21/\d+\b", flat(body)))), "lines": ap_decisions(body)})
    return head, subs


def pair_votes(episodes, subs):
    """Attach vote episodes to decision lines. -> [{sub, decision, vote}] in AP order, plus referrals per sub.
    A vote goes to the decision that names one of its Drucksachen; else to the next sub-item without a vote; a
    Schlussabstimmung stays with the sub-item of the preceding vote."""
    out, last = [], None
    pool = list(episodes)
    for s in subs:
        decs, refs = decisions(s["lines"])
        for d in decs:
            rec = {"sub": s["letter"], "decision": d, "vote": None, "referrals": []}
            if not d.startswith("wird abgesetzt"):
                ds = set(re.findall(r"\b21/\d+\b", d)) | set(s["drucksachen"])
                hit = next((v for v in pool if set(v["drucksachen"]) & ds), None)
                if hit is None and pool and not pool[0]["drucksachen"]:
                    hit = pool[0]
                if hit is None and pool and len(subs) == 1 and len(decs) == 1:
                    hit = pool[0]
                if hit is not None:
                    pool.remove(hit)
                    rec["vote"] = hit
                    last = s["letter"]
            out.append(rec)
        if refs:
            out.append({"sub": s["letter"], "decision": None, "vote": None,
                        "referrals": [c for r in refs for c in committees(r)]})
    return out
