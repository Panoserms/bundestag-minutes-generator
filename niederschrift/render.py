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
    return f"{h['sitting']}. Sitzung" + ("" if same else f" als {h['as']}") + (
        f", {h['position']}. Punkt" if h["position"] else "")


def kept_spans(text, nds_speech):
    """Character spans of `text` (the speech's paragraphs joined by spaces) that the Niederschrift keeps."""
    spans, at = [], 0
    for p in nds_speech.get("parts", []):
        if p.get("type") != "quote":
            continue
        for seg in re.split(r"\s*\[…\]\s*", p["text"]):
            seg = seg.strip().replace("\n", " ")
            if not seg:
                continue
            k = text.find(seg, at)
            if k == -1:
                k = text.find(seg)
            if k == -1:
                continue
            spans.append((k, k + len(seg)))
            at = k + len(seg)
    return sorted(spans)


def marked(text, start, spans):
    """HTML of one paragraph (text at offset `start` of the joined speech) with the kept spans marked."""
    out, pos = [], 0
    end = start + len(text)
    for a, b in spans:
        a, b = max(a, start) - start, min(b, end) - start
        if a >= b:
            continue
        out.append(esc(text[pos:a]))
        out.append(f"<mark>{esc(text[a:b])}</mark>")
        pos = b
    out.append(esc(text[pos:]))
    return "".join(out)


# ------------------------------------------------------------------ the week file: one page, several modes per speech

MODES = [   # key, name, what it is
    ("kurz", "Kurzfassung (Regel)", "ein Satz, nach Regeln aus der Rede gebildet"),
    ("kurzllm", "Kurzfassung (Sprachmodell)", "ein Satz, vom Sprachmodell geschrieben und von Regeln geprüft"),
    ("auszug", "Eigene Worte (Auszug)", "die ausgewählten Absätze im Wortlaut, Auslassungen mit […]"),
    ("wortlaut", "Wortlaut", "die ganze Rede; gelb = im Auszug verwendet"),
]
DEFAULT_MODE = "auszug"

WEEK_CSS = """@page{size:A4;margin:20mm 20mm 24mm 22mm}
body{font-family:Arial,Helvetica,sans-serif;font-size:10.5pt;line-height:1.42;color:#111}
.draft{font-size:8pt;color:#8a1f11;border:.6pt solid #c9a19a;padding:1.5mm 3mm;margin:0 0 8mm;text-align:center}
.org{font-size:10pt;margin-bottom:14mm}
h1{font-size:15pt;letter-spacing:.35em;text-align:center;font-weight:bold;margin:0 0 4mm}
.sub{text-align:center;margin:0 0 6mm}
.notebox{border:.6pt solid #b9b9b9;background:#f6f6f4;padding:3mm 4mm;font-size:8.6pt;margin:0 0 7mm}
.notebox b{font-weight:bold}
h2{font-size:11pt;font-weight:bold;margin:7mm 0 3mm;text-transform:uppercase;letter-spacing:.04em}
table.to{border-collapse:collapse;width:100%;font-size:9.6pt}
table.to td,table.to th{padding:1mm 2mm 1mm 0;vertical-align:top;text-align:left}
table.to th{border-bottom:.5pt solid #555;font-weight:bold}
table.to td.n{width:13mm;white-space:nowrap}
table.to tr.parent td{font-weight:bold;padding-top:2mm}
.item{margin:6mm 0 0;page-break-inside:auto}
.ih{font-weight:bold;display:grid;grid-template-columns:13mm 1fr;margin-bottom:2mm}
.ih.parent{margin-top:7mm}
p{margin:0 0 2.2mm}
.sp{font-weight:bold;margin:3mm 0 .6mm}
.sp .t{font-weight:normal;color:#777;font-size:7.5pt;margin-left:2mm}
.sp .flag{font-weight:normal;color:#8a1f11;font-size:8pt}
.kurz{font-style:italic;color:#444;font-size:9.4pt;margin:0 0 1mm}
.lab{margin:0 0 1mm}
.dec{margin:0 0 2.5mm 0;color:#555}
.hint{margin:2.5mm 0}
.hint b{font-weight:normal;text-decoration:underline}

@media screen{
 html{background:#f3f5f8} body{max-width:54rem;margin:0 auto;padding:4.6rem 1.2rem 4rem;background:#fff;font-size:16px;line-height:1.55;
  box-shadow:0 0 0 1px #e3e7ec}
 .draft{margin-top:.5rem}
 h2{scroll-margin-top:4.5rem} .item{scroll-margin-top:4.5rem}
 .ih{grid-template-columns:4.6rem 1fr} table.to td.n{width:4.6rem}
 table.to a{color:#2f5d8a;text-decoration:none} table.to a:hover{text-decoration:underline}
}
.toolbar{position:fixed;top:0;left:0;right:0;z-index:10;display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;justify-content:center;
 padding:.55rem 1rem;background:#ffffffee;border-bottom:1px solid #d5d9df;font:600 13px "Helvetica Neue",Arial,sans-serif;backdrop-filter:blur(4px)}
.toolbar b{margin-right:.6rem} .toolbar button,.toolbar a{font:inherit;border:1px solid #c5ccd5;background:#fff;color:#1b1f24;padding:.3rem .7rem;
 border-radius:5px;cursor:pointer;text-decoration:none} .toolbar button:hover,.toolbar a:hover{border-color:#2f5d8a}
.toolbar .leg{font-weight:400;color:#5b6470} .toolbar mark{padding:0 .3rem}
.contrib{border-left:3px solid #dfe5ec;padding:.1rem 0 .1rem .8rem;margin:.9rem 0}
.contrib .sp{margin-top:.2rem}
.badge{font:600 .68em "Helvetica Neue",Arial,sans-serif;background:#eef2f7;color:#2f5d8a;padding:.08rem .45rem;border-radius:3px;margin-left:.35rem;
 vertical-align:.1em;letter-spacing:.02em}
details summary{cursor:pointer;color:#2f5d8a;font:600 .82em "Helvetica Neue",Arial,sans-serif;margin:.35rem 0;list-style-position:outside}
details summary:focus-visible{outline:2px solid #2f5d8a;outline-offset:2px}
details[open]{background:#fafbfc;border:1px solid #e3e7ec;border-radius:5px;padding:.3rem .8rem .5rem}
details p{margin:.45rem 0} mark{background:#fff1a8;color:inherit}
details.cat{margin:.25rem 0} details.cat>summary{color:#2f5d8a}
details.cat>summary .why{font-weight:400;color:#6b7480;margin-left:.4rem}
details.cat[open]>summary{color:#1b1f24}
.src{font:600 .66em "Helvetica Neue",Arial,sans-serif;color:#5b6470;background:#f1f3f6;border-radius:3px;padding:.05rem .4rem;margin-left:.3rem}
.flag{font:.78em "Helvetica Neue",Arial,sans-serif;color:#8a3b12;margin:.2rem 0 0}
@media print{.toolbar{display:none} details{display:block}}
.toolbar button[aria-pressed=true]{background:#2f5d8a;border-color:#2f5d8a;color:#fff}
@media screen{body{padding-top:6.4rem} table.to td:last-child{min-width:9.5rem} .item,h2{scroll-margin-top:6.4rem}}
"""

WEEK_JS = """
function show(mode){document.querySelectorAll('.contrib').forEach(c=>{const d=[...c.querySelectorAll('details.cat')];
 const want=d.find(x=>x.dataset.mode===mode)||d.find(x=>x.dataset.mode==='auszug')||d[0];d.forEach(x=>x.open=x===want);});
 document.querySelectorAll('.toolbar button[data-mode]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.mode===mode));}
function closeAll(){document.querySelectorAll('details.cat').forEach(d=>d.open=false);}
"""


def _contribution(sp):
    """One speech: the speaker line, then every mode that exists for it, one of them open."""
    line = speaker_name(sp)
    if sp.get("kind") in KIND_NOTE:
        line += f" – {KIND_NOTE[sp['kind']]}"
    modes, names = sp["modes"], dict((k, n) for k, n, _ in MODES)
    open_one = DEFAULT_MODE if DEFAULT_MODE in modes else next(iter(modes), None)
    h = [f'<div class="contrib"><p class="sp">{esc(line)}</p>']
    for key in (k for k, _, _ in MODES if k in modes):
        why = ""
        if key == "wortlaut":
            why = f' <span class="why">({modes[key]["words"]} Wörter)</span>'
        elif key == "auszug" and modes.get("wortlaut"):
            why = f' <span class="why">({modes[key]["words"]} von {modes["wortlaut"]["words"]} Wörtern)</span>'
        h.append(f'<details class="cat" data-mode="{key}"{" open" if key == open_one else ""}><summary>{names[key]}{why}</summary>'
                 + "".join(f"<p>{par}</p>" for par in modes[key]["html"]) + "</details>")
    h.append("</div>")
    return "".join(h)


def _week_votes(it):
    h = []
    for v in it["votes"]:
        h.append(f'<p class="lab">{esc(v.get("result_sentence") or "Der Bundestag beschließt:")}</p>')
        if v.get("decision"):
            h.append(f'<p class="dec">„{esc(v["decision"])}“</p>')
    if it["referrals"]:
        h.append(f'<p class="lab">Der Bundestag beschließt die Überweisung an:</p><p class="dec">{esc("; ".join(it["referrals"]))}</p>')
    return h


def _week_item(it):
    ds = f' <span class="t">Drs. {esc(", ".join(it["drucksachen"]))}</span>' if it["drucksachen"] else ""
    h = [f'<div class="item" id="{esc(it["anchor"])}"><div class="ih"><span>{esc(it["id"])}</span>'
         f'<span class="sp" style="margin:0">{esc(it["title"])}{ds}</span></div>']
    for n in it["notes"]:
        if n.startswith("Prüfhinweis"):
            h.append(f'<p class="flag">{esc(n)}</p>')
        else:
            h.append(f'<p class="hint"><b>Hinweis:</b> {esc(n)}</p>')
    if it["speeches"]:
        h.append(f"<p>{'In der Befragung sprachen' if 'Befragung' in it['title'] else 'In der Fragestunde sprachen' if it['kind'] == 'qa' else 'In der Aussprache sprachen'} "
                 f"{len(dict.fromkeys(speaker_name(s) for s in it['speeches']))} Personen:</p>")
        h += [_contribution(s) for s in it["speeches"]]
    h += _week_votes(it)
    h.append("</div>")
    return "".join(h)


def render_week(wk):
    """-> HTML string of one Niederschrift for a whole sitting week: the printed weekly agenda from the first planned
    item to the last, then the items that were not on it. Every speech offers the modes of MODES."""
    first, last = wk["metas"][0], wk["metas"][-1]
    items = [dict(it, anchor=re.sub(r"\W+", "-", it["id"]).strip("-")) for it in wk["items"]]
    speeches = [s for it in items for s in it["speeches"]]
    count = {k: sum(k in s["modes"] for s in speeches) for k, _, _ in MODES}
    used = [(k, n, d) for k, n, d in MODES if count[k]]
    llm = [m["number"] for m in wk["metas"] if m["sid"] in wk["llm_sittings"]]
    h = [f"<!doctype html><html lang=de><head><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'>"
         f"<title>Niederschrift {first['number']}.–{last['number']}. Sitzung</title><style>{WEEK_CSS}</style><script>{WEEK_JS}</script></head><body>",
         f'<div class="toolbar"><b>Niederschrift {first["number"]}.–{last["number"]}. Sitzung</b>'
         + "".join(f'<button type="button" data-mode="{k}" aria-pressed="{str(k == DEFAULT_MODE).lower()}" onclick="show(\'{k}\')">{n}</button>' for k, n, _ in used)
         + '<button type="button" onclick="closeAll()">Alle schließen</button><a href="#tagesordnung">Tagesordnung</a>'
         + '<span class="leg"><mark>gelb</mark> = im Auszug verwendet</span></div>',
         '<div class="draft">ENTWURF – maschinell erstellt mit Regeln – nicht amtlich – Forschungsprototyp</div>',
         f'<div class="org">Deutscher Bundestag<br>{first.get("period", 21)}. Wahlperiode</div>', "<h1>Niederschrift</h1>",
         f'<p class="sub">über die {first["number"]}. bis {last["number"]}. Sitzung des Deutschen Bundestages<br>'
         f'vom {esc(first["date"])} bis {esc(last["date"])}</p>',
         '<div class="notebox"><b>Wie dieser Entwurf entstanden ist.</b> Die Plenarprotokolle der Woche sind zu einer Niederschrift '
         'zusammengeführt. Die Gliederung folgt der vor der Sitzungswoche gedruckten Tagesordnung: Jeder geplante Punkt steht an seiner '
         'Stelle, auch wenn er an einem anderen Tag oder unter einer anderen Nummer aufgerufen wurde; ein Hinweis nennt dann Sitzung und '
         'Nummer. Verweise verwenden die gedruckten Nummern. Beschlusstexte stehen wörtlich wie im Amtlichen Protokoll; das '
         'Abstimmungsverhalten kommt aus den Frage-Antwort-Paaren der Sitzungsleitung. <b>Die Formen:</b> Jeder Redebeitrag lässt sich in '
         'mehreren Formen lesen, einzeln auf Klick oder für alle über die Leiste oben: '
         + "; ".join(f"<b>{n}</b> – {d} ({count[k]} von {len(speeches)})" for k, n, d in used) + ". "
         + (f"Die Form mit Sprachmodell gibt es hier für die {', '.join(f'{n}.' for n in llm)} Sitzung. " if llm else "") + "</div>",
         "<h2 id=tagesordnung>Tagesordnung der Sitzungswoche</h2><table class=to><tr><th>TOP</th><th>Bezeichnung</th><th>geplant</th>"
         "<th>Drucksache-Nr.</th><th>aufgerufen</th></tr>"]
    for it in items:
        t = it["title"]
        mark = "" if it["planned_day"] else "<i>nicht gedruckt:</i> "
        h.append(f'<tr><td class=n><a href="#{esc(it["anchor"])}">{esc(it["id"])}</a></td><td>{mark}{esc(t[:200])}{"…" if len(t) > 200 else ""}</td>'
                 f'<td>{esc((it["planned_day"] or "–")[:2])}</td><td>{esc(", ".join(it["drucksachen"]))}</td><td>{esc(_handled_text(it))}</td></tr>')
    h.append("</table><h2>Verlauf der Sitzungswoche</h2>")
    main = [it for it in items if it["section"] == "main"]
    h += [_week_item(it) for it in main]
    if len(main) < len(items):
        h.append("<h2>Weitere Punkte (nicht in der gedruckten Tagesordnung)</h2>")
        h += [_week_item(it) for it in items if it["section"] != "main"]
    h.append("</body></html>")
    return "\n".join(h)


def to_pdf(html_path, pdf_path):
    """HTML -> PDF with Microsoft Edge headless (A4 from the CSS @page rule)."""
    html_path, pdf_path = Path(html_path).resolve(), Path(pdf_path).resolve()
    Path(PROFILE).mkdir(parents=True, exist_ok=True)
    subprocess.run([EDGE, "--headless", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={PROFILE}",
                    f"--print-to-pdf={pdf_path}", html_path.as_uri()], check=True, capture_output=True, timeout=180)
    return pdf_path
