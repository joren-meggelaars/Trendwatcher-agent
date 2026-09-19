from uuid import uuid4

# Market items contain a market pattern ("acquires"); news items deliberately
# share most of their vocabulary with them but contain none. That way a
# category-blind scorer would let the market 👍 lift the news items too.
MARKET_LIKED = (
    "Acme acquires Globex",
    "Cisco router firmware vendor Acme acquires Globex network security firm",
)
MARKET_SIMILAR = (
    "Initech acquires Hooli",
    "Cisco router firmware vendor Initech acquires Hooli network security firm",
)
NEWS_SIMILAR_VOCABULARY = (
    "Cisco router firmware flaw",
    "Cisco router firmware vendor network security firm flaw",
)
NEWS_LIKED = (
    "Junos firewall zero-day exploited",
    "Juniper Junos firewall zero-day actively exploited remote attackers",
)
NEWS_SIMILAR = (
    "Junos firewall flaw exploited",
    "Juniper Junos firewall zero-day exploited by remote attackers",
)


def _score(client, article: tuple[str, str]) -> dict:
    title, content = article
    resp = client.post(
        "/score",
        json={
            "source": "test",
            "title": title,
            "url": f"https://example.com/{uuid4().hex}",
            "raw_content": content,
        },
    )
    assert resp.status_code == 200
    return resp.json()


def _thumb(client, item_id: int, label: str) -> None:
    assert client.post("/feedback", json={"item_id": item_id, "label": label}).status_code == 200


def test_thumbs_up_on_market_item_lifts_similar_market_items_only(client):
    liked = _score(client, MARKET_LIKED)
    assert liked["category"] == "markt"
    _thumb(client, liked["item_id"], "interessant")

    market = _score(client, MARKET_SIMILAR)
    news = _score(client, NEWS_SIMILAR_VOCABULARY)

    assert market["category"] == "markt" and news["category"] == "nieuws"
    assert market["relevance_score"] > 0.6
    # Same vocabulary, but there is no news feedback yet -> stays neutral.
    assert news["relevance_score"] == 0.5


def test_thumbs_up_on_news_item_does_not_lift_market_items(client):
    liked = _score(client, NEWS_LIKED)
    assert liked["category"] == "nieuws"
    _thumb(client, liked["item_id"], "interessant")

    assert _score(client, NEWS_SIMILAR)["relevance_score"] > 0.6
    assert _score(client, MARKET_SIMILAR)["relevance_score"] == 0.5


def test_thumbs_down_lowers_similar_items_in_the_same_category_only(client):
    disliked = _score(client, NEWS_LIKED)
    _thumb(client, disliked["item_id"], "niet_interessant")

    assert _score(client, NEWS_SIMILAR)["relevance_score"] < 0.4
    assert _score(client, MARKET_SIMILAR)["relevance_score"] == 0.5


def test_thumbs_down_pulls_a_liked_neighbourhood_back_down(client):
    liked = _score(client, NEWS_LIKED)
    _thumb(client, liked["item_id"], "interessant")
    baseline = _score(client, NEWS_SIMILAR)["relevance_score"]

    disliked = _score(client, (NEWS_LIKED[0] + " again", NEWS_LIKED[1] + " again"))
    _thumb(client, disliked["item_id"], "niet_interessant")

    # scored again as a different article: the same title from the same source
    # would (rightly) come back as the item that is already stored
    again = (NEWS_SIMILAR[0] + " again", NEWS_SIMILAR[1])
    assert _score(client, again)["relevance_score"] < baseline


def test_no_feedback_at_all_scores_neutral(client):
    assert _score(client, MARKET_SIMILAR)["relevance_score"] == 0.5
    assert _score(client, NEWS_SIMILAR)["relevance_score"] == 0.5
