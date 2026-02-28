# MCP Toolbox Config Templates

These `tools.*.yaml` files are **reference templates** for the
[MCP Toolbox for Databases](https://github.com/googleapis/genai-toolbox)
approach, where each SQL query is pre-defined as a named tool.

## Current status: NOT IN USE

The production system uses **direct Text-to-SQL** — the LLM generates SQL
dynamically from schema metadata, with guardrails enforced in `agent.py`
(read-only validation, blocked keywords, LIMIT injection, statement_timeout).

## When would you use these?

- If you want to restrict queries to a fixed set (maximum safety, less flexibility).
- If you deploy MCP Toolbox as a sidecar on Cloud Run alongside the ADK agent.
- If you migrate to a hybrid approach: pre-defined tools for common queries +
  Text-to-SQL fallback for ad-hoc questions.

## Available templates

| File | Database |
|------|----------|
| `tools.postgres.yaml` | Cloud SQL PostgreSQL |
| `tools.mssql.yaml` | SQL Server |
| `tools.mysql.yaml` | MySQL |
| `tools.bigquery.yaml` | BigQuery |
| `tools.spanner.yaml` | Cloud Spanner |
| `tools.alloydb-postgres.yaml` | AlloyDB |
| ... | See all files in this directory |
