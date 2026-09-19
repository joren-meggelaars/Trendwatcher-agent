from app.embeddings import FakeEmbeddingProvider
from uuid import uuid4

NETWORK_A = (
    "Critical vulnerability discovered in Cisco router firmware allows remote "
    "code execution on enterprise network devices."
)
NETWORK_B = (
    "Cisco router firmware vulnerability lets attackers execute remote code "
    "on network devices without authentication."
)
PHISHING = (
    "New phishing campaign targets bank customers via fake login emails "
    "asking them to confirm their password."
)


def _score(client, raw_content: str, title: str = "T"):
    resp = client.post(
        "/score",
        json={"source": "test", "title": title, "url": f"http://example.com/{uuid4().hex}", "raw_content": raw_content},
    )
    assert resp.status_code == 200
    return resp.json()


def test_score_returns_item_id_summary_and_neutral_cold_start_score(client):
    body = _score(client, NETWORK_A)
    assert isinstance(body["item_id"], int)
    assert body["summary"]
    # No feedback exists yet -> neutral score, not a crash and not 0.
    assert body["relevance_score"] == 0.5


def test_similar_articles_have_higher_cosine_similarity_than_unrelated(client):
    provider = FakeEmbeddingProvider(dim=1024)
    emb_a = provider.embed(NETWORK_A)
    emb_b = provider.embed(NETWORK_B)
    emb_c = provider.embed(PHISHING)

    from app.scoring import cosine_similarity

    sim_related = cosine_similarity(emb_a, emb_b)
    sim_unrelated = cosine_similarity(emb_a, emb_c)

    assert sim_related > sim_unrelated


def test_feedback_raises_relevance_of_similar_unlabeled_items(client):
    scored_a = _score(client, NETWORK_A, title="A")
    scored_c = _score(client, PHISHING, title="C")

    client.post("/feedback", json={"item_id": scored_a["item_id"], "label": "interessant"})

    scored_b = _score(client, NETWORK_B, title="B")
    scored_c2 = _score(client, PHISHING, title="C2")

    assert scored_b["relevance_score"] > scored_c2["relevance_score"]
    assert scored_c["relevance_score"] == 0.5  # baseline, scored before any feedback existed
