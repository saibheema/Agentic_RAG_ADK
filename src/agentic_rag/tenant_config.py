from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentic_rag.config import AppSettings


@dataclass(slots=True)
class TenantConfig:
    tenant_id: str
    db_type: str
    db_host: str = ""
    db_name: str = ""
    db_port: int = 0
    secret_name: str = ""
    rag_corpus_id: str = ""
    features: list[str] = field(default_factory=list)
    pii_rules: list[str] = field(default_factory=list)
    credentials: dict[str, Any] = field(default_factory=dict)


def _default_rules(settings: AppSettings) -> list[str]:
    raw = settings.pii_default_rules.strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_tenant_config(tenant_id: str, settings: AppSettings) -> TenantConfig:
    return TenantConfig(
        tenant_id=tenant_id,
        db_type=settings.tenant_default_db_type,
        secret_name=settings.tenant_default_secret_name,
        rag_corpus_id=settings.rag_corpus_name,
        features=["text_to_sql", "doc_rag"],
        pii_rules=_default_rules(settings),
    )


def _load_secret(secret_name: str) -> dict[str, Any]:
    if not secret_name:
        return {}

    try:
        from google.cloud import secretmanager
    except ImportError:
        return {}

    client = secretmanager.SecretManagerServiceClient()
    payload = client.access_secret_version(request={"name": f"{secret_name}/versions/latest"})
    raw = payload.payload.data.decode("utf-8")

    import json

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def resolve_tenant_config(tenant_id: str, settings: AppSettings) -> TenantConfig:
    if not settings.tenant_config_use_firestore:
        config = _default_tenant_config(tenant_id=tenant_id, settings=settings)
        config.credentials = _load_secret(config.secret_name)
        return config

    try:
        from google.cloud import firestore
    except ImportError:
        config = _default_tenant_config(tenant_id=tenant_id, settings=settings)
        config.credentials = _load_secret(config.secret_name)
        return config

    db = firestore.Client(project=settings.project_id or None)
    ref = db.collection("tenants").document(tenant_id)
    snap = ref.get()

    if not snap.exists:
        config = _default_tenant_config(tenant_id=tenant_id, settings=settings)
        config.credentials = _load_secret(config.secret_name)
        return config

    data = snap.to_dict() or {}
    config = TenantConfig(
        tenant_id=tenant_id,
        db_type=data.get("db_type", settings.tenant_default_db_type),
        db_host=data.get("db_host", ""),
        db_name=data.get("db_name", ""),
        db_port=int(data.get("db_port", 0) or 0),
        secret_name=data.get("secret_name", ""),
        rag_corpus_id=data.get("rag_corpus_id", settings.rag_corpus_name),
        features=list(data.get("features", ["text_to_sql", "doc_rag"])),
        pii_rules=list(data.get("pii_rules", _default_rules(settings))),
    )
    config.credentials = _load_secret(config.secret_name)
    return config
