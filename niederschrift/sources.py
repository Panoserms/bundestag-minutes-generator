"""Read the three public sources of one Bundestag sitting into plain Python data.

    from niederschrift.sources import load_sitting
    s = load_sitting("21100")

Sources (all in data/raw/, see data/README.md):
  - Plenarprotokoll XML (the verbatim record). We keep the spoken text and the reactions printed after each
    paragraph ("(Beifall bei der SPD)"), but REMOVE every piece of agenda markup (<tagesordnungspunkt>, klasse T_*,
    roll-call lists AL_*): the system has to find the agenda items itself. The removed markup is returned separately
    as `gold`, for evaluation only.
  - Tagesordnung as printed before the sitting week (archived "Tagesordnung-komplett" PDFs, as text).
  - Amtliches Protokoll (the Bundestag's Beschlussprotokoll, § 120 GO-BT), as text.

The printed Tagesordnung uses the numbering of the day it was printed; items were renumbered, merged and swapped
afterwards. `map_agenda` links every printed item to its final number in the Amtliches Protokoll (Drucksache overlap
first, then title-word coverage), so all systems get the same agenda.
"""
import json
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
SPLIT = json.loads((ROOT / "data" / "split.json").read_text(encoding="utf-8"))

CHAIR = ("Präsident", "Präsidentin", "Vizepräsident", "Vizepräsidentin", "Alterspräsident", "Alterspräsidentin")
ITEM = re.compile(r"(Tagesordnungspunkt|Zusatzpunkt)\s+(\d+)")
ITEM_LIST = re.compile(r"(Tagesordnungspunkt|Zusatzpunkt)(?:e|en)?\s+((?:\d+(?:\s?[a-z]\b)?(?:\s*(?:,|und|bis|sowie)\s*)?)+)")
DS = re.compile(r"\b21/\d{2,5}\b")


def item_id(kind, num):
    return ("TOP " if kind.startswith("Tages") else "ZP ") + str(int(num))


def refs(text):
    """Every agenda item a text names: 'Tagesordnungspunkte 7 a und 7 b sowie Zusatzpunkt 4' -> {TOP 7, ZP 4}."""
    out = set()
    for m in ITEM_LIST.finditer(text):
        kind = "TOP " if m.group(1) == "Tagesordnungspunkt" else "ZP "
        out.update(kind + str(int(n)) for n in re.findall(r"\d+", m.group(2)))
    return out


# ------------------------------------------------------------------ Plenarprotokoll XML

def _text(el):
    return " ".join("".join(el.itertext()).split())


def parse_xml(path):
    """-> meta, units, gold.

    units: one per spoken paragraph, in spoken order:
      {i, rede (XML rede id or None for chair turns), speaker, group, role (chair|member|government|other),
       text, reactions: [str]}   reactions = the <kommentar> lines printed right after the paragraph
    gold:  {"unit_block": {i: block}, "blocks": [{block, top_id, items}]}   (evaluation only)
    """
    root = ET.parse(path).getroot()
    units, blocks, unit_block = [], [], {}
    st = {"speaker": None, "group": "", "role": "other", "block": None, "rede": None}

    def walk(el):
        for ch in el:
            tag = ch.tag
            if tag == "tagesordnungspunkt":
                top = ch.get("top-id") or ""
                items = set()
                m = ITEM.match(top)
                if m:
                    items.add(item_id(m.group(1), m.group(2)))
                agenda_text = " ".join(_text(p) for p in ch.findall("p") if p.get("klasse", "").startswith("T_"))
                items |= refs(agenda_text)
                blocks.append({"block": len(blocks), "top_id": top, "items": sorted(items)})
                st["block"] = len(blocks) - 1
                walk(ch)
            elif tag == "rede":
                st["rede"] = ch.get("id")
                walk(ch)
                st["rede"] = None
            elif tag == "name":                         # chair (or other non-member) takes the floor
                st["speaker"] = _text(ch).rstrip(":").lstrip(",;. ")
                st["role"] = "chair" if st["speaker"].startswith(CHAIR) else "other"
                st["group"] = ""
            elif tag == "kommentar":
                if units:
                    units[-1]["reactions"].append(_text(ch))
            elif tag == "p":
                kl = ch.get("klasse", "")
                if kl == "redner":
                    red = ch.find("redner/name")
                    first, last = red.findtext("vorname", ""), red.findtext("nachname", "")
                    title = red.findtext("titel", "")
                    st["speaker"] = " ".join(x for x in (title, first, last) if x)
                    st["group"] = red.findtext("fraktion") or red.findtext("rolle/rolle_kurz") or ""
                    st["role"] = "member" if red.findtext("fraktion") else "government"
                    continue
                if kl.startswith(("T_", "AL_")):
                    continue
                t = _text(ch)
                if t:
                    unit_block[len(units)] = st["block"]
                    units.append({"i": len(units), "rede": st["rede"] if st["role"] != "chair" else None,
                                  "speaker": st["speaker"], "group": st["group"], "role": st["role"],
                                  "text": t, "reactions": []})
            elif tag == "redner":
                continue
            else:
                walk(ch)

    walk(root.find("sitzungsverlauf"))
    meta = {"sid": root.get("wahlperiode") + root.get("sitzung-nr").zfill(3), "date": root.get("sitzung-datum"),
            "number": int(root.get("sitzung-nr")), "period": int(root.get("wahlperiode"))}
    return meta, units, {"unit_block": unit_block, "blocks": blocks}


# ------------------------------------------------------------------ printed Tagesordnung

AG_ITEM = re.compile(r"^\s*(?:(\d{1,3})\.|ZP\s+(\d{1,3}))\s+(\S.*)$")
DAY = {"Mi.": "Mittwoch", "Do.": "Donnerstag", "Fr.": "Freitag", "Di.": "Dienstag", "Mo.": "Montag"}
MONTH = r"(Sitzung|Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)"


def parse_agenda(text):
    """Printed items in printed order: [{id, day, minutes, title, drucksachen, raw}]."""
    items, cur, day, minutes = [], None, None, None
    for ln in (l.rstrip() for l in text.splitlines()):
        s = ln.strip()
        if s in DAY:
            day = DAY[s]
            continue
        mm = re.match(r"^\((\d+)\s*Min\.\)$", s)
        if mm:
            minutes = int(mm.group(1))
            continue
        if re.match(r"^(21\. Wahlperiode|Seite \d+ von|Tagesordnung$|\d+\. bis \d+\. Sitzung|Stand:|Beratung$)", s):
            continue
        m = AG_ITEM.match(ln)
        if m and not re.match(r"^\s*\d+\.\s*" + MONTH, ln):
            iid = f"TOP {int(m.group(1))}" if m.group(1) else f"ZP {int(m.group(2))}"
            cur = {"id": iid, "day": day, "minutes": minutes, "raw": m.group(3).strip()}
            items.append(cur)
            minutes = None
        elif cur and s:
            cur["raw"] += "\n" + s
    out, seen = [], set()
    for it in items:
        if it["id"] in seen:                       # repeated on a later page (continued items)
            continue
        seen.add(it["id"])
        it["drucksachen"] = sorted(set(DS.findall(it["raw"])))
        title = re.split(r"\n(?:Drucksache|Überweisungsvorschlag|Berichterstattung|Beschlussempfehlung und Bericht)",
                         it["raw"])[0]
        it["title"] = re.sub(r"\s+", " ", title).strip()
        out.append(it)
    return out


def agenda_for(meta):
    """The printed agenda of one sitting: latest archived weekly PDF that lists the sitting, filtered to its day."""
    n = meta["number"]
    best = None
    head = re.compile(r"(\d+)\.\s*Sitzung des Deutschen Bundestages\s*\n\s*am\s+(\w+)")
    for f in sorted((RAW / "tagesordnung").glob("to_*.txt")):
        t = f.read_text(encoding="utf-8")
        days = {int(m.group(1)): m.group(2) for m in head.finditer(t)}
        if n in days:
            best = (f, t, days[n])
    if not best:
        return [], None
    f, t, day = best
    return [it for it in parse_agenda(t) if it["day"] == day], f.name


# ------------------------------------------------------------------ Amtliches Protokoll

AP_HEAD = re.compile(r"^\s*(Tagesordnungspunkt|Zusatzpunkt)\s+(\d+)\s*$", re.M)


def parse_amtprot(text):
    """[{id, text, drucksachen}] in the order of the Amtliches Protokoll."""
    text = re.sub(r"(\w)-\s*\n\s*([a-zäöüß])", r"\1\2", text)       # join hyphenated line breaks of the PDF
    heads = list(AP_HEAD.finditer(text))
    out = []
    for k, m in enumerate(heads):
        body = text[m.end(): heads[k + 1].start() if k + 1 < len(heads) else len(text)]
        iid = item_id(m.group(1), m.group(2))
        if out and out[-1]["id"] == iid:
            out[-1]["text"] += body
        else:
            out.append({"id": iid, "text": body})
    for it in out:
        it["text"] = re.sub(r"\n{3,}", "\n\n", it["text"]).strip()
        it["drucksachen"] = sorted(set(DS.findall(it["text"])))
    return out


_GENERIC = {"beratung", "eingebrachten", "entwurfs", "gesetzes", "bundesregierung", "antrags", "fraktion",
            "abgeordneten", "weiterer", "erste", "zweite", "dritte", "drucksache"}


def _toks(t):
    return set(re.findall(r"[a-zäöüß]{4,}", t.lower())) - _GENERIC


def map_agenda(agenda, ap):
    """Link every printed item to its final number in the Amtliches Protokoll (greedy, best pair first:
    Drucksache overlap, then share of the printed title's content words found in the AP item, >= 0.6).
    Merged items (two printed items -> one final item) are allowed in a second pass.
    Printed items without a partner keep their printed number with the prefix 'TO-' (not handled that day)."""
    pairs = []
    for a in agenda:
        ta = _toks(a["title"])
        for b in ap:
            ds = len(set(a["drucksachen"]) & set(b["drucksachen"]))
            cov = len(ta & _toks(b["text"][:1500])) / max(1, len(ta))
            if ds or cov >= 0.6:
                pairs.append((ds, cov, a["id"], b["id"]))
    pairs.sort(reverse=True)
    used_a, used_b, final = set(), set(), {}
    for ds, cov, ia, ib in pairs:
        if ia in used_a or ib in used_b:
            continue
        used_a.add(ia)
        used_b.add(ib)
        final[ia] = (ib, ds, round(cov, 2))
    for ds, cov, ia, ib in pairs:
        if ia not in final:
            final[ia] = (ib, ds, round(cov, 2))
    out = []
    for a in agenda:
        b = dict(a, printed_id=a["id"])
        if a["id"] in final:
            b["id"], b["match"] = final[a["id"]][0], {"drucksachen": final[a["id"]][1], "title_cov": final[a["id"]][2]}
        else:
            b["id"], b["match"] = "TO-" + a["id"], None
        out.append(b)
    return out


# ------------------------------------------------------------------ one sitting

@lru_cache(maxsize=None)
def load_sitting(sid):
    """All inputs of one sitting. The returned dict is shared (cached): do not modify it.

    {"meta": {sid, date, number, period, split},
     "units": [...],            spoken order, no agenda markup (see parse_xml)
     "agenda": [...],           printed Tagesordnung of the day, mapped to final numbers (see map_agenda)
     "agenda_source": str,
     "amtprot": [...],          Amtliches Protokoll items (see parse_amtprot)
     "amtprot_text": str,       full text (for the LLM-only baseline)
     "gold": {...}}             agenda markup removed from the XML; evaluation only
    """
    meta, units, gold = parse_xml(RAW / "xml" / f"{sid}.xml")
    meta["split"] = SPLIT.get(sid)
    ap_text = (RAW / "amtprot" / f"{sid}.txt").read_text(encoding="utf-8")
    ap = parse_amtprot(ap_text)
    agenda, src = agenda_for(meta)
    return {"meta": meta, "units": units, "agenda": map_agenda(agenda, ap), "agenda_source": src,
            "amtprot": ap, "amtprot_text": re.sub(r"(\w)-\s*\n\s*([a-zäöüß])", r"\1\2", ap_text), "gold": gold}


def sittings(split=None):
    """Sitting ids, optionally only 'dev' or 'test'."""
    return sorted(s for s, v in SPLIT.items() if split is None or v == split)
