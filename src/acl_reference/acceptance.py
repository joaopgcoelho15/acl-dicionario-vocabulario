from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def default_cases_path() -> Path:
    return Path(__file__).resolve().parents[2] / "acceptance" / "phase1-cases.json"


def run_search_acceptance(
    base_url: str, cases_path: str | Path | None = None
) -> dict:
    specification = json.loads(
        Path(cases_path or default_cases_path()).read_text(encoding="utf-8")
    )
    results = []
    for case in specification["search_cases"]:
        params = {"q": case["query"], "limit": 100, "offset": 0}
        if case.get("resource"):
            params["resource"] = case["resource"]
        with urlopen(
            base_url.rstrip("/") + "/api/v1/search?" + urlencode(params), timeout=30
        ) as response:
            payload = json.load(response)
        hits = [
            hit
            for group in payload.get("results", [])
            for hit in group.get("hits", [])
        ]
        lemmas = [str(item.get("lemma") or "") for item in hits]
        failures = []
        if case.get("expected_lemma") and case["expected_lemma"] not in lemmas:
            failures.append("expected_lemma")
        if case.get("expected_lemma_prefix") and not any(
            lemma.startswith(case["expected_lemma_prefix"]) for lemma in lemmas
        ):
            failures.append("expected_lemma_prefix")
        if len(hits) < int(case.get("minimum_hits", 0)):
            failures.append("minimum_hits")
        if "maximum_hits" in case and len(hits) > int(case["maximum_hits"]):
            failures.append("maximum_hits")
        if case.get("expected_domain") and not any(
            case["expected_domain"] in (item.get("domains") or []) for item in hits
        ):
            failures.append("expected_domain")
        results.append(
            {
                "id": case["id"],
                "description": case["description"],
                "passed": not failures,
                "failures": failures,
                "hit_count": len(hits),
                "first_lemmas": lemmas[:10],
            }
        )
    return {
        "version": specification["version"],
        "approval_status": specification["approval_status"],
        "passed": all(item["passed"] for item in results),
        "results": results,
    }
