from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .meili import MeiliClient
from .publication import current_release, verify_release


@dataclass
class SearchService:
    client: MeiliClient

    def search(
        self,
        query: str,
        *,
        resource: str | None = None,
        limit: int = 20,
        offset: int = 0,
        filters: list[str] | None = None,
    ) -> dict:
        if resource not in {None, "dictionary", "vocabulary"}:
            raise ValueError("Recurso inválido.")
        return self.client.search(
            query,
            resource=resource,
            limit=max(1, min(limit, 100)),
            offset=max(0, offset),
            filter_expression=" AND ".join(filters or []) or None,
        )

    def suggest(
        self, query: str, *, resource: str | None = None, limit: int = 10
    ) -> list[dict]:
        result = self.search(query, resource=resource, limit=limit)
        suggestions = []
        for group in result.get("results", []):
            for hit in group.get("hits", []):
                suggestions.append(
                    {
                        "id": hit.get("id"),
                        "resource": hit.get("resource"),
                        "lemma": hit.get("lemma"),
                    }
                )
        return suggestions[:limit]


@dataclass
class EntryRepository:
    client: MeiliClient

    def get_entry(self, resource: str, entry_id: str) -> dict:
        if resource not in {"dictionary", "vocabulary"}:
            raise ValueError("Recurso inválido.")
        return self.client.get_entry(resource, entry_id)

    def get_relations(self, resource: str, entry_id: str) -> list[dict]:
        return self.get_entry(resource, entry_id).get("relations", [])


@dataclass
class ReleaseService:
    releases_root: Path
    _cached_release_id: str | None = None
    _cached_manifest: dict | None = None

    def current(self, *, verify: bool = False) -> dict:
        release_id = current_release(self.releases_root)
        if not release_id:
            return {"available": False}
        release_path = self.releases_root / release_id
        if release_id != self._cached_release_id or self._cached_manifest is None:
            self._cached_manifest = json.loads(
                (release_path / "manifest.json").read_text(encoding="utf-8")
            )
            self._cached_release_id = release_id
        result = {
            "available": True,
            "release_id": release_id,
            "manifest": self._cached_manifest,
        }
        if verify:
            result["verification"] = verify_release(release_path)
        return result

    def asset_path(self, relative_path: str) -> Path | None:
        release_id = current_release(self.releases_root)
        if not release_id:
            return None
        root = (self.releases_root / release_id / "images").resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None
