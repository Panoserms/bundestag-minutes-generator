from niederschrift import debate, kurz, reported_llm as r, topic

SRC = ["Sie von der AfD wollen doch nur Stimmung machen.", "Das haben wir 2019 schon einmal erlebt."]
LAB = "Anna Beispiel (SPD)"


def test_check_accepts_a_subjectless_present_tense_sentence():
    ok, problems = r.check("wirft der AfD vor, nur Stimmung machen zu wollen.", SRC, LAB)
    assert ok, problems


def test_check_rejects_invented_number():
    ok, problems = r.check("erinnert daran, dass man dies 2021 erlebt hat.", SRC, LAB)
    assert not ok and "number:2021" in problems


def test_check_rejects_too_long_and_two_sentences():
    long = "kritisiert die AfD. " + " ".join(["sehr"] * 40) + " lang."
    ok, problems = r.check(long, SRC, LAB)
    assert not ok and "length" in problems and "sentences" in problems


def test_check_rejects_subject_pronoun_and_quotes():
    ok, problems = r.check("Sie kritisiert die AfD.", SRC, LAB)
    assert not ok and "start" in problems
    ok, problems = r.check("sagt „Stimmung machen“.", SRC, LAB)
    assert not ok and "quote" in problems


def test_parse_reply_keeps_numbered_lines_only():
    text = "Hier die Zeilen:\n[0] kritisiert die AfD.\n[1] verweist auf 2019.\n[7] zu viel"
    assert r.parse_reply(text, 2) == {0: "kritisiert die AfD.", 1: "verweist auf 2019."}


def test_paragraph_continuation_is_joined():
    assert debate._continues("Die AfD ist eine demokratische Partei,", "die …")
    assert debate._continues("Das sehen wir anders", "Und zwar …")
    assert not debate._continues("Das sehen wir anders.", "Und zwar …")


def test_rule_kurzfassung_moves_the_verb_to_the_end():
    nlp = topic.nlp()
    assert kurz.to_verb_final(nlp("Der Mindestbetrag muss auf 440 Euro steigen."), "dass") == \
        "dass der Mindestbetrag auf 440 Euro steigen muss"
    assert kurz.to_verb_final(nlp("Wir brauchen mehr Tarifbindung."), "dass") is None     # whom "wir" means is unclear


def test_check_accepts_inflected_and_compound_forms_of_source_words():
    src = ["Der Mindestbetrag beim Elterngeld steigt nicht.", "Den Antrag der Grünen lehnen wir ab."]
    ok, problems = r.check("lehnt den Grünen-Antrag zum Elterngeld-Mindestbetrag ab.", src, LAB)
    assert ok, problems
    assert r._in_source("Mindestbetrags", " ".join(src).lower())
    assert not r._in_source("Bundesverfassungsgerichts", " ".join(src).lower())
