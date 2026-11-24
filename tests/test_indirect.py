"""Indirect speech in Konjunktiv I, by general grammar. A sentence that cannot be moved safely is refused (None)."""
import pytest

from niederschrift import indirect, topic


@pytest.fixture(scope="module")
def nlp():
    return topic.nlp()


@pytest.mark.parametrize("said, reported", [
    ("Der Mindestbetrag muss auf 440 Euro steigen.", "der Mindestbetrag müsse auf 440 Euro steigen."),
    ("Die Reform ist ein wichtiger Schritt.", "die Reform sei ein wichtiger Schritt."),
    ("Der Antrag hat viele Schwächen.", "der Antrag habe viele Schwächen."),
    ("Der Bund stellt fünf Milliarden Euro bereit.", "der Bund stelle fünf Milliarden Euro bereit."),
    ("Die Bundesregierung gibt den Kommunen mehr Geld.", "die Bundesregierung gebe den Kommunen mehr Geld."),
    ("Die Kommunen sind überlastet.", "die Kommunen seien überlastet."),
    ("Die Länder haben das Gesetz abgelehnt.", "die Länder hätten das Gesetz abgelehnt."),          # KI = indicative -> KII
    ("Die Beschäftigten können davon profitieren.", None),                                          # points back
    ("Das Gesetz wurde im Juli beschlossen.", None),                                                # "Das …" may point back
    ("Der Ausschuss hatte den Entwurf geändert.", "der Ausschuss habe den Entwurf geändert."),
    ("Deutschland braucht mehr Wohnungen, weil die Mieten in den Städten steigen.", None),          # the tagger misses "steigen"
    ("Deutschland braucht Wohnungen, die bezahlbar sind.", "Deutschland brauche Wohnungen, die bezahlbar seien."),
    ("Viele Familien brauchen Verlässlichkeit.", "viele Familien würden Verlässlichkeit brauchen."),
])
def test_sentences(nlp, said, reported):
    assert indirect.reported(nlp(said)) == reported


@pytest.mark.parametrize("said", [
    "Wir brauchen mehr Tarifbindung.", "Ich danke dem Ausschuss.", "Sie haben das Gesetz verhindert.",
    "Wann kommt die Reform?", "Deshalb steigt der Beitrag.", "Er kritisiert den Antrag.", "Und der Beitrag steigt.",
    "Der Minister sagte: „Es reicht.“", "Liebe Kolleginnen und Kollegen, der Antrag ist falsch.",
    # found by reading the output on development sittings:
    "Für Personen ohne Aufenthaltsrecht gibt es ein Rückführungssystem, das Rückkehrzentren auch in Drittstaaten ermöglicht.",
    "Nie wieder ein 7. Oktober 2023, aber auch nie wieder sollen Jüdinnen und Juden Angst haben müssen.",
    "Stattdessen 800 Millionen Euro für die Erweiterung des Kanzleramtes, das schon jetzt größer ist als das Weiße Haus.",
    "Selbst schuld, liebe Union, wer sich von diesen Parteien noch irgendwas sagen lässt.",
    "Es leistet jeden Tag einen wichtigen Beitrag.",
    "Der Preis stieg im letzten Jahr.", "Kommen wir zum Antrag.", "Der Beitrag – das wissen alle – steigt.",
])
def test_what_is_refused(nlp, said):
    assert indirect.reported(nlp(said)) is None


def sents(*texts):
    return [{"k": i, "text": t, "score": s} for i, (t, s) in enumerate(texts)]


def test_indirect_mode_names_the_speaker_once_and_marks_gaps(nlp):
    core = sents(("Der Antrag hat viele Schwächen.", 1), ("Wir lehnen ihn ab.", 1), ("Die Reform ist ein wichtiger Schritt.", 1))
    assert indirect.indirect_mode("Eva Muster", core, nlp) == \
        "Eva Muster erklärt, der Antrag habe viele Schwächen. […] Die Reform sei ein wichtiger Schritt."


def test_indirect_mode_is_left_out_when_nothing_can_be_moved(nlp):
    assert indirect.indirect_mode("Eva Muster", sents(("Wir lehnen den Antrag ab.", 1)), nlp) is None


def test_summary_takes_the_best_sentences_in_spoken_order_and_quotes_what_cannot_be_moved(nlp):
    core = sents(("Der Antrag hat viele Schwächen.", .5), ("Die Opposition redet über anderes.", .1),
                 ("Wir lehnen den Antrag heute ab.", .9), ("Die Reform ist ein wichtiger Schritt.", .8))
    assert indirect.summary_mode("Eva Muster", core, nlp, words_of_speech=300) == \
        "Eva Muster erklärt, der Antrag habe viele Schwächen. Wörtlich: „Wir lehnen den Antrag heute ab.“ Die Reform sei ein wichtiger Schritt."


def test_summary_is_left_out_for_short_speeches(nlp):
    assert indirect.summary_mode("Eva Muster", sents(("Der Antrag hat viele Schwächen.", 1)), nlp, words_of_speech=60) is None


def test_summary_does_not_say_the_same_thing_twice(nlp):
    core = sents(("Der Mindestbetrag muss auf 440 Euro steigen.", .9), ("Der Mindestbetrag muss schnell auf 440 Euro steigen.", .8),
                 ("Die Kommunen sind überlastet.", .3))
    s = indirect.summary_mode("Eva Muster", core, nlp, words_of_speech=300)
    assert s.count("Mindestbetrag") == 1 and "Kommunen" in s


def test_summary_skips_salutations_and_sentences_that_point_back(nlp):
    core = sents(("Meine Damen und Herren, der Antrag ist falsch.", .9), ("Das tut auch der Minister seit Jahren.", .9),
                 ("Der Antrag hat viele Schwächen.", .5), ("Wir lehnen die Kürzungen heute ab.", .4))
    assert indirect.summary_mode("Eva Muster", core, nlp, words_of_speech=300) == \
        "Eva Muster erklärt, der Antrag habe viele Schwächen. Wörtlich: „Wir lehnen die Kürzungen heute ab.“"


def test_summary_joins_neighbouring_quotations(nlp):
    core = sents(("Wir lehnen die Kürzungen heute ab.", .9), ("Ich fordere mehr Geld für Schulen.", .8))
    assert indirect.summary_mode("Eva Muster", core, nlp, words_of_speech=300) == \
        "Eva Muster erklärt: „Wir lehnen die Kürzungen heute ab. Ich fordere mehr Geld für Schulen.“"
