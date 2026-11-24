"""Exploratory study: what the agenda (Tagesordnung) and the decision record (Beschlussprotokoll) of a German county
council say about each other, and what that means for rule-based minutes.

    python3 konstanz_agenda_study/fetch.py      # public PDFs -> data/ (not in the repository)
    python3 konstanz_agenda_study/analyse.py    # -> results/study.json (statistics only, no document text)

Data: the public agendas and decision records of the Kreistag Konstanz, sessions 2009-2026 (fetch.py). Nothing else.
Names of persons never leave this script: examples are masked ("Herr [Name]").

Measures
  M1 corpus and agenda size per period
  M2 item types from the agenda title (rules below) and their canonical position in the agenda
  M3 agenda -> record: same items, same numbers, same order? withdrawn and added items; order notes (Hinweis)
  M4 what the record decides per agenda item type: result label classes (formula only vs. needs drafting)
  M5 can the agenda title alone predict a formula-only item? learned on 2011-2021, tested on 2022-2026
  M6 house style of the result labels over time
  M7 vocabulary the decision text adds to the agenda title
  M8 planned speaking time ("Dauer") per item type, in agendas that give it
  M9 mood in reported dass-clauses of the record (Konjunktiv I vs indicative), with spaCy morphology
"""
import collections
import difflib
import json
import math
import os
import re
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEXT = Path(os.environ.get("KONSTANZ_DATA", HERE / "data")) / "text"
OUT = HERE / "results"
WORD = r"[A-Za-zÄÖÜäöüß]+"
PERIODS = [("2009–2014", 2009, 2014), ("2015–2019", 2015, 2019), ("2020–2023", 2020, 2023), ("2024–2026", 2024, 2026)]


def period(year):
    return next(p for p, a, b in PERIODS if a <= year <= b)


# ------------------------------------------------------------------------------------------------ cleaning
def first_pass(text):
    t = text.replace("­", "")
    t = re.sub(r"(?m)^\s*\d{1,2}\s*$", "", t)                                    # page numbers
    t = re.sub(r"(\w)\s?-\s*\n\s*(?!und\b|oder\b|bzw\b|sowie\b)([a-zäöüß])", r"\1\2", t)    # line-end hyphenation
    t = re.sub(r"(\w) -(?=[A-Za-zÄÖÜäöüß])", r"\1-", t)                            # "Ja -Stimmen"
    return re.sub(r"[ \t]+", " ", t)


def repair_pass(text, vocab):
    """Join words split by PDF extraction ("Si t-zung", "einsti mmig") when the joined form is a corpus word."""
    def join(m):
        a, b = m.group(1), m.group(2)
        return a + b if vocab[a + b] >= 3 and vocab[a + b] > min(vocab[a], vocab[b]) else m.group(0)
    return re.sub(rf"\b({WORD}) ([a-zäöüß]+)\b", join, text)


NAME = re.compile(r"\b(Kreisrätinnen|Kreisrätin|Kreisrat|Kreisräte|Herrn?|Frau|Landrat|Landrätin|Bürgermeister(?:in)?|"
                  r"Oberbürgermeister(?:in)?|Ministerin|Minister|Polizeipräsident(?:in)?)\s+(?:Dr\.\s+|Prof\.\s+)*"
                  r"([A-ZÄÖÜ][\wäöüß\-]+)(?:\s+(?:Dr\.\s+)?[A-ZÄÖÜ][\wäöüß\-]+){0,2}")


def mask(s):
    return NAME.sub(lambda m: f"{m.group(1)} [Name]", s)


def load():
    docs = []
    for f in sorted(TEXT.glob("*.txt")):
        date, *kind, doc_id = f.stem.split("_")
        kind = "_".join(kind)
        docs.append({"date": date, "year": int(date[:4]), "kind": kind, "id": doc_id,
                     "clean": first_pass(f.read_text(encoding="utf-8"))})
    for _ in range(3):
        vocab = collections.Counter(w for d in docs for w in re.findall(WORD, d["clean"]))
        for d in docs:
            d["clean"] = repair_pass(d["clean"], vocab)
    return docs


# ------------------------------------------------------------------------------------------------ parsing
DS = re.compile(r"\b(?:\d{4}/\d{3}|\d{3}/\d{4})\b")                 # Drucksache: 2024/175 (old: 340/2009)
DAUER = re.compile(r"\b(\d{1,3})\s*Min\b")
AG_HEAD = re.compile(r"(?m)^[ \t]*(\d{1,2})\.(?:(\d{1,2})\.)?(?:[ \t]+(.*)|[ \t]*)$")   # title may start on the next line


def agenda_items(text):
    """Top-level items "k. Title" with k = previous + 1, and sub-items "k.m. Title" inside k."""
    body = text.split("TOP Bezeichnung", 1)[-1]
    heads, expect, cur = [], 1, 0
    for m in AG_HEAD.finditer(body):
        k, sub = int(m.group(1)), m.group(2)
        if sub is None and k == expect:
            heads.append((k, None, m.start(), m.end(), m.group(3) or ""))
            expect, cur = k + 1, k
        elif sub is not None and k == cur:
            heads.append((k, int(sub), m.start(), m.end(), m.group(3) or ""))
    out = []
    for i, (k, sub, s, e, first) in enumerate(heads):
        end = heads[i + 1][2] if i + 1 < len(heads) else len(body)
        chunk = first + "\n" + body[e:end]
        ds = DS.findall(chunk)
        dauer = [int(x) for x in DAUER.findall(DS.sub(" ", chunk)) if 0 < int(x) <= 240]   # "2023/310 Min": no number
        title = DS.split(DAUER.sub(" ", chunk))[0]
        title = re.sub(r"\s+", " ", re.split(r"\n[ \t]*\n[ \t]*\n", title)[0]).strip(" -;")
        out.append({"k": k, "sub": sub, "title": title[:300], "ds": ds, "dauer": dauer[0] if dauer else None})
    return out


REC_HEAD = re.compile(r"(?m)^[ \t]*(?:(\d{1,2})\.[ \t]+(?=[A-ZÄÖÜ\"„(])(.*)|TOP[ \t]+(\d{1,2})[ \t]*"
                      r"(?:\n[ \t]*\d{4}/\d{3})?[ \t]*\n\s*(.*))$")
LABEL = re.compile(r"Besch?luss(?:[ \t]*\d+)?(?:[ \t]*[a-z]\))?(?:[ \t]+nach[^:(\n]{0,40})?[ \t]*"
                   r"(?:\(\s*([^)]{0,200}?)\s*\)[ \t]*:?|:|(?=\s+Entfällt))")
BLOCK = re.compile(r"(?m)^[ \t]*(Hinweis\w*|Zusage\w*|Sonstige Hinweise[^:\n]*|Nachrichtlich|Vorbemerkung\w*)[ \t]*:")


HEAD_ANY = re.compile(r"(?m)^[ \t]*(?:TOP[ \t]+)?(\d{1,2})\.?(?=[ \t\n])")


def head_text(text, end):
    """The words after a header: the item title (Drucksache numbers and 'TOP k' removed)."""
    return re.sub(r"\s+", " ", DS.sub(" ", text[end:end + 260])).strip()


def record_items(text, agenda_tops=None):
    """Items of a decision record. With the agenda: each agenda item is looked up IN ORDER as a header with its
    number whose following words resemble its title (sim >= 0.5); else as any later header with a similar title
    (renumbered). Numbered lists inside a decision text never pass the title check. Without the agenda: headers
    k = previous k + 1 (descriptive use only)."""
    heads = []
    if agenda_tops is None:
        expect = 1
        for m in REC_HEAD.finditer(text):
            k = int(m.group(1) or m.group(3))
            if k == expect:
                heads.append((k, m.start(), m.end(), m.group(2) or m.group(4)))
                expect = k + 1
    else:
        cands = [(int(m.group(1)), m.start(), m.end()) for m in HEAD_ANY.finditer(text)]
        pos = 0
        for x in agenda_tops:
            hit = next((c for c in cands if c[1] >= pos and c[0] == x["k"]
                        and sim(x["title"], head_text(text, c[2])) >= 0.5), None)
            if hit is None:
                hit = next((c for c in cands if c[1] >= pos and sim(x["title"], head_text(text, c[2])) >= 0.6), None)
            if hit is not None:
                heads.append((hit[0], hit[1], hit[2], "", x))
                pos = hit[2]
        # items not found in order: anywhere in the record (a record that departs from the agenda sequence)
        taken = {h[1] for h in heads}
        for x in agenda_tops:
            if any(h[4] is x for h in heads):
                continue
            hit = next((c for c in cands if c[1] not in taken and c[0] == x["k"]
                        and sim(x["title"], head_text(text, c[2])) >= 0.5), None)
            if hit is not None:
                heads.append((hit[0], hit[1], hit[2], "", x))
                taken.add(hit[1])
        heads.sort(key=lambda h: h[1])
    out = []
    for i, h in enumerate(heads):
        k, s, e, first = h[:4]
        end = heads[i + 1][1] if i + 1 < len(heads) else len(text)
        body = text[e:end]
        lab = LABEL.search(body)
        title = first + " " + (body[: lab.start()] if lab and lab.start() < 400 else re.split(r"\n\s*\n", body)[0])
        rest = body[lab.start():] if lab else body
        parts = BLOCK.split(rest)
        blocks = collections.defaultdict(list)
        for j in range(1, len(parts) - 1, 2):
            blocks[parts[j].split()[0]].append(re.sub(r"\s+", " ", parts[j + 1]).strip())
        out.append({"k": k, "title": re.sub(r"\s+", " ", DS.sub(" ", title)).strip()[:300],
                    "labels": [(m.group(1) or "").strip() for m in LABEL.finditer(rest)], "has_label": bool(lab),
                    "decision": re.sub(r"\s+", " ", LABEL.sub(" ", parts[0])).strip(), "blocks": dict(blocks),
                    "flat": re.sub(r"\s+", " ", body), "agenda": h[4] if len(h) > 4 else None})
    return out


# ------------------------------------------------------------------------------------------------ item types (M2)
SQ = lambda t: re.sub(r"\s+", "", t)                      # "Si t-zung" survivors: match without spaces
TYPES = [   # agenda title -> type; first match wins (patterns run on the title without spaces)
    ("opening: non-public decisions announced", r"Bekanntgabe.*(nichtöffentlich|Eilentscheidung)|Eilentscheidung"),
    ("opening: formal approval", r"GenehmigungderNiederschrift"),
    ("residents' question time", r"Bürgerfragestunde|Einwohnerfragestunde|Fragestunde"),
    ("membership, elections, appointments", r"Besetzung|Wahl(?!kreis)|Bestellung|Benennung|Verpflichtung|Konstituierung|"
                                            r"Ausscheiden|Hinderungsgr|Nachrücken|Entsendung|Sitzordnung|Jury|Kuratorium"),
    ("budget and finance", r"Haushalt|Jahresabschluss|Budget|Finanz|Kreisumlage|Wirtschaftsplan|Beteiligungsbericht|"
                           r"Kredit|Bürgschaft|Gebühr|Entgelt|Zuschuss|Förder"),
    ("statute, rules", r"Satzung|Geschäftsordnung|Richtlinie|Verordnung|Hauptsatzung"),
    ("closing: miscellaneous, questions", r"Verschiedenes|Anfragen|Sonstiges|Wünsche"),
    ("motion by a group", r"Antragder|Anträgeder|AntragdesKreisrat|AntragvonKreisr"),
    ("report, presentation", r"Bericht|Vorstellung|Präsentation|Sachstand|Information|Mitteilung|Bilanz|Statistik|"
                             r"Entwicklung|Einführungsrede|Ausblick"),
]


def item_type(title):
    t = SQ(title)
    return next((name for name, rx in TYPES if re.search(rx, t)), "other subject item")


# ------------------------------------------------------------------------------------------------ outcome (M4)
def outcome(it):
    """Result class of a record item, from its label(s) and decision text."""
    labs, d = " | ".join(it["labels"]), it["decision"]
    if re.search(r"\babgesetzt\b|von der Tagesordnung", d[:300]):
        return "withdrawn"
    if not it["has_label"] or re.match(r"\s*Entfällt", d) or re.search(r"(?i)entfällt", labs):
        if re.search(r"zur? Kenntnis", d):
            return "noted (Kenntnisnahme formula)"
        return "no decision (formula or copied list)"
    if len(it["labels"]) > 1:
        return "several decisions"
    lab = it["labels"][0]
    if re.search(r"abgelehnt|Mehrheit der Nein|kein Einvernehmen", lab):
        return "rejected"
    if re.search(r"Mehrheit|mehrheitlich|Gegenstimme|Nein-Stimme", lab):
        return "majority"
    if re.search(r"Enthaltung", lab):
        return "unanimous with abstentions"
    if re.search(r"einstimmig|Einvernehmen|ohne förmliche|ohne Gegenstimme", lab):
        return "unanimous"
    return "other label"


FORMULA = {"withdrawn", "noted (Kenntnisnahme formula)", "no decision (formula or copied list)", "unanimous",
           "unanimous with abstentions"}


# ------------------------------------------------------------------------------------------------ helpers
STOP = set("""der die das den dem des ein eine einer eines einem einen und oder sowie bzw für von vom zu zum zur im in
am an auf aus bei mit nach über unter vor durch gegen ohne um als auch nicht nur noch wird werden wurde wurden ist
sind sei hat haben es er sie wir ihr man sich dass ob wie so da dann damit dies diese dieser dieses jeweils bis
beschluss""".split())


def stems(text):
    out = set()
    for w in re.findall(r"[a-zäöüß]+", mask(text).lower()):
        if len(w) > 2 and w not in STOP:
            for suf in ("ungen", "ung", "en", "er", "es", "e", "n", "s"):
                if w.endswith(suf) and len(w) - len(suf) >= 4:
                    w = w[: -len(suf)]
                    break
            out.add(w)
    return out


def sim(a, b):
    a, b = SQ(a).lower()[:120], SQ(b).lower()[:120]
    return difflib.SequenceMatcher(None, a, b).ratio()


def kendall_tau(seq):
    n, conc, disc = len(seq), 0, 0
    for i in range(n):
        for j in range(i + 1, n):
            conc += seq[i] < seq[j]
            disc += seq[i] > seq[j]
    return (conc - disc) / max(1, conc + disc)


ORDER_NOTES = {
    "moved": r"(?:wird|wurde|werden|wurden)\b[^.]{0,60}\b(?:vor|nach) (?:TOP|Tagesordnungspunkt)\s*\d[^.]{0,30}"
             r"(?:behandelt|beraten|aufgerufen)|\bvorgezogen\b|\bim Anschluss an (?:TOP|Tagesordnungspunkt)",
    "withdrawn": r"\b(?:TOP|Tagesordnungspunkt|Punkt|Beratung)\b[^.]{0,80}\babgesetzt\b|\babgesetzt\b[^.]{0,40}"
                 r"\b(?:TOP|Tagesordnung)|von der Tagesordnung (?:genommen|abgesetzt)",
    "deferred": r"\bvertagt\b|\b(?:Beratung|Beschlussfassung|Entscheidung|Beschlussteil)\b[^.]{0,80}\bzurückgestellt\b",
}


def label_style(lab):
    """House-style family of a majority label."""
    if re.search(r"Mehrheit der Ja-Stimmen", lab):
        return "Mehrheit der Ja-Stimmen gegen N Nein-Stimmen"
    if re.search(r"(?i)mehrheitliche Ja-Stimmen", lab):
        return "mehrheitliche Ja-Stimmen, N Gegenstimmen"
    if re.search(r"(?i)mehrheitlich", lab):
        return "mehrheitlich (other)"
    return "other"


# ------------------------------------------------------------------------------------------------ main
def main():
    docs = load()
    by_date = collections.defaultdict(dict)
    for d in docs:
        by_date[d["date"]].setdefault(d["kind"], d)        # first document of a kind (sorted by id)
    res = {"note": "Statistics from the public agendas and decision records of the Kreistag Konstanz (fetch.py)."}

    # M1 corpus
    sessions = []
    for date, ks in sorted(by_date.items()):
        ag = ks.get("agenda") or ks.get("agenda_updated")
        rec = ks.get("record")
        sessions.append({"date": date, "year": int(date[:4]),
                         "agenda": agenda_items(ag["clean"]) if ag else None,
                         "agenda_updated": agenda_items(ks["agenda_updated"]["clean"]) if "agenda_updated" in ks and "agenda" in ks else None,
                         "record": record_items(rec["clean"], [x for x in agenda_items(ag["clean"]) if x["sub"] is None])
                                   if rec and ag else (record_items(rec["clean"]) if rec else None),
                         "record_seq": record_items(rec["clean"]) if rec else None,
                         "record_flat": re.sub(r"\s+", " ", rec["clean"]) if rec else ""})
    m1 = {}
    for p, a, b in PERIODS:
        ss = [s for s in sessions if a <= s["year"] <= b]
        tops = [len([x for x in s["agenda"] if x["sub"] is None]) for s in ss if s["agenda"]]
        subs = [len([x for x in s["agenda"] if x["sub"] is not None]) for s in ss if s["agenda"]]
        m1[p] = {"sessions": len(ss), "with_agenda": len(tops), "with_record": sum(1 for s in ss if s["record"]),
                 "items_median": st.median(tops) if tops else None, "items_total": sum(tops),
                 "sub_items_total": sum(subs)}
    res["M1_corpus"] = {"sessions": len(sessions), "first": sessions[0]["date"], "last": sessions[-1]["date"],
                        "documents": len(docs), "by_period": m1}

    # M2 item types and their position
    rows = []
    for s in sessions:
        tops = [x for x in (s["agenda"] or []) if x["sub"] is None]
        for x in tops:
            rows.append({"date": s["date"], "year": s["year"], "k": x["k"], "n": len(tops), "type": item_type(x["title"]),
                         "title": x["title"], "dauer": x["dauer"], "ds": bool(x["ds"])})
    types = collections.Counter(r["type"] for r in rows)
    pos = collections.defaultdict(list)
    for r in rows:
        pos[r["type"]].append((r["k"] - 1) / max(1, r["n"] - 1))
    by_period = {p: collections.Counter(r["type"] for r in rows if a <= r["year"] <= b) for p, a, b in PERIODS}
    res["M2_types"] = {
        "items": len(rows),
        "types": {t: {"items": c, "share": round(c / len(rows), 3), "median_relative_position": round(st.median(pos[t]), 2),
                      "with_drucksache": round(sum(r["ds"] for r in rows if r["type"] == t) / c, 2)}
                  for t, c in types.most_common()},
        "by_period": {p: {t: round(c / max(1, sum(cnt.values())), 3) for t, c in cnt.most_common()} for p, cnt in by_period.items()},
    }
    # canonical frame: opening items first, closing items last
    frame_ok, frame_n = 0, 0
    for s in sessions:
        ts = [item_type(x["title"]) for x in (s["agenda"] or []) if x["sub"] is None]
        if len(ts) < 4:
            continue
        frame_n += 1
        opens = [i for i, t in enumerate(ts) if t.startswith("opening")]
        closes = [i for i, t in enumerate(ts) if t.startswith("closing")]
        if all(i < 3 for i in opens) and all(i >= len(ts) - 2 for i in closes):
            frame_ok += 1
    res["M2_types"]["canonical_frame"] = {"sessions": frame_n, "opening_first_and_closing_last": frame_ok}
    qt = [r for r in rows if r["type"] == "residents' question time"]
    res["M2_types"]["question_time_position"] = {
        "items": len(qt), "first_item": sum(r["k"] == 1 for r in qt), "last_item": sum(r["k"] == r["n"] for r in qt),
        "in_between": sum(1 < r["k"] < r["n"] for r in qt),
        "agenda_names_a_clock_time": sum(bool(re.search(r"\d{1,2}(?::\d\d)?\s*Uhr", r["title"])) for r in qt)}

    # M3 agenda -> record
    pairs, align = [], []
    for s in sessions:
        if not (s["agenda"] and s["record"]):
            continue
        tops = [x for x in s["agenda"] if x["sub"] is None]
        rec = s["record"]
        matched = [(r["agenda"], r) for r in rec]
        numbering_same = sum(r["k"] == x["k"] for x, r in matched)
        missing = [x for x in tops if x["k"] not in {m[0]["k"] for m in matched}]
        added = [r for r in s["record_seq"] if max((sim(r["title"], x["title"]) for x in tops), default=0) < 0.5]
        seq = [x["k"] for x, _ in matched]                      # agenda numbers in record order
        withdrawn_in_record = sum(outcome(r) == "withdrawn" for _, r in matched)
        align.append({"date": s["date"], "agenda_items": len(tops), "record_items": len(s["record_seq"]),
                      "matched": len(matched), "same_number": numbering_same, "agenda_only": len(missing),
                      "record_only": len(added), "record_in_agenda_order": seq == sorted(seq),
                      "withdrawn_in_record": withdrawn_in_record,
                      "agenda_only_titles": [mask(x["title"])[:80] for x in missing],
                      "record_only_titles": [mask(r["title"])[:80] for r in added]})
        for x, r in matched:
            pairs.append({"date": s["date"], "year": s["year"], "type": item_type(x["title"]), "outcome": outcome(r),
                          "title": x["title"], "decision": r["decision"], "labels": r["labels"],
                          "title_stems": stems(x["title"]), "decision_stems": stems(r["decision"]), "dauer": x["dauer"]})
    notes = collections.defaultdict(list)
    for s in sessions:
        for sent in re.split(r"(?<=[.!?])\s+", s["record_flat"]):
            if len(sent) > 250 or not re.search(r"\bTOP\b|Tagesordnung|Beratung|Beschlussfassung|Bürgerfragestunde", sent):
                continue                    # long decision sentences ("… vorgezogen wird", lists of contracts)
            for kind, rx in ORDER_NOTES.items():
                if re.search(rx, sent):
                    notes[kind].append({"date": s["date"], "sentence": mask(sent)[:220]})
    note_sessions = {k: sorted({h["date"] for h in v}) for k, v in notes.items()}
    n_rec = sum(1 for s in sessions if s["record"])
    upd = []
    for s in sessions:
        if s["agenda_updated"] and s["agenda"]:
            a = [x["title"][:60] for x in s["agenda"] if x["sub"] is None]
            b = [x["title"][:60] for x in s["agenda_updated"] if x["sub"] is None]
            upd.append({"date": s["date"], "items_before": len(a), "items_after": len(b),
                        "new_titles": sum(1 for t in b if max((sim(t, u) for u in a), default=0) < 0.6),
                        "dropped_titles": sum(1 for t in a if max((sim(t, u) for u in b), default=0) < 0.6)})
    res["M3_agenda_vs_record"] = {
        "sessions_with_both": len(align),
        "agenda_items": sum(a["agenda_items"] for a in align),
        "matched_items": sum(a["matched"] for a in align),
        "matched_under_the_same_number": sum(a["same_number"] for a in align),
        "agenda_items_not_in_record": sum(a["agenda_only"] for a in align),
        "agenda_items_not_in_record_by_type": dict(collections.Counter(
            item_type(t) for a in align for t in a["agenda_only_titles"]).most_common()),
        "record_items_not_in_agenda_UNRELIABLE": sum(a["record_only"] for a in align),
        "sessions_record_in_agenda_numbering": sum(a["record_in_agenda_order"] for a in align),
        "sessions_identical_item_list": sum(a["agenda_only"] == 0 and a["record_only"] == 0 for a in align),
        "order_notes_in_records": {k: {"sentences": len(v), "sessions": len(note_sessions[k])} for k, v in notes.items()},
        "sessions_with_moved_or_withdrawn_note": len(set(note_sessions.get("moved", [])) | set(note_sessions.get("withdrawn", []))),
        "sessions_with_record": n_rec,
        "examples_moved": [h["sentence"] for h in notes.get("moved", []) if re.search(r"TOP|vorgezogen", h["sentence"])][:8],
        "updated_agendas": upd,
        "per_session": align,
    }

    # M4 outcome per type
    cross = collections.defaultdict(collections.Counter)
    for p in pairs:
        cross[p["type"]][p["outcome"]] += 1
    res["M4_outcome_by_type"] = {
        t: {"items": sum(c.values()), "formula_only_share": round(sum(v for k, v in c.items() if k in FORMULA) / sum(c.values()), 2),
            "outcomes": dict(c.most_common())} for t, c in sorted(cross.items(), key=lambda kv: -sum(kv[1].values()))}
    allc = collections.Counter(p["outcome"] for p in pairs)
    res["M4_outcome_all"] = {"items": len(pairs), "outcomes": dict(allc.most_common()),
                             "formula_only": sum(v for k, v in allc.items() if k in FORMULA)}

    # M5 predict formula-only from the agenda type, by time split (learn on 2011-2021, test on 2022-2026)
    train = [p for p in pairs if p["year"] <= 2021]
    test = [p for p in pairs if p["year"] >= 2022]
    share = {}
    for t in {p["type"] for p in train}:
        ps = [p for p in train if p["type"] == t]
        share[t] = (sum(p["outcome"] in FORMULA for p in ps) / len(ps), len(ps))
    is_f = lambda p: p["outcome"] in FORMULA
    majority = sum(map(is_f, train)) / len(train) >= 0.5
    m5 = {"train": "2011–2021", "test": "2022–2026", "train_items": len(train), "test_items": len(test),
          "train_formula_share_by_type": {t: {"share": round(v, 2), "items": n} for t, (v, n) in
                                          sorted(share.items(), key=lambda kv: -kv[1][0])},
          "test_formula_share": round(sum(map(is_f, test)) / len(test), 3),
          "majority_baseline_accuracy": round(sum(is_f(p) == majority for p in test) / len(test), 3)}
    for thr in (0.5, 0.9, 0.95):
        sure = {t for t, (v, n) in share.items() if v >= thr and n >= 20}
        pred = [p for p in test if p["type"] in sure]
        m5[f"rule_types_with_train_share_>={thr}"] = {
            "types": sorted(sure), "test_items_covered": len(pred), "coverage": round(len(pred) / len(test), 3),
            "precision_formula": round(sum(map(is_f, pred)) / max(1, len(pred)), 3)}
    res["M5_predict_formula_from_title"] = m5

    # M6 label style over time
    styles = collections.defaultdict(collections.Counter)
    consent = collections.Counter()
    for p in pairs:
        for lab in p["labels"]:
            if re.search(r"(?i)mehrheit", lab):
                styles[p["year"]][label_style(lab)] += 1
            if re.search(r"Einvernehmen", lab):
                consent[p["year"]] += 1
    res["M6_label_style"] = {"majority_labels_by_year": {y: dict(c) for y, c in sorted(styles.items())},
                             "einvernehmen_by_year": dict(sorted(consent.items()))}
    kn = collections.Counter()
    for p in pairs:
        m = re.search(r"(Die Mitglieder des Kreistag\w* nehmen|Der Kreistag nimmt)", p["decision"])
        if m:
            kn[("Die Mitglieder des Kreistags nehmen … zur Kenntnis" if m.group(1).startswith("Die") else
                "Der Kreistag nimmt … zur Kenntnis", period(p["year"]))] += 1
    res["M6_label_style"]["kenntnisnahme_formula"] = {f"{a} | {b}": c for (a, b), c in sorted(kn.items())}

    # M7 vocabulary the decision text adds
    gains = [len(p["decision_stems"] - p["title_stems"]) / max(1, len(p["decision_stems"]))
             for p in pairs if len(p["decision_stems"]) >= 5]
    res["M7_vocabulary"] = {
        "items_with_decision_text": len(gains),
        "title_stems_median": st.median(len(p["title_stems"]) for p in pairs),
        "decision_stems_median": st.median(len(p["decision_stems"]) for p in pairs if len(p["decision_stems"]) >= 5),
        "share_new_in_decision_median": round(st.median(gains), 2),
    }
    df = collections.Counter(w for p in pairs for w in (p["title_stems"] | p["decision_stems"]))
    n_items = len(pairs)
    res["M7_vocabulary"]["most_widespread_stems"] = [(w, round(c / n_items, 2)) for w, c in df.most_common(15)]

    # M8 planned time
    timed = [r for r in rows if r["dauer"]]
    by_t = collections.defaultdict(list)
    for r in timed:
        by_t[r["type"]].append(r["dauer"])
    res["M8_planned_minutes"] = {
        "agendas_with_times": len({r["date"] for r in timed}), "items_with_time": len(timed),
        "first": min((r["date"] for r in timed), default=None),
        "by_type": {t: {"items": len(v), "median_min": st.median(v), "max_min": max(v)} for t, v in
                    sorted(by_t.items(), key=lambda kv: -st.median(kv[1]))},
        "session_total_median_min": st.median([sum(r["dauer"] for r in timed if r["date"] == d)
                                               for d in {r["date"] for r in timed}]) if timed else None,
    }
    timed_pairs = [p for p in pairs if p["dauer"]]
    res["M8_planned_minutes"]["minutes_by_outcome"] = {
        o: {"items": len(v), "median_min": st.median(v)} for o, v in
        ((o, [p["dauer"] for p in timed_pairs if p["outcome"] == o]) for o in sorted({p["outcome"] for p in timed_pairs})) if v}

    # M9 mood in reported dass-clauses (spaCy morphology)
    try:
        import spacy
        nlp = spacy.load("de_core_news_md", disable=["ner"])
    except Exception as e:                                  # spaCy missing: skip M9
        res["M9_mood"] = {"skipped": str(e)}
        nlp = None
    if nlp:
        mood = collections.defaultdict(collections.Counter)
        for s in sessions:
            if not s["record"]:
                continue
            clauses = re.findall(r"\b(?:teilt\w*|sagt\w*|erklärt|berichtet|informiert|weist\w*|betont|erläutert)[^.]{0,80}?"
                                 r",\s*dass\s+([^.;]{5,200})", s["record_flat"])
            for doc in nlp.pipe(clauses[:200]):
                fin = [t for t in doc if t.morph.get("VerbForm") == ["Fin"]]
                if fin:
                    last = fin[-1]
                    m = last.morph.get("Mood")
                    mood[period(s["year"])]["Konjunktiv" if m == ["Sub"] else "Indikativ"] += 1
        res["M9_mood"] = {p: {**dict(c), "konjunktiv_share": round(c["Konjunktiv"] / max(1, sum(c.values())), 3)}
                          for p, c in sorted(mood.items())}

    for a in align:                                   # counts only in the saved file, no titles
        a.pop("agenda_only_titles", None)
        a.pop("record_only_titles", None)
    OUT.mkdir(exist_ok=True)
    (OUT / "study.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
    brief = {k: v for k, v in res.items() if k not in ("M3_agenda_vs_record",)}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=list)[:9000])
    print(json.dumps({k: v for k, v in res["M3_agenda_vs_record"].items() if k not in ("per_session",)},
                     ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
