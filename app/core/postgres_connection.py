"""Explicit PostgreSQL connection for existing-data operations; never substitute staging data."""
import os


def connect_postgres(*, readonly=True, configuration=None):
    import psycopg2

    config = os.environ if configuration is None else configuration
    dsn = config.get("NORMALIZATION_DATABASE_URL") or config.get("DATABASE_URL")
    if dsn:
        connection = psycopg2.connect(dsn, connect_timeout=5)
    else:
        required = ("DB_HOST", "DB_NAME", "DB_USER")
        if any(not config.get(key) for key in required):
            raise RuntimeError("Configure NORMALIZATION_DATABASE_URL or DB_HOST, DB_NAME and DB_USER; staging is not a database fallback")
        connection = psycopg2.connect(
            host=config["DB_HOST"], port=int(config.get("DB_PORT") or "5432"),
            dbname=config["DB_NAME"], user=config["DB_USER"],
            password=config.get("DB_PASSWORD") or config.get("DB_PASS") or "",
            connect_timeout=5,
        )
    connection.set_session(readonly=readonly, isolation_level="REPEATABLE READ" if readonly else "READ COMMITTED")
    return connection
