# Side study: what a council's agenda and decision record tell a minutes generator

An exploratory corpus study of a second, smaller parliament: the **Kreistag Konstanz** (the county council of the
Landkreis Konstanz, Baden-Württemberg).
The main project works on the Bundestag, from its public verbatim record.
This study asks which of its rules carry over to local councils, using only two public document types:

- the **Tagesordnung** (agenda), published before each session;
- the **Beschlussprotokoll** (decision record, until 2025 called "Beschlüsse und Zusagen"), published after it.

Both come from the council information system at `kreistag.lrakn.de/buergerinfo`. **108 sessions, 2009 – March 2026, 203
documents.** 91 sessions have both an agenda and a decision record. Only statistics are stored in this repository
(`results/study.json`). The PDFs are downloaded by `fetch.py` and are not committed. Names of persons are masked.

```bash
python3 konstanz_agenda_study/fetch.py      # ~200 public PDFs -> konstanz_agenda_study/data/ (about 3 min)
python3 konstanz_agenda_study/analyse.py    # -> konstanz_agenda_study/results/study.json (spaCy de_core_news_md for M9)
```

Set `KONSTANZ_DATA=/some/folder` to keep the downloads outside the repository.

## Key findings

1. **The decision record follows the agenda numbering, even when the session did not.** 1,213 of the 1,214
   matched items carry the same number in both documents. In all 91 sessions, the record lists the items in agenda
   order. Yet at least **25 of the 94 records (27%) note an item handled out of order** in a *Hinweis*
   ("Der TOP wurde nach TOP 9 aufgerufen"). This is the convention the Bundestag minutes use too: the plan's
   order plus a note, not the spoken order.
2. **Residents' question time is the item that moves most.** It sits in the middle of every agenda (95 of 95, never
   first or last), and 33 agendas tie it to a clock time ("ca. 17 Uhr"). When the session runs late, it is moved
   ("Aufgrund der fortgeschrittenen Zeit wurde die Bürgerfragestunde nach TOP 5 aufgerufen"). An order model must
   treat it as anchored to time, not to position.
3. **About three in four items need only a formula in the record.** Of 1,214 matched items, 894 (74%) are
   unanimous votes, *Kenntnisnahme* (the members "take note"), withdrawals, or items without a decision. Only
   majority votes, several decisions in one item and rejections need more than a template.
4. **The agenda title alone predicts this only for a few item types.** A rule learned on 2011–2021 and tested on
   2022–2026 cannot beat "always formula" overall (75%). But for three types it is nearly certain: announcements of
   non-public decisions, the closing item and reports. Together they are **17% of the test items, at 98%
   precision**. That is a cheap first filter, not a full classifier.
5. **The decision text adds most of the topic vocabulary.** An agenda title has a median of 6 content stems and its
   decision text 25, and 88% of the decision text's stems are not in the title. The Bundestag system uses the same
   idea: its topic signatures seed from the agenda title *and* the official decision record.
6. **House style changes, so templates need a date.** Majority labels changed from "Mehrheit der Ja-Stimmen gegen N
   Nein-Stimmen" (2011–2021, every year) to "mehrheitliche Ja-Stimmen, N Gegenstimmen" (from 2022, no overlap).
   "Einvernehmen" appears only in 2025. Reported *dass*-clauses moved from **Konjunktiv I to the indicative**: 63%
   Konjunktiv in 2009–2014, 11% in 2015–2019, 3% in 2020–2023, 0% in 2024–2026 (spaCy `Mood=Sub`, small counts).

## M1 Corpus

| Period | Sessions | With agenda | With record | Agenda items (median per session) | Sub-items |
|---|---|---|---|---|---|
| 2009–2014 | 39 | 39 | 28 | 14 | 17 |
| 2015–2019 | 35 | 34 | 34 | 14 | 17 |
| 2020–2023 | 21 | 19 | 21 | 20 | 104 |
| 2024–2026 | 13 | 13 | 11 | 17 | 111 |

Agendas got longer and much more nested after 2020: sub-items "7.1., 7.2." grew from 17 to over 100 per period.
For a segmenter, the sub-item is now a real unit, not an exception.

## M2 Item types and the shape of an agenda

Item types come from 10 title rules (`TYPES` in `analyse.py`), over 1,629 agenda items.

| Type | Share | Median position (0 = first, 1 = last) | With a Drucksache |
|---|---|---|---|
| other subject item | 31% | 0.50 | 86% |
| budget and finance | 19% | 0.50 | 93% |
| membership, elections, appointments | 14% | 0.29 | 97% |
| report, presentation | 9% | 0.82 | 77% |
| closing: miscellaneous, questions | 6% | 1.00 | 6% |
| residents' question time | 6% | 0.79 | 0% |
| opening: non-public decisions announced | 5% | 0.05 | 1% |
| opening: formal approval | 4% | 0.00 | 6% |
| statute, rules | 4% | 0.50 | 96% |
| motion by a group | 3% | 0.57 | 98% |

The agenda is a genre with a fixed frame. In **94 of 99 sessions** with four or more items, the opening items
are among the first three and the closing items among the last two. Items without a Drucksache (a numbered
committee paper) are mostly frame items. This gives a segmenter strong position priors for them.

## M3 Agenda vs. decision record

| | |
|---|---|
| Sessions with both documents | 91 |
| Agenda items | 1,438 |
| Found in the record (number + title, title similarity ≥ 0.5) | 1,214 (84%) |
| … under the same number | 1,213 |
| Records in agenda numbering | 91 / 91 |
| Records with a note: item **moved** | 25 (38 notes, hand check 38/38 after a length filter) |
| Records with a note: item **withdrawn** | 10 (12 notes, 11 real) |
| Records with a note: item **deferred** | 15 (16 notes, 15 real) |
| Records with a moved or withdrawn note | 30 of 94 (32%) |

Agenda items **missing from the record** (224) are mostly items with nothing to decide: residents' question time
(78), reports and notices (60), announcements of urgent decisions (14). The record leaves an item out when no
decision was taken. The remaining 72 (51 subject items, 10 budget items and a few others) include real withdrawals
and some parse misses (titles split differently in the two documents). Items in the record but not on the agenda are not reported: the count (151)
is dominated by numbered lists inside decision texts, so it is unreliable.

Four sessions also have an updated agenda. Each changed one or two titles, and only one added an item.

## M4 What the record decides, by agenda type

| Agenda type | Items | Formula only |
|---|---|---|
| other subject item | 415 | 63% |
| budget and finance | 285 | 68% |
| membership, elections, appointments | 149 | 83% |
| closing: miscellaneous, questions | 87 | 98% |
| report, presentation | 71 | 93% |
| opening: formal approval | 62 | 76% |
| opening: non-public decisions announced | 59 | 100% |
| statute, rules | 45 | 67% |
| motion by a group | 36 | 69% |

"Formula only" = unanimous (with or without abstentions), Kenntnisnahme, withdrawn, or no decision. All items:
unanimous 490, several decisions 225, no decision 199, Kenntnisnahme 135, majority 78, unanimous with
abstentions 59, withdrawn 11, rejected 6, other 11.

The Kenntnisnahme sentence is stable: "Die Mitglieder des Kreistags nehmen … zur Kenntnis" in 155 items, against 7
for "Der Kreistag nimmt … zur Kenntnis", and none of the latter since 2020.

## M5 Can the agenda title predict a formula-only item?

Learned on 2011–2021 (883 items), tested on 2022–2026 (331 items, 75% formula only).

| Rule: predict "formula" for types whose training share is ≥ … | Test items covered | Precision |
|---|---|---|
| 0.5 (nearly every type) | 98.5% | 74.5% (= the "always formula" baseline) |
| 0.9 (announcements, closing item, reports) | 16.9% | **98.2%** |
| 0.95 (announcements, closing item) | 11.5% | 100% |

An honest negative result for the general case: the title type does not separate contested items from routine
ones. A high-precision subset exists, and a pipeline can use it to skip drafting for one item in six.

## M6 House style over time

| Year | "Mehrheit der Ja-Stimmen gegen N Nein-Stimmen" | "mehrheitliche Ja-Stimmen, N Gegenstimmen" |
|---|---|---|
| 2011–2021 | present every year (4–14 per year) | 0 |
| 2022–2026 | 0 | present every year (3–15 per year) |

The switch is abrupt (between the 2021 and 2022 records), so a label generator trained on older records would
write the wrong label for every majority vote today.

## M7 Vocabulary

Median content stems per item: title 6, decision text 25. The median decision text has 88% stems that are not in
the title. The stems that occur under almost every item (*Konstanz, Kreistag, Landkreis, Vorsitzende, Mitglied,
zugestimmt, Kenntnis, beauftragt, Verwaltung*) say "this is a council item", not which one. They need a low weight,
like the genre words of the Bundestag background corpus (`data/resources/bt_genre_words.tsv`).

## M8 Planned speaking time

Since May 2022, 19 agendas give a planned duration per item (296 items, median session total 137 minutes).

| Type | Items | Median planned minutes |
|---|---|---|
| report, presentation | 24 | 10 |
| motion by a group | 13 | 10 |
| other subject item | 112 | 5 |
| budget and finance | 73 | 5 |
| membership, elections, appointments | 38 | 4 |
| statute, rules | 11 | 2 |
| opening items | 24 | 1–1.5 |

Planned time also tracks the outcome: unanimous items are planned at a median of 3 minutes, Kenntnisnahme items at
10. The office plans reports as talks and routine votes as formalities. This is a free prior for how much debate
to expect under an item.

## M9 Mood in reported speech

Finite verb of *dass*-clauses after a reporting verb ("teilt mit, dass …") in the decision records. Counted with
spaCy de_core_news_md (`Mood=Sub`), at most 200 clauses per record.

| Period | Clauses | Konjunktiv |
|---|---|---|
| 2009–2014 | 41 | 63% |
| 2015–2019 | 36 | 11% |
| 2020–2023 | 39 | 3% |
| 2024–2026 | 14 | 0% |

Small counts, and the tagger was not hand-checked, but the direction is clear and monotone: the office stopped
using Konjunktiv I for reported speech. A minutes generator should not assume the textbook Konjunktiv I.

## What this means for the Bundestag rules

| Finding here | Rule in the main project |
|---|---|
| The record keeps the agenda numbering and notes moves (M3) | P1–P6: printed order + "(Hinweis: … nach TOP X behandelt.)" |
| Question time moves because it is tied to a clock time (M2, M3) | Order notes are computed from the handled sequence, never assumed |
| Withdrawn items are noted, not deleted (M3) | S6b: a withdrawal announcement is not a call; the item gets "Der Punkt wurde abgesetzt." |
| 74% of items need only a formula (M4) | Votes and decisions come from rules + the official record; a model writes only one sentence per speech |
| The decision text carries the topic vocabulary (M7) | Topic signatures seed from the agenda title and the Amtliches Protokoll |
| House style is dated (M6, M9) | The frame is a style setting (`docs/style_guide.md`), not learned from old documents |

## Limits

- One council, one office: the style findings describe this office's documents, not councils in general.
- Rules and parsers were written and checked by the same author, with no second annotator. Only the order notes
  and a sample of the parses were checked by hand.
- PDF extraction splits words ("Si t-zung"). A repair pass joins most of them, but older PDFs are noisier.
- The record is a decision document. It shows what was decided, not what was said.
