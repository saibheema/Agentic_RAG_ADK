# Implementation & Testing Plan

## Scope

- Phase 1: Implement PostgreSQL-first Agentic RAG with Python 3.12+.
- Phase 2: Add SQL Server support with behavior parity and no core router rewrite.

## Phase 1 — PostgreSQL implementation

### Deliverables

1. Router/DB/RAG agent runtime in Python.
2. MCP toolbox setup with PostgreSQL read-only tools (`tools.postgres.yaml`).
3. Tenant-aware request envelope (tenant id passed through route handlers).
4. PII masking middleware contract (to be plugged in after DB response).
5. Vertex AI RAG corpus integration contract.

### Work breakdown

1. Bootstrap app skeleton and route logic.
2. Connect `DatabaseAgent` to MCP endpoint and implement tool call adapters.
3. Connect `RagAgent` to Vertex AI RAG corpus retrieval.
4. Add policy/guardrails:
   - read-only tools only,
   - query size and row limits,
   - allowlisted schemas/tables,
   - prompt injection checks before tool execution.
5. Add observability and request tracing (route, tool, latency, tenant).

## Phase 1 — testing strategy (PostgreSQL)

### Unit tests

- Query classification and route selection.
- Guardrail logic (disallowed keywords, over-limit requests).
- Response normalization and masking integration points.

### Integration tests

- Router -> DB agent -> MCP toolbox -> PostgreSQL (seed database).
- Router -> RAG agent -> Vertex RAG retrieval path.
- Secret/config loading for local dev and CI.

### Security tests

- Prompt injection attempts against DB tools.
- Unauthorized schema/table access attempts.
- PII leakage tests pre/post masking stage.

### Non-functional tests

- p50/p95 latency by route.
- Concurrency smoke tests.
- Timeout and retry behavior.

### Exit criteria

- 100% pass for critical route + guardrail tests.
- No write query execution path.
- Stable integration test run in CI.

## Phase 2 — SQL Server plan

### Migration approach

Keep application layer stable (`RouterAgent`, contracts, test harness) and swap:

1. MCP datasource configuration from PostgreSQL to SQL Server.
2. Tool SQL from PostgreSQL dialect to SQL Server dialect.
3. SQL Server authentication/network configuration.

### SQL Server-specific implementation tasks

1. Create `config/tools.sqlserver.yaml` with read-only allowlisted queries.
2. Add dialect adapter for pagination/date/null differences where needed.
3. Add SQL Server connection settings and secret references.
4. Validate collation/case-sensitivity assumptions.

### SQL Server testing (parity suite)

1. Run full Phase 1 suite against SQL Server.
2. Add cross-DB parity tests for canonical prompts.
3. Compare semantic equivalence of responses and acceptable tolerance ranges.
4. Benchmark latency deltas and update SLO thresholds.

### SQL Server exit criteria

- Phase 1 critical tests pass unchanged.
- Cross-DB parity thresholds met.
- No new high-severity security findings.
