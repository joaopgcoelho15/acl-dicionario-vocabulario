from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import traceback

from .editorial_db import connect
from .editorial_service import EditorialService
from .governance import GovernanceService
from .meili import MeiliClient
from .publication import (
    activate_local_release,
    approve_release,
    build_release,
    current_release,
    integrity_failure_message,
    release_records,
    set_release_state,
    verify_release,
)
from .repository_backup import RepositoryBackupService


class PublicationJobManager:
    """Publica numa só operação funcional, preservando as etapas técnicas internas."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        releases_root: str | Path,
        images_root: str | Path | None,
        meili_url: str,
        meili_key: str,
        rng_path: str | Path | None = None,
        repository_backup: RepositoryBackupService | None = None,
    ):
        self.db_path = Path(db_path)
        self.releases_root = Path(releases_root)
        self.images_root = Path(images_root) if images_root else None
        self.rng_path = Path(rng_path) if rng_path else None
        self.client = MeiliClient(meili_url, meili_key)
        self.editorial = EditorialService(self.db_path)
        self.governance = GovernanceService(self.db_path)
        self.repository_backup = repository_backup
        self._lock = threading.Lock()
        self._state: dict = self._idle()

    @staticmethod
    def _idle() -> dict:
        return {
            "state": "idle",
            "operation": None,
            "phase": None,
            "release_id": None,
            "message": None,
            "started_at": None,
            "finished_at": None,
        }

    def status(self) -> dict:
        with self._lock:
            state = dict(self._state)
        state["releases"] = release_records(self.db_path)
        state["active_release"] = current_release(self.releases_root)
        state["repository_backup"] = (
            self.repository_backup.status() if self.repository_backup else {
                "configured": False,
                "state": "disabled",
                "message": "A sincronização GitHub não está configurada.",
            }
        )
        return state

    def prepare(self, *, actor: str, description: str) -> dict:
        self.governance.require_user(
            actor, {"editor", "reviewer", "approver", "administrator"}
        )
        valid, message = self.editorial.can_publish(require_selection=True)
        if not valid:
            raise ValueError(message)
        release_id = datetime.now(timezone.utc).strftime("local-%Y%m%d-%H%M%S")
        self._start(
            operation="prepare",
            release_id=release_id,
            message="A construir e validar a versão candidata…",
            target=self._prepare,
            args=(release_id, actor, description),
        )
        return self.status()

    def publish_selected(self, *, actor: str, description: str = "") -> dict:
        self.governance.require_user(actor, {"approver", "administrator"})
        valid, message = self.editorial.can_publish(require_selection=True)
        if not valid:
            raise ValueError(message)
        release_id = datetime.now(timezone.utc).strftime("local-%Y%m%d-%H%M%S")
        self._start(
            operation="publish",
            release_id=release_id,
            message="A validar e publicar as entradas selecionadas…",
            target=self._prepare_and_publish,
            args=(release_id, actor, description),
        )
        return self.status()

    def approve(self, release_id: str, *, actor: str, comment: str) -> dict:
        value = approve_release(
            self.db_path,
            self.releases_root,
            release_id,
            actor=actor,
            comment=comment,
        )
        return {"release": value, **self.status()}

    def publish(self, release_id: str, *, actor: str) -> dict:
        self.governance.require_user(actor, {"approver", "administrator"})
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT state FROM releases WHERE release_id=?", (release_id,)
            ).fetchone()
        if row is None or row["state"] != "approved":
            raise ValueError("Apenas uma candidata aprovada pode ser publicada.")
        self._start(
            operation="publish",
            release_id=release_id,
            message="A indexar a versão aprovada…",
            target=self._publish,
            args=(release_id, actor, False, ""),
        )
        return self.status()

    def run_synchronously(
        self,
        release_id: str,
        *,
        actor: str,
        rollback: bool = False,
        comment: str = "",
    ) -> dict:
        """Executa o mesmo workflow governado para automação/CLI."""
        self.governance.require_user(actor, {"approver", "administrator"})
        state = self._release_state(release_id)
        if rollback:
            if release_id == current_release(self.releases_root):
                raise ValueError("A release selecionada já está ativa.")
            if state not in {"archived", "active"}:
                raise ValueError(
                    "Só é possível reverter para uma release anteriormente publicada."
                )
        elif state != "approved":
            raise ValueError("Apenas uma candidata aprovada pode ser publicada.")
        self._publish(release_id, actor, rollback, comment)
        result = self.status()
        if result["state"] == "failed":
            raise RuntimeError(result["message"])
        return result

    def rollback(
        self, release_id: str, *, actor: str, comment: str
    ) -> dict:
        self.governance.require_user(actor, {"approver", "administrator"})
        if release_id == current_release(self.releases_root):
            raise ValueError("A release selecionada já está ativa.")
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT state FROM releases WHERE release_id=?", (release_id,)
            ).fetchone()
        if row is None or row["state"] not in {"archived", "active"}:
            raise ValueError("Só é possível reverter para uma release anteriormente publicada.")
        self._start(
            operation="rollback",
            release_id=release_id,
            message="A reconstruir os índices da versão anterior…",
            target=self._publish,
            args=(release_id, actor, True, comment),
        )
        return self.status()

    def _start(self, *, operation, release_id, message, target, args) -> None:
        with self._lock:
            if self._state["state"] == "running":
                raise RuntimeError("Já existe uma operação de publicação em curso.")
            self._state = {
                "state": "running",
                "operation": operation,
                "phase": operation,
                "release_id": release_id,
                "message": message,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
            }
        threading.Thread(
            target=target,
            args=args,
            name=f"acl-{operation}-{release_id}",
            daemon=True,
        ).start()

    def _prepare(self, release_id: str, actor: str, description: str) -> None:
        try:
            result = build_release(
                self.db_path,
                self.releases_root,
                release_id=release_id,
                images_root=self.images_root,
                prepared_by=actor,
                description=description or "Versão candidata",
                rng_path=self.rng_path,
                selection_mode=True,
            )
            self._finish(
                "succeeded",
                f"Candidata {result.release_id} preparada e pronta para aprovação.",
            )
        except Exception as exc:
            self._failed(exc)

    def _prepare_and_publish(
        self, release_id: str, actor: str, description: str
    ) -> None:
        try:
            self._phase("prepare", "A construir a versão técnica…")
            build_release(
                self.db_path,
                self.releases_root,
                release_id=release_id,
                images_root=self.images_root,
                prepared_by=actor,
                description=description or "Publicação editorial",
                rng_path=self.rng_path,
                selection_mode=True,
            )
            self._phase("approve", "A confirmar a integridade da versão…")
            approve_release(
                self.db_path,
                self.releases_root,
                release_id,
                actor=actor,
                comment="Aprovação integrada na publicação",
            )
        except Exception as exc:
            self._failed(exc)
            return
        self._publish(release_id, actor, False, description)

    def _publish(
        self, release_id: str, actor: str, rollback: bool, comment: str
    ) -> None:
        previous_release = current_release(self.releases_root)
        original_state = self._release_state(release_id)
        indexes_swapped = False
        pointer_activated = False
        try:
            path = self.releases_root / release_id
            self._phase("verify", "A verificar a integridade da versão…")
            verification = verify_release(path)
            if not verification["valid"]:
                raise RuntimeError(
                    "A release não passou a verificação de integridade: "
                    + integrity_failure_message(verification)
                )
            self._phase("index", "A construir os índices versionados…")
            prepared = self.client.build_release_indexes(path)
            set_release_state(
                self.db_path,
                release_id,
                "indexed",
                actor=actor,
                comment="Índices reconstruídos" if rollback else "Índices criados",
            )
            self._phase("test", "A testar os índices antes de os tornar públicos…")
            smoke = self._smoke(path, prepared["indexes"])
            if not smoke["valid"]:
                raise RuntimeError("Os testes de publicação falharam: " + json.dumps(smoke))
            set_release_state(
                self.db_path,
                release_id,
                "tested",
                actor=actor,
                comment="Testes automáticos concluídos",
            )
            self._phase("activate", "A trocar os índices e ativar a versão testada…")
            self.client.activate_release_indexes(path)
            indexes_swapped = True
            activate_local_release(
                self.db_path,
                self.releases_root,
                release_id,
                verify_integrity=False,
            )
            pointer_activated = True
            if not rollback:
                self.editorial.mark_published(release_id, actor=actor)
            with connect(self.db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO audit_events(
                        event_type, actor, release_id, resulting_state,
                        comment, details_json
                    ) VALUES (?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        "RELEASE_ROLLBACK" if rollback else "RELEASE_PUBLISHED",
                        actor,
                        release_id,
                        comment or None,
                        json.dumps(smoke, ensure_ascii=False),
                    ),
                )
            verb = "reposta" if rollback else "publicada"
            backup_note = ""
            if self.repository_backup:
                self._phase(
                    "backup",
                    "A versão está ativa; a sincronizar o backup com o GitHub…",
                )
                try:
                    self.repository_backup.sync(actor=actor, release_id=release_id)
                    backup_note = " Backup sincronizado com o GitHub."
                except Exception as backup_error:
                    backup_note = (
                        " A publicação está ativa, mas a sincronização GitHub "
                        f"ficou pendente: {backup_error}"
                    )
            self._finish(
                "succeeded",
                f"Versão {release_id} {verb} e verificada.{backup_note}",
            )
        except Exception as exc:
            recovery_error = self._recover_failed_activation(
                release_id=release_id,
                original_state=original_state,
                previous_release=previous_release,
                indexes_swapped=indexes_swapped,
                pointer_activated=pointer_activated,
            )
            if recovery_error:
                exc = RuntimeError(f"{exc}; falhou também a reposição: {recovery_error}")
            self._failed(exc)

    def _smoke(self, release_path: Path, indexes: dict[str, str]) -> dict:
        manifest = json.loads(
            (release_path / "manifest.json").read_text(encoding="utf-8")
        )
        expected = manifest["counts"]
        actual = {}
        for resource in ("dictionary", "vocabulary"):
            stats = self.client.request("GET", f"/indexes/{indexes[resource]}/stats")
            actual[resource] = int(stats.get("numberOfDocuments", -1))
        hits = 0
        for resource in ("dictionary", "vocabulary"):
            sample = self.client.search_index(
                indexes[resource], {"q": "cavalo", "limit": 3}
            )
            hits += len(sample.get("hits", []))
        queries_ok = hits > 0
        return {
            "valid": (
                actual["dictionary"] == expected["dictionary"]
                and actual["vocabulary"] == expected["vocabulary"]
                and queries_ok
            ),
            "expected": {
                "dictionary": expected["dictionary"],
                "vocabulary": expected["vocabulary"],
            },
            "actual": actual,
            "representative_query": "cavalo",
            "query_ok": queries_ok,
        }

    def _release_state(self, release_id: str) -> str:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT state FROM releases WHERE release_id=?", (release_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("Release inexistente.")
        return str(row["state"])

    def _recover_failed_activation(
        self,
        *,
        release_id: str,
        original_state: str,
        previous_release: str | None,
        indexes_swapped: bool,
        pointer_activated: bool,
    ) -> str | None:
        try:
            if indexes_swapped and previous_release:
                previous_path = self.releases_root / previous_release
                self.client.activate_release_indexes(previous_path)
                if pointer_activated:
                    activate_local_release(
                        self.db_path,
                        self.releases_root,
                        previous_release,
                        verify_integrity=False,
                    )
            with connect(self.db_path) as connection:
                connection.execute(
                    "UPDATE releases SET state=? WHERE release_id=?",
                    (original_state, release_id),
                )
            return None
        except Exception as recovery_exc:
            return str(recovery_exc)

    def _phase(self, phase: str, message: str) -> None:
        with self._lock:
            self._state["phase"] = phase
            self._state["message"] = message

    def _failed(self, exc: Exception) -> None:
        self._finish(
            "failed",
            f"{type(exc).__name__}: {exc}",
            traceback.format_exc(),
        )

    def _finish(self, state: str, message: str, details: str | None = None) -> None:
        with self._lock:
            self._state["state"] = state
            self._state["message"] = message
            self._state["details"] = details
            self._state["finished_at"] = datetime.now(timezone.utc).isoformat()
