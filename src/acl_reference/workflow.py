from __future__ import annotations

import unicodedata


WORKFLOW_XML_STATUS = {
    "DRAFT": "draft",
    "EDITED": "edited",
    "REVIEWED": "reviewed",
    "NEEDS_REVISION": "needs revision",
    "VALIDATED": "validated",
    "PUBLISHED": "published",
    "REMOVED": "removed",
}


def _status_key(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.casefold().replace("_", " ").replace("-", " ").split())


_XML_WORKFLOW_ALIASES = {
    "": "DRAFT",
    "imported": "DRAFT",
    "importada": "DRAFT",
    "importado": "DRAFT",
    "new": "DRAFT",
    "nova": "DRAFT",
    "novo": "DRAFT",
    "draft": "DRAFT",
    "rascunho": "DRAFT",
    "edited": "EDITED",
    "editing": "EDITED",
    "editada": "EDITED",
    "editado": "EDITED",
    "review": "REVIEWED",
    "reviewed": "REVIEWED",
    "revised": "REVIEWED",
    "revista": "REVIEWED",
    "revisto": "REVIEWED",
    "revisada": "REVIEWED",
    "revisado": "REVIEWED",
    "needs revision": "NEEDS_REVISION",
    "precisa de revisao": "NEEDS_REVISION",
    "validated": "VALIDATED",
    "validada": "VALIDATED",
    "validado": "VALIDATED",
    "published": "PUBLISHED",
    "publicada": "PUBLISHED",
    "publicado": "PUBLISHED",
    "removed": "REMOVED",
    "deleted": "REMOVED",
    "apagada": "REMOVED",
    "apagado": "REMOVED",
}


def workflow_from_xml_status(value: str | None) -> str:
    """Converte o estado canónico/legado do XML no workflow da aplicação."""
    return _XML_WORKFLOW_ALIASES.get(_status_key(value), "DRAFT")


def xml_status_from_workflow(value: str) -> str:
    return WORKFLOW_XML_STATUS.get(value, str(value or "draft").casefold())


def workflow_origin_from_xml_status(value: str | None) -> str:
    return "new" if _status_key(value) in {"new", "nova", "novo"} else "imported"
