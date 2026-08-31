from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import hmac
import json
from pathlib import Path
import tempfile
import threading
from urllib.parse import parse_qs, unquote, urlparse

from .editorial_service import EditorialError, EditorialService
from .publication_jobs import PublicationJobManager
from .validation import validate_active_run, validation_summary
from .repository_backup import RepositoryBackupService


def serve_editorial(
    db_path: str | Path,
    *,
    releases_root: str | Path = "releases",
    images_root: str | Path | None = None,
    meili_url: str = "http://127.0.0.1:7700",
    meili_key: str = "acl-local-development-key",
    rng_path: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8089,
    base_path: str = "",
    password: str | None = None,
    github_repository: str | Path | None = None,
    github_remote: str = "origin",
    github_branch: str = "main",
    github_require_lfs: bool = True,
    github_push: bool = True,
    github_backup_interval: int = 1800,
    usage_db: str | Path | None = None,
    runtime_env: str | Path | None = None,
) -> None:
    service = EditorialService(db_path, Path(releases_root) / "exports")
    threading.Thread(
        target=service.warm_entry_facets,
        name="acl-editorial-facets",
        daemon=True,
    ).start()
    repository_backup = RepositoryBackupService(
        db_path=db_path,
        releases_root=releases_root,
        repository_path=github_repository,
        usage_db=usage_db,
        runtime_env=runtime_env,
        remote=github_remote,
        branch=github_branch,
        require_lfs=github_require_lfs,
        push=github_push,
    ) if github_repository else None
    if repository_backup:
        repository_backup.start_periodic(
            interval_seconds=github_backup_interval,
            actor="sistema.backup",
        )
    jobs = PublicationJobManager(
        db_path=db_path,
        releases_root=releases_root,
        images_root=images_root,
        meili_url=meili_url,
        meili_key=meili_key,
        rng_path=rng_path,
        repository_backup=repository_backup,
        exports_root=service.exports_root,
    )
    handler = type(
        "ACLEditorialHandler",
        (_EditorialHandler,),
        {
            "service": service,
            "jobs": jobs,
            "web_root": Path(__file__).resolve().parents[2] / "editorial_app" / "web",
            "public_web_root": Path(__file__).resolve().parents[2] / "public_app" / "web",
            "base_path": _normalise_base_path(base_path),
            "password": password,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Aplicação editorial local: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class _EditorialHandler(BaseHTTPRequestHandler):
    service: EditorialService
    jobs: PublicationJobManager
    web_root: Path
    public_web_root: Path
    base_path: str
    password: str | None

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/" and "x" in parse_qs(parsed.query):
            key = parse_qs(parsed.query).get("x", [""])[0]
            if not self.service.readonly_key_is_valid(key):
                return self._access_denied(
                    HTTPStatus.FORBIDDEN, "A chave de avaliação é inválida ou está desativada."
                )
            token = self.service.readonly_session_token(key)
            cookie_path = f"{self.base_path}/" if self.base_path else "/"
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", cookie_path)
            self.send_header(
                "Set-Cookie",
                f"acl_editor_readonly={token}; Path={cookie_path}; Max-Age=28800; "
                f"HttpOnly; SameSite=Strict{secure}",
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if parsed.path != "/health" and not self._require_auth():
            return
        try:
            if parsed.path == "/":
                return self._file("index.html", "text/html; charset=utf-8")
            if parsed.path == "/editorial.js":
                return self._file("editorial.js", "text/javascript; charset=utf-8")
            if parsed.path == "/editorial.css":
                return self._file("editorial.css", "text/css; charset=utf-8")
            if parsed.path == "/public-styles.css":
                return self._file(
                    "assets/styles.css", "text/css; charset=utf-8", root=self.public_web_root
                )
            if parsed.path == "/acl-logo.png":
                return self._file("assets/acl-logo.png", "image/png", root=self.public_web_root)
            if parsed.path == "/health":
                return self._json(HTTPStatus.OK, {"status": "ok", "mode": "editable"})
            if parsed.path == "/api/editorial/session":
                return self._json(
                    HTTPStatus.OK,
                    {
                        "mode": self.auth_mode,
                        "read_only": self.auth_mode == "key",
                    },
                )
            if parsed.path == "/api/editorial/admin/access-key":
                if self.auth_mode != "basic":
                    return self._access_denied(
                        HTTPStatus.FORBIDDEN,
                        "A administração exige autenticação principal.",
                    )
                return self._json(
                    HTTPStatus.OK, self.service.readonly_access_status()
                )
            if parsed.path == "/api/editorial/overview":
                return self._json(HTTPStatus.OK, self.service.overview())
            if parsed.path == "/api/editorial/audit":
                return self._json(HTTPStatus.OK, self.service.audit_report())
            if parsed.path.startswith("/api/editorial/exports/"):
                filename = unquote(parsed.path.rsplit("/", 1)[-1])
                if not filename or Path(filename).name != filename:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_name"})
                content_type = (
                    "application/xml; charset=utf-8"
                    if filename.endswith(".xml")
                    else "text/csv; charset=utf-8"
                    if filename.endswith(".csv")
                    else "application/json; charset=utf-8"
                )
                return self._file(
                    filename, content_type, root=self.service.exports_root
                )
            if parsed.path == "/api/editorial/users":
                return self._json(
                    HTTPStatus.OK, {"items": self.service.governance.users()}
                )
            if parsed.path == "/api/editorial/controlled-values":
                query = parse_qs(parsed.query)
                return self._json(
                    HTTPStatus.OK,
                    {
                        "items": self.service.governance.list_values(
                            query.get("category", [None])[0],
                            query.get("status", [None])[0],
                            query.get("sort", ["alphabetical"])[0],
                            query.get("direction", ["asc"])[0],
                        )
                    },
                )
            if parsed.path == "/api/editorial/validation":
                return self._json(
                    HTTPStatus.OK,
                    validation_summary(self.service.db_path).as_dict(),
                )
            if parsed.path == "/api/editorial/entries":
                query = parse_qs(parsed.query)
                return self._json(
                    HTTPStatus.OK,
                    self.service.list_entries(
                        query.get("q", [""])[0],
                        int(query.get("limit", ["50"])[0]),
                        int(query.get("offset", ["0"])[0]),
                        resource=query.get("resource", [None])[0],
                        workflow_status=query.get("workflow", [None])[0],
                        editorial_status=query.get("editorial_status", [None])[0],
                        grammar=query.get("grammar", [None])[0],
                        domain=query.get("domain", [None])[0],
                        severity=query.get("severity", [None])[0],
                        issue_rule=query.get("issue_rule", [None])[0],
                    ),
                )
            if parsed.path == "/api/editorial/publish/status":
                return self._json(HTTPStatus.OK, self.jobs.status())
            if parsed.path == "/api/editorial/github-backup/status":
                return self._json(
                    HTTPStatus.OK,
                    self.jobs.status()["repository_backup"],
                )
            if parsed.path == "/api/editorial/publication-entries":
                query = parse_qs(parsed.query)
                return self._json(
                    HTTPStatus.OK,
                    self.service.publication_entries(
                        int(query.get("limit", ["200"])[0]),
                        int(query.get("offset", ["0"])[0]),
                    ),
                )
            if parsed.path.startswith("/api/editorial/entries/"):
                public_id, action = self._entry_target(parsed.path)
                entry = self.service.get_entry(public_id)
                if action == "revisions":
                    return self._json(HTTPStatus.OK, {"items": entry["revisions"]})
                if action:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return self._json(HTTPStatus.OK, entry)
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except Exception as exc:
            self._handle_error(exc)

    def do_PATCH(self):  # noqa: N802
        parsed = urlparse(self.path)
        if not self._require_auth(write=True):
            return
        try:
            if parsed.path.startswith("/api/editorial/controlled-values/"):
                value_id = int(parsed.path.rsplit("/", 1)[-1])
                return self._json(
                    HTTPStatus.OK,
                    self.service.governance.update_value(value_id, self._body()),
                )
            if not parsed.path.startswith("/api/editorial/entries/"):
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            public_id, action = self._entry_target(parsed.path)
            if action:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return self._json(
                HTTPStatus.OK, self.service.update_entry(public_id, self._body())
            )
        except Exception as exc:
            self._handle_error(exc)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if not self._require_auth(write=True):
            return
        try:
            if parsed.path == "/api/editorial/admin/access-key":
                payload = self._body()
                return self._json(
                    HTTPStatus.OK,
                    self.service.set_readonly_access_key(
                        str(payload.get("key") or ""),
                        actor=str(payload.get("actor") or ""),
                    ),
                )
            if parsed.path == "/api/editorial/compare-xml":
                actor = self.headers.get("X-ACL-Actor", "").strip()
                filename = Path(self.headers.get("X-ACL-Filename", "upload.xml")).name
                suffix = ".xz" if filename.lower().endswith(".xz") else ".xml"
                temporary = self._upload(suffix)
                try:
                    report = self.service.compare_xml(
                        temporary, actor=actor, source_label=filename
                    )
                finally:
                    temporary.unlink(missing_ok=True)
                return self._json(HTTPStatus.OK, report)
            if parsed.path == "/api/editorial/import":
                actor = self.headers.get("X-ACL-Actor", "").strip()
                filename = Path(self.headers.get("X-ACL-Filename", "upload.xml")).name
                suffix = ".xz" if filename.lower().endswith(".xz") else ".xml"
                temporary = self._upload(suffix)
                try:
                    result = self.service.replace_from_xml(
                        temporary,
                        actor=actor,
                        expected_sha256=self.headers.get(
                            "X-ACL-Comparison-SHA256", ""
                        ).strip(),
                        source_label=filename,
                    )
                finally:
                    temporary.unlink(missing_ok=True)
                return self._json(
                    HTTPStatus.CREATED,
                    {
                        "mode": "replace",
                        "run_id": result.run_id,
                        "imported": result.imported,
                        "errors": result.errors,
                    },
                )
            if parsed.path == "/api/editorial/controlled-values":
                return self._json(
                    HTTPStatus.CREATED,
                    self.service.governance.create_value(self._body()),
                )
            if parsed.path == "/api/editorial/controlled-values/merge":
                payload = self._body()
                return self._json(
                    HTTPStatus.OK,
                    self.service.governance.merge_values(
                        int(payload.get("source_id") or 0),
                        int(payload.get("target_id") or 0),
                        actor=str(payload.get("actor") or ""),
                        comment=str(payload.get("comment") or ""),
                    ),
                )
            if parsed.path == "/api/editorial/publication-selection":
                payload = self._body()
                return self._json(
                    HTTPStatus.OK,
                    self.service.select_for_publication(
                        payload.get("public_ids") or [],
                        actor=str(payload.get("actor") or ""),
                        selected=bool(payload.get("selected", True)),
                    ),
                )
            if parsed.path == "/api/editorial/save-canonical":
                payload = self._body()
                return self._json(
                    HTTPStatus.OK,
                    self.service.save_canonical(
                        actor=str(payload.get("actor") or ""),
                        rng_path=self.jobs.rng_path,
                    ),
                )
            if parsed.path == "/api/editorial/github-backup/sync":
                payload = self._body()
                actor = str(payload.get("actor") or "")
                self.service.governance.require_user(
                    actor, {"approver", "administrator"}
                )
                if not self.jobs.repository_backup:
                    raise RuntimeError(
                        "A sincronização GitHub não está configurada no servidor."
                    )
                return self._json(
                    HTTPStatus.ACCEPTED,
                    self.jobs.repository_backup.start(actor=actor),
                )
            if parsed.path == "/api/editorial/publish-selected":
                payload = self._body()
                return self._json(
                    HTTPStatus.ACCEPTED,
                    self.jobs.publish_selected(
                        actor=str(payload.get("actor") or ""),
                        description=str(payload.get("description") or ""),
                    ),
                )
            if parsed.path in {"/api/editorial/publish", "/api/editorial/releases/prepare"}:
                payload = self._body()
                return self._json(
                    HTTPStatus.ACCEPTED,
                    self.jobs.prepare(
                        actor=str(payload.get("actor") or ""),
                        description=str(payload.get("description") or ""),
                    ),
                )
            if parsed.path == "/api/editorial/validate":
                payload = self._body(optional=True)
                result = validate_active_run(
                    self.service.db_path,
                    rng_path=payload.get("rng_path") or self.jobs.rng_path,
                )
                return self._json(HTTPStatus.OK, result.as_dict())
            if parsed.path.startswith("/api/editorial/releases/"):
                suffix = parsed.path.removeprefix("/api/editorial/releases/")
                parts = suffix.split("/", 1)
                if len(parts) != 2:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                release_id, action = unquote(parts[0]), parts[1]
                payload = self._body()
                actor = str(payload.get("actor") or "")
                comment = str(payload.get("comment") or "")
                if action == "approve":
                    value = self.jobs.approve(release_id, actor=actor, comment=comment)
                    return self._json(HTTPStatus.OK, value)
                if action == "publish":
                    return self._json(
                        HTTPStatus.ACCEPTED,
                        self.jobs.publish(release_id, actor=actor),
                    )
                if action == "rollback":
                    return self._json(
                        HTTPStatus.ACCEPTED,
                        self.jobs.rollback(
                            release_id, actor=actor, comment=comment
                        ),
                    )
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            if parsed.path.startswith("/api/editorial/entries/"):
                public_id, action = self._entry_target(parsed.path)
                payload = self._body()
                if action and action.startswith("issues/"):
                    issue_parts = action.split("/")
                    if len(issue_parts) == 3 and issue_parts[2] == "waive":
                        return self._json(
                            HTTPStatus.OK,
                            self.service.waive_issue(
                                public_id,
                                unquote(issue_parts[1]),
                                actor=str(payload.get("actor") or ""),
                                reason=str(payload.get("reason") or ""),
                            ),
                        )
                    if len(issue_parts) == 3 and issue_parts[2] == "fix":
                        return self._json(
                            HTTPStatus.OK,
                            self.service.apply_issue_fix(
                                public_id,
                                unquote(issue_parts[1]),
                                str(payload.get("fix_code") or ""),
                                actor=str(payload.get("actor") or ""),
                                comment=str(payload.get("comment") or ""),
                            ),
                        )
                if action == "workflow":
                    return self._json(
                        HTTPStatus.OK,
                        self.service.set_workflow(
                            public_id,
                            str(payload.get("target") or ""),
                            actor=str(payload.get("actor") or ""),
                            comment=str(payload.get("comment") or ""),
                            confirmed=bool(payload.get("confirmed", False)),
                        ),
                    )
                if action and action.startswith("revisions/") and action.endswith("/restore"):
                    revision_no = int(action.split("/")[1])
                    return self._json(
                        HTTPStatus.OK,
                        self.service.restore_revision(
                            public_id,
                            revision_no,
                            actor=str(payload.get("actor") or ""),
                            comment=str(payload.get("comment") or ""),
                        ),
                    )
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except Exception as exc:
            self._handle_error(exc)

    def do_DELETE(self):  # noqa: N802
        parsed = urlparse(self.path)
        if not self._require_auth(write=True):
            return
        try:
            if parsed.path.startswith("/api/editorial/controlled-values/"):
                value_id = int(parsed.path.rsplit("/", 1)[-1])
                payload = self._body(optional=True)
                self.service.governance.delete_value(
                    value_id,
                    actor=str(payload.get("actor") or ""),
                    comment=str(payload.get("comment") or ""),
                )
                return self._json(HTTPStatus.OK, {"deleted": value_id})
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except Exception as exc:
            self._handle_error(exc)

    def _handle_error(self, exc: Exception) -> None:
        if isinstance(exc, EditorialError):
            status = HTTPStatus(exc.status)
        elif isinstance(exc, PermissionError):
            status = HTTPStatus.FORBIDDEN
        elif isinstance(exc, RuntimeError):
            status = HTTPStatus.CONFLICT
        else:
            status = HTTPStatus.BAD_REQUEST
        self._json(status, {"error": type(exc).__name__, "message": str(exc)})

    @staticmethod
    def _entry_target(path: str) -> tuple[str, str | None]:
        suffix = path.removeprefix("/api/editorial/entries/")
        parts = suffix.split("/", 1)
        return unquote(parts[0]), parts[1] if len(parts) > 1 else None

    def _body(self, optional: bool = False) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            if optional:
                return {}
            raise ValueError("Corpo JSON em falta.")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("O corpo do pedido deve ser um objeto JSON.")
        return value

    def _upload(self, suffix: str) -> Path:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("Ficheiro de importação em falta.")
        if length > 1_500_000_000:
            raise ValueError("O ficheiro de importação excede 1,5 GB.")
        handle = tempfile.NamedTemporaryFile(
            prefix="acl-import-", suffix=suffix, delete=False
        )
        remaining = length
        try:
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("A transferência do ficheiro ficou incompleta.")
                handle.write(chunk)
                remaining -= len(chunk)
        finally:
            handle.close()
        return Path(handle.name)

    def _file(self, name, content_type, *, root=None):
        body = ((root or self.web_root) / name).read_bytes()
        if content_type.startswith("text/html"):
            encoded_base = self.base_path.encode("utf-8")
            encoded_href = (
                self.base_path + "/" if self.base_path else "/"
            ).encode("utf-8")
            body = body.replace(b"__ACL_EDITOR_BASE_HREF__", encoded_href)
            body = body.replace(b"__ACL_EDITOR_BASE_PATH__", encoded_base)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_auth(self, *, write: bool = False) -> bool:
        self.auth_mode = None
        if not self.password:
            self.auth_mode = "basic"
            return True
        header = self.headers.get("Authorization", "")
        valid = False
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
                username, supplied = decoded.split(":", 1)
                valid = hmac.compare_digest(username, "acl") and hmac.compare_digest(
                    supplied, self.password
                )
            except (ValueError, UnicodeDecodeError):
                valid = False
        if valid:
            self.auth_mode = "basic"
            return True
        cookies = {}
        for part in self.headers.get("Cookie", "").split(";"):
            name, separator, value = part.strip().partition("=")
            if separator:
                cookies[name] = value
        if self.service.readonly_session_is_valid(
            cookies.get("acl_editor_readonly", "")
        ):
            self.auth_mode = "key"
            if write:
                return self._access_denied(
                    HTTPStatus.FORBIDDEN,
                    "Este acesso de avaliação é apenas de leitura.",
                )
            return True
        return self._access_denied(
            HTTPStatus.UNAUTHORIZED,
            "Autenticação necessária.",
            challenge=True,
        )

    def _access_denied(
        self, status: HTTPStatus, message: str, *, challenge: bool = False
    ) -> bool:
        body = message.encode("utf-8")
        self.send_response(status)
        if challenge:
            self.send_header("WWW-Authenticate", 'Basic realm="ACL - gestao editorial"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def _json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def _normalise_base_path(value: str) -> str:
    value = str(value or "").strip()
    if not value or value == "/":
        return ""
    return "/" + value.strip("/")
