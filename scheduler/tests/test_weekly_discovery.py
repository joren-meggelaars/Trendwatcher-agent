from jobs.weekly_discovery import extract_search_terms


def test_extract_search_terms_ignores_stopwords_and_short_words():
    items = [
        {"title": "Kritieke kwetsbaarheid in Cisco routerfirmware ontdekt"},
        {"title": "Nieuwe kwetsbaarheid treft Cisco routerfirmware wereldwijd"},
        {"title": "Onderzoekers vinden kwetsbaarheid in Cisco apparatuur"},
    ]
    terms = extract_search_terms(items, top_n=3)
    assert "kwetsbaarheid" in terms
    assert "cisco" in terms
    assert "in" not in terms  # stopword
    assert "de" not in terms  # stopword


def test_extract_search_terms_returns_at_most_top_n():
    items = [{"title": "alpha bravo charlie delta echo foxtrot golf"}]
    terms = extract_search_terms(items, top_n=2)
    assert len(terms) <= 2


def test_extract_search_terms_handles_empty_input():
    assert extract_search_terms([], top_n=5) == []
