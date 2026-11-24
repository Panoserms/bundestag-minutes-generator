"""Render NDS-JSON as a Münster-style Niederschrift (A4, Arial) in HTML, and HTML -> PDF with Microsoft Edge.

    html = render_html(nds, structure, meta);  to_pdf(html_path, pdf_path)
"""
import html as _html
import re
import subprocess
import tempfile
from pathlib import Path

from . import frames

EDGE = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
PROFILE = str(Path(tempfile.gettempdir()) / "edge-profile-niederschrift")
GOV = re.compile(r"(Bundes)?[Mm]inister|Staatsminister|Staatssekretär|Bundeskanzler|Beauftragte")

SYSTEM_NOTE = {
    "A": "System A (nur Sprachmodell): Ein Sprachmodell hat die Niederschrift in einem Aufruf aus Plenarprotokoll, "
         "gedruckter Tagesordnung und Amtlichem Protokoll geschrieben.",
    "B": "System B (hybrid): Gliederung, Reihenfolge, Abstimmungen, Beschlüsse und die Auswahl der Redeteile in den "
         "Worten der Redner nach Regeln; nur die Kurzfassung über jedem Beitrag schreibt ein Sprachmodell.",
    "C": "System C (nur Regeln): Gliederung, Reihenfolge, Abstimmungen, Beschlüsse, Auswahl der Redeteile und "
         "Kurzfassung nach Regeln, ohne Sprachmodell.",
}

CSS = """
@page { size: A4; margin: 22mm 20mm 20mm 25mm;
  @bottom-right { content: counter(page); font: 9pt Arial, sans-serif; color: #555; } }
body { font: 10.5pt/1.42 Arial, Helvetica, sans-serif; color: #111; background: #fff; margin: 0; }
h1 { font-size: 17pt; margin: 0 0 2mm; }
.sub { font-size: 11pt; margin: 0 0 1mm; }
.meta { font-size: 8.5pt; color: #444; border-top: .6pt solid #999; border-bottom: .6pt solid #999; padding: 2mm 0; margin: 4mm 0 6mm; }
h2 { font-size: 12pt; margin: 8mm 0 3mm; border-bottom: .6pt solid #444; padding-bottom: 1mm; }
table.to { border-collapse: collapse; width: 100%; font-size: 9pt; }
table.to td, table.to th { vertical-align: top; padding: 1.2mm 2mm 1.2mm 0; border-bottom: .3pt solid #ccc; text-align: left; }
table.to th { border-bottom: .8pt solid #444; }
table.to td.n { white-space: nowrap; width: 18mm; } table.to td.h { white-space: nowrap; width: 22mm; color: #444; }
section.item { margin: 7mm 0 0; }
.ihead { display: grid; grid-template-columns: 50mm 1fr; column-gap: 4mm; font-weight: bold; margin-bottom: 2.5mm; break-after: avoid; break-inside: avoid; }
.ihead .ds { font-weight: normal; font-size: 8.5pt; color: #333; margin-top: .5mm; }
.ttl { font-size: 10pt; }
p { margin: 0 0 2.2mm; hyphens: auto; }
p.note { font-style: italic; color: #333; font-size: 9.5pt; }
p.check { font-size: 9pt; border-left: 2pt solid #b45309; padding: 1mm 0 1mm 3mm; color: #7c2d12; }
p.result { margin-top: 3mm; break-after: avoid; }
blockquote { margin: 0 0 2.5mm 6.35mm; padding: 0; break-inside: avoid; }
.means { display: block; margin-top: .8mm; }
.subitem { display: grid; grid-template-columns: 6.35mm 1fr; margin: 3mm 0 0 6.35mm; }
.subitem .sl { font-weight: bold; }
p.stitle .ds { font-size: 8.5pt; color: #333; }
ul { margin: 0 0 2.5mm 0; padding-left: 12.7mm; }
ul li { margin: 0 0 .6mm; padding-left: 1mm; }
ul.speakers { columns: 2; column-gap: 8mm; font-size: 9.5pt; }
ul.speakers li { break-inside: avoid; }
.beitrag { margin: 0 0 3.5mm; }
.beitrag p { margin: 0 0 1.2mm; }
p.spk { font-weight: bold; break-after: avoid; }
p.kurz { font-style: italic; color: #333; break-after: avoid; }
p.kurz .kl { font-style: normal; font-size: 9pt; color: #555; }
.tail { margin-top: 10mm; font-size: 8.5pt; color: #444; border-top: .6pt solid #999; padding-top: 2mm; }
"""


def esc(t):
    return _html.escape(t or "", quote=False)


def speaker_name(s):
    """'Claudia Roth (BÜNDNIS 90/DIE GRÜNEN)'; a government member with the full office:
    'Reem Alabali Radovan, Bundesministerin für wirtschaftliche Zusammenarbeit und Entwicklung'."""
    g = s.get("group") or ""
    if not g:
        return s.get("speaker", "")
    if s.get("role") == "government" or GOV.search(g):
        return f"{s['speaker']}, {frames.function_of(g, s.get('gender'))}"
    return f"{s['speaker']} ({g})"


KIND_NOTE = {"Zwischenfrage": "Zwischenfrage", "Kurzintervention": "Kurzintervention", "Erwiderung": "Erwiderung"}


def speech_block(sp):
    """Speaker line, then the one-sentence Kurzfassung, then the speaker's own words without quotation marks
    (wortnah: close to the speaker's words)."""
    line = speaker_name(sp)
    if sp.get("kind") in KIND_NOTE:
        line += f" – {KIND_NOTE[sp['kind']]}"
    h = [f'<div class="beitrag"><p class="spk">{esc(line)}</p>']
    for p in sp.get("parts", []):
        if p.get("type") == "summary":
            h.append(f'<p class="kurz"><span class="kl">Kurzfassung:</span> {esc(p["text"])}</p>')
        elif p.get("type") == "quote":
            h += [f'<p class="wort">{esc(par)}</p>' for par in p["text"].split("\n")]
        else:
            h.append(f"<p>{esc(p.get('display') or p.get('text') or '')}</p>")
    h.append("</div>")
    return "".join(h)


def _note_html(n):
    n = re.sub(r"^\(?Hinweis:\s*", "", n.strip()).rstrip(")")
    if n.startswith("Prüfhinweis"):
        return f'<p class="check">{esc(n)}</p>'
    return f'<p class="note">(Hinweis: {esc(n)})</p>'


def _vote_html(v):
    h = [f'<p class="result">{esc(v.get("result_sentence") or "Der Bundestag beschließt:")}</p>']
    d = v.get("decision") or ""
    parts = re.split(r";\s*das bedeutet:\s*", d)
    if len(parts) > 1:
        h.append(f'<blockquote>„{esc(parts[0])}“<span class="means">Das bedeutet: {esc(parts[1])}</span></blockquote>')
    elif d:
        h.append(f"<blockquote>„{esc(d)}“</blockquote>")
    return h


def _refs_html(refs):
    if not refs:
        return []
    return ['<p class="result">Der Bundestag beschließt die Überweisung an:</p><ul class="refs">'
            + "".join(f"<li>{esc(c)}</li>" for c in refs) + "</ul>"]


def item_html(it, st_item):
    iid = it["id"]
    lab = it.get("label") or (st_item or {}).get("label") or iid
    ds = it.get("drucksachen") or (st_item or {}).get("drucksachen") or []
    at = f' id="{esc(it["anchor"])}"' if it.get("anchor") else ""
    h = [f'<section class="item"{at}><div class="ihead"><div class="lab">{esc(lab)}'
         f'{"<div class=ds>Drs. " + esc(", ".join(ds)) + "</div>" if ds else ""}</div>'
         f'<div class="ttl">{esc(it.get("title", ""))}</div></div>']
    h += [_note_html(n) for n in it.get("notes", [])]
    sp = it.get("speeches") or []
    if sp:
        kind = it.get("kind") or (st_item or {}).get("kind")
        intro = ("In der Befragung sprachen:" if "Befragung" in it.get("title", "") else
                 "In der Fragestunde sprachen:" if kind == "qa" else "In der Aussprache sprachen:")
        names = list(dict.fromkeys(speaker_name(s) for s in sp))
        h.append(f"<p>{intro}</p><ul class=\"speakers\">" + "".join(f"<li>{esc(x)}</li>" for x in names) + "</ul>")
        h += [speech_block(s) for s in sp]
    subs = [s for s in it.get("subitems", []) if s.get("letter")]
    vs = it.get("votes", [])
    if len(subs) >= 2:
        for s in subs:
            h.append(f'<div class="subitem"><div class="sl">{esc(s["letter"])})</div><div class="sb">'
                     f'<p class="stitle">{esc(s["title"])}'
                     f'{"<span class=ds> · Drs. " + esc(", ".join(s["drucksachen"])) + "</span>" if s.get("drucksachen") else ""}</p>')
            for v in vs:
                if v.get("sub") == s["letter"]:
                    h += _vote_html(v)
            h += _refs_html(s.get("referrals", []))
            h.append("</div></div>")
        for v in vs:
            if v.get("sub") not in {s["letter"] for s in subs}:
                h += _vote_html(v)
    else:
        for v in vs:
            h += _vote_html(v)
        h += _refs_html(it.get("referrals", []))
    h.append("</section>")
    return "\n".join(h)


def render_html(nds, structure, meta):
    """-> HTML string of the whole Niederschrift (title block, agenda table, items in plan order, Zusatzpunkte)."""
    sid = str(nds.get("sid") or structure.get("sid") or meta.get("sid", ""))
    number = meta.get("number") or (int(sid[2:]) if sid[2:].isdigit() else "")
    st_items = structure.get("items", {})
    rest = set(structure.get("rest", []))
    system = nds.get("system")
    note = SYSTEM_NOTE.get(system, "Maschinell erstellt.")
    comp = nds.get("compute") or {}
    cost = (f" Sprachmodell: {comp.get('llm_calls', 0)} Aufrufe, {comp.get('cost_usd', 0):.2f} USD."
            if comp.get("llm_calls") else "")
    h = [f"<!doctype html><html lang=de><head><meta charset=utf-8><title>Niederschrift {esc(sid)}</title>"
         f"<style>{CSS}</style></head><body>",
         "<h1>Niederschrift (Entwurf)</h1>",
         f'<p class="sub">über die {number}. Sitzung des Deutschen Bundestages, {meta.get("period", 21)}. Wahlperiode, '
         f'am {esc(meta.get("date", ""))}</p>',
         f'<div class="meta">Maschinell erstellt. {esc(note)}{esc(cost)} Reihenfolge = gedruckte Tagesordnung, nicht '
         f'Reihenfolge der Behandlung. Beschlusstexte wörtlich aus dem Amtlichen Protokoll. Redebeiträge wortnah: Auszüge in den Worten der Redner, '
         f'ausgelassene Stellen sind mit […] markiert.</div>',
         "<h2>Tagesordnung</h2><table class=to><tr><th>Nr.</th><th>Gegenstand</th><th>behandelt als</th></tr>"]
    for it in nds.get("items", []):
        st = st_items.get(it["id"], {})
        k = st.get("handled_pos")
        t = it.get("title", "")
        h.append(f'<tr><td class=n>{esc(it["id"].replace("TO-", "gedr. "))}</td><td>{esc(t[:180])}{"…" if len(t) > 180 else ""}'
                 f'</td><td class=h>{f"{k}. Punkt" if k else "–"}</td></tr>')
    h.append("</table><h2>Verlauf der Sitzung</h2>")
    main = [it for it in nds.get("items", []) if it.get("section", "rest" if it["id"] in rest else "main") == "main"]
    tail = [it for it in nds.get("items", []) if it not in main]
    h += [item_html(it, st_items.get(it["id"])) for it in main]
    if tail:
        h.append("<h2>Zusatzpunkte (nicht in der gedruckten Tagesordnung)</h2>")
        h += [item_html(it, st_items.get(it["id"])) for it in tail]
    h.append('<div class="tail">Entwurf zur Prüfung durch die Schriftführung. Abstimmungsverhalten der Fraktionen aus '
             'den Frage-Antwort-Paaren der Sitzungsleitung.</div></body></html>')
    return "\n".join(h)


def _handled_text(it):
    """The agenda table's last column in a week file: which sitting handled the item, as what, and in which position."""
    h = it.get("handled")
    if any(n == "Der Punkt wurde abgesetzt." for n in it["notes"]):
        return "abgesetzt"
    if not h:
        return "kein Aufruf gefunden"
    if it.get("merged_into"):
        return f"mit {it['merged_into']}"
    same = h["as"] == it["id"] or not it["planned_day"]
    return f"{h['sitting']}. Sitzung ({h['day'][:2]})" + ("" if same else f" als {h['as']}") + (
        f", {h['position']}. Punkt" if h["position"] else "")


WEEK_CSS = """
table.to td.n a { color: inherit; } table.to td.h { width: 44mm; white-space: normal; } table.to td.d { width: 14mm; color: #444; }
@media screen { body { max-width: 54rem; margin: 2rem auto; padding: 0 1rem; } .item { scroll-margin-top: 1rem; } }
"""


def render_week(wk):
    """-> HTML string of one Niederschrift for a whole sitting week: the printed weekly agenda from the first planned
    item to the last, then the items that were not on it."""
    metas = wk["metas"]
    first, last = metas[0], metas[-1]
    cost = (f" Sprachmodell: {wk['compute']['llm_calls']} Aufrufe, {wk['compute']['cost_usd']:.2f} USD."
            if wk["compute"].get("llm_calls") else "")
    items = [dict(it, anchor=re.sub(r"\W+", "-", it["id"])) for it in wk["items"]]
    h = [f"<!doctype html><html lang=de><head><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'>"
         f"<title>Niederschrift {first['number']}.–{last['number']}. Sitzung</title><style>{CSS}{WEEK_CSS}</style></head><body>",
         "<h1>Niederschrift der Sitzungswoche (Entwurf)</h1>",
         f'<p class="sub">über die {first["number"]}. bis {last["number"]}. Sitzung des Deutschen Bundestages, '
         f'{first.get("period", 21)}. Wahlperiode, vom {esc(first["date"])} bis {esc(last["date"])}</p>',
         f'<div class="meta">Maschinell erstellt. {esc(SYSTEM_NOTE.get(wk["system"], ""))}{esc(cost)} Die Sitzungen der Woche sind zu '
         f'einer Niederschrift zusammengeführt. Reihenfolge = vor der Woche gedruckte Tagesordnung: Jeder geplante Punkt steht an '
         f'seiner Stelle, auch wenn er an einem anderen Tag oder unter einer anderen Nummer aufgerufen wurde. Verweise verwenden die '
         f'gedruckten Nummern. Beschlusstexte wörtlich aus dem Amtlichen Protokoll. Redebeiträge wortnah: Auszüge in den Worten der '
         f'Redner, ausgelassene Stellen sind mit […] markiert.</div>',
         "<h2>Tagesordnung der Sitzungswoche</h2><table class=to><tr><th>Nr.</th><th>Gegenstand</th><th>geplant</th>"
         "<th>behandelt</th></tr>"]
    for it in items:
        t = it["title"]
        h.append(f'<tr><td class=n><a href="#{esc(it["anchor"])}">{esc(it["id"])}</a></td><td>{esc(t[:180])}{"…" if len(t) > 180 else ""}</td>'
                 f'<td class=d>{esc((it["planned_day"] or "–")[:2])}</td><td class=h>{esc(_handled_text(it))}</td></tr>')
    h.append("</table><h2>Verlauf der Sitzungswoche</h2>")
    main = [it for it in items if it["section"] == "main"]
    h += [item_html(it, None) for it in main]
    if len(main) < len(items):
        h.append("<h2>Weitere Punkte (nicht in der gedruckten Tagesordnung)</h2>")
        h += [item_html(it, None) for it in items if it["section"] != "main"]
    h.append('<div class="tail">Entwurf zur Prüfung durch die Schriftführung. Abstimmungsverhalten der Fraktionen aus '
             'den Frage-Antwort-Paaren der Sitzungsleitung.</div></body></html>')
    return "\n".join(h)


def to_pdf(html_path, pdf_path):
    """HTML -> PDF with Microsoft Edge headless (A4 from the CSS @page rule)."""
    html_path, pdf_path = Path(html_path).resolve(), Path(pdf_path).resolve()
    Path(PROFILE).mkdir(parents=True, exist_ok=True)
    subprocess.run([EDGE, "--headless", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={PROFILE}",
                    f"--print-to-pdf={pdf_path}", html_path.as_uri()], check=True, capture_output=True, timeout=180)
    return pdf_path
