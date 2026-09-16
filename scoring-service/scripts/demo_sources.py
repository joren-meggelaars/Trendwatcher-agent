"""Demonstrates the source instroom/krimp lifecycle against a running scoring-service.

Usage (with the server already running, e.g. `uv run uvicorn app.main:app --reload`):

    uv run python scripts/demo_sources.py

Steps:
  1. Score a link-carrying item and show a new candidate Source got registered
     automatically (link_following), without the /score contract changing.
  2. Manually register a Source via POST /sources (status "kandidaat").
  3. Instroom: score 5 "instroom" items from data/discovery_testset.jsonl against
     that source and show evaluate_source promotes it to "actief".
  4. Krimp: bring a second source to "actief" the same way, then feed it 10
     irrelevant items, mark 8 "niet_interessant", and show evaluate_source
     demotes it to "gedeactiveerd".
"""

import json
import sys
from pathlib import Path

import httpx

BASE_URL = "http://127.0.0.1:8000"
TESTSET_PATH = Path(__file__).resolve().parent.parent / "data" / "discovery_testset.jsonl"

SEED_LIKED_CONTENT = (
    "Kritieke kwetsbaarheid in populaire routerfirmware maakt remote code "
    "execution op netwerkapparatuur mogelijk voor ongeauthenticeerde aanvallers."
)


def load_testset() -> list[dict]:
    with TESTSET_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def score(client: httpx.Client, article: dict, source_id: int | None = None) -> dict:
    payload = {
        "source": "discovery-demo",
        "title": article["title"],
        "url": article["url"],
        "raw_content": article["raw_content"],
    }
    if source_id is not None:
        payload["source_id"] = source_id
    resp = client.post(f"{BASE_URL}/score", json=payload)
    resp.raise_for_status()
    return resp.json()


def feedback(client: httpx.Client, item_id: int, label: str) -> None:
    resp = client.post(f"{BASE_URL}/feedback", json={"item_id": item_id, "label": label})
    resp.raise_for_status()


def create_source(client: httpx.Client, url: str, type_: str) -> dict:
    resp = client.post(f"{BASE_URL}/sources", json={"url": url, "type": type_})
    resp.raise_for_status()
    return resp.json()


def evaluate_source(client: httpx.Client, source_id: int) -> dict:
    resp = client.post(f"{BASE_URL}/sources/{source_id}/evaluate")
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    articles = load_testset()
    failures: list[str] = []

    with httpx.Client(timeout=30.0) as client:
        print("== Seed: markeer een item als 'interessant' zodat relevance_score kan uitschieten ==")
        seed = client.post(
            f"{BASE_URL}/score",
            json={
                "source": "discovery-demo",
                "title": "seed",
                "url": "https://example.com/seed",
                "raw_content": SEED_LIKED_CONTENT,
            },
        ).json()
        feedback(client, seed["item_id"], "interessant")

        print("\n== Stap 1: link-extractie registreert automatisch een kandidaat-bron ==")
        result = score(
            client,
            {
                "title": "Artikel met link naar onbekend domein",
                "url": "https://example.com/link-demo",
                "raw_content": "Zie ook https://ontdekt-tijdens-demo.example/analyse voor meer details.",
            },
        )
        assert set(result.keys()) == {"item_id", "summary", "relevance_score"}, (
            "link-discovery mag de /score-response niet veranderen"
        )
        candidates = client.get(f"{BASE_URL}/sources", params={"status": "kandidaat"}).json()
        discovered = [s for s in candidates if s["url"] == "ontdekt-tijdens-demo.example"]
        if discovered and discovered[0]["discovery_method"] == "link_following":
            print(f"PASS: kandidaat-bron automatisch geregistreerd: {discovered[0]}")
        else:
            failures.append("link-following registratie niet gevonden")
            print("FAIL: geen kandidaat-bron geregistreerd voor het onbekende domein")

        print("\n== Stap 2: instroom-scenario ==")
        instroom_items = [a for a in articles if a["scenario"] == "instroom"]
        source = create_source(client, instroom_items[0]["source_url"], instroom_items[0]["source_type"])
        print(f"Nieuwe bron aangemaakt: {source}")
        scored = [score(client, a, source_id=source["id"]) for a in instroom_items]
        for a, r in zip(instroom_items, scored):
            print(f"  score={r['relevance_score']:.3f}  {a['title'][:60]!r}")
        evaluated = evaluate_source(client, source["id"])
        if evaluated["status"] == "actief":
            print(f"PASS: bron {source['url']} is 'actief' na instroom-evaluatie.")
        else:
            failures.append(f"instroom: bron bleef status={evaluated['status']!r}")
            print(f"FAIL: bron bleef status={evaluated['status']!r} in plaats van 'actief'")

        print("\n== Stap 3: krimp-scenario ==")
        activation_items = [a for a in articles if a["scenario"] == "krimp_activation"]
        noise_items = [a for a in articles if a["scenario"] == "krimp_noise"]
        noisy_source = create_source(
            client, activation_items[0]["source_url"], activation_items[0]["source_type"]
        )
        print(f"Nieuwe bron aangemaakt: {noisy_source}")
        for a in activation_items:
            score(client, a, source_id=noisy_source["id"])
        activated = evaluate_source(client, noisy_source["id"])
        print(f"  status na activatie-batch: {activated['status']}")

        noisy_scored = [score(client, a, source_id=noisy_source["id"]) for a in noise_items]
        for item in noisy_scored[:8]:
            feedback(client, item["item_id"], "niet_interessant")
        print("  8 van de 10 items gemarkeerd als 'niet_interessant'")

        shrunk = evaluate_source(client, noisy_source["id"])
        if shrunk["status"] == "gedeactiveerd":
            print(f"PASS: bron {noisy_source['url']} is 'gedeactiveerd' na krimp-evaluatie.")
        else:
            failures.append(f"krimp: bron bleef status={shrunk['status']!r}")
            print(f"FAIL: bron bleef status={shrunk['status']!r} in plaats van 'gedeactiveerd'")

    if failures:
        print(f"\n{len(failures)} scenario(s) gefaald: {failures}")
        sys.exit(1)
    print("\nAlle discovery-scenario's geslaagd.")


if __name__ == "__main__":
    main()
