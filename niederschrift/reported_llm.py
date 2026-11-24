"""System B: the only place where the hybrid uses an LLM.

The rules have already chosen the excerpt that is printed in the speaker's own words (about half of each speech,
on-topic sentences first). The LLM writes the one-sentence Kurzfassung that stands on top of it: the speaker's main
point, as a frame without a subject (a speech-act verb in the present
tense: "fordert, dass …", "kritisiert …", "fragt, ob …"). One call per agenda item covers all its speeches. The LLM
sees only the best-scored excerpt sentences and a few off-topic ones, never the whole record.

Every reply is checked by rule before it is used (`check`):
  - one sentence, at most 30 words, no quotation marks
  - starts with a lower-case verb, not with a pronoun or an article (the speaker line is the subject)
  - no digit that is not in the source sentences
  - no proper name that is not in the source sentences (inflected and compound forms count), the speaker label,
    the item's speakers or the group list
A reply that fails is replaced by the rule Kurzfassung (kurz.rule), or left out if no rule applies.
"""
import re

from . import llm

SYSTEM = ("Du bist Schriftführer im Deutschen Bundestag und schreibst die Niederschrift einer Sitzung. "
          "Du schreibst knapp, sachlich und ohne Wertung.")

PROMPT = """Tagesordnungspunkt {item}: {title}

Unter jedem Redebeitrag stehen die wichtigsten Sätze des Redners. Sie werden in der Niederschrift wörtlich
wiedergegeben. Darüber steht eine Kurzfassung, die du schreibst.

Schreibe je Redebeitrag GENAU EINEN Satz, höchstens 25 Wörter: die Hauptaussage oder Position des Redners.
- Kein Subjekt: Der Name steht schon in der Zeile darüber. Beginne mit einem Verb in der 3. Person Singular Präsens,
  klein geschrieben, z. B. „fordert …“, „kritisiert, dass …“, „lehnt den Antrag ab, weil …“, „fragt, ob …“,
  „stellt den Gesetzentwurf vor und …“, „begründet den Antrag mit …“, „verweist auf …“, „warnt vor …“.
- Wähle das Verb nach dem, was der Redner tut. Nicht jeden Satz mit „betont“ oder „erklärt“ beginnen.
- Indikativ, Präsens. Nur Inhalte, Namen und Zahlen aus diesen Sätzen. Keine Wertung, keine Anführungszeichen.
- Gib nur die Zeilen im Format „[Nummer] Satz“ aus, eine Zeile je Redebeitrag.

{blocks}"""


def label(d):
    g = d.get("group") or ""
    return f"{d['speaker']} ({g})" if g else d["speaker"]


KIND = {"Rede": "Rede", "Zwischenfrage": "Zwischenfrage", "Kurzintervention": "Kurzintervention",
        "Erwiderung": "Erwiderung", "Frage": "Frage", "Antwort": "Antwort der Bundesregierung"}


def build_prompt(item_id, title, entries):
    """entries: [(n, speaker_label, kind, [sentences])]"""
    blocks = []
    for n, lab, kind, sents in entries:
        blocks.append(f"[{n}] {lab} – {KIND.get(kind, kind)}\n" + " ".join(sents))
    return PROMPT.format(item=item_id, title=(title or "")[:300], blocks="\n\n".join(blocks))


def parse_reply(text, n_max):
    out = {}
    for line in text.splitlines():
        m = re.match(r"^\s*\[(\d+)\]\s*(.+?)\s*$", line)
        if m and int(m.group(1)) < n_max:
            out[int(m.group(1))] = m.group(2)
    return out


NAME = re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?\b")
GROUP_WORDS = {"CDU", "CSU", "SPD", "AfD", "Grünen", "Grüne", "Linke", "Linken", "BÜNDNIS", "Union", "Koalition",
               "Bundesregierung", "Regierung", "Bundestag", "Opposition", "Fraktion", "Fraktionen"}
BAD_START = {"er", "sie", "es", "man", "der", "die", "das", "dieser", "diese", "zudem", "außerdem"}


def _in_source(word, low):
    """An inflected or compound form of a source word counts as known: "Mindestbetrags" (source: Mindestbetrag),
    "Grünen-Antrag" (source: Grünen, Antrag)."""
    for part in word.lower().split("-"):
        if not any(len(stem) >= 4 and stem in low for stem in (part, part[:-1], part[:-2])):
            return False
    return True


def check(sentence, source_sents, speaker_label, nlp=None, names=()):
    """-> (ok, problems). See module docstring. `names`: surnames of the item's speakers, which may be named."""
    problems = []
    src = " ".join(source_sents)
    first = sentence.split()[0] if sentence.split() else ""
    if not first[:1].islower() or first.lower() in BAD_START:
        problems.append("start")
    if len(sentence.split()) > 30:
        problems.append("length")
    if len(re.findall(r"[.!?](?:\s|$)", sentence.strip())) > 1:
        problems.append("sentences")
    if re.search(r"[„“”\"]", sentence):
        problems.append("quote")
    for num in re.findall(r"\d+(?:[.,]\d+)*", sentence):
        if num not in src:
            problems.append(f"number:{num}")
    known = set(NAME.findall(src)) | set(NAME.findall(speaker_label)) | GROUP_WORDS | set(names)
    low = src.lower() + " " + speaker_label.lower()
    propn = None
    if nlp is not None:
        doc = nlp(sentence)
        propn = {t.text for t in doc if t.pos_ == "PROPN"} | {w for e in doc.ents for w in e.text.split()}
    for w in NAME.findall(sentence):
        # German capitalises every noun, so only a proper name that occurs nowhere in the sources counts
        # (parts of hyphenated compounds such as "5G-Abdeckung" are found as substrings of the source)
        if w in known or _in_source(w, low):
            continue
        if propn is not None and w not in propn:
            continue
        problems.append(f"name:{w}")
    return not problems, problems


def kurz_for_item(item_id, title, digests, tag, nlp=None):
    """-> ({speech index: {"text", "source": "llm", "ok", "problems"}}, llm record) for the digests that have
    d["kurz_sents"] (prepared by compose)."""
    entries, idx = [], []
    for k, d in enumerate(digests):
        sents = d.get("kurz_sents") or []
        if not sents:
            continue
        entries.append((len(entries), label(d), d.get("kind"), sents))
        idx.append(k)
    if not entries:
        return {}, None
    text, rec = llm.call(build_prompt(item_id, title, entries), SYSTEM, tag=tag, effort="low")
    replies = parse_reply(text, len(entries))
    names = {d["speaker"].split()[-1] for d in digests}
    out = {}
    for n, lab, kind, sents in entries:
        r = replies.get(n)
        if not r:
            continue
        ok, problems = check(r, sents, lab, nlp, names)
        out[idx[n]] = {"text": r, "source": "llm", "ok": ok, "problems": problems}
    return out, rec
