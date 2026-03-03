"""Provision Secret Manager secrets for DB passwords.

Usage:
    python scripts/provision_secrets.py

Requires Application Default Credentials with Secret Manager access:
    gcloud auth application-default login
"""
from __future__ import annotations

PROJECT = "unicon-494419"

SECRETS = [
    # (secret_id, password_value)
    ("yisbeta-db-password", "3vAnalyst3*"),
    ("local-pg-password", ""),          # no password for local PG — empty
]


def main() -> None:
    from google.cloud import secretmanager  # type: ignore[import-untyped]

    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{PROJECT}"

    for secret_id, value in SECRETS:
        # ── Create secret ────────────────────────────────────────────────────
        try:
            secret = client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
            print(f"✓ Created  {secret.name}")
        except Exception as exc:
            if "already exists" in str(exc).lower():
                print(f"  Secret {secret_id!r} already exists — adding new version")
            else:
                print(f"✗ Error creating {secret_id!r}: {exc}")
                continue

        # ── Add version ──────────────────────────────────────────────────────
        if value:
            ver = client.add_secret_version(
                request={
                    "parent": f"{parent}/secrets/{secret_id}",
                    "payload": {"data": value.encode("utf-8")},
                }
            )
            print(f"  ↳ Version added: {ver.name}")
        else:
            print(f"  ↳ No password for {secret_id!r} — secret created (no version).")

    print("\nDone. Secret paths to use in connections.json:")
    for secret_id, _ in SECRETS:
        print(f"  projects/{PROJECT}/secrets/{secret_id}/versions/latest")


if __name__ == "__main__":
    main()
