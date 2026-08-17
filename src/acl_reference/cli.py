from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .api import serve
from .acceptance import run_search_acceptance
from .editorial_db import initialize
from .editorial_app import serve_editorial
from .external_sources import set_source_publication, source_publication_status
from .importer import import_xml
from .meili import MeiliClient
from .publication_jobs import PublicationJobManager
from .publication import (
    approve_release,
    build_release,
    verify_release,
)
from .schema_assets import fetch_official_schema, official_schema_path
from .spe_importer import import_spe
from .validation import validate_active_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acl-reference")
    parser.add_argument("--db", default=os.getenv("EDITORIAL_DB", "var/editorial.sqlite"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("fetch-schema")

    acceptance = sub.add_parser("acceptance-test")
    acceptance.add_argument("--base-url", default="http://127.0.0.1:8090")
    acceptance.add_argument("--cases")

    validate = sub.add_parser("validate")
    validate.add_argument("--rng-path")

    import_command = sub.add_parser("import-xml")
    import_command.add_argument("source")
    import_command.add_argument("--limit", type=int)

    spe = sub.add_parser("import-spe")
    spe.add_argument("source")

    external = sub.add_parser("external-source")
    external.add_argument("code", nargs="?")
    external.add_argument("--actor", default="aprovador.demo")
    state = external.add_mutually_exclusive_group()
    state.add_argument("--enable", action="store_true")
    state.add_argument("--defer", action="store_true")
    external.add_argument("--comment", default="")

    build = sub.add_parser("build-release")
    build.add_argument("--release-id")
    build.add_argument(
        "--releases-root", default=os.getenv("RELEASES_ROOT", "releases")
    )
    build.add_argument("--images-root")
    build.add_argument("--actor", default="editor.demo")
    build.add_argument("--description", default="Versão candidata")
    build.add_argument("--rng-path")
    build.add_argument("--resume", action="store_true")

    verify = sub.add_parser("verify-release")
    verify.add_argument("path")

    index = sub.add_parser("index-release")
    index.add_argument("path")
    index.add_argument("--meili-url", default=os.getenv("MEILI_URL", "http://127.0.0.1:7700"))
    index.add_argument("--meili-key", default=os.getenv("MEILI_MASTER_KEY", "acl-local-development-key"))

    bootstrap = sub.add_parser("bootstrap-release")
    bootstrap.add_argument("path")
    bootstrap.add_argument("--meili-url", default=os.getenv("MEILI_URL", "http://127.0.0.1:7700"))
    bootstrap.add_argument("--meili-key", default=os.getenv("MEILI_MASTER_KEY", "acl-local-development-key"))

    publish = sub.add_parser("publish-release")
    publish.add_argument("release_id")
    publish.add_argument("--actor", required=True)
    publish.add_argument(
        "--releases-root", default=os.getenv("RELEASES_ROOT", "releases")
    )
    publish.add_argument("--meili-url", default=os.getenv("MEILI_URL", "http://127.0.0.1:7700"))
    publish.add_argument("--meili-key", default=os.getenv("MEILI_MASTER_KEY", "acl-local-development-key"))

    rollback = sub.add_parser("rollback-release")
    rollback.add_argument("release_id")
    rollback.add_argument("--actor", required=True)
    rollback.add_argument("--comment", required=True)
    rollback.add_argument(
        "--releases-root", default=os.getenv("RELEASES_ROOT", "releases")
    )
    rollback.add_argument("--meili-url", default=os.getenv("MEILI_URL", "http://127.0.0.1:7700"))
    rollback.add_argument("--meili-key", default=os.getenv("MEILI_MASTER_KEY", "acl-local-development-key"))

    approve = sub.add_parser("approve-release")
    approve.add_argument("release_id")
    approve.add_argument("--actor", required=True)
    approve.add_argument("--comment", default="")
    approve.add_argument(
        "--releases-root", default=os.getenv("RELEASES_ROOT", "releases")
    )

    public = sub.add_parser("serve")
    public.add_argument("--host", default=os.getenv("PUBLIC_HOST", "127.0.0.1"))
    public.add_argument("--port", type=int, default=int(os.getenv("PUBLIC_PORT", "8090")))
    public.add_argument("--meili-url", default=os.getenv("MEILI_URL", "http://127.0.0.1:7700"))
    public.add_argument("--meili-key", default=os.getenv("MEILI_MASTER_KEY", "acl-local-development-key"))
    public.add_argument(
        "--releases-root", default=os.getenv("RELEASES_ROOT", "releases")
    )
    public.add_argument(
        "--base-path", default=os.getenv("PUBLIC_BASE_PATH", "")
    )
    public.add_argument("--usage-db", default=os.getenv("USAGE_DB"))

    editor = sub.add_parser("serve-editorial")
    editor.add_argument("--host", default="127.0.0.1")
    editor.add_argument("--port", type=int, default=8089)
    editor.add_argument(
        "--releases-root", default=os.getenv("RELEASES_ROOT", "releases")
    )
    editor.add_argument("--images-root")
    editor.add_argument("--rng-path")
    editor.add_argument(
        "--base-path", default=os.getenv("EDITORIAL_BASE_PATH", "")
    )
    editor.add_argument(
        "--password", default=os.getenv("EDITORIAL_PASSWORD", "ACL")
    )
    editor.add_argument(
        "--meili-url",
        default=os.getenv("MEILI_URL", "http://127.0.0.1:7700"),
    )
    editor.add_argument(
        "--meili-key",
        default=os.getenv(
            "MEILI_MASTER_KEY", "acl-local-development-key"
        ),
    )

    args = parser.parse_args(argv)
    if args.command == "init-db":
        initialize(args.db)
        print(Path(args.db).resolve())
    elif args.command == "fetch-schema":
        print(json.dumps(fetch_official_schema(), ensure_ascii=False, indent=2))
    elif args.command == "acceptance-test":
        result = run_search_acceptance(args.base_url, args.cases)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    elif args.command == "validate":
        rng = args.rng_path or official_schema_path()
        print(
            json.dumps(
                validate_active_run(args.db, rng_path=rng).as_dict(),
                ensure_ascii=False,
            )
        )
    elif args.command == "import-xml":
        result = import_xml(args.source, args.db, limit=args.limit)
        print(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "imported": result.imported,
                    "errors": result.errors,
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "import-spe":
        result = import_spe(args.source, args.db)
        print(
            json.dumps(
                {
                    "source_records": result.source_records,
                    "portuguese_terms": result.portuguese_terms,
                    "imported": result.imported,
                    "unmatched": result.unmatched,
                    "source_sha256": result.source_sha256,
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "external-source":
        if args.code and (args.enable or args.defer):
            value = set_source_publication(
                args.db,
                args.code,
                enabled=args.enable,
                actor=args.actor,
                comment=args.comment,
            )
        elif args.code:
            values = source_publication_status(args.db)
            value = next((item for item in values if item["code"] == args.code), None)
            if value is None:
                raise ValueError(f"Fonte externa inexistente: {args.code}")
        else:
            value = source_publication_status(args.db)
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif args.command == "build-release":
        result = build_release(
            args.db,
            args.releases_root,
            release_id=args.release_id,
            images_root=args.images_root,
            prepared_by=args.actor,
            description=args.description,
            rng_path=args.rng_path or official_schema_path(),
            resume=args.resume,
        )
        print(
            json.dumps(
                {
                    "release_id": result.release_id,
                    "path": str(result.path.resolve()),
                    "entries": result.entries,
                    "errors": result.errors,
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "verify-release":
        result = verify_release(args.path)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["valid"] else 1
    elif args.command == "index-release":
        result = MeiliClient(args.meili_url, args.meili_key).build_release_indexes(args.path)
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "bootstrap-release":
        verification = verify_release(args.path)
        if not verification["valid"]:
            print(json.dumps(verification, ensure_ascii=False))
            return 1
        result = MeiliClient(args.meili_url, args.meili_key).index_release(args.path)
        print(
            json.dumps(
                {"verification": verification, "indexation": result},
                ensure_ascii=False,
            )
        )
    elif args.command in {"publish-release", "rollback-release"}:
        manager = PublicationJobManager(
            db_path=args.db,
            releases_root=args.releases_root,
            images_root=None,
            meili_url=args.meili_url,
            meili_key=args.meili_key,
        )
        print(
            json.dumps(
                manager.run_synchronously(
                    args.release_id,
                    actor=args.actor,
                    rollback=args.command == "rollback-release",
                    comment=getattr(args, "comment", ""),
                ),
                ensure_ascii=False,
            )
        )
    elif args.command == "approve-release":
        print(
            json.dumps(
                approve_release(
                    args.db,
                    args.releases_root,
                    args.release_id,
                    actor=args.actor,
                    comment=args.comment,
                ),
                ensure_ascii=False,
            )
        )
    elif args.command == "serve":
        serve(
            meili_url=args.meili_url,
            meili_key=args.meili_key,
            releases_root=args.releases_root,
            host=args.host,
            port=args.port,
            base_path=args.base_path,
            usage_db=args.usage_db,
        )
    elif args.command == "serve-editorial":
        serve_editorial(
            args.db,
            releases_root=args.releases_root,
            images_root=args.images_root,
            meili_url=args.meili_url,
            meili_key=args.meili_key,
            rng_path=args.rng_path or official_schema_path(),
            host=args.host,
            port=args.port,
            base_path=args.base_path,
            password=args.password,
        )
    return 0
