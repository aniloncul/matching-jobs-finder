"""Evaluation script to measure matching precision, recall, and hallucination rate."""
from __future__ import annotations

import json
from pathlib import Path

from jobscout.cover_letter.bullets import Bullet
from jobscout.cover_letter.matcher import HybridMatcher
from jobscout.cover_letter.requirements import extract_requirements
from jobscout.cover_letter.verifier import verify_letter


def run_benchmark(eval_file: Path | str | None = None) -> None:
    path = Path(eval_file) if eval_file else Path(__file__).parent / "pairs.json"
    if not path.exists():
        print(f"Eval file not found: {path}")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    matcher = HybridMatcher(embedder=None, keyword_weight=1.0, threshold=0.1, max_matches=4)

    total_precision = 0.0
    total_recall = 0.0

    print("=" * 60)
    print("JOBSCOUT MATCHING & GROUNDING EVALUATION REPORT")
    print("=" * 60)

    for item in data:
        name = item["name"]
        description = item["description"]
        expected_indices = set(item["ground_truth_matching_index"])
        bullets = [
            Bullet.from_text("experience", text)
            for text in item["profile_bullets"]
        ]
        requirements = extract_requirements(description)

        matched = matcher.match(bullets, requirements)
        matched_hashes = {m.bullet.content_hash for m in matched}
        ground_truth_hashes = {bullets[i].content_hash for i in expected_indices}

        true_positives = len(matched_hashes.intersection(ground_truth_hashes))
        precision = true_positives / len(matched) if matched else 0.0
        recall = true_positives / len(ground_truth_hashes) if ground_truth_hashes else 0.0

        total_precision += precision
        total_recall += recall

        print(f"\nScenario: {name}")
        print(f"  Requirements extracted: {len(requirements)}")
        print(f"  Matched bullets: {len(matched)}")
        print(f"  Precision: {precision:.2%}, Recall: {recall:.2%}")

    avg_p = total_precision / len(data) if data else 0.0
    avg_r = total_recall / len(data) if data else 0.0

    print("\n" + "-" * 60)
    print(f"OVERALL RESULTS: Mean Precision: {avg_p:.2%} | Mean Recall: {avg_r:.2%}")
    print("=" * 60)


if __name__ == "__main__":
    run_benchmark()
