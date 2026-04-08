"""MySQL connection factory for DB-API drivers."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from urllib.parse import unquote, urlparse

from post_bot.shared.errors import ExternalDependencyError, ValidationError

@dataclass(frozen=True, slots=True)
class MySQLSettings:
    host: str
    port: int
    user: str
    password: str
    database: str

def parse_mysql_dsn(dsn: str) -> MySQLSettings:
    parsed = urlparse(dsn)
    if parsed.scheme != "mysql":
        raise ValidationError(
            code="MYSQL_DSN_SCHEME_INVALID",
            message="DATABASE_DSN must use mysql:// scheme.",
            details={"dsn": dsn},
        )
    if not parsed.hostname or not parsed.username or not parsed.path:
        raise ValidationError(
            code="MYSQL_DSN_INVALID",
            message="DATABASE_DSN is missing required parts.",
            details={"dsn": dsn},
        )

    database = parsed.path.lstrip("/")
    if not database:
        raise ValidationError(
            code="MYSQL_DSN_DATABASE_MISSING",
            message="DATABASE_DSN must include a database name.",
            details={"dsn": dsn},
        )

    return MySQLSettings(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=unquote(parsed.username),
        password=unquote(parsed.password or ""),
        database=database,
    )

class MySQLConnectionFactory:
    """Creates MySQL connections via mysql-connector-python driver."""

    def __init__(self, dsn: str) -> None:
        self._settings = parse_mysql_dsn(dsn)

    def create(self):
        try:
            mysql_connector = import_module("mysql.connector")
        except ModuleNotFoundError as exc:
            raise ExternalDependencyError(
                code="MYSQL_DRIVER_MISSING",
                message="mysql.connector is required for MySQL connections.",
                retryable=False,
            ) from exc

        return mysql_connector.connect(
            host=self._settings.host,
            port=self._settings.port,
            user=self._settings.user,
            password=self._settings.password,
            database=self._settings.database,
            autocommit=False,
        )