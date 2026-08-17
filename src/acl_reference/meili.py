from __future__ import annotations

import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class MeiliError(RuntimeError):
    pass


class MeiliClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def health(self) -> dict:
        return self.request("GET", "/health")

    def index_release(self, release_path: str | Path) -> dict:
        """Compatibilidade: constrói e ativa os índices numa só chamada."""
        prepared = self.build_release_indexes(release_path)
        activated = self.activate_release_indexes(release_path)
        return {**prepared, **activated}

    def build_release_indexes(self, release_path: str | Path) -> dict:
        """Constrói índices versionados sem alterar os índices públicos."""
        root = Path(release_path)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if manifest["state"] not in {"validated", "indexed", "tested", "active"}:
            raise MeiliError("A release ainda não está validada.")
        tasks = []
        for resource in ("dictionary", "vocabulary"):
            temporary = manifest["indexes"][resource]
            self._delete_index_if_exists(temporary)
            task = self.request(
                "POST",
                "/indexes",
                {"uid": temporary, "primaryKey": "id"},
            )
            self.wait_task(task["taskUid"])
            settings = json.loads(
                (root / f"{resource}-settings.json").read_text(encoding="utf-8")
            )
            tasks.append(
                self.request("PATCH", f"/indexes/{temporary}/settings", settings)[
                    "taskUid"
                ]
            )
            for batch in _read_ndjson_batches(
                root / f"{resource}.ndjson", batch_size=5000
            ):
                tasks.append(
                    self.request(
                        "POST",
                        f"/indexes/{temporary}/documents?primaryKey=id",
                        batch,
                    )["taskUid"]
                )
        for uid in tasks:
            self.wait_task(uid)
        return {
            "release_id": manifest["release_id"],
            "indexes": manifest["indexes"],
        }

    def activate_release_indexes(self, release_path: str | Path) -> dict:
        """Troca em conjunto os dois índices públicos pelos já testados."""
        root = Path(release_path)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        pairs = []
        for resource in ("dictionary", "vocabulary"):
            self._ensure_index(resource)
            pairs.append({"indexes": [resource, manifest["indexes"][resource]]})
        swap = self.request("POST", "/swap-indexes", pairs)
        self.wait_task(swap["taskUid"])
        return {
            "release_id": manifest["release_id"],
            "indexes": ["dictionary", "vocabulary"],
            "task_uid": swap["taskUid"],
        }

    def search(
        self,
        query: str,
        *,
        resource: str | None = None,
        limit: int = 20,
        offset: int = 0,
        filter_expression: str | None = None,
    ) -> dict:
        indexes = [resource] if resource else ["dictionary", "vocabulary"]
        queries = []
        for index in indexes:
            item = {
                "indexUid": index,
                "q": query,
                "limit": limit,
                "offset": offset,
                # A tolerância a gralhas é configurada no índice; esta
                # estratégia permite ainda relaxar o último termo da pesquisa.
                "matchingStrategy": "last",
                "facets": ["resource", "grammatical_categories", "domains", "status"],
                "attributesToHighlight": ["lemma", "definitions_text"],
            }
            if filter_expression:
                item["filter"] = filter_expression
            queries.append(item)
        return self.request("POST", "/multi-search", {"queries": queries})

    def search_index(self, index: str, body: dict) -> dict:
        return self.request("POST", f"/indexes/{index}/search", body)

    def get_entry(self, resource: str, entry_id: str) -> dict:
        try:
            return self.request("GET", f"/indexes/{resource}/documents/{entry_id}")
        except MeiliError as exc:
            if "404" not in str(exc):
                raise
        escaped = entry_id.replace("\\", "\\\\").replace('"', '\\"')
        result = self.request(
            "POST",
            f"/indexes/{resource}/search",
            {
                "q": "",
                "filter": f'source_id = "{escaped}"',
                "limit": 2,
            },
        )
        hits = result.get("hits", [])
        if len(hits) != 1:
            raise MeiliError(
                f"Não foi encontrada uma entrada única para o ID {entry_id!r}."
            )
        return hits[0]

    def wait_task(self, uid: int, timeout: float = 300) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.request("GET", f"/tasks/{uid}")
            if task["status"] == "succeeded":
                return task
            if task["status"] in {"failed", "canceled"}:
                raise MeiliError(json.dumps(task, ensure_ascii=False))
            time.sleep(0.2)
        raise TimeoutError(f"Tarefa Meilisearch {uid} excedeu {timeout}s.")

    def request(self, method: str, path: str, body=None):
        data = (
            json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None
            else None
        )
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=60) as response:
                content = response.read()
                return json.loads(content) if content else {}
        except HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise MeiliError(f"{method} {path}: {exc.code} {message}") from exc

    def _ensure_index(self, uid: str) -> None:
        try:
            self.request("GET", f"/indexes/{uid}")
        except MeiliError as exc:
            if "404" not in str(exc):
                raise
            task = self.request(
                "POST", "/indexes", {"uid": uid, "primaryKey": "id"}
            )
            self.wait_task(task["taskUid"])

    def _delete_index_if_exists(self, uid: str) -> None:
        try:
            self.request("GET", f"/indexes/{uid}")
        except MeiliError as exc:
            if "404" in str(exc):
                return
            raise
        task = self.request("DELETE", f"/indexes/{uid}")
        self.wait_task(task["taskUid"])


def _read_ndjson_batches(path: Path, batch_size: int):
    batch = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                batch.append(json.loads(line))
            if len(batch) == batch_size:
                yield batch
                batch = []
    if batch:
        yield batch
