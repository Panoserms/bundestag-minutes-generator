"""Parse system A's Markdown Niederschrift into NDS-JSON (best effort; every rule is listed here and tested in
tests/test_eval_parser.py).

    from baseline.parse_md import parse_markdown
    nds = parse_markdown(md_text, "21100", compute={...})
    .venv/bin/python -m baseline.parse_md outputs/21100_A.md        -> outputs/21100_A.json

Rules
  1. Items. A Markdown heading that names an item ("### TOP 35 – Titel", "### ZP 10 – …", "### Punkt 35 der
     Tagesordnung – …", "### Tagesordnungspunkt 35", "### TO-TOP 33 …") opens an item. Further item numbers in the
     heading before the title ("ZP 10 und ZP 11", "TOP 7 a und b") are kept as `joint_ids`. A non-item heading at the
     same or a higher level closes the item (its text goes to `preamble`); a deeper non-item heading ("#### Abstimmung")
     stays inside the item and is ignored.
  2. Blocks. The body of an item is split into blocks at blank lines. Emphasis markers (** __), quote markers (>) and
     list markers are ignored for classification. Quotation marks are canonicalised to „…“ (from »…«, «…», “…” and
     "…").
  3. Speech. A block (or a sentence inside a block, see 6) that STARTS with a speaker - "Name (Group)", "Name,
     Bundesministerin für …, …", or a chair title + name ("Vizepräsidentin Andrea Lindholz eröffnete …") - opens a
     speech. The parenthesis must look like a parliamentary group or an office (CDU, SPD, AfD, GRÜNE, Linke, minister,
     Staatssekretär, …). Leading words such as "Die Abgeordnete" are dropped from the name.
  4. Continuation. A block without a speaker that follows a speech and starts with a pronoun or connector (Sie, Er,
     Zudem, Abschließend, …) or with a quotation mark continues that speech.
  5. Parts. A speech is split into sentences (quote-aware: no sentence break inside „…“; abbreviations and ordinal
     numbers are no breaks). In each sentence every „…“ span becomes a QUOTE part. If the text before the first span
     ends with ":" it is the quote's `lead` ("Sie forderte:"); otherwise the quote is `inline` (embedded in a reported
     sentence). The rest of the sentence - with the quotes replaced by „…“ - becomes a REPORTED part when it has at
     least 3 words beyond the lead. A sentence without quotes is a REPORTED part, or a FORMULA part when it is a
     short "bat um Zustimmung" sentence. The speaker prefix is removed from the first sentence and kept, with the rest
     of that sentence, as the speech's `opening` (used for the frame-monotony metric).
  6. Speaker switch inside a block. A sentence that starts with a new "Name (Group)" (optionally after "In einer
     Zwischenfrage/Kurzintervention …" + verb), or that starts with the surname of a speaker already seen in the item
     followed by a reporting verb ("Wiegelmann erwiderte, …"), starts a new speech.
  7. Kind: Zwischenfrage / Kurzintervention / Frage when the opening names it, Erwiderung for erwiderte/entgegnete/
     antwortete, Sitzungsleitung for the chair, otherwise Rede.
  8. Votes. A block without a speaker that starts like a decision ("Der Bundestag beschloss|lehnte|nahm …", "Der Punkt
     wurde abgesetzt", "Der Antrag wurde angenommen", "Überweisung …") is a VOTE: result_sentence = its first line or
     sentence, decision = the quoted text („…“) in the same block or the next block(s) that consist of a quotation;
     outcome = abgesetzt | Überweisung | Ablehnung | Annahme | sonstige. Committees after "Überweisung an:" (list
     lines or a comma list) become `referrals`.
  9. Other blocks: "Drucksache(n) 21/…" lines -> item `drucksachen`; "Punkt N der Tagesordnung" lines -> skipped;
     everything else (Hinweise, "Gemeinsam beraten mit …", chair narrative without a chair title) -> item `notes`.
Everything the parser could not place is counted in nds["parse"]["warnings"].
"""
import json
import re
import sys
from pathlib import Path

PARSER_VERSION = "parse_md 1.0"

# ------------------------------------------------------------------ patterns

HEADING = re.compile(r"^\s{0,3}(#{1,6})\s*(.*?)\s*#*\s*$")
ITEM_NUM = re.compile(r"(TO-)?(TOP|ZP|Tagesordnungspunkt|Zusatzpunkt)\s*(\d+)|Punkt\s+(\d+)\s+der\s+Tagesordnung", re.I)
TITLE_SEP = re.compile(r"\s+[–—-]\s+|:\s+|\s*[–—]\s*")
DS = re.compile(r"\b\d{2}/\d{1,5}\b")

TITLE_TOK = r"(?:Dr\.|Prof\.|h\.\s?c\.|Dipl\.-[A-Za-zäöü]+\.?)"
NAMEPART = r"[A-ZÄÖÜ][a-zäöüßéèáàóòíìúùçñëïøåł'’]+(?:-[A-ZÄÖÜ][a-zäöüßéèáàóòíìúùçñëïøåł'’]+)*"
PARTICLE = r"(?:von|van|vom|zu|zur|der|den|de|di|da|du|ten|ter|al|el|dos|das)"
NAME = rf"(?:{TITLE_TOK}\s+)*{NAMEPART}(?:\s+(?:{PARTICLE}\s+)*(?:{TITLE_TOK}\s+)*{NAMEPART}){{0,5}}"
FUNC = (r"(?:Bundeskanzler(?:in)?|Bundesminister(?:in)?|Staatsminister(?:in)?|Parlamentarische[rn]?\s+Staatssekretär(?:in)?"
        r"|Staatssekretär(?:in)?|Bundesbeauftragte[r]?|Wehrbeauftragte[r]?|Ministerpräsident(?:in)?)")
CHAIR = r"(?:Präsident(?:in)?|Vizepräsident(?:in)?|Alterspräsident(?:in)?)"
GROUPLIKE = re.compile(r"CDU|CSU|SPD|AfD|GRÜNE|Grüne|BÜNDNIS|Linke|LINKE|fraktionslos|BSW|FDP|SSW|[Mm]inister|"
                       r"Staatssekretär|Bundeskanzler|[Bb]eauftragte|Bundesregierung|Präsident|BM[A-Z]|Senator")
SPK_PAREN = re.compile(rf"^(?P<pre>(?:{FUNC}(?:\s+(?:für|der|des|im)\s+[^()]+?)?\s+)?)(?P<name>{NAME})\s*"
                       rf"\((?P<group>[^()]{{1,160}})\)")
SPK_APPOS = re.compile(rf"^(?P<name>{NAME}),\s*(?P<group>{FUNC}[^,]{{0,140}}),\s")
SPK_CHAIR = re.compile(rf"^(?P<title>{CHAIR})\s+(?P<name>{NAME})(?=\s+[a-zäöüß])")
# "In einer Zwischenfrage fragte Name (Group) …", "Auf eine Kurzintervention von … antwortete" is NOT a switch
SWITCH_LEAD = re.compile(r"^(?:(?:In|Mit) (?:einer|seiner|ihrer) (?:Zwischenfrage|Kurzintervention|Erwiderung|Nachfrage|"
                         r"Frage|Bemerkung|Antwort)\w*\s+(?:\w+\s+){0,2}?)(?=[A-ZÄÖÜ])")
DROP_NAME_LEAD = {"Die", "Der", "Das", "Abgeordnete", "Abgeordneter", "Herr", "Frau", "Kollege", "Kollegin", "Auch",
                  "Zudem", "Dann", "Danach", "Anschließend", "Schließlich", "Für", "Als", "Zuletzt", "Zunächst",
                  "Fraktionslose", "Fraktionsloser", "Bundestagsabgeordnete", "Bundestagsabgeordneter"}
CONTINUATION = re.compile(r"^(?:„|Sie\b|Er\b|Ihr\b|Ihre\b|Sein\b|Seine\b|Dabei\b|Zudem\b|Außerdem\b|Weiter\b|Ferner\b|"
                          r"Abschließend\b|Zum Schluss\b|Darüber hinaus\b|Zugleich\b|Gleichzeitig\b|Auch\b|Schließlich\b|"
                          r"Daneben\b|Dazu\b|Hierzu\b|Ebenso\b|Insbesondere\b|Mit Blick\b|Zum\b|Zur\b)")
REPORT_VERB = re.compile(r"^(?:erwiderte|entgegnete|antwortete|replizierte|fragte|sagte|erklärte|betonte|stellte|wies|"
                         r"räumte|hielt|warf|kritisierte|forderte|bekräftigte|verwies|unterstrich|ergänzte|bat|"
                         r"widersprach|bestritt|meinte|führte|wandte)\b")
VOTE_START = re.compile(r"^(?:Der (?:Deutsche )?Bundestag|Das (?:Plenum|Haus)|Der Punkt|Die Punkte|Der Tagesordnungspunkt|"
                        r"Der Zusatzpunkt|Die Beschlussempfehlung|Der Antrag|Die Anträge|Der Gesetzentwurf|"
                        r"Die Gesetzentwürfe|Die Vorlage|Die Vorlagen|Der Entschließungsantrag|Die Überweisung|"
                        r"Überweisung|Abstimmung|Ergebnis|Beschluss|Die Wahl|In namentlicher)", re.I)
VOTE_CUE = re.compile(r"beschloss|lehnte|nahm\b|angenommen|abgelehnt|überwies|überwiesen|Überweisung|abgesetzt|"
                      r"verabschiedete|stimmte|zugestimmt|Zustimmung|Annahme|Ablehnung|erledigt|Kenntnis|wählte|gewählt|"
                      r"Ja-Stimmen|Nein-Stimmen", re.I)
DS_LINE = re.compile(r"^(?:Drucksachen?|Drs\.)\s*:?\s*\d{2}/", re.I)
TITLE_LINE = re.compile(r"^(?:Punkt \d+ der Tagesordnung|Zusatzpunkt \d+|Tagesordnungspunkt \d+)\s*[a-z]?\)?\s*:?$")
LIST_MARK = re.compile(r"^\s*(?:[-*•–]|\d+\.)\s+")
FORMULA = re.compile(r"\bbat\b[^.]{0,40}\bum Zustimmung\b|\bwarb\b[^.]{0,30}\bfür Zustimmung\b")
ABBR = {"Dr", "Prof", "Nr", "Abs", "Art", "St", "bzw", "ca", "vgl", "z", "B", "u", "a", "d", "h", "S", "Mio", "Mrd",
        "Tsd", "Std", "Min", "gem", "Abg", "Bd", "Jh", "Kap", "Ziff", "lit", "Buchst", "inkl", "evtl", "ggf", "usw",
        "etc", "Mt", "Hr", "Fr", "Dipl", "Ing", "rer", "nat", "pol", "c", "v", "Chr", "Std", "Jan", "Feb", "Okt",
        "Nov", "Dez", "Aug", "Sept", "Sep"}
VERB_LEX = re.compile(r"\b(sagte|erklärte|betonte|forderte|kritisierte|warnte|fragte|stellte|begrüßte|kündigte|führte|"
                      r"wies|verwies|hob|unterstrich|bezeichnete|warf|mahnte|erinnerte|berichtete|erläuterte|begründete|"
                      r"sprach|appellierte|argumentierte|bekräftigte|entgegnete|erwiderte|antwortete|lobte|beklagte|"
                      r"bemängelte|bat|nannte|verteidigte|unterstützte|lehnte|plädierte|zitierte|schilderte|beschrieb|"
                      r"räumte|verlangte|versicherte|hielt|fügte|schloss|wandte|richtete|machte|zog|sah|meinte|"
                      r"befürwortete|bedauerte|rief|monierte|beanstandete|legte|äußerte|ergänzte|widersprach|"
                      r"bestritt|bezweifelte|verdeutlichte|skizzierte|resümierte|konstatierte|bilanzierte|gab|wertete|"
                      r"zeigte|sicherte|sprach|stimmte|kündigte|warb|eröffnete|erteilte|rügte|teilte|unterbrach|dankte|"
                      r"gedachte|gratulierte|verkündete|stellte|schilderte|vertrat|beschrieb|kam|ging|verlas|"
                      r"informierte|leitete|bilanzierte|hinterfragte|reagierte|erkannte|sprach)\b")
PARTICLES = {"aus", "vor", "an", "hin", "zu", "ab", "fest", "dar", "ein", "heraus", "zurück", "entgegen", "mit", "auf",
             "klar", "fort", "bei", "zusammen", "her", "voran", "nach"}


# ------------------------------------------------------------------ small helpers

def _canon_quotes(t):
    """All double-quote styles -> „…“ (German low-high)."""
    t = re.sub(r"»([^»«]*)«", r"„\1“", t)
    t = re.sub(r"«([^»«]*)»", r"„\1“", t)
    if "”" in t:
        t = re.sub(r"[“„]([^“”„]*)”", r"„\1“", t)
    t = re.sub(r"\"([^\"\n]*)\"", r"„\1“", t)
    return t


def _clean(t):
    t = re.sub(r"(\*\*|__)", "", t)
    t = re.sub(r"(?m)^\s*>\s?", "", t)
    return t.strip()


def _words(t):
    return re.findall(r"[0-9A-Za-zÄÖÜäöüßéèáàóòíìúùçñ]+(?:[-'’][0-9A-Za-zÄÖÜäöüß]+)*", t)


def item_id_of(kind, num, to_prefix=""):
    k = kind.lower()
    base = ("TOP " if k in ("top", "tagesordnungspunkt", "punkt") else "ZP ") + str(int(num))
    return ("TO-" if to_prefix else "") + base


def heading_items(text):
    """'TOP 35 – Titel' -> (['TOP 35'], 'Titel'); 'ZP 10 und ZP 11: …' -> (['ZP 10', 'ZP 11'], '…'); None if the
    heading does not START with an item number."""
    t = _clean(text)
    m = ITEM_NUM.match(t)
    if not m:
        return None
    # the id part runs up to the title separator
    sep = TITLE_SEP.search(t, m.end())
    idpart, title = (t[:sep.start()], t[sep.end():]) if sep else (t, "")
    ids = []
    last_kind = None
    for mm in re.finditer(r"(TO-)?(TOP|ZP|Tagesordnungspunkt|Zusatzpunkt)\s*(\d+)|Punkt\s+(\d+)\s+der\s+Tagesordnung|"
                          r"(?:und|sowie|,)\s+(\d+)\b", idpart, re.I):
        if mm.group(2):
            last_kind = mm.group(2)
            ids.append(item_id_of(mm.group(2), mm.group(3), mm.group(1)))
        elif mm.group(4):
            last_kind = "TOP"
            ids.append(item_id_of("TOP", mm.group(4)))
        elif mm.group(5) and last_kind:
            ids.append(item_id_of(last_kind, mm.group(5)))
    ids = list(dict.fromkeys(ids))
    if not title:                                   # "### TOP 35" alone, or "### Punkt 35 der Tagesordnung"
        title = re.sub(r"^\s*[a-z](?:\s*(?:[-–,]|und|bis)\s*[a-z])*\)?\s*", "", t[m.end():]).strip(" –-:")
    return ids, title.strip()


def split_sentences(text):
    """Quote-aware sentence split: no break inside „…“, after abbreviations or after ordinal digits."""
    out, start, depth, i = [], 0, 0, 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "„":
            depth += 1
        elif c == "“":
            depth = max(0, depth - 1)
        elif c in ".!?" and depth == 0:
            j = i + 1
            while j < n and text[j] in "“\")]":
                j += 1
            m = re.match(r"\s+(?=[A-ZÄÖÜ„(\[])", text[j:])
            if m:
                prev = re.search(r"([A-Za-zÄÖÜäöüß0-9]+)$", text[start:i])
                tok = prev.group(1) if prev else ""
                if c == "." and (tok in ABBR or tok.isdigit() or (len(tok) == 1 and tok.isalpha())):
                    i += 1
                    continue
                out.append(text[start:j].strip())
                start = j + m.end()
                i = start
                continue
        i += 1
    if text[start:].strip():
        out.append(text[start:].strip())
    return out


def quote_spans(s):
    """Top-level „…“ spans of a sentence: [(start, end, inner)]. An unclosed „ runs to the end (flag 'open')."""
    spans, i = [], 0
    while True:
        a = s.find("„", i)
        if a < 0:
            break
        depth, j = 0, a
        while j < len(s):
            if s[j] == "„":
                depth += 1
            elif s[j] == "“":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= len(s):
            spans.append((a, len(s), s[a + 1:].strip(), True))
            break
        spans.append((a, j + 1, s[a + 1:j].strip(), False))
        i = j + 1
    return spans


def frame_verb(text):
    """Best-effort reporting verb of a lead / opening ('stellte den Entwurf vor:' -> 'stellte vor')."""
    m = VERB_LEX.search(text or "")
    if not m:
        return None
    v = m.group(1)
    clause = re.split(r"[,:;]", text[m.end():], maxsplit=1)[0]
    toks = clause.split()
    if toks and toks[-1].strip(".") in PARTICLES:
        v += " " + toks[-1].strip(".")
    return v


def match_speaker(s):
    """Speaker prefix at the start of s -> (speaker, group, role, prefix_end) or None."""
    t = s
    lead = SWITCH_LEAD.match(t)
    off = lead.end() if lead else 0
    t2 = t[off:]
    m = SPK_PAREN.match(t2)
    if m and GROUPLIKE.search(m.group("group")):
        name, group = m.group("name"), m.group("group").strip()
        pre = (m.group("pre") or "").strip()
        if pre and not group:
            group = pre
        role = "chair" if re.match(CHAIR, group) else "member"
        return _strip_name(name), group, role, off + m.end()
    m = SPK_APPOS.match(t2)
    if m:
        return _strip_name(m.group("name")), m.group("group").strip(), "member", off + m.end()
    if not lead:
        m = SPK_CHAIR.match(t2)
        if m:
            return f"{m.group('title')} {_strip_name(m.group('name'))}", "", "chair", m.end()
    return None


def _strip_name(name):
    toks = name.split()
    while len(toks) > 1 and toks[0] in DROP_NAME_LEAD:
        toks.pop(0)
    return " ".join(toks)


def surname(name):
    toks = [t for t in re.sub(r"\(.*?\)", "", name).split() if t[:1].isupper() and not t.endswith(".")]
    return toks[-1] if toks else name


def kind_of(opening, role):
    if role == "chair":
        return "Sitzungsleitung"
    o = opening[:160]
    if re.search(r"Kurzintervention", o):
        return "Kurzintervention"
    if re.search(r"Zwischenfrage", o):
        return "Zwischenfrage"
    if re.match(r"\W*(erwiderte|entgegnete|antwortete|replizierte)", o) or re.search(r"\b(erwiderte|entgegnete)\b", o[:60]):
        return "Erwiderung"
    if re.search(r"\bFrage\b|\bfragte\b", o[:60]):
        return "Frage"
    return "Rede"


# ------------------------------------------------------------------ parts of one speech

def sentence_parts(s, is_first):
    """Parts of one sentence of a speech (the speaker prefix already removed)."""
    parts = []
    spans = quote_spans(s)
    if not spans:
        if FORMULA.search(s) and len(_words(s)) <= 10:
            return [{"type": "formula", "text": s.strip(), "verb": None, "source": "llm"}], 0
        return [{"type": "reported", "text": s.strip(), "verb": frame_verb(s), "source": "llm"}], 0
    pre = s[:spans[0][0]]
    colon = pre.rstrip().endswith(":")
    lead = pre.strip() if colon else ""
    unclosed = 0
    for k, (a, b, inner, open_) in enumerate(spans):
        if not inner:
            continue
        unclosed += open_
        parts.append({"type": "quote", "text": inner, "verb": frame_verb(lead) if (k == 0 and colon) else None,
                      "source": "llm", "lead": lead if k == 0 else "", "inline": not (k == 0 and colon)})
    residue = s
    for a, b, inner, open_ in reversed(spans):
        residue = residue[:a] + "„…“" + residue[b:]
    rest = residue[len(pre):] if colon else residue
    rest_words = [w for w in _words(rest)]
    if len(rest_words) >= 3:
        r = residue.strip()
        if FORMULA.search(rest) and len(rest_words) <= 6:
            parts.append({"type": "formula", "text": rest.replace("„…“", "").strip(" .,;"), "verb": None,
                          "source": "llm"})
        else:
            parts.append({"type": "reported", "text": r, "verb": frame_verb(r), "source": "llm", "has_quote": True})
    return parts, unclosed


# ------------------------------------------------------------------ main parser

def _new_item(ids, title, heading):
    return {"id": ids[0], "title": title, "notes": [], "speeches": [], "votes": [], "referrals": [],
            "drucksachen": [], "joint_ids": ids[1:], "heading": heading}


def _outcome(t):
    if re.search(r"abgesetzt", t):
        return "abgesetzt"
    if re.search(r"Überweisung|überwies|überwiesen", t):
        return "Überweisung"
    if re.search(r"lehnte\b.*\bab\b|abgelehnt|Ablehnung", t, re.S):
        return "Ablehnung"
    if re.search(r"beschloss|nahm\b.*\ban\b|angenommen|Annahme|verabschiedete|stimmte\b.*\bzu\b|zugestimmt", t, re.S):
        return "Annahme"
    return "sonstige"


def _referrals(text):
    """Committees after 'Überweisung an:' / 'überwiesen an' (list lines or a comma/und list)."""
    m = re.search(r"(?:Überweisung|überwies\w*|überwiesen)[^:]*?\ban(?:\s+den|\s+die)?\s*:?\s*(.*)$", text, re.S)
    if not m:
        return []
    rest = m.group(1).strip()
    lines = [LIST_MARK.sub("", l).strip() for l in rest.splitlines() if l.strip()]
    if len(lines) > 1:
        items = lines
    else:
        items = re.split(r",\s*|\s+und\s+(?=(?:den|die|das)?\s*[A-ZÄÖÜ])|;\s*", lines[0] if lines else "")
    out = []
    for c in items:
        c = re.sub(r"^(?:an\s+)?(?:den|die|das|dem)\s+", "", c.strip()).strip(" .;:")
        if c and re.search(r"[Aa]usschuss|A\. f\.|Gremium|Kommission|Beirat|federführend", c):
            out.append(c)
    return out


def _parse_speech_text(text, speaker, group, role, item_speakers, warnings):
    """-> list of speeches (more than one when the speaker changes inside the block, rule 6)."""
    speeches = []
    cur = {"speaker": speaker, "group": group, "role": role, "kind": None, "opening": "", "parts": []}
    sents = split_sentences(text)
    for k, s in enumerate(sents):
        if k > 0:
            sw = match_speaker(s)
            if sw is None:
                m = re.match(r"^(?:Dr\.\s+)?([A-ZÄÖÜ][\w\-]+)\s+(\w+)", s)
                if m and m.group(1) in item_speakers and REPORT_VERB.match(m.group(2)):
                    full = item_speakers[m.group(1)]
                    sw = (full[0], full[1], full[2], m.start(2))
            if sw and not (sw[0] == cur["speaker"]):
                speeches.append(cur)
                cur = {"speaker": sw[0], "group": sw[1], "role": sw[2], "kind": None, "opening": "", "parts": []}
                item_speakers[surname(sw[0])] = (sw[0], sw[1], sw[2])
                s = s[sw[3]:].strip()
                lead_words = SWITCH_LEAD.match(sents[k])
                cur["opening"] = s
                cur["kind"] = kind_of((lead_words.group(0) if lead_words else "") + " " + s, sw[2])
        if not cur["opening"]:
            cur["opening"] = s
        parts, unclosed = sentence_parts(s, k == 0)
        if unclosed:
            warnings.append(f"unclosed quote: {s[:60]}")
        cur["parts"].extend(parts)
    speeches.append(cur)
    for sp in speeches:
        if sp["kind"] is None:
            sp["kind"] = kind_of(sp["opening"], sp["role"])
    return speeches


def parse_markdown(md, sid, compute=None):
    """Markdown of system A -> NDS-JSON dict (see docs/ARCHITECTURE.md), plus parse diagnostics in nds['parse']."""
    md = md.replace("\r\n", "\n")
    lines = md.split("\n")
    items, preamble, warnings = [], [], []
    cur, cur_level, other = None, 0, None
    sections = []                                    # (item or None, [lines])
    for ln in lines:
        h = HEADING.match(ln)
        if h:
            level = len(h.group(1))
            hi = heading_items(h.group(2))
            if hi and hi[0]:
                cur = _new_item(hi[0], hi[1], h.group(2).strip())
                cur_level = level
                items.append(cur)
                sections.append((cur, []))
                continue
            if cur is not None and level > cur_level:
                continue                             # sub-heading inside the item (rule 1)
            cur = None
            other = {"heading": h.group(2).strip(), "text": ""}
            preamble.append(other)
            sections.append((None, []))
            continue
        if not sections:
            other = {"heading": "", "text": ""}
            preamble.append(other)
            sections.append((None, []))
        sections[-1][1].append(ln)
    pre_iter = iter(preamble)
    for item, body in sections:
        text = "\n".join(body)
        if item is None:
            next(pre_iter)["text"] = text.strip()
            continue
        _fill_item(item, text, warnings)
    counts = {"items": len(items),
              "speeches": sum(len(i["speeches"]) for i in items),
              "quote_parts": sum(1 for i in items for s in i["speeches"] for p in s["parts"] if p["type"] == "quote"),
              "reported_parts": sum(1 for i in items for s in i["speeches"] for p in s["parts"]
                                    if p["type"] == "reported"),
              "formula_parts": sum(1 for i in items for s in i["speeches"] for p in s["parts"] if p["type"] == "formula"),
              "votes": sum(len(i["votes"]) for i in items), "referrals": sum(len(i["referrals"]) for i in items),
              "notes": sum(len(i["notes"]) for i in items), "preamble_sections": len(preamble),
              "warnings": len(warnings)}
    return {"sid": sid, "system": "A", "items": items,
            "compute": compute or {"rule_seconds": 0.0, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
                                   "cost_usd": 0.0, "llm_seconds": 0.0},
            "preamble": preamble,
            "parse": {"version": PARSER_VERSION, "counts": counts, "warnings": warnings[:50]}}


def _blocks(text):
    out, buf = [], []
    for ln in text.split("\n"):
        if ln.strip():
            buf.append(ln)
        elif buf:
            out.append("\n".join(buf))
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out


def _fill_item(item, text, warnings):
    blocks = [_canon_quotes(_clean(b)) for b in _blocks(text)]
    item_speakers = {}
    last = None                                       # 'speech' | 'vote' | 'other'
    for raw in blocks:
        b = raw.strip()
        flat = " ".join(LIST_MARK.sub("", l).strip() for l in b.splitlines())
        flat = re.sub(r"\s+", " ", flat).strip()
        if not flat:
            continue
        if TITLE_LINE.match(flat):
            continue
        if DS_LINE.match(flat) and len(_words(re.sub(DS, "", flat))) <= 6:
            item["drucksachen"] += [d for d in DS.findall(flat) if d not in item["drucksachen"]]
            last = "other"
            continue
        sp = match_speaker(flat)
        if sp:
            speaker, group, role, end = sp
            rest = flat[end:].strip()
            item_speakers[surname(speaker)] = (speaker, group, role)
            item["speeches"].extend(_parse_speech_text(rest, speaker, group, role, item_speakers, warnings))
            last = "speech"
            continue
        if last == "vote" and flat.startswith("„"):
            v = item["votes"][-1]
            v["decision"] = (v["decision"] + " " if v["decision"] else "") + flat.strip("„“ ")
            continue
        if last == "vote" and item["votes"] and item["votes"][-1]["outcome"] == "Überweisung" and \
                re.search(r"[Aa]usschuss|A\. f\.|federführend", flat) and len(flat) < 400:
            item["referrals"] += _referrals("Überweisung an: " + b)
            continue
        if VOTE_START.match(flat) and VOTE_CUE.search(flat):
            first = re.split(r"(?<=[.:])\s+(?=[A-ZÄÖÜ„])", flat, maxsplit=1)[0]
            quoted = " ".join(inner for _, _, inner, _ in quote_spans(flat[len(first):]))
            v = {"result_sentence": first, "decision": quoted, "outcome": _outcome(first + " " + quoted)}
            item["votes"].append(v)
            if v["outcome"] == "Überweisung":
                item["referrals"] += _referrals(b)
            last = "vote"
            continue
        if last == "speech" and item["speeches"] and CONTINUATION.match(flat):
            s = item["speeches"][-1]
            more = _parse_speech_text(flat, s["speaker"], s["group"], s["role"], item_speakers, warnings)
            s["parts"].extend(more[0]["parts"])
            item["speeches"].extend(more[1:])
            continue
        item["notes"].append(flat)
        last = "other"


def main():
    for f in sys.argv[1:]:
        p = Path(f)
        sid = p.stem.split("_")[0]
        nds = parse_markdown(p.read_text(encoding="utf-8"), sid)
        old = p.with_suffix(".json")
        if old.exists():                               # keep the compute record of the run
            try:
                o = json.loads(old.read_text(encoding="utf-8"))
                nds["compute"] = o.get("compute", nds["compute"])
                if "truncation" in o:
                    nds["truncation"] = o["truncation"]
            except json.JSONDecodeError:
                pass
        old.write_text(json.dumps(nds, ensure_ascii=False, indent=1), encoding="utf-8")
        print(p.name, nds["parse"]["counts"])


if __name__ == "__main__":
    main()
