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


# --- a search result is fetched through safe_fetch --------------------------------------------------------


def test_a_search_result_pointing_inside_is_not_fetched_and_falls_back_to_the_snippet(monkeypatch):
    import httpx
    import rate_limit
    import safe_fetch
    from jobs import weekly_discovery
    from search_provider import SearchResult

    def refuse(url, **kwargs):
        raise safe_fetch.UnsafeURL("het adres wijst naar een intern of niet-openbaar netwerk")

    posted = {}

    class _Client:
        def post(self, url, json):
            posted.update(json)
            return httpx.Response(200, json={"id": 1}, request=httpx.Request("POST", url))

    monkeypatch.setattr(weekly_discovery.safe_fetch, "fetch", refuse)
    monkeypatch.setattr(rate_limit, "wait_for_scoring_slot", lambda: None)
    result = SearchResult(title="Interne pagina", url="http://10.0.100.8:8080/admin", snippet="alleen de snippet")

    weekly_discovery.score_search_result(_Client(), result, {"id": 7, "url": "10.0.100.8"})

    assert posted["raw_content"] == "alleen de snippet" and posted["source_id"] == 7
