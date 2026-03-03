"""Seed a SQL Server database with sample data.

Usage:
    python scripts/seed_mssql.py \
        --db-host=your-server.database.windows.net \
        --db-user=sa \
        --db-password='YOUR_PASSWORD' \
        --db-name=agentic_rag \
        --sql-file=sql/min_prod_seed_mssql.sql
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pymssql  # type: ignore[import-untyped]


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed SQL Server with sample data")
    parser.add_argument("--db-host", required=True, help="SQL Server hostname or IP")
    parser.add_argument("--db-port", type=int, default=1433, help="SQL Server port (default: 1433)")
    parser.add_argument("--db-user", required=True)
    parser.add_argument("--db-password", required=True)
    parser.add_argument("--db-name", required=True)
    parser.add_argument(
        "--sql-file",
        default="sql/min_prod_seed_mssql.sql",
        help="Path to SQL file with schema and seed data",
    )
    args = parser.parse_args()

    sql_text = Path(args.sql_file).read_text(encoding="utf-8")

    conn = pymssql.connect(
        server=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=args.db_password,
        database=args.db_name,
    )
    try:
        cur = conn.cursor()
        # pymssql can handle multi-statement batches separated by GO
        # but our seed file uses semicolons, so execute as one batch
        cur.execute(sql_text)
        conn.commit()
        print("SQL Server seed completed successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
