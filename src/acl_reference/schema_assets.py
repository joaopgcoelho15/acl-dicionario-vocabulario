from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.request import Request, urlopen


ACADEMIA_URL = "https://exist.dacl.zbr.pt/exist/rest/db/schemas/academia.rng"
TEILEX0_URL = "https://exist.dacl.zbr.pt/exist/rest/db/schemas/TEILex0.rng"


def schema_root() -> Path:
    return Path(__file__).resolve().parents[2] / "contracts" / "schemas"


def official_schema_path() -> Path | None:
    root = schema_root()
    main = root / "academia.rng"
    dependency = root / "TEILex0.rng"
    return main if main.is_file() and dependency.is_file() else None


def fetch_official_schema(target_root: str | Path | None = None) -> dict:
    root = Path(target_root) if target_root else schema_root()
    root.mkdir(parents=True, exist_ok=True)
    files = []
    for name, url in (("academia.rng", ACADEMIA_URL), ("TEILex0.rng", TEILEX0_URL)):
        request = Request(url, headers={"User-Agent": "ACL-Reference/1"})
        with urlopen(request, timeout=60) as response:
            body = response.read()
        if not body.lstrip().startswith(b"<"):
            raise RuntimeError(f"O recurso {name} não parece ser XML.")
        path = root / name
        temporary = root / f".{name}.tmp"
        temporary.write_bytes(body)
        temporary.replace(path)
        files.append(
            {
                "path": str(path.resolve()),
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "source": url,
            }
        )
    return {"schema": str((root / "academia.rng").resolve()), "files": files}
