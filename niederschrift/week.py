"""One Niederschrift for a whole sitting week: all sittings of the week joined into one file that follows the weekly
Tagesordnung as printed before the week, from the first planned item to the last.

    .venv/bin/python -m niederschrift.week 21030 21031 21032 [--llm] [--pdf]
    -> outputs/21030-21032.json, .html (.pdf)

Every speech can be read in several modes (render.MODES): a one-sentence summary written by a rule, the same written by
the LLM (--llm; from the saved replies, or by new calls if NIEDERSCHRIFT_LLM_CMD is set), a short summary, the speaker's
own words as an excerpt, the excerpt in indirect speech, and the full text with the excerpt highlighted.

The single-sitting pipeline does the work inside each sitting (segmentation, votes, the speakers' own words). This
module only decides where each item stands (rules W1-W6, docs/RULES.md):

  W1 The printed agenda of the whole week is mapped to the Amtliche Protokolle of ALL its sittings at once. A day-by-day
     mapping cannot see an item that moved to another day.
  W2 Every planned item stands at its printed place, with the content of whichever sitting handled it. A note names the
     sitting if the day changed and the number if it changed.
  W3 Two planned items handled as one are reported once, under the one that became sub-item a); the other refers to it.
  W4 Withdrawn items, and items for which no call was found, keep their place with a note.
  W5 An unplanned item called together with a planned one follows it. The other unplanned items close the file, in the
     order in which they were called.
  W6 "Dieser Tagesordnungspunkt wurde nach TOP X behandelt": the planned item handled before it in that sitting is not
     the one that stands before it here. References use the planned numbers.
"""
import argparse
import json
import re

from . import compose, indirect, order, render, sources, structure, topic, votes

MOVED = re.compile(r"^(?:Dieser Tagesordnungspunkt wurde nach|Dieser Punkt stand nicht|An dieser Stelle|Mit diesem Punkt|"
                   r"Gemeinsam beraten|Der Punkt wurde in dieser Sitzung nicht aufgerufen)")
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November",
          "Dezember")
# the margin of the printed agenda ("TOP 10a+b verbundene Beratung", "Kernzeit-Debatte: ...") runs into some titles
MARGIN = re.compile(r"\s(?:TOP \d+\s?[a-z][a-z+–-]*\s|Kernzeit-Debatte|verbundene|\(Aufruf |Berlin, den)")


def has_content(item):
    return bool(item["handled_pos"] or item["decisions"] and not withdrawn(item))


def withdrawn(item):
    return not item["handled_pos"] and any(n == "Der Punkt wurde abgesetzt." for n in item["notes"])


def number(sid):
    return int(sid[2:])


def day_of(meta):
    """('Donnerstag', 'Donnerstag, dem 8. Oktober 2026') from the date in the Plenarprotokoll."""
    import datetime
    d, m, y = (int(x) for x in meta["date"].split("."))
    name = WEEKDAYS[datetime.date(y, m, d).weekday()]
    return name, f"{name}, dem {d}. {MONTHS[m - 1]} {y}"


def week_numbers(text):
    """Numbers of all sittings listed on the printed weekly agenda."""
    return [int(n) for n in re.findall(r"(?m)^\s*(\d+)\.\s*Sitzung des Deutschen Bundestages\s*\n\s*am\s", text)]


def own_number(key):
    """The number an unplanned item was called under. A TOP number can also be the planned number of another item, so
    the sitting is added; Zusatzpunkte are numbered through the whole week."""
    sid, iid = key
    return f"{iid} ({number(sid)}. Sitzung)" if iid.startswith("TOP") else iid


def clean_title(title):
    return MARGIN.split(re.sub(r"\s+", " ", title), maxsplit=1)[0].strip()


def build_plan(sids):
    """-> {sids, entries, sittings, structures, days}. An entry is one place in the file:
    {printed: agenda item | None, key: (sid, final id) | None, merged_into: printed id | None,
     section: 'main' | 'rest', notes: [str]}"""
    sids = sorted(sids)
    sit = {s: sources.load_sitting(s) for s in sids}
    files = {sit[s]["agenda_source"] for s in sids}
    if len(files) != 1 or None in files:
        raise ValueError(f"the sittings do not share one printed weekly agenda: {sorted(map(str, files))}")
    text = (sources.RAW / "tagesordnung" / files.pop()).read_text(encoding="utf-8")
    missing = [n for n in week_numbers(text) if n not in {number(s) for s in sids}]
    if missing:
        raise ValueError(f"the printed agenda also covers sitting(s) {', '.join(map(str, missing))}: give all of them")
    st = {s: structure.build_structure(sit[s]) for s in sids}
    items = {(s, i): it for s in sids for i, it in st[s]["items"].items() if not i.startswith("TO-")}
    days = {s: day_of(sit[s]["meta"]) for s in sids}

    # W1: one mapping for the whole week
    ap = [dict(a, id=f"{s}|{a['id']}") for s in sids for a in sit[s]["amtprot"]]
    agenda = sources.map_agenda(sources.parse_agenda(text), ap)
    sub_a = {}
    for a in ap:
        subs = votes.ap_parts(a["text"])[1]
        sub_a[a["id"]] = subs[0]["title"] if subs else ""
    key_of = lambda a: None if a["id"].startswith("TO-") else tuple(a["id"].split("|"))
    # W3: the planned item that became sub-item a) carries the content
    anchor = {k: order._anchor_printed(agenda, f"{k[0]}|{k[1]}", sub_a.get(f"{k[0]}|{k[1]}"))
              for k in {key_of(a) for a in agenda} - {None}}
    shown = lambda k: anchor.get(k) or own_number(k)            # planned number, else the number it was called under

    joint = {}
    for s in sids:
        for i, partners in structure._joint(st[s]["events"]).items():
            joint[(s, i)] = [(s, p) for p in partners if (s, p) in items]
    handled = {s: [(s, i) for i in st[s]["handled"] if (s, i) in items and has_content(items[(s, i)])] for s in sids}

    # W2 to W5: places
    entries, placed = [], set()
    for a in agenda:
        k = key_of(a)
        merged = anchor[k] if k and anchor[k] != a["printed_id"] else None
        entries.append({"printed": a | {"id": a["printed_id"], "title": clean_title(a["title"])}, "key": k,
                        "merged_into": merged, "section": "main", "notes": []})
        if k and not merged:
            placed.add(k)
    for e in list(entries):
        if not e["key"] or e["merged_into"]:
            continue
        at = entries.index(e) + 1
        for p in sorted(joint.get(e["key"], []), key=lambda k: order.num(k[1])):
            if p not in placed and p not in anchor and has_content(items[p]):
                entries.insert(at, {"printed": None, "key": p, "merged_into": None, "section": "main", "notes": []})
                placed.add(p)
                at += 1
    for s in sids:
        rest = [k for k in handled[s] if k not in placed]
        rest += [k for k in items if k[0] == s and k not in placed and k not in rest and has_content(items[k])]
        for k in rest:
            entries.append({"printed": None, "key": k, "merged_into": None, "section": "rest", "notes": []})
            placed.add(k)

    # notes (W2, W3, W4, W6)
    in_file = {s: [e["key"] for e in entries if e["key"] and e["key"][0] == s and e["printed"] and not e["merged_into"]
                   and e["key"] in handled[s]] for s in sids}
    for e in entries:
        k, pr, note = e["key"], e["printed"], e["notes"].append
        if k is None:
            note("In den Sitzungen dieser Woche wurde kein Aufruf dieses Punktes gefunden.")
            continue
        s, iid = k
        where = f"in der {number(s)}. Sitzung am {days[s][1]},"
        if e["merged_into"]:
            note(f"Dieser Punkt wurde {where} zusammen mit {e['merged_into']}"
                 f"{'' if iid == e['merged_into'] else f' als {iid}'} aufgerufen und ist dort wiedergegeben.")
            continue
        if withdrawn(items[k]):
            note("Der Punkt wurde abgesetzt.")
            continue
        if not pr:
            note(f"Dieser Punkt stand nicht in der gedruckten Tagesordnung; er wurde {where} als {iid} aufgerufen.")
        elif pr["day"] != days[s][0]:
            note(f"Dieser Tagesordnungspunkt war für {pr['day']} vorgesehen und wurde {where} als {iid} aufgerufen.")
        elif pr["id"] != iid:
            note(f"Dieser Tagesordnungspunkt wurde als {iid} aufgerufen.")
        others = [m["printed"]["id"] for m in entries if m["merged_into"] and m["key"] == k]
        if others:
            note(f"Zusammen mit diesem Punkt wurde {' und '.join(others)} aufgerufen.")
        printed_handled = [x for x in handled[s] if x in anchor]
        if pr and pr["day"] == days[s][0] and k in printed_handled:
            n = printed_handled.index(k)
            before = printed_handled[n - 1] if n else None
            m = in_file[s].index(k)
            if before and before != (in_file[s][m - 1] if m else None) and before not in joint.get(k, []):
                note(f"Dieser Tagesordnungspunkt wurde nach {shown(before)} behandelt.")
        partners = [p for p in joint.get(k, []) if p in placed]
        if partners:
            owner = (s, items[k]["debate_owner"])
            note(f"Gemeinsam beraten mit {', '.join(shown(p) for p in partners)}."
                 + (f" Die Aussprache ist unter {shown(owner)} wiedergegeben." if owner != k and owner in placed
                        and items[owner]["kind"] in ("debate", "qa") else ""))
        e["notes"] += [n for n in items[k]["notes"] if not MOVED.match(n) and n not in e["notes"]]
    return {"sids": sids, "entries": entries, "sittings": sit, "structures": st, "days": days}


def modes_of(sp, units, llm_summary=None):
    """{mode: {"html": [paragraphs], "words": n}} of one composed speech (see render.MODES)."""
    parts = {p["type"]: p for p in sp["parts"]}
    paragraphs = [units[i]["text"] for i in sp["units"]]
    full = " ".join(paragraphs)
    spans = render.kept_spans(full, sp)
    out, at, marked = {}, 0, []
    for par in paragraphs:
        marked.append(render.marked(par, at, spans))
        at += len(par) + 1
    if "summary" in parts:
        out["kurz"] = {"html": [render.esc(parts["summary"]["text"])], "words": len(parts["summary"]["text"].split())}
    if llm_summary:
        out["kurzllm"] = {"html": [render.esc(llm_summary)], "words": len(llm_summary.split())}
    nlp = topic.nlp() if sp.get("core") else None
    if nlp:
        s = indirect.summary_mode(sp["speaker"], sp["core"], nlp, len(full.split()))
        if s:
            out["zusammen"] = {"html": [render.esc(s)], "words": len(s.split())}
    if "quote" in parts:
        text = parts["quote"]["text"]
        out["auszug"] = {"html": [render.esc(p) for p in text.split("\n")], "words": len(text.replace("[…]", "").split())}
    if nlp:
        s = indirect.indirect_mode(sp["speaker"], sp["core"], nlp)
        if s:
            out["indirekt"] = {"html": [render.esc(s)], "words": len(s.split())}
    out["wortlaut"] = {"html": marked, "words": len(full.split())}
    return out


def llm_summaries(sid):
    """{(item id, position): summary} written by the LLM and accepted by the rules; {} if a reply is missing and no
    LLM command is set."""
    try:
        nds, _, _ = compose.compose(sid, "B")
    except RuntimeError:
        return {}
    return {(it["id"], k): p["text"] for it in nds["items"] for k, sp in enumerate(it["speeches"])
            for p in sp["parts"] if p["type"] == "summary" and p["source"] == "llm"}


def compose_week(sids, llm=False):
    """The plan filled with what the single-sitting pipeline wrote for every item -> one NDS dict for the week."""
    plan = build_plan(sids)
    done, compute, llm_sittings = {}, {}, []
    for s in plan["sids"]:
        nds, _, _ = compose.compose(s, "C", keep_core=True)
        extra = llm_summaries(s) if llm else {}
        if extra:
            llm_sittings.append(s)
        units = plan["sittings"][s]["units"]
        for it in nds["items"]:
            for k, sp in enumerate(it["speeches"]):
                sp["modes"] = modes_of(sp, units, extra.get((it["id"], k)))
            done[(s, it["id"])] = it
        for name, v in nds["compute"].items():
            compute[name] = round(compute.get(name, 0) + v, 4)
    items = []
    for e in plan["entries"]:
        k, pr = e["key"], e["printed"]
        src = {} if k is None or e["merged_into"] else done[k]
        iid = pr["id"] if pr else k[1]
        day, long_day = plan["days"][k[0]] if k else (None, None)
        items.append({
            "id": iid if pr else own_number(k), "title": pr["title"] if pr else clean_title(src["title"]),
            "drucksachen": src.get("drucksachen") or (pr or {}).get("drucksachen", []), "kind": src.get("kind"),
            "section": e["section"], "notes": e["notes"], "speeches": src.get("speeches", []), "votes": src.get("votes", []),
            "referrals": src.get("referrals", []), "planned_day": pr["day"] if pr else None, "merged_into": e["merged_into"],
            "handled": {"sid": k[0], "sitting": number(k[0]), "day": day, "date": long_day, "as": k[1],
                        "position": plan["structures"][k[0]]["items"][k[1]]["handled_pos"]} if k else None})
    metas = [plan["sittings"][s]["meta"] for s in plan["sids"]]
    return {"sids": plan["sids"], "llm_sittings": llm_sittings, "items": items, "compute": compute, "metas": metas}


def main():
    a = argparse.ArgumentParser(description="One Niederschrift for all sittings of a week, in the printed agenda's order")
    a.add_argument("sids", nargs="+")
    a.add_argument("--llm", action="store_true", help="add the LLM's one-sentence summaries where they are available")
    a.add_argument("--pdf", action="store_true")
    x = a.parse_args()
    wk = compose_week(x.sids, x.llm)
    compose.OUT.mkdir(parents=True, exist_ok=True)
    stem = compose.OUT / f"{wk['sids'][0]}-{wk['sids'][-1]}"
    stem.with_suffix(".html").write_text(render.render_week(wk), encoding="utf-8")
    if x.pdf:
        render.to_pdf(stem.with_suffix(".html"), stem.with_suffix(".pdf"))
    for it in wk["items"]:                                  # the modes are for reading; the data keeps the parts
        for sp in it["speeches"]:
            modes = sp.pop("modes")
            sp.pop("core", None)
            for key, name in (("kurzllm", "summary_llm"), ("zusammen", "core_summary"), ("indirekt", "indirect")):
                sp[name] = modes[key]["html"][0] if key in modes else None
    stem.with_suffix(".json").write_text(json.dumps(wk, ensure_ascii=False, indent=1), encoding="utf-8")
    planned = sum(it["planned_day"] is not None for it in wk["items"])
    print(f"{stem.name}: {planned} planned items + {len(wk['items']) - planned} others, "
          f"{sum(len(it['speeches']) for it in wk['items'])} speeches, rules {wk['compute']['rule_seconds']} s CPU, "
          f"LLM summaries for {len(wk['llm_sittings'])} sitting(s) -> {stem}.html")


if __name__ == "__main__":
    main()
