"""Centralised settings for Agentic RAG.

Only includes env vars that agent.py and supporting modules actually consume.
See .env.example for the full list with descriptions.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Cloud / Auth ─────────────────────────────────────────
    project_id: str = Field(default="", alias="GOOGLE_CLOUD_PROJECT")
    location: str = Field(default="us-central1", alias="GOOGLE_CLOUD_LOCATION")

    # ── Agent model ──────────────────────────────────────────
    agent_model: str = Field(default="gemini-3.0-flash-preview", alias="AGENT_MODEL")

    # ── Database ───────────────────────────────────────────────
    # DB_TYPE: "postgres" (default) or "mssql" for SQL Server
    db_type: str = Field(default="postgres", alias="DB_TYPE")
    db_name: str = Field(default="agentic_rag", alias="DB_NAME")
    db_user: str = Field(default="app_user", alias="DB_USER")
    db_password: str = Field(default="", alias="DB_PASSWORD")
    db_password_secret: str = Field(default="", alias="DB_PASSWORD_SECRET")
    db_host: str = Field(default="127.0.0.1", alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")  # 5432 for PG, 1433 for MSSQL

    # ── Text-to-SQL guardrails ───────────────────────────────
    allowed_tables: str = Field(default="orders,customers,products,order_items", alias="TEXT_TO_SQL_ALLOWED_TABLES")
    max_rows: int = Field(default=200, alias="TEXT_TO_SQL_MAX_ROWS")
    query_timeout_ms: int = Field(default=15000, alias="TEXT_TO_SQL_QUERY_TIMEOUT_MS")

    # ── PII masking ──────────────────────────────────────────
    pii_masking_enabled: bool = Field(default=True, alias="PII_MASKING_ENABLED")
    pii_use_presidio: bool = Field(default=False, alias="PII_USE_PRESIDIO")
    pii_default_rules: str = Field(default="name,ssn,email", alias="PII_DEFAULT_RULES")

    # ── RAG (Vertex AI RAG Engine) ───────────────────────────
    rag_corpus_name: str = Field(default="", alias="VERTEX_RAG_CORPUS")

    # ── Tenant config (future multi-tenant) ──────────────────
    tenant_config_use_firestore: bool = Field(default=False, alias="TENANT_CONFIG_USE_FIRESTORE")
    tenant_default_db_type: str = Field(default="postgres", alias="TENANT_DEFAULT_DB_TYPE")
    tenant_default_secret_name: str = Field(default="", alias="TENANT_DEFAULT_SECRET_NAME")


def get_settings() -> AppSettings:
    return AppSettings()
