"""
Lightweight MariaDB driver shim backed by PyMySQL.

Provides a minimal mariadb-compatible surface:
- connect(...): returns a wrapped PyMySQL connection
- Error (alias to PyMySQL MySQLError)

The wrapper translates DB-API qmark-style placeholders ('?')
to PyMySQL's format-style ('%s') in execute/executemany, so
existing SQL strings in the codebase do not need to change.

Note: This shim is intentionally minimal for test/runtime compatibility.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

try:  # Lazy/optional dependency for environments without PyMySQL
    import pymysql as _pymysql  # type: ignore

    _HAVE_PYMYSQL = True
    Error = _pymysql.MySQLError  # type: ignore
except Exception:  # pragma: no cover - allow import even if PyMySQL missing
    _pymysql = None  # type: ignore
    _HAVE_PYMYSQL = False

    # Provide a fallback Error type so tests can import mariadb.Error
    class Error(Exception):  # type: ignore
        pass


_QMARK_RE = re.compile(r"\?")
_PERCENT_NOT_PLACEHOLDER_RE = re.compile(r"%(?!s)")


def _translate_qmark(sql: str) -> str:
    # Replace qmark placeholders with %s expected by PyMySQL
    translated = _QMARK_RE.sub("%s", sql)
    # Escape literal percent signs so PyMySQL's percent-formatting doesn't
    # interpret them as placeholders (e.g., LIKE '%foo%').
    translated = _PERCENT_NOT_PLACEHOLDER_RE.sub("%%", translated)
    return translated


class _CursorWrapper:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def execute(
        self,
        query: str,
        args: Optional[Union[Sequence[Any], Mapping[str, Any]]] = None,
    ) -> Any:
        return self._inner.execute(_translate_qmark(query), args)

    def executemany(
        self,
        query: str,
        args: Iterable[Union[Sequence[Any], Mapping[str, Any]]],
    ) -> Any:
        return self._inner.executemany(_translate_qmark(query), args)

    def fetchone(self):
        return self._inner.fetchone()

    def fetchall(self):
        return self._inner.fetchall()

    def close(self) -> None:
        return self._inner.close()

    def __iter__(self):
        # Delegate to inner iterator if available; otherwise, yield rows via fetchone
        try:
            return iter(self._inner)
        except TypeError:

            def _gen():
                while True:
                    row = self._inner.fetchone()
                    if not row:
                        break
                    yield row

            return _gen()

    # Context manager support ensures cursors are always closed
    def __enter__(self) -> "_CursorWrapper":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.close()
        except Exception:
            pass

    # Fallback: attempt graceful close on GC
    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        try:
            self.close()
        except Exception:
            pass


class _ConnWrapper:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        # Compatibility no-op attribute expected by existing code
        self.auto_reconnect = True

    def cursor(self) -> _CursorWrapper:
        return _CursorWrapper(self._inner.cursor())

    def commit(self) -> None:
        return self._inner.commit()

    def rollback(self) -> None:
        return self._inner.rollback()

    def close(self) -> None:
        return self._inner.close()

    def ping(self, reconnect: bool = False) -> None:
        return self._inner.ping(reconnect=reconnect)

    # Context manager support ensures connections are closed on exit
    def __enter__(self) -> "_ConnWrapper":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # On context exit, try to commit if no exception; otherwise rollback
        try:
            if exc_type is None:
                try:
                    self._inner.commit()
                except Exception:
                    pass
            else:
                try:
                    self._inner.rollback()
                except Exception:
                    pass
        finally:
            try:
                self.close()
            except Exception:
                pass

    # Fallback: attempt graceful close on GC
    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        try:
            self.close()
        except Exception:
            pass


def connect(
    *,
    user: Optional[str] = None,
    password: Optional[str] = None,
    host: Optional[str] = None,
    port: int = 3306,
    database: Optional[str] = None,
    charset: str = "utf8mb4",
    autocommit: bool = False,
    **kwargs: Any,
) -> _ConnWrapper:
    if not _HAVE_PYMYSQL:
        raise ImportError("PyMySQL is required for mariadb shim but is not installed")
    conn = _pymysql.connect(  # type: ignore[attr-defined]
        user=user,
        password=password,
        host=host,
        port=port,
        db=database,
        charset=charset,
        autocommit=autocommit,
        **kwargs,
    )
    # Keep the connection alive (optional)
    try:
        conn.ping(reconnect=True)
    except Exception:
        pass
    return _ConnWrapper(conn)
