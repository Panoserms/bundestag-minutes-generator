"""Part S: structure of one sitting (items, segmentation, plan order, votes, AP decisions) and the NDS skeleton.

    .venv/bin/python -m niederschrift.structure            # dev sittings -> work/structure/<sid>.json + scores
"""
import json
import re
import sys
from pathlib import Path

from . import order, segment, votes

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "work" / "structure"
QA = re.compile(r"Befragung der Bundesregierung|Fragestunde")


def _joint(events):
    joint = {}
    for e in events:
        if e["type"] == "call" and len(e["items"]) > 1:
            for x in e["items"]:
                joint.setdefault(x, [])
                joint[x] += [y for y in e["items"] if y != x and y not in joint[x]]
    return joint


def label(iid):
    if iid.startswith("TO-"):
        return f"Punkt {iid.split()[-1]} der gedruckten Tagesordnung"
    n = iid.split()[1]
    return f"Punkt {n} der Tagesordnung" if iid.startswith("TOP") else f"Zusatzpunkt {n}"


def build_structure(sitting):
    """-> {sid, order, rest, handled, unit_items, items{...}} (docs/ARCHITECTURE.md)."""
    units, agenda, ap = sitting["units"], sitting["agenda"], sitting["amtprot"]
    seg = segment.segment(units, agenda, ap)
    handled, joint = seg["handled_order"], _joint(seg["events"])
    ap_by = {a["id"]: a for a in ap}
    ag_by = {}
    for a in agenda:
        ag_by.setdefault(a["id"], []).append(a)
    unit_items = {s["i"]: [x for x in s["items"] if x != "ERÖFFNUNG"] for s in seg["units"]}
    known = list(dict.fromkeys([a["id"] for a in agenda] + [a["id"] for a in ap] + handled))
    parts = {iid: votes.ap_parts(ap_by[iid]["text"]) if iid in ap_by else ("", []) for iid in known}
    sub_a = {iid: (p[1][0]["title"] if p[1] else "") for iid, p in parts.items()}
    seq, rest, rule = order.plan_order(agenda, [i for i in known if not i.startswith("TO-")], joint, sub_a)
    seq_all = seq + rest
    for i in known:                                      # printed items that are neither handled nor in the AP
        if i not in seq_all:
            seq_all.append(i)
            seq.append(i)
    owner = {}
    for iid in seq_all:
        grp = [iid] + joint.get(iid, [])
        owner[iid] = min(grp, key=lambda x: seq_all.index(x) if x in seq_all else 999)
    lab = {s["i"]: s for s in seg["units"]}
    items = {}
    for iid in seq_all:
        us = [s["i"] for s in seg["units"] if iid in s["items"]]
        printed = ag_by.get(iid, [])
        head, subs = parts[iid]
        title = (printed[0]["title"] if printed else "") or head or (subs[0]["title"] if subs else "")
        title = re.sub(r"\s+", " ", title).strip()
        ap_text = ap_by.get(iid, {}).get("text", "")
        ds = sorted(set(sum((p.get("drucksachen", []) for p in printed), [])) | set(ap_by.get(iid, {}).get("drucksachen", [])))
        speeches = [i for i in us if units[i]["role"] in ("member", "government") and units[i]["rede"]]
        ap_lines = votes.ap_decisions(ap_text)
        abgesetzt = any(l.startswith(("wird abgesetzt", "Absetzung")) for l in ap_lines) and not us
        if iid not in handled and not us:
            kind = "not_handled"
        elif QA.search(title + " " + ap_text[:200]):
            kind = "qa"
        elif speeches or (owner[iid] != iid and any(units[i]["rede"] for i in us)):
            kind = "debate"
        else:
            kind = "no_debate"
        notes = []
        if rule.get(iid) == "P2":
            slot = next((a for a in agenda if a["id"] == "TO-" + iid), None)
            if slot:
                notes.append(f"An dieser Stelle der gedruckten Tagesordnung stand: „{slot['title'][:160]}“. "
                             "Der Punkt ist nach dem Druck an dessen Stelle getreten.")
        if rule.get(iid) in ("P3", "P4", "P5") and iid not in [a["id"] for a in agenda]:
            notes.append("Dieser Punkt stand nicht in der gedruckten Tagesordnung dieses Sitzungstages.")
        if len(printed) > 1:
            others = [p for p in printed if p["printed_id"] != order._anchor_printed(agenda, iid, sub_a.get(iid))]
            for p in others:
                notes.append(f"Mit diesem Punkt wurde der gedruckte {p['printed_id'].replace('TOP', 'Punkt')} "
                             f"(„{p['title'][:120]}“) verbunden.")
        on = order.order_note(iid, seq_all, handled, joint)
        if on:
            notes.append(on)
        if joint.get(iid):
            partners = ", ".join(joint[iid])
            notes.append(f"Gemeinsam beraten mit {partners}." + (
                f" Die Aussprache ist unter {owner[iid]} wiedergegeben." if owner[iid] != iid else ""))
        if abgesetzt:
            notes.append("Der Punkt wurde abgesetzt.")
        elif kind == "not_handled":
            notes.append("Der Punkt wurde in dieser Sitzung nicht aufgerufen.")
        n_speakers = len({units[i]["speaker"] for i in speeches})
        if re.search(r"ohne Aussprache", title + " " + head) and n_speakers >= 2:
            notes.append("Prüfhinweis: Unter einem Punkt „ohne Aussprache“ wurden Redebeiträge zugeordnet. "
                         "Wahrscheinlich wurde ein Aufruf nicht erkannt; Zuordnung bitte prüfen.")
        if ap_lines and not us and not abgesetzt and iid in handled:
            notes.append("Prüfhinweis: Laut Amtlichem Protokoll behandelt, im Plenarprotokoll kein Aufruf gefunden.")
        eps = []
        for i in us:
            if units[i]["role"] == "chair" and lab[i]["phase"] in ("Abstimmung", "Ergebnis"):
                for e in votes.vote_episodes(units[i]["text"]):
                    if e["positions"] or e["result"]:
                        own = set(ds)
                        if e["drucksachen"] and own and not set(e["drucksachen"]) & own:
                            continue                     # a jointly called partner's vote (shared chair text)
                        eps.append(dict(e, unit=i, text=e["text"][:600]))
        paired = votes.pair_votes(eps, subs) if subs else []
        for p in paired:
            v = p["vote"]
            if p["decision"] and not p["decision"].startswith("wird abgesetzt"):
                p["result_sentence"] = votes.result_sentence(v["positions"] if v else {}, v["result"] if v else None,
                                                             v["reading"] if v else None, p["decision"])
                if v and v["result"] and ((v["result"] == "angenommen") != (not p["decision"].startswith("Ablehnung"))):
                    notes.append(f"Prüfhinweis: Ergebnis der Sitzungsleitung („{v['result']}“) und Amtliches "
                                 f"Protokoll („{p['decision'][:60]}…“) passen nicht zusammen.")
        topic_text = "\n".join(x for x in [title] + [p["raw"] for p in printed] + [ap_text] if x)
        items[iid] = {"id": iid, "label": label(iid), "title": title, "drucksachen": ds, "kind": kind,
                      "units": us, "debate_owner": owner[iid], "notes": notes, "topic_text": topic_text,
                      "votes": eps, "ap_lines": ap_lines, "decisions": paired,
                      "subitems": [{k: s[k] for k in ("letter", "title", "drucksachen")} for s in subs if s["letter"]],
                      "printed_id": printed[0]["printed_id"] if printed else None, "rule": rule.get(iid, "P1"),
                      "handled_pos": handled.index(iid) + 1 if iid in handled else None}
    return {"sid": sitting["meta"]["sid"], "order": seq, "rest": rest, "handled": handled,
            "unit_items": unit_items, "items": items, "announcements": seg["announcements"],
            "events": seg["events"], "segmentation": seg["units"]}


def nds_skeleton(structure, sitting=None):
    """NDS-JSON without speeches: items in Niederschrift order with id, title, notes, votes, referrals.
    Extra keys for render.py: label, drucksachen, kind, section ('main'|'rest'), debate_owner, subitems."""
    items = []
    for iid in structure["order"] + structure["rest"]:
        it = structure["items"][iid]
        vs, refs = [], []
        subs = {s["letter"]: dict(s, referrals=[]) for s in it["subitems"]}
        for p in it["decisions"]:
            if p["referrals"]:
                refs += [c for c in p["referrals"] if c not in refs]
                if p["sub"] in subs:
                    subs[p["sub"]]["referrals"] += p["referrals"]
            elif p["decision"] and not p["decision"].startswith("wird abgesetzt"):
                vs.append({"result_sentence": p["result_sentence"], "decision": p["decision"], "sub": p["sub"]})
        items.append({"id": iid, "label": it["label"], "title": it["title"], "drucksachen": it["drucksachen"],
                      "kind": it["kind"], "section": "rest" if iid in structure["rest"] else "main",
                      "debate_owner": it["debate_owner"], "notes": list(it["notes"]), "speeches": [],
                      "votes": vs, "referrals": refs, "subitems": list(subs.values())})
    return {"sid": structure["sid"], "system": None, "items": items, "compute": {}}


def main(argv=None):
    from .sources import load_sitting, sittings
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for sid in sittings("dev"):
        s = load_sitting(sid)
        assert s["meta"]["split"] == "dev"
        st = build_structure(s)
        (OUT / f"{sid}.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        r = segment.score(st["segmentation"], s["units"], s["gold"], sid)
        r.pop("errors")
        rows.append(r)
        print(json.dumps(r))
    (OUT / "dev_scores.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
