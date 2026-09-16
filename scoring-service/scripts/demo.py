"""Demonstrates the feedback loop against a running scoring-service instance.

Usage (with the server already running, e.g. `uv run uvicorn app.main:app`):

    uv run python scripts/demo.py

Steps:
  1. POST every article in data/testset.jsonl to /score and log the result.
  2. Mark two "netwerkapparatuur" articles as "interessant" via /feedback.
  3. Score fresh, not-yet-labeled articles from each cluster again and show
     that "netwerkapparatuur" articles now score higher than the others.
"""

import json
import sys
from pathlib import Path

import httpx

BASE_URL = "http://127.0.0.1:8000"
TESTSET_PATH = Path(__file__).resolve().parent.parent / "data" / "testset.jsonl"


def load_testset() -> list[dict]:
    with TESTSET_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def score(client: httpx.Client, article: dict) -> dict:
    payload = {k: article[k] for k in ("source", "title", "url", "raw_content")}
    resp = client.post(f"{BASE_URL}/score", json=payload)
    resp.raise_for_status()
    return resp.json()


def feedback(client: httpx.Client, item_id: int, label: str) -> None:
    resp = client.post(f"{BASE_URL}/feedback", json={"item_id": item_id, "label": label})
    resp.raise_for_status()


def main() -> None:
    articles = load_testset()

    with httpx.Client(timeout=30.0) as client:
        print("== Stap 1: alle testset-items scoren (voor feedback) ==")
        baseline_by_cluster: dict[str, list[float]] = {}
        for article in articles:
            result = score(client, article)
            cluster = article["cluster"]
            baseline_by_cluster.setdefault(cluster, []).append(result["relevance_score"])
            print(
                f"[{cluster}] item_id={result['item_id']} score={result['relevance_score']:.3f} "
                f"summary={result['summary'][:60]!r}"
            )

        print("\n== Stap 2: markeer twee 'netwerkapparatuur' items als interessant ==")
        network_articles = [a for a in articles if a["cluster"] == "netwerkapparatuur"]
        for article in network_articles[:2]:
            result = score(client, article)  # re-score to get an item_id to give feedback on
            feedback(client, result["item_id"], "interessant")
            print(f"-> feedback 'interessant' gegeven op item_id={result['item_id']} ({article['title']})")

        print("\n== Stap 3: her-scoor niet-gelabelde items per cluster ==")
        rescored_by_cluster: dict[str, list[float]] = {}
        for article in articles:
            result = score(client, article)
            cluster = article["cluster"]
            rescored_by_cluster.setdefault(cluster, []).append(result["relevance_score"])

        print("\n== Resultaat: gemiddelde relevance_score per cluster ==")
        avg = {c: sum(v) / len(v) for c, v in rescored_by_cluster.items()}
        for cluster, mean_score in sorted(avg.items(), key=lambda x: -x[1]):
            print(f"{cluster:20s} gemiddelde score na feedback: {mean_score:.3f}")

        network_avg = avg["netwerkapparatuur"]
        other_avg = max(v for c, v in avg.items() if c != "netwerkapparatuur")
        if network_avg > other_avg:
            print(
                f"\nPASS: 'netwerkapparatuur' scoort na feedback hoger ({network_avg:.3f}) "
                f"dan de best scorende andere cluster ({other_avg:.3f})."
            )
        else:
            print(
                f"\nFAIL: 'netwerkapparatuur' ({network_avg:.3f}) scoort niet hoger dan "
                f"andere clusters ({other_avg:.3f})."
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
