"""MySQL connection factory for DB-API drivers."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module

from post_bot.shared.errors import ExternalDependencyError


@dataclass(frozen=True, slots=True)
class MySQLSettings:
    host: str
    port: int
    user: str
    password: str
    database: str


class MySQLConnectionFactory:
    """Creates MySQL connections via mysql-connector-python driver."""

    def __init__(self, settings: MySQLSettings) -> None:
        self._settings = settings

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
