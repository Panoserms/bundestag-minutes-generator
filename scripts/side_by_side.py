"""Plenarprotokoll and Niederschrift side by side, speech by speech.

    .venv/bin/python scripts/side_by_side.py 21032 [B]     -> outputs/21032_B_vergleich.html

Left: the record (the speeches and the chair's turns, with the reactions), sorted into the order of the printed
Tagesordnung like the Niederschrift; the words the Niederschrift keeps are highlighted. Right: the Niederschrift.
Each row is one speech, so what was kept, condensed or left out can be read across.
"""
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from niederschrift import render, structure  # noqa: E402
from niederschrift.sources import load_sitting  # noqa: E402

OUT = ROOT / "outputs"
PDF_URL = "https://dserver.bundestag.de/btp/21/{sid}.pdf"

CSS = """
:root { --ink: #1a1a1a; --muted: #5b6168; --line: #d9dde1; --bg: #fafaf8; --paper: #fff; --mark: #fff1a8;
  --chair: #6b7280; --accent: #1d4e89; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.5 -apple-system, "Segoe UI", Arial, sans-serif; }
header { position: sticky; top: 0; z-index: 5; background: var(--paper); border-bottom: 1px solid var(--line); }
.top { max-width: 1560px; margin: 0 auto; padding: 12px 20px 8px; }
h1 { font-size: 18px; margin: 0 0 4px; }
.legend { color: var(--muted); font-size: 12.5px; }
.legend mark { padding: 0 3px; }
.cols { max-width: 1560px; margin: 0 auto; display: grid; grid-template-columns: 1fr 1fr; gap: 24px; padding: 6px 20px;
  font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
main { max-width: 1560px; margin: 0 auto; padding: 0 20px 60px; }
.item { margin: 22px 0 0; background: var(--paper); border: 1px solid var(--line); border-radius: 6px; }
.ihead { padding: 10px 14px; border-bottom: 1px solid var(--line); }
.ihead .id { font-weight: 700; color: var(--accent); }
.ihead .pos { float: right; font-size: 12px; color: var(--muted); }
.ihead .t { font-size: 13px; color: var(--muted); margin-top: 2px; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; padding: 10px 14px; border-top: 1px solid #eef0f2; }
.row:first-of-type { border-top: 0; }
.src p { margin: 0 0 6px; }
.src .who { font-weight: 700; margin-bottom: 4px; }
.src .stat { font-weight: 400; font-size: 12px; color: var(--muted); }
.src .chair { color: var(--chair); font-size: 12.5px; }
.src .react { color: var(--muted); font-style: italic; font-size: 12.5px; }
mark { background: var(--mark); color: inherit; padding: 0 1px; }
.nds p { margin: 0 0 6px; }
.nds .spk { font-weight: 700; }
.nds .kurz { font-style: italic; color: #333; }
.nds .kl { font-style: normal; font-size: 12px; color: var(--muted); }
.nds .note { font-style: italic; color: var(--muted); }
.nds .result { margin-top: 6px; }
.nds blockquote { margin: 0 0 6px 18px; }
.nds ul { margin: 0 0 6px; padding-left: 22px; }
.empty { color: var(--muted); font-style: italic; }
@media (max-width: 900px) { .row, .cols { grid-template-columns: 1fr; } .cols { display: none; } }
"""


def de(n):
    return f"{n:,}".replace(",", ".")


def esc(t):
    return html.escape(t or "", quote=False)


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


def source_speech(sp, by_i):
    units = [by_i[i] for i in sp["units"] if i in by_i]
    text = " ".join(u["text"] for u in units)
    spans = kept_spans(text, sp)
    kept = sum(len(text[a:b].split()) for a, b in spans)
    total = len(text.split())
    h = [f'<p class="who">{esc(render.speaker_name(sp))}'
         f'{" – " + esc(sp["kind"]) if sp.get("kind") not in (None, "Rede") else ""}'
         f' <span class="stat">· {kept} von {total} Wörtern wörtlich übernommen ({round(100 * kept / max(1, total))} %)'
         f'</span></p>']
    off = 0
    for u in units:
        h.append(f"<p>{marked(u['text'], off, spans)}</p>")
        for r in u.get("reactions") or []:
            h.append(f'<p class="react">{esc(r)}</p>')
        off += len(u["text"]) + 1
    return "".join(h)


def chair_html(units):
    out = []
    for u in units:
        who = f"{u['speaker']}: " if u.get("speaker") else ""
        out.append(f'<p class="chair">{esc(who)}{esc(u["text"])}</p>')
        for r in u.get("reactions") or []:
            out.append(f'<p class="react">{esc(r)}</p>')
    return "".join(out)


def item_html(it, st_item, by_i):
    ids = sorted(st_item.get("units") or []) if st_item else []
    speeches = it.get("speeches") or []
    in_speech = {i for sp in speeches for i in sp.get("units", [])}
    pos = (st_item or {}).get("handled_pos")
    h = [f'<section class="item"><div class="ihead"><span class="pos">'
         f'{f"im Plenarprotokoll als {pos}. Punkt aufgerufen" if pos else "nicht aufgerufen"}</span>'
         f'<span class="id">{esc(it["id"])}</span><div class="t">{esc(it.get("title", "")[:400])}</div></div>']
    notes = "".join(render._note_html(n) for n in it.get("notes", []))
    if notes:
        h.append(f'<div class="row"><div class="src"></div><div class="nds">{notes}</div></div>')
    shown = set()
    for sp in sorted(speeches, key=lambda s: min(s.get("units") or [0])):
        first = min(sp.get("units") or [0])
        before = [by_i[i] for i in ids if i < first and i not in in_speech and i not in shown]
        shown |= {u["i"] for u in before}
        h.append(f'<div class="row"><div class="src">{chair_html(before)}{source_speech(sp, by_i)}</div>'
                 f'<div class="nds">{render.speech_block(sp)}</div></div>')
    rest = [by_i[i] for i in ids if i not in in_speech and i not in shown]
    right = []
    subs = [s for s in it.get("subitems", []) if s.get("letter")]
    for v in it.get("votes", []):
        lab = next((f"{s['letter']}) " for s in subs if s["letter"] == v.get("sub")), "")
        right.append(f"<p><b>{esc(lab)}</b></p>" if lab else "")
        right += render._vote_html(v)
    right += render._refs_html(it.get("referrals", []) or [r for s in subs for r in s.get("referrals", [])])
    if rest or right:
        h.append(f'<div class="row"><div class="src">{chair_html(rest) or "<p class=empty>–</p>"}</div>'
                 f'<div class="nds">{"".join(right) or "<p class=empty>–</p>"}</div></div>')
    if not ids and not speeches:
        h.append('<div class="row"><div class="src"><p class="empty">Im Plenarprotokoll nicht als eigener Abschnitt.'
                 '</p></div><div class="nds"><p class="empty">Siehe Hinweis.</p></div></div>')
    h.append("</section>")
    return "".join(h)


def build(sid, system="B"):
    nds = json.loads((OUT / f"{sid}_{system}.json").read_text())
    sitting = load_sitting(sid)
    st = structure.build_structure(sitting)
    by_i = {u["i"]: u for u in sitting["units"]}
    meta = sitting["meta"]
    spoken = sum(sp.get("words_source") or 0 for it in nds["items"] for sp in it.get("speeches", []))
    kept = sum(len(p["text"].split()) for it in nds["items"] for sp in it.get("speeches", [])
               for p in sp["parts"] if p["type"] == "quote")
    kurz = sum(len(p["text"].split()) for it in nds["items"] for sp in it.get("speeches", [])
               for p in sp["parts"] if p["type"] == "summary")
    number = meta.get("number") or int(sid[2:])
    h = [f"<!doctype html><html lang=de><head><meta charset=utf-8><meta name=viewport content='width=device-width,"
         f"initial-scale=1'><title>Vergleich {sid}</title><style>{CSS}</style></head><body><header><div class=top>"
         f"<h1>{number}. Sitzung, {esc(meta.get('date', ''))}: Plenarprotokoll und Niederschrift ({system})</h1>"
         f"<div class=legend><mark>gelb</mark> = steht wörtlich in der Niederschrift. Reden: {de(spoken)} Wörter; "
         f"Niederschrift: {de(kept)} Wörter wörtlich ({round(100 * kept / max(1, spoken))} %) + {de(kurz)} Wörter "
         f"Kurzfassungen. Links in der Reihenfolge der gedruckten Tagesordnung umsortiert. "
         f"<a href='{PDF_URL.format(sid=sid)}'>Plenarprotokoll (PDF)</a> · "
         f"<a href='{sid}_{system}.pdf'>Niederschrift (PDF)</a></div></div>"
         f"<div class=cols><div>Plenarprotokoll</div><div>Niederschrift</div></div></header><main>"]
    h += [item_html(it, st["items"].get(it["id"]), by_i) for it in nds["items"]]
    h.append("</main></body></html>")
    out = OUT / f"{sid}_{system}_vergleich.html"
    out.write_text("".join(h), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "B"))
