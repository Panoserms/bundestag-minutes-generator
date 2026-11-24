# Rulebook: Bundestag Niederschrift by rule

Every rule the system applies, in pipeline order, with the linguistic idea behind it and where it lives in the code.
The style the rules produce is fixed in [`style_guide.md`](style_guide.md); the measured effect is in [`RESULTS.md`](RESULTS.md).
Rule IDs are stable: tests and comments refer to them.

## Overview

| Step | Method | Linguistic idea |
|---|---|---|
| Segmentation into agenda items | the chair's call cues ("Ich rufe Tagesordnungspunkt 18 auf", also verb-second "Damit kommen wir zum …"), return cues, Drucksache numbers, a tf-idf fallback | discourse markers open segments; a return is an embedded segment (Grosz & Sidner 1986); a Drucksache number is a referring expression, i.e. cohesion by reference (Halliday & Hasan) |
| Order | rules P1–P6: printed position, slot replacement, joint calls, Zusatzpunkte at the end | the Niederschrift follows the plan, not the spoken sequence (59/59 Münster minutes) |
| Votes | the chair's "Wer stimmt dafür? – …" question–answer pairs, rendered with the Münster vote formula | adjacency pairs |
| Topic of an item | **topic signature** (Lin & Hovy 2000): seed lemmas from the agenda and Amtliches Protokoll text, plus log-likelihood (G²) key lemmas of the debate against a background corpus of 58 other sittings | topic vocabulary vs. **genre vocabulary**: words spread evenly over all items (Gries' DP), plus the genre stoplist from a corpus study of 59 Münster Niederschriften, are not topic evidence |
| Sentence labels | topic score + information cues (numbers, named entities, deontic modals, stance verbs, a following *(Beifall bei …)*) − formula cues (salutations, thanks, "Ich komme zum Schluss", replies to heckling) | applause marks the rhetorical high points (Atkinson 1984); deontic modality marks demands |
| Sentences split by a heckle | a sentence that the XML breaks over two paragraphs (an interjection in mid-sentence) is joined again: no sentence-final punctuation before the break, or a lower-case start after it | — (removed all 10 cut-off quotes of v2) |
| Choosing the excerpt (v3) | the speaker's **paragraphs** are the units: a paragraph with at least one on-topic sentence is a candidate, valued by its mean sentence score; Maximal Marginal Relevance (Carbonell & Goldstein 1998) with a bonus for neighbouring paragraphs, under a budget of 50% of the speech (≤ 450 words); one-sentence gaps are filled; questions of a question turn are always kept | paragraphs as topical segments (cf. Hearst 1997); cohesion: neighbouring passages read as one text (Halliday & Hasan) |
| Questions | in question time, the answer is scored against its own question | adjacency pair: the question sets the local topic |
| Kurzfassung, B | one LLM call per agenda item; the LLM sees only the best-scored excerpt sentences (≤ 180 words) and ≤ 4 off-topic sentences per speech. Each reply is checked by rule (one sentence, ≤ 30 words, starts with a verb, no quotation marks, no new numbers, no names that are not in the source) and replaced by the C rule if it fails | — |
| Kurzfassung, C | verb-final transformation of the best-scored sentence that can be moved safely ("Der Mindestbetrag muss … steigen." → *dass der Mindestbetrag … steigen muss*), framed by the speech-act verb of that sentence in the present tense (*fordert, kritisiert, warnt, fragt, ob …*). Sentences with *wir/ich*, pronoun subjects or anaphoric openers are skipped | speech acts (Searle 1976); German V2 → verb-final clause; deixis cannot be resolved without context |
| Speaker line | name (Fraktion); government members with their office | gender is never guessed from first names |

## 1. Segmentation: which agenda item is being discussed (`niederschrift/segment.py`)

The transcript has no agenda markup (removed in `sources.py`), so items are found from the chair's language.

| ID | Rule | Linguistic idea |
|---|---|---|
| S1 | The paragraph is the unit; only chair turns can change the item. Every other unit inherits the current item. | Topic continuity: a discourse segment lasts until it is closed or shifted (Grosz & Sidner 1986) |
| S2 | **Call cue.** "Ich rufe … auf", "Wir kommen zu Tagesordnungspunkt 5", verb-second "Damit kommen wir jetzt zum Zusatzpunkt 4", a paragraph that opens with the item number. All items named in the call become the current set (joint calls). | Discourse markers open segments; the separable verb *aufrufen* closes its clause with *auf* |
| S2b | **Cataphoric colon.** A chair turn that ends in "… Tagesordnungspunkt 2:" is a call, whatever the verb ("steigen jetzt ein in"). | The colon points forward to the item title that follows |
| S3 | **Return cue.** "Ich komme zurück zu Tagesordnungspunkt 14" opens an embedded segment: the next speech returns to the interrupted item. "Wir setzen die Aussprache zu TOP 9 fort" replaces the current item. | Embedded segments and the focus stack (Grosz & Sidner) |
| S4 | **Drucksache reference.** A call without a number is resolved by the Drucksache numbers in the next chair turns; a vote paragraph naming another item's Drucksache is filed under that item. | A Drucksache number is a referring expression (cohesion by reference, Halliday & Hasan 1976) |
| S5 | **Lexical cohesion.** A call with neither number nor Drucksache goes to the not-yet-handled item with the most similar title (tf-idf cosine ≥ 0.2, genre words removed). | Lexical cohesion as a segment signal (Hearst 1997) |
| S6 | Announcements before the first call (*abgesetzt, vorgezogen, erweitert*) are kept as notes, not topics. | — |
| S6b | **Withdrawal is not a call.** Items in a sentence whose predicate withdraws them ("Tagesordnungspunkt 8 … werden abgesetzt", *vertagt*, "von der Tagesordnung") are not called, even when the sentence opens with an item number. | The predicate, not the noun phrase, decides the speech act |
| — | Items in the scope of *nach / vor / im Anschluss an* ("TOP 9 wird nach TOP 10 aufgerufen") are reference points, not calls. | Prepositional scope |
| — | Sub-item headers ("Tagesordnungspunkt 43a:") keep the item and record the letter. | — |

Phases inside an item (Aufruf, Aussprache, Rede, Zwischenfrage, Kurzintervention, Abstimmung, Ergebnis,
Überweisung) are tagged from cue words, for the side-by-side view and for vote pairing.

## 2. Order: the printed plan, not the spoken sequence (`niederschrift/order.py`)

| ID | Rule |
|---|---|
| P1 | Printed items keep their printed position, also when renumbered. A merged item appears once, at the slot of the printed item that became its sub-item a). |
| P2 | An unplanned item that took over the number of an unmatched printed item stands in that slot, if the number fits between its matched neighbours. |
| P3 | Other unplanned TOPs (moved from another day) are placed by number among the TOPs. |
| P4 | Unplanned partners of a joint call follow their anchor; the debate is reported once, under the first. |
| P5 | Remaining Zusatzpunkte go to the end, by number. |
| P6 | An item not handled right after its predecessor in this list gets "(Hinweis: Dieser Tagesordnungspunkt wurde nach TOP X behandelt.)". |

Evidence: in all 59 public Münster council minutes of the corpus study, the minutes follow the agenda, not the spoken
order. The side study in [`konstanz_agenda_study/`](../konstanz_agenda_study/FINDINGS.md) finds the same in a county
council's decision records: 91 of 91 keep agenda numbering, and at least 25 note a moved item.

### A whole week in one file (`niederschrift/week.py`)

| ID | Rule |
|---|---|
| W1 | The printed agenda of the whole week is mapped to the Amtliche Protokolle of all its sittings at once. A day-by-day mapping cannot see an item that moved to another day. |
| W2 | Every planned item stands at its printed place, with the content of whichever sitting handled it. A note names the sitting if the day changed and the number if it changed. |
| W3 | Two planned items handled as one are reported once, under the one that became sub-item a); the other refers to it. |
| W4 | Withdrawn items, and items for which no call was found, keep their place with a note. |
| W5 | An unplanned item called together with a planned one follows it. The other unplanned items close the file, in the order in which they were called. |
| W6 | The order note ("… wurde nach TOP X behandelt") compares the planned items of one sitting with their order in the file. References use the planned numbers. |

## 3. Votes and decisions (`niederschrift/votes.py`)

| ID | Rule | Linguistic idea |
|---|---|---|
| V1 | A vote is a question–answer pair: "Wer stimmt dafür?" / "Gegenprobe!" / "Enthaltungen?" + the answer naming groups. An answer only answers the question right before it. | Adjacency pairs (conversation analysis) |
| V2 | The Bundestag names voters instead of counting them ("Das sind CDU/CSU und SPD"); "alle übrigen" is resolved against the groups not yet named; "niemand", "sehe ich nicht" = none. | Reference resolution within the vote routine |
| V3 | The closing third states the result ("… ist angenommen / abgelehnt"); the result always comes from that sentence, never from counting. | Three-part routine: question, answer, result |
| V4 | Decision texts are copied word for word from the Amtliches Protokoll (the Bundestag's own decision record), never from the speech. | — |
| V5 | Result line in the present-tense frame: "Der Bundestag beschließt mit Mehrheit (CDU/CSU, SPD) bei Gegenstimmen (AfD):" / "… lehnt … ab:"; referrals as "Der Bundestag beschließt die Überweisung an:". Groups only if named in the sitting. | — |
| V6 | A withdrawn item gets "Der Punkt wurde abgesetzt."; an item with no speeches and no vote gets "Der Punkt wurde in dieser Sitzung nicht aufgerufen." | — |

## 4. What each speaker said: the excerpt (`topic.py`, `select.py`, `debate.py`)

| ID | Rule | Linguistic idea |
|---|---|---|
| T1 | **Topic signature** per item: seed lemmas from the agenda and Amtliches Protokoll text, plus the top log-likelihood (G²) lemmas of the debate against a background of 58 other sittings. | Topic signatures (Lin & Hovy 2000); keyness (Dunning 1993) |
| T2 | **Genre words are not topic evidence**: lemmas spread evenly over all items (low Gries DP, high range) and the Münster genre stoplist are removed. | Dispersion (Gries 2008) |
| E1 | Each sentence gets topic score + information cues (numbers, named entities, Drucksache/§ references, deontic modals, stance verbs, a following *(Beifall …)*) − formula cues (salutations, thanks, "Ich komme zum Schluss", replies to heckling, meta-discourse). | Applause marks rhetorical high points (Atkinson 1984); deontic modality marks demands |
| E2 | A sentence that the XML splits around a heckle (no final punctuation before the break, or a lower-case start after it) is joined again. | — |
| E3 | The speaker's **paragraphs** are the units of the excerpt. A paragraph with an on-topic sentence is a candidate, valued by its mean sentence score. | Paragraphs as topical segments (Hearst 1997) |
| E4 | Choice by Maximal Marginal Relevance with a bonus for neighbouring paragraphs, under a budget of 50% of the speech (≤ 450 words); one-sentence gaps are filled; speeches under 60 words are kept whole; omissions are marked […]. | MMR (Carbonell & Goldstein 1998); cohesion of adjacent passages |
| E5 | The questions of a question turn (Zwischenfrage, Fragestunde) are always kept in the speaker's words. | A question is the first part of an adjacency pair: a paraphrase loses what exactly was asked |
| E6 | In question time, an answer is scored against its own question, not only against the item. | The question sets the local topic |
| E7 | Speaker line: name (Fraktion); government members with their office. Gender is never guessed from first names. | — |

## 5. The one-sentence Kurzfassung (`kurz.py`, `reported_llm.py`)

| ID | Rule |
|---|---|
| K1 | Form: no subject (the speaker line is the subject), a speech-act verb in the present tense, indicative, ≤ 25 words, no quotation marks, no evaluation. |
| K2 | **Rule version (system C, and B's fallback):** the best-scored sentence that can be moved safely becomes a verb-final clause ("Der Mindestbetrag muss … steigen." → *dass der Mindestbetrag … steigen muss*), framed by the speech-act verb of that sentence (*fordert, kritisiert, warnt, fragt, ob …*). Sentences with *wir/ich*, pronoun subjects, anaphoric openers or more than one finite verb are skipped (deixis cannot be resolved without context). |
| K3 | **Model version (system B):** one call per agenda item; the model sees only the best-scored excerpt sentences (≤ 180 words) and ≤ 4 off-topic sentences per speech. |
| K4 | **Every model sentence is checked by rule**: one sentence, ≤ 30 words, starts with a lower-case verb, no quotation marks, no digit and no proper name that is not in the source. A sentence that fails is replaced by K2 or left out. |

## 6. Checks on the output (`scripts/evaluate.py`)

| ID | Check |
|---|---|
| C1 | Every excerpt segment (split at […]) is an exact substring of the attributed speaker's own words. |
| C2 | Every excerpt comes from a paragraph inside the XML agenda block of its item (or a joint partner). |
| C3 | No excerpt starts in lower case or ends in a comma (sentence-split errors). |
| C4 | Printed items appear in printed order (Kendall discordant pairs = 0). |
