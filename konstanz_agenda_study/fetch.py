"""Download the public agendas and decision records of the Kreistag Konstanz (exploratory side study).

    python3 konstanz_agenda_study/fetch.py          # -> konstanz_agenda_study/data/{pdf,text}/  (not in the repository)

Source: the council information system (SessionNet) of the Landkreis Konstanz, kreistag.lrakn.de/buergerinfo.
For each public Kreistag session it publishes the agenda ("Tagesordnung öffentlich", sometimes an updated
version) and a decision record ("Beschlüsse und Zusagen ö", since 2026 "Beschlussprotokoll ö").
Only these two public document types are used. The corpus is frozen at sessions up to 31 March 2026.
"""
import html
import os
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("KONSTANZ_DATA", HERE / "data"))   # downloads live outside the repo if set
BASE = "https://kreistag.lrakn.de/buergerinfo/"
LAST_DATE = "2026-03-31"
KINDS = [("agenda_updated", re.compile(r"^Aktualisierte Tagesordnung|Tagesordnung.*aktualisiert")),
         ("agenda", re.compile(r"^Tagesordnung öffentlich")),
         ("record", re.compile(r"^Beschl(?:üsse und Zusagen|ussprotokoll)"))]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (corpus study, non-commercial)"})
    return urllib.request.urlopen(req, timeout=60).read()


def kind_of(title):
    return next((k for k, rx in KINDS if rx.search(title)), None)


def documents():
    """[(date, kind, doc id, title)] for every Kreistag session up to LAST_DATE."""
    page = get(BASE + "si0041.asp?__cwpall=1&__kgrnr=1").decode("utf-8", errors="replace")
    out = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        date = re.search(r"(\d\d)\.(\d\d)\.(\d{4})", row)
        if not date:
            continue
        iso = f"{date.group(3)}-{date.group(2)}-{date.group(1)}"
        for doc_id, title in re.findall(r'href="getfile\.asp\?id=(\d+)&type=do"[^>]*>(.*?)</a>', row, re.S):
            title = html.unescape(re.sub(r"<[^>]+>|\s+", " ", title)).strip()
            kind = kind_of(title)
            if kind and iso <= LAST_DATE:
                out.add((iso, kind, doc_id, title))
    return sorted(out)


def main():
    (DATA / "pdf").mkdir(parents=True, exist_ok=True)
    (DATA / "text").mkdir(parents=True, exist_ok=True)
    docs = documents()
    print(f"{len(docs)} documents", file=sys.stderr)
    index = []
    for date, kind, doc_id, title in docs:
        pdf = DATA / "pdf" / f"{date}_{kind}_{doc_id}.pdf"
        if not pdf.exists():
            pdf.write_bytes(get(f"{BASE}getfile.asp?id={doc_id}&type=do"))
            time.sleep(0.5)
        try:
            text = "\n".join(p.extract_text() or "" for p in PdfReader(pdf).pages)
        except Exception as e:                      # not a PDF or unreadable: note it and go on
            print(f"{date} {kind} {doc_id}: {e}", file=sys.stderr)
            continue
        (DATA / "text" / f"{date}_{kind}_{doc_id}.txt").write_text(text, encoding="utf-8")
        index.append({"date": date, "kind": kind, "doc_id": doc_id, "title": title, "words": len(text.split())})
    (DATA / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(index)} texts -> {DATA / 'text'}", file=sys.stderr)


if __name__ == "__main__":
    main()
