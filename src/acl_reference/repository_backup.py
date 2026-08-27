from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import lzma
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import threading


class RepositoryBackupError(RuntimeError):
    pass


class RepositoryBackupService:
    """Cria um snapshot restaurável e sincroniza-o com um repositório Git."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        releases_root: str | Path,
        repository_path: str | Path | None,
        usage_db: str | Path | None = None,
        runtime_env: str | Path | None = None,
        remote: str = "origin",
        branch: str = "main",
        require_lfs: bool = True,
        push: bool = True,
    ):
        self.db_path = Path(db_path)
        self.releases_root = Path(releases_root)
        self.repository_path = Path(repository_path) if repository_path else None
        self.usage_db = Path(usage_db) if usage_db else None
        self.runtime_env = Path(runtime_env) if runtime_env else None
        self.remote = remote
        self.branch = branch
        self.require_lfs = require_lfs
        self.push = push
        self.status_path = self.db_path.parent / "github-backup-status.json"
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._periodic_stop = threading.Event()
        self._periodic_thread: threading.Thread | None = None
        self._state = self._load_status()

    @property
    def configured(self) -> bool:
        return self.repository_path is not None

    def status(self) -> dict:
        with self._state_lock:
            value = dict(self._state)
        value["configured"] = self.configured
        value["repository"] = str(self.repository_path) if self.repository_path else None
        return value

    def start(self, *, actor: str, release_id: str | None = None) -> dict:
        if not self.configured:
            raise RepositoryBackupError(
                "A sincronização GitHub não está configurada no servidor."
            )
        if not self._operation_lock.acquire(blocking=False):
            raise RepositoryBackupError("Já existe uma sincronização em curso.")
        self._set_state(
            state="running",
            message="A criar o snapshot restaurável para o GitHub…",
            actor=actor,
            release_id=release_id,
            started_at=_now(),
            finished_at=None,
            commit=None,
        )
        threading.Thread(
            target=self._background_sync,
            args=(actor, release_id),
            daemon=True,
            name="acl-github-backup",
        ).start()
        return self.status()

    def start_periodic(self, *, interval_seconds: int, actor: str) -> None:
        """Salvaguarda alterações editoriais regularmente sem bloquear a aplicação."""
        if not self.configured or interval_seconds <= 0 or self._periodic_thread:
            return

        def run() -> None:
            while not self._periodic_stop.wait(interval_seconds):
                try:
                    self.sync_if_changed(actor=actor)
                except RepositoryBackupError as exc:
                    # Uma publicação pode já estar a sincronizar. Nesse caso, essa
                    # operação produz o snapshot e o ciclo periódico tenta novamente
                    # no intervalo seguinte.
                    if "Já existe uma sincronização" not in str(exc):
                        continue
                except Exception:
                    continue

        self._periodic_thread = threading.Thread(
            target=run,
            daemon=True,
            name="acl-github-backup-periodic",
        )
        self._periodic_thread.start()

    def sync(self, *, actor: str, release_id: str | None = None) -> dict:
        if not self.configured:
            raise RepositoryBackupError(
                "A sincronização GitHub não está configurada no servidor."
            )
        if not self._operation_lock.acquire(blocking=False):
            raise RepositoryBackupError("Já existe uma sincronização em curso.")
        self._set_state(
            state="running",
            message="A criar o snapshot restaurável para o GitHub…",
            actor=actor,
            release_id=release_id,
            started_at=_now(),
            finished_at=None,
            commit=None,
        )
        try:
            return self._sync_locked(actor=actor, release_id=release_id)
        finally:
            self._operation_lock.release()

    def sync_if_changed(self, *, actor: str) -> dict:
        """Sincroniza apenas quando o estado editorial mudou desde o snapshot."""
        fingerprint = self._source_fingerprint()
        with self._state_lock:
            previous = self._state.get("source_fingerprint")
        if previous and previous == fingerprint:
            return {**self.status(), "skipped": True}
        return self.sync(actor=actor)

    def _background_sync(self, actor: str, release_id: str | None) -> None:
        try:
            self._sync_locked(actor=actor, release_id=release_id)
        except Exception:
            pass
        finally:
            self._operation_lock.release()

    def _sync_locked(self, *, actor: str, release_id: str | None) -> dict:
        try:
            repository = self._validate_repository()
            active_release = release_id or _active_release(self.releases_root)
            if not active_release:
                raise RepositoryBackupError("Não existe uma release ativa para salvaguardar.")
            release_path = self.releases_root / active_release
            if not release_path.is_dir():
                raise RepositoryBackupError(
                    f"A pasta da release ativa não existe: {active_release}."
                )
            self._prepare_lfs(repository)
            manifest = self._build_snapshot(repository, active_release, actor)
            commit = self._commit_and_push(repository, active_release, actor)
            result = self._set_state(
                state="succeeded",
                message=f"Snapshot {active_release} sincronizado com o GitHub.",
                actor=actor,
                release_id=active_release,
                started_at=self._state.get("started_at"),
                finished_at=_now(),
                commit=commit,
                manifest=manifest,
                source_fingerprint=self._source_fingerprint(),
            )
            return result
        except Exception as exc:
            self._set_state(
                state="pending",
                message=f"Sincronização GitHub pendente: {type(exc).__name__}: {exc}",
                actor=actor,
                release_id=release_id,
                started_at=self._state.get("started_at"),
                finished_at=_now(),
                commit=None,
            )
            raise

    def _source_fingerprint(self) -> str:
        """Identifica alterações persistentes sem comprimir toda a base."""
        values = [str(self.db_path.resolve()), _active_release(self.releases_root) or ""]
        for path in (self.db_path, Path(f"{self.db_path}-wal")):
            if path.is_file():
                stat = path.stat()
                values.extend((str(path), str(stat.st_size), str(stat.st_mtime_ns)))
            else:
                values.extend((str(path), "missing"))
        return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()

    def _validate_repository(self) -> Path:
        repository = self.repository_path
        if repository is None or not repository.is_dir():
            raise RepositoryBackupError("A cópia local do repositório de dados não existe.")
        if not (repository / ".git").exists():
            raise RepositoryBackupError(
                f"{repository} não é um repositório Git."
            )
        return repository

    def _prepare_lfs(self, repository: Path) -> None:
        available = _run_git(repository, "lfs", "version", check=False).returncode == 0
        if self.require_lfs and not available:
            raise RepositoryBackupError("Git LFS não está instalado no serviço editorial.")
        if available:
            _run_git(repository, "lfs", "install", "--local")
            _run_git(repository, "lfs", "track", "current/*.xz")

    def _build_snapshot(self, repository: Path, release_id: str, actor: str) -> dict:
        temporary = Path(tempfile.mkdtemp(prefix=".acl-snapshot-", dir=repository))
        current = temporary / "current"
        current.mkdir()
        try:
            sqlite_copy = temporary / "editorial.sqlite"
            _sqlite_backup(self.db_path, sqlite_copy)
            _compress(sqlite_copy, current / "editorial.sqlite.xz")
            sqlite_copy.unlink()

            if self.usage_db and self.usage_db.is_file():
                usage_copy = temporary / "usage.sqlite"
                _sqlite_backup(self.usage_db, usage_copy)
                _compress(usage_copy, current / "usage.sqlite.xz")
                usage_copy.unlink()

            with tarfile.open(current / "active-release.tar.xz", "w:xz") as archive:
                archive.add(self.releases_root / release_id, arcname=release_id)

            if self.runtime_env and self.runtime_env.is_file():
                shutil.copy2(self.runtime_env, current / "runtime.env")

            files = {}
            for path in sorted(current.iterdir()):
                files[path.name] = {
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            manifest = {
                "format": "acl-editorial-backup",
                "format_version": 1,
                "created_at": _now(),
                "actor": actor,
                "active_release": release_id,
                "files": files,
                "restore": {
                    "software_repository": "joaopgcoelho15/acl-dicionario-vocabulario",
                    "data_repository": "joaopgcoelho15/acl-dicionario-vocabulario-dados",
                },
            }
            (current / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            destination = repository / "current"
            previous = repository / ".current-previous"
            if previous.exists():
                shutil.rmtree(previous)
            if destination.exists():
                destination.rename(previous)
            current.rename(destination)
            if previous.exists():
                shutil.rmtree(previous)
            return manifest
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _commit_and_push(self, repository: Path, release_id: str, actor: str) -> str:
        _run_git(repository, "config", "user.name", "ACL Editorial Backup")
        _run_git(repository, "config", "user.email", "illlp@acad-ciencias.pt")
        _run_git(repository, "add", "--all", "current")
        if (repository / ".gitattributes").is_file():
            _run_git(repository, "add", ".gitattributes")
        changed = _run_git(repository, "diff", "--cached", "--quiet", check=False)
        if changed.returncode != 0:
            _run_git(
                repository,
                "commit",
                "-m",
                f"Backup editorial {release_id} por {actor}",
            )
        commit = _run_git(repository, "rev-parse", "HEAD").stdout.strip()
        if self.push:
            _run_git(repository, "push", self.remote, f"HEAD:{self.branch}")
        return commit

    def _load_status(self) -> dict:
        if self.status_path.is_file():
            try:
                value = json.loads(self.status_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    if value.get("state") == "running":
                        value["state"] = "pending"
                        value["message"] = (
                            "A sincronização anterior foi interrompida; "
                            "execute Sincronizar agora."
                        )
                    return value
            except (OSError, json.JSONDecodeError):
                pass
        return {
            "state": "idle" if self.configured else "disabled",
            "message": "Ainda não foi executada uma sincronização GitHub.",
            "started_at": None,
            "finished_at": None,
            "release_id": None,
            "actor": None,
            "commit": None,
        }

    def _set_state(self, **values) -> dict:
        with self._state_lock:
            self._state = {**self._state, **values}
            serializable = dict(self._state)
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.status_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(serializable, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.status_path)
            return dict(self._state)


def restore_repository_snapshot(
    repository_path: str | Path,
    *,
    db_path: str | Path,
    releases_root: str | Path,
    usage_db: str | Path | None = None,
    env_target: str | Path | None = None,
) -> dict:
    repository = Path(repository_path)
    current = repository / "current"
    manifest_path = current / "manifest.json"
    if not manifest_path.is_file():
        raise RepositoryBackupError("O snapshot não contém manifest.json.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "acl-editorial-backup":
        raise RepositoryBackupError("Formato de backup desconhecido.")
    for name, expected in manifest.get("files", {}).items():
        path = current / name
        if not path.is_file() or _sha256(path) != expected.get("sha256"):
            raise RepositoryBackupError(f"Falha de integridade no ficheiro {name}.")

    db_target = Path(db_path)
    db_target.parent.mkdir(parents=True, exist_ok=True)
    backup_root = db_target.parent / "restore-backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root.mkdir(parents=True)
    if db_target.exists():
        shutil.copy2(db_target, backup_root / db_target.name)
    _decompress_atomic(current / "editorial.sqlite.xz", db_target)
    with sqlite3.connect(db_target) as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RepositoryBackupError("O SQLite restaurado falhou o quick_check.")

    releases = Path(releases_root)
    releases.mkdir(parents=True, exist_ok=True)
    release_id = str(manifest["active_release"])
    with tarfile.open(current / "active-release.tar.xz", "r:xz") as archive:
        _safe_extract(archive, releases)
    (releases / "ACTIVE_RELEASE").write_text(release_id + "\n", encoding="utf-8")

    if usage_db and (current / "usage.sqlite.xz").is_file():
        _decompress_atomic(current / "usage.sqlite.xz", Path(usage_db))
    if env_target and (current / "runtime.env").is_file():
        shutil.copy2(current / "runtime.env", Path(env_target))
    return {
        "restored": True,
        "active_release": release_id,
        "backup_of_previous_state": str(backup_root),
    }


def _active_release(releases_root: Path) -> str | None:
    pointer = releases_root / "ACTIVE_RELEASE"
    return pointer.read_text(encoding="utf-8").strip() if pointer.is_file() else None


def _sqlite_backup(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise RepositoryBackupError(f"Base SQLite inexistente: {source}.")
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as origin:
        with sqlite3.connect(destination) as target:
            origin.backup(target)


def _compress(source: Path, destination: Path) -> None:
    with source.open("rb") as source_handle:
        with lzma.open(destination, "wb", preset=6) as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)


def _decompress_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".restoring")
    with lzma.open(source, "rb") as source_handle:
        with temporary.open("wb") as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
    os.replace(temporary, destination)


def _safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    destination_resolved = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if target != destination_resolved and destination_resolved not in target.parents:
            raise RepositoryBackupError("O arquivo da release contém um caminho inválido.")
    archive.extractall(destination, filter="data")


def _run_git(repository: Path, *arguments: str, check: bool = True):
    try:
        environment = dict(os.environ)
        environment.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": str(repository.resolve()),
        })
        return subprocess.run(
            ["git", *arguments],
            cwd=repository,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=600,
            check=check,
        )
    except FileNotFoundError as exc:
        raise RepositoryBackupError("Git não está instalado.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RepositoryBackupError(f"git {' '.join(arguments)}: {detail}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
