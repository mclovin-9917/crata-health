from app.pipeline.quotes import normalize, quote_in_source

SOURCE = "Okay, then no amoxicillin. I will give you Azithromycin 500mg, once a day for 3 days."


def test_ignores_case_spacing_and_punctuation():
    assert quote_in_source("azithromycin 500 mg once a day", SOURCE)
    assert quote_in_source("  AZITHROMYCIN   500MG,  ", SOURCE)


def test_rejects_words_not_in_source():
    assert not quote_in_source("azithromycin 250 mg", SOURCE)
    assert not quote_in_source("twice a day", SOURCE)


def test_matches_whole_words_only():
    assert not quote_in_source("mycin 500", SOURCE)  # part of a word is not a quote
    assert not quote_in_source("3 day", SOURCE)


def test_empty_quote_or_source_fails():
    assert not quote_in_source("", SOURCE)
    assert not quote_in_source("...", SOURCE)
    assert not quote_in_source("amoxicillin", None)


def test_hindi_keeps_vowel_signs():
    hindi = "मुझे पेनिसिलिन से एलर्जी है।"
    assert quote_in_source("पेनिसिलिन से एलर्जी", hindi)
    assert not quote_in_source("पनसलन से एलरज", hindi)  # vowel signs stripped: a different text
    assert normalize("एलर्जी है।") == "एलर्जी है"
