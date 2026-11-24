# Architecture

How the parts of the pipeline fit together. The rules themselves are in [`RULES.md`](RULES.md).

## Pipeline

```
sources.load_sitting(sid)                 the three public sources, agenda markup removed
   │
   ├── structure.build_structure(sitting)  items, segmentation, plan order, votes, decisions
   │
   ├── debate.digest_item(...)             per speech: the speaker's own words / reported / dropped
   │
   ├── compose.py                          joins both, adds the Kurzfassung (B: reported_llm.py, C: kurz.py)
   ├── render.render_html(nds) / to_pdf    Münster layout, HTML → PDF
   │
   └── baseline/ and scripts/evaluate.py   system A, and the metrics for A, B and C
```

Systems compared:
- **A, LLM only:** one LLM call per sitting gets the transcript (no agenda markup), the printed agenda, the Amtliches
  Protokoll text and the style guide, and writes the whole Niederschrift.
- **B, hybrid:** rules for structure, order, votes, decisions and the choice of the speaker's words; the LLM writes one
  sentence per speech.
- **C, rules only:** as B, but the sentence comes from a rule.

## Data split

`data/split.json`: dev = 21055, 21098, 21099, 21100; test = 21030, 21032, 21085. Rules were built and tuned on dev
sittings only. Background statistics come from the 58 other public sittings in `data/background_sittings.txt`
(`scripts/build_background.py --fetch`), never from the test sittings.

## Shared code

- `niederschrift/sources.py`: `load_sitting(sid)`, `sittings(split)`, `refs(text)`, `item_id`, `parse_xml`.
  A unit is `{i, rede, speaker, group, role (chair|member|government|other), text, reactions: [str]}`.
  `gold` = agenda blocks from the XML, used only to score the segmentation.
- `niederschrift/llm.py`: `call(prompt, system, tag, effort=None)`, cached and logged; `usage(prefix)`.

## Interfaces

### `niederschrift/structure.py`

```python
build_structure(sitting) -> dict
{
  "sid": "21100",
  "order": ["TOP 18", "ZP 6", ...],          # Niederschrift order = printed Tagesordnung (rules P1–P6)
  "rest": ["ZP 10", ...],                     # Zusatzpunkte not in the printed agenda (P5), rendered at the end
  "handled": [...],                           # order in which items were called in the sitting
  "unit_items": {i: ["TOP 18"], ...},         # segmentation: unit index -> item ids
  "items": {
    "TOP 18": {
      "id": "TOP 18", "title": str, "drucksachen": [str],
      "kind": "debate" | "qa" | "no_debate" | "not_handled",   # qa = Befragung der Bundesregierung, Fragestunde
      "units": [i, ...],                      # all units filed under the item
      "debate_owner": "TOP 18",               # joint calls: the item under which the debate is reported
      "notes": [str],                         # Hinweise (order, joint call, abgesetzt, slot replacement, checks)
      "topic_text": str,                      # title + printed agenda text + AP item text: seed for the topic profile
      "votes": [...], "ap_lines": [...], "subitems": [...]   # read by render.py
    }, ...
  }
}
```

### `niederschrift/debate.py`

```python
group_speeches(units: list, item_unit_ids: list[int]) -> list[Speech]
digest_item(item_id, topic_text, speeches, sitting_units, cfg=None) -> list[Digest]
render_reported_rule(digest) -> str | None          # system C template for the reported part
```

A `Speech`: `{"rede", "speaker", "group", "role", "kind": "Rede"|"Zwischenfrage"|"Kurzintervention"|"Erwiderung"|
"Frage"|"Antwort", "gender": "f"|"m"|None, "sentences": [{"k", "text", "unit", "reactions": [str]}], "words": int}`

A `Digest` (one per speech, in speech order):
```python
{"speaker", "group", "role", "kind", "gender", "rede", "words_source": int,
 "parts": [                                     # in output order
    {"type": "quote", "text": "exact words", "sent": [k, ...], "verb": "forderte", "lead": "Claudia Roth (…) forderte:"},
    {"type": "formula", "text": "und bat um Zustimmung"},
 ],
 "report_sents": [k, ...],                      # off-topic but contentful sentences: input for the reported part
 "report_hint": {"verb": "kritisierte", "addressees": ["AfD"], "keywords": [...]},
 "dropped": [k, ...], "scores": [{"k", "topic", "info", "label": "QUOTE"|"REPORT"|"DROP"}]}
```
Every quote must be an exact substring of the speech's own text (whitespace-normalised). The tests check this.

### NDS-JSON (output of every system)

`outputs/<sid>_<system>.json` (the runs behind the results are in `results/nds/`):
```python
{"sid", "system": "A"|"B"|"C",
 "items": [ {"id", "title", "notes": [str],
             "speeches": [ {"speaker", "group", "kind",
                            "parts": [ {"type": "quote"|"reported"|"formula", "text": str, "verb": str|None,
                                        "source": "rule"|"llm"} ]} ],
             "votes": [ {"result_sentence": str, "decision": str} ], "referrals": [str] } ],
 "compute": {"rule_seconds", "llm_calls", "input_tokens", "output_tokens", "cost_usd", "llm_seconds"}}
```
For system A, `baseline/parse_md.py` turns the model's Markdown into this format.

## Version 3: the speaker's words + Kurzfassung

- `select.choose` picks the speaker's paragraphs (cfg `unit: paragraph`, `ratio 0.50`, `max_quote_words 450`).
- `compose.body_of` joins the picks into ONE quote part per speech: the speaker's words, `\n` at their paragraph
  breaks, `[…]` for omissions.
- The `summary` part (the Kurzfassung) comes before it, written by `reported_llm.kurz_for_item` (B) or
  `kurz.rule` (C and B's fallback).
- `render.speech_block` prints the speaker line, then the Kurzfassung, then the words.
- NDS speeches now carry `units` (the XML paragraphs), which `scripts/side_by_side.py` uses.
- The version 2 parts (quote leads, the reported part) are no longer rendered. `frames.assign` still classifies the speech
  acts.
