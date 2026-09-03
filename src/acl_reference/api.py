from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import time
from urllib.parse import parse_qs, unquote, urlparse

from .meili import MeiliClient, MeiliError
from .public_compat import PublicCompatibilityService
from .services import EntryRepository, ReleaseService, SearchService
from .usage_log import UsageLog


def serve(
    *,
    meili_url: str,
    meili_key: str,
    releases_root: str | Path,
    host: str = "127.0.0.1",
    port: int = 8090,
    base_path: str = "",
    usage_db: str | Path | None = None,
) -> None:
    client = MeiliClient(meili_url, meili_key)
    release_service = ReleaseService(Path(releases_root))
    usage_log = UsageLog(
        Path(usage_db) if usage_db else Path(releases_root).parent / "usage-logs"
    )
    handler = type(
        "ACLReferenceHandler",
        (_Handler,),
        {
            "search_service": SearchService(client),
            "entry_repository": EntryRepository(client),
            "release_service": release_service,
            "compatibility": PublicCompatibilityService(
                client, release_service
            ),
            "usage_log": usage_log,
            "base_path": _normalise_base_path(base_path),
            "web_root": Path(__file__).resolve().parents[2] / "public_app" / "web",
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Aplicação pública local: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class _Handler(BaseHTTPRequestHandler):
    search_service: SearchService
    entry_repository: EntryRepository
    release_service: ReleaseService
    compatibility: PublicCompatibilityService
    web_root: Path
    usage_log: UsageLog
    base_path: str

    def handle_one_request(self) -> None:
        started = time.perf_counter()
        self._response_status = 0
        super().handle_one_request()
        if getattr(self, "command", None):
            self.usage_log.record(
                client_ip=self.client_address[0],
                method=self.command,
                raw_path=self.path,
                status_code=self._response_status or 0,
                duration_ms=(time.perf_counter() - started) * 1000,
                user_agent=self.headers.get("User-Agent"),
                referrer=self.headers.get("Referer"),
            )

    def send_response(self, code, message=None):
        self._response_status = int(code)
        return super().send_response(code, message)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/dicionario", "/vocabulario"} or parsed.path.startswith("/id/"):
                self._file("index.html", "text/html; charset=utf-8")
            elif parsed.path == "/estatisticas":
                self._file("stats.html", "text/html; charset=utf-8")
            elif parsed.path == "/dados":
                self._file("data.html", "text/html; charset=utf-8")
            elif parsed.path in {"/assets/app.js", "/assets/app-v2.js"}:
                self._file("assets/app.js", "text/javascript; charset=utf-8")
            elif parsed.path in {"/assets/styles.css", "/assets/styles-v2.css"}:
                self._file("assets/styles.css", "text/css; charset=utf-8")
            elif parsed.path == "/assets/stats.js":
                self._file("assets/stats.js", "text/javascript; charset=utf-8")
            elif parsed.path == "/assets/acl-logo.png":
                self._file("assets/acl-logo.png", "image/png", cache=True)
            elif parsed.path in {
                "/assets/readability-muito-facil.png",
                "/assets/readability-facil.png",
                "/assets/readability-claro.png",
            }:
                self._file(
                    f"assets/{parsed.path.rsplit('/', 1)[-1]}",
                    "image/png",
                    cache=True,
                )
            elif parsed.path == "/health":
                current = self.release_service.current(verify=False)
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "search": self.search_service.client.health(),
                        "release": {
                            "available": current.get("available", False),
                            "release_id": current.get("release_id"),
                            "counts": current.get("manifest", {}).get(
                                "counts", {}
                            ),
                        },
                    },
                )
            elif parsed.path == "/api/v1/search":
                self._search(parse_qs(parsed.query))
            elif parsed.path == "/api/v1/suggest":
                self._suggest(parse_qs(parsed.query))
            elif parsed.path == "/api/v1/releases/current":
                self._json(HTTPStatus.OK, self.release_service.current())
            elif parsed.path == "/api/entry-counts":
                self._json(
                    HTTPStatus.OK, self.compatibility.entry_counts()
                )
            elif parsed.path == "/api/facets":
                self._json(
                    HTTPStatus.OK, self.compatibility.global_facets()
                )
            elif parsed.path == "/api/entries":
                self._legacy_search(parse_qs(parsed.query))
            elif parsed.path == "/api/catalogue":
                self._legacy_catalogue(parse_qs(parsed.query))
            elif parsed.path == "/api/dashboard":
                self._json(
                    HTTPStatus.OK,
                    self._dashboard(parse_qs(parsed.query)),
                )
            elif parsed.path.startswith("/api/entries/") and parsed.path.endswith("/source"):
                public_id = unquote(
                    parsed.path.removeprefix("/api/entries/").removesuffix("/source")
                )
                source = self.compatibility.debug_entry(public_id)
                self._json(
                    HTTPStatus.OK,
                    {"public_id": public_id, "raw_xml": source.get("raw_xml", "")},
                )
            elif parsed.path.startswith("/api/resolve/"):
                resolution = self.compatibility.resolve(
                    unquote(parsed.path.removeprefix("/api/resolve/"))
                )
                for match in resolution.get("matches", []):
                    match.pop("_source_xml", None)
                if not resolution["matches"]:
                    self._json(
                        HTTPStatus.NOT_FOUND, resolution
                    )
                else:
                    self._json(HTTPStatus.OK, resolution)
            elif parsed.path.startswith("/api/entries/"):
                entry = self.compatibility.get_entry(
                    unquote(parsed.path.removeprefix("/api/entries/"))
                )
                entry.pop("_source_xml", None)
                self._json(HTTPStatus.OK, entry)
            elif parsed.path.startswith("/release-assets/images/"):
                self._image(
                    unquote(
                        parsed.path.removeprefix("/release-assets/images/")
                    )
                )
            elif parsed.path.startswith("/entry-images/"):
                self._image(
                    unquote(parsed.path.removeprefix("/entry-images/"))
                )
            elif parsed.path.startswith("/api/v1/dictionary/entries/"):
                self._entry(
                    "dictionary",
                    unquote(parsed.path.removeprefix("/api/v1/dictionary/entries/")),
                )
            elif parsed.path.startswith("/api/v1/vocabulary/entries/"):
                self._entry(
                    "vocabulary",
                    unquote(parsed.path.removeprefix("/api/v1/vocabulary/entries/")),
                )
            elif parsed.path.startswith("/api/v1/entries/") and parsed.path.endswith(
                "/relations"
            ):
                entry_id = unquote(
                    parsed.path.removeprefix("/api/v1/entries/").removesuffix(
                        "/relations"
                    )
                )
                resource = parse_qs(parsed.query).get("resource", ["dictionary"])[0]
                self._json(
                    HTTPStatus.OK,
                    {
                        "items": self.entry_repository.get_relations(
                            resource, entry_id
                        )
                    },
                )
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except (ValueError, MeiliError) as exc:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": type(exc).__name__, "message": str(exc)},
            )
        except FileNotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _search(self, query: dict[str, list[str]]) -> None:
        term = query.get("q", [""])[0]
        resource = query.get("resource", [None])[0] or None
        limit = int(query.get("limit", ["20"])[0])
        offset = int(query.get("offset", ["0"])[0])
        filters = []
        for key in ("status", "domain", "grammar"):
            value = query.get(key, [""])[0]
            if not value:
                continue
            attribute = {
                "status": "status",
                "domain": "domains",
                "grammar": "grammatical_categories",
            }[key]
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            filters.append(f'{attribute} = "{escaped}"')
        result = self.search_service.search(
            term,
            resource=resource,
            limit=limit,
            offset=offset,
            filters=filters,
        )
        self._json(HTTPStatus.OK, result)

    def _suggest(self, query: dict[str, list[str]]) -> None:
        self._json(
            HTTPStatus.OK,
            {
                "items": self.search_service.suggest(
                    query.get("q", [""])[0],
                    resource=query.get("resource", [None])[0] or None,
                    limit=int(query.get("limit", ["10"])[0]),
                )
            },
        )

    def _legacy_search(self, query: dict[str, list[str]]) -> None:
        self._json(
            HTTPStatus.OK,
            self.compatibility.search(
                query=query.get("query", [""])[0],
                collection=query.get("collection", [None])[0],
                grammar=query.get("grammar", [None])[0],
                domain=query.get("domain", [None])[0],
                status=query.get("status", [None])[0],
                limit=max(
                    1, min(int(query.get("limit", ["30"])[0]), 100)
                ),
                offset=max(0, int(query.get("offset", ["0"])[0])),
            ),
        )

    def _legacy_catalogue(self, query: dict[str, list[str]]) -> None:
        self._json(
            HTTPStatus.OK,
            self.compatibility.catalogue(
                collection=query.get("collection", [None])[0],
                grammar=query.get("grammar", [None])[0],
                domain=query.get("domain", [None])[0],
                status=query.get("status", [None])[0],
                letter=query.get("letter", [None])[0],
                limit=max(
                    1, min(int(query.get("limit", ["60"])[0]), 100)
                ),
                cursor=query.get("cursor", [None])[0],
            ),
        )

    def _dashboard(self, query: dict[str, list[str]]) -> dict:
        from datetime import datetime, timezone

        current = self.release_service.current(verify=False)
        manifest = current.get("manifest", {})
        counts = manifest.get("counts", {})
        facets = self.compatibility.global_facets()
        collections = [
            {"value": "DLP", "count": counts.get("dictionary", 0)},
            {
                "value": "VOCABULARIO",
                "count": counts.get("vocabulary", 0),
            },
        ]
        corpus = {
            "available": current.get("available", False),
            "totals": {
                "entries": counts.get("entries", 0),
                "forms": counts.get("forms", 0),
                "senses": counts.get("senses", 0),
                "definitions": counts.get("definitions", 0),
                "references": counts.get("relations", 0),
                "labels": counts.get("labels", 0),
            },
            "collections": collections,
            "grammar": facets.get("grammar", []),
            "domains": facets.get("domains", []),
            "source_statuses": facets.get("statuses", []),
            "workflow_statuses": [
                {
                    "value": "PUBLISHED",
                    "count": counts.get("entries", 0),
                }
            ],
            "publication": [
                {"value": "published", "count": counts.get("entries", 0)}
            ],
            "collection_status": [],
            "anomalies": [],
            "quality": manifest.get("quality", {}),
            "external_sources": manifest.get("external_sources", []),
        }
        try:
            days = max(0, int(query.get("days", ["30"])[0]))
        except ValueError:
            days = 30
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "corpus": corpus,
            "usage": self.usage_log.dashboard(days),
        }

    def _entry(self, resource: str, entry_id: str) -> None:
        self._json(
            HTTPStatus.OK,
            self.entry_repository.get_entry(resource, entry_id),
        )

    def _file(
        self, name: str, content_type: str, *, cache: bool = False
    ) -> None:
        body = (self.web_root / name).read_bytes()
        if content_type.startswith("text/html"):
            encoded_base = self.base_path.encode("utf-8")
            encoded_href = (self.base_path + "/" if self.base_path else "/").encode(
                "utf-8"
            )
            body = body.replace(
                b'<base href="/dicionario-vocabulario/">',
                b'<base href="' + encoded_href + b'">',
            ).replace(
                b'content="/dicionario-vocabulario"',
                b'content="' + encoded_base + b'"',
            )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Cache-Control",
            "public, max-age=86400" if cache else "no-cache",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _image(self, name: str) -> None:
        path = self.release_service.asset_path(name)
        if path is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "image_not_found"})
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _normalise_base_path(value: str) -> str:
    value = str(value or "").strip()
    if not value or value == "/":
        return ""
    return "/" + value.strip("/")
