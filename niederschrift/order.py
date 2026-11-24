"""Order of the Niederschrift: the Tagesordnung as printed BEFORE the sitting (style guide rule 1).

  P1 Printed items keep their printed position (also when renumbered); a merged item appears once, at the slot of
     the printed item that became its sub-item a).
  P2 Slot replacement: an unplanned item that took over the number of an unmatched printed item stands in that slot,
     but only if the number fits the final numbering there (matched neighbours before < n < after).
  P3 Other unplanned TOPs (moved from another day) are placed by number among the TOPs.
  P4 Unplanned partners of a joint call follow their anchor (one debate, reported once).
  P5 Remaining Zusatzpunkte go to the end, by number ("rest").
  P6 An item not handled right after its predecessor in this list gets the Münster note
     "Dieser Tagesordnungspunkt wurde nach TOP X behandelt."
"""
import re


def num(it):
    m = re.search(r"(TOP|ZP) (\d+)", it)
    return (m.group(1), int(m.group(2))) if m else ("?", 999)


def _anchor_printed(agenda, final_id, sub_a_title):
    """Printed item that is the anchor of a merged final item: best title overlap with the AP sub-item a)."""
    cands = [a for a in agenda if a["id"] == final_id]
    if len(cands) < 2 or not sub_a_title:
        return cands[0]["printed_id"] if cands else None
    words = set(re.findall(r"[a-zäöüß]{5,}", sub_a_title.lower()))
    return max(cands, key=lambda a: len(words & set(re.findall(r"[a-zäöüß]{5,}", a["title"].lower()))))["printed_id"]


def plan_order(agenda, items, joint, sub_a_titles=None):
    """agenda: sitting['agenda'] (printed order, mapped ids); items: all final item ids known for the sitting
    (handled or in the AP); joint: {item: [partners]}. -> (seq, rest, rule)."""
    sub_a_titles = sub_a_titles or {}
    rule, seq = {}, []
    planned = [a["id"] for a in agenda]
    anchors = {fid: _anchor_printed(agenda, fid, sub_a_titles.get(fid)) for fid in set(planned) if fid[:3] != "TO-"}
    unplanned = [i for i in items if i not in planned]
    final_of = [(a["printed_id"], a["id"]) for a in agenda]
    slots = {}
    for k, (pid, fid) in enumerate(final_of):
        if not fid.startswith("TO-"):
            continue
        cand = pid if pid in unplanned else None
        if not cand:
            continue
        before = [num(f)[1] for p, f in final_of[:k] if not f.startswith("TO-") and f[:3] == pid[:3]]
        after = [num(f)[1] for p, f in final_of[k + 1:] if not f.startswith("TO-") and f[:3] == pid[:3]]
        n = num(cand)[1]
        if (not before or before[-1] < n) and (not after or n < after[0]):
            slots[fid] = cand
            rule[cand] = "P2"
    for a in agenda:
        fid = a["id"]
        if fid in slots:
            seq.append(slots[fid])
        elif fid.startswith("TO-"):
            seq.append(fid)
            rule[fid] = "P1"
        elif fid not in seq and anchors.get(fid) == a["printed_id"]:
            seq.append(fid)
            rule[fid] = "P1"
    for u in sorted(unplanned, key=num):                                       # P3
        if u in rule or not u.startswith("TOP") or any(j in seq for j in joint.get(u, [])):
            continue
        n = num(u)[1]
        pos = next((k for k, x in enumerate(seq) if x.startswith("TOP") and num(x)[1] > n), len(seq))
        seq.insert(pos, u)
        rule[u] = "P3"
    for anchor in list(seq):                                                   # P4
        ins = seq.index(anchor) + 1
        for j in joint.get(anchor, []):
            if j in items and j not in seq and j not in planned:
                seq.insert(ins, j)
                ins += 1
                rule[j] = "P4"
    rest = sorted([i for i in items if i not in seq], key=num)                # P5
    for r in rest:
        rule[r] = "P5"
    return seq, rest, rule


def order_note(it, seq_all, handled, joint):
    """P6: '(Hinweis: Dieser Tagesordnungspunkt wurde nach TOP X behandelt.)' when the handled predecessor differs."""
    if it not in handled:
        return None
    k = handled.index(it)
    prev_h = handled[k - 1] if k > 0 else None
    pos = seq_all.index(it)
    prev_r = seq_all[pos - 1] if pos > 0 else None
    if prev_h is None or prev_h == prev_r or prev_h in joint.get(it, []):
        return None
    if prev_r in joint.get(prev_h, []) or (prev_r and prev_r.startswith("TO-") and k > 0 and prev_h == next(
            (x for x in reversed(seq_all[:pos]) if x in handled), None)):
        return None
    return f"Dieser Tagesordnungspunkt wurde nach {prev_h} behandelt."
