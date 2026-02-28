from __future__ import annotations

import argparse
from pathlib import Path

from google.cloud.sql.connector import Connector, IPTypes
import pg8000


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Cloud SQL PostgreSQL with sample data")
    parser.add_argument("--instance-connection-name", required=True)
    parser.add_argument("--db-user", required=True)
    parser.add_argument("--db-password", required=True)
    parser.add_argument("--db-name", required=True)
    parser.add_argument(
        "--ip-type",
        choices=["PUBLIC", "PRIVATE"],
        default="PRIVATE",
        help="Cloud SQL connection IP type",
    )
    parser.add_argument(
        "--sql-file",
        default="sql/min_prod_seed.sql",
        help="Path to SQL file with schema and seed data",
    )
    args = parser.parse_args()

    sql_text = Path(args.sql_file).read_text(encoding="utf-8")

    connector = Connector()

    def getconn() -> pg8000.dbapi.Connection:
        ip_type = IPTypes.PRIVATE if args.ip_type == "PRIVATE" else IPTypes.PUBLIC
        return connector.connect(
            args.instance_connection_name,
            "pg8000",
            user=args.db_user,
            password=args.db_password,
            db=args.db_name,
            ip_type=ip_type,
        )

    conn = getconn()
    try:
        cur = conn.cursor()
        cur.execute(sql_text)
        conn.commit()
        print("Seed completed successfully.")
    finally:
        conn.close()
        connector.close()


if __name__ == "__main__":
    main()
