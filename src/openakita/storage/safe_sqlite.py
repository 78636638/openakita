"""Centralised, fault-tolerant SQLite open helpers.

Every long-lived SQLite database the backend touches (memory store, token
tracking, asset bus, feedback store, scheduler etc.) should be opened
through one of the helpers in this module so we get a uniform safety
net:

* ``PRAGMA busy_timeout`` set explicitly (so concurrent writers fail
  with a sane error instead of hanging the event loop).
* Optional ``PRAGMA journal_mode=WAL`` for readers-don't-block-writers.
* ``PRAGMA quick_check`` runs before any DDL/DML touches the file, so
  we detect corruption at open time instead of mid-query.
* Sidecar (``-wal`` / ``-journal``) "hot journal orphan" detection,
  which catches the specific pattern where a previous process crashed
  and left a near-empty main file alongside a fat WAL.
* Windows-friendly retry loop for the brief window where another
  process holds the file open exclusively.

Failures are surfaced as :class:`SQLiteUnavailable` with a structured
``reason`` aligned to ``memory.MemoryStorageUnavailable`` so callers
can decide whether to degrade, retry, or escalate. The async helpers
are offered in two flavours — a plain ``async def`` returning a raw
connection (caller owns ``close()``) and an ``@asynccontextmanager``
wrapper for ``async with`` callers (e.g. existing ``aiosqlite.connect``
usages migrating in place).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import time
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

__all__ = [
    "SQLiteUnavailable",
    "safe_open_sync",
    "safe_open_async",
    "safe_open_async_ctx",
    "quick_check_or_raise_sync",
    "quick_check_or_raise_async",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


@dataclass
class SQLiteUnavailable(Exception):
    """Raised when a SQLite database cannot be opened safely.

    The ``reason`` field is intentionally a string enum (not a Python
    ``enum.Enum``) so it stays JSON-serialisable for ``/api/health``
    payloads and the degraded-registry. Aligned with
    :class:`openakita.memory.exceptions.MemoryStorageUnavailable`.

    Known reasons:

    * ``corrupted`` -- ``PRAGMA quick_check`` returned anything but ``ok``.
    * ``hot_journal_orphan`` -- main file looks empty but the sidecar
      ``-wal``/``-journal`` is sizeable; almost always indicates the
      previous process crashed mid-checkpoint and left an unreadable pair.
    * ``disk_full`` -- ``ENOSPC`` (errno 28) or SQLite "disk is full".
    * ``permission_denied`` -- ``EACCES`` (errno 13).
    * ``filesystem_error`` -- any other ``OSError``.
    * ``path_in_sync_folder`` -- path contains Dropbox/OneDrive/GoogleDrive
      markers or a UNC prefix; SQLite WAL is unsafe there.
    * ``schema_init_failed`` -- caller-side DDL (CREATE TABLE etc.) raised.
    * ``unknown_db_error`` -- catch-all.
    """

    reason: str
    path: Path | None = None
    details: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        suffix = f": {self.details}" if self.details else ""
        path_part = f" (path={self.path})" if self.path else ""
        return f"{self.reason}{path_part}{suffix}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SYNC_FOLDER_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive")
_QUICK_CHECK_STAMP_VERSION = 1


# #region debug-point A:sqlite-debug-report
def _debug_report(hypothesis_id: str, location: str, msg: str, data: dict[str, Any]) -> None:
    _env = Path(".dbg/sqlite-quick-check-block.env")
    _url = "http://127.0.0.1:7777/event"
    _session = "sqlite-quick-check-block"
    try:
        if _env.exists():
            for _line in _env.read_text(encoding="utf-8").splitlines():
                if _line.startswith("DEBUG_SERVER_URL="):
                    _url = _line.split("=", 1)[1].strip() or _url
                elif _line.startswith("DEBUG_SESSION_ID="):
                    _session = _line.split("=", 1)[1].strip() or _session
        _payload = {
            "sessionId": _session,
            "runId": os.environ.get("OPENAKITA_DEBUG_RUN_ID", "pre-fix"),
            "hypothesisId": hypothesis_id,
            "location": location,
            "msg": msg,
            "data": data,
            "ts": int(time.time() * 1000),
        }
        urllib.request.urlopen(
            urllib.request.Request(
                _url,
                data=json.dumps(_payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            ),
            timeout=0.8,
        ).read()
    except Exception:
        return


# #endregion


def _is_sync_folder_path(path: Path) -> bool:
    """Return ``True`` for paths that live inside a cloud sync folder.

    SQLite WAL is unsafe on Dropbox/OneDrive/GoogleDrive because the
    sync agent may rename or copy ``-wal``/``-shm`` files mid-write,
    corrupting the database. UNC paths (``\\\\server\\share\\...``) are
    treated the same way to be conservative.

    Override with ``OPENAKITA_ALLOW_SYNC_FOLDER_DB=1`` for power users
    who accept the risk.
    """
    text = str(path).lower()
    if os.environ.get("OPENAKITA_ALLOW_SYNC_FOLDER_DB") == "1":
        return False
    return any(marker in text for marker in _SYNC_FOLDER_MARKERS) or text.startswith("\\\\")


def _looks_like_corruption(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "malformed" in msg
        or "corrupt" in msg
        or "not a database" in msg
        or "file is not a database" in msg
        or "database disk image is malformed" in msg
    )


def _classify_oserror(exc: OSError) -> str:
    errno = getattr(exc, "errno", None)
    if errno == 13:  # EACCES
        return "permission_denied"
    if errno == 28:  # ENOSPC
        return "disk_full"
    return "filesystem_error"


def _hot_journal_orphan(path: Path) -> tuple[int, int] | None:
    """Detect the "empty DB + fat WAL/journal" failure mode.

    Returns ``(main_size, sidecar_size)`` when a hot journal orphan is
    detected, or ``None`` otherwise. We require main < 1KB and the
    sidecar to be at least 64KB to fire — small thresholds tuned to
    avoid false positives on freshly-created DBs.
    """
    try:
        main_size = path.stat().st_size if path.exists() else 0
    except OSError:
        return None
    if main_size >= 1024:
        return None

    for sidecar_suffix in ("-wal", "-journal"):
        side = Path(str(path) + sidecar_suffix)
        try:
            if side.exists() and side.stat().st_size >= 64 * 1024:
                return main_size, side.stat().st_size
        except OSError:
            continue
    return None


def _quick_check_stamp_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.quickcheck.json")


def _file_fingerprint(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"exists": False}
    return {
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _sidecar_fingerprint(path: Path) -> dict[str, Any]:
    payload = _file_fingerprint(path)
    if not payload.get("exists"):
        return {"exists": False}
    if int(payload.get("size", 0) or 0) <= 0:
        return {"exists": False}
    return payload


def _build_quick_check_fingerprint(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "main": _file_fingerprint(path),
    }


def _load_quick_check_stamp(path: Path) -> dict[str, Any] | None:
    stamp_path = _quick_check_stamp_path(path)
    try:
        payload = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _store_quick_check_stamp(path: Path) -> None:
    stamp_path = _quick_check_stamp_path(path)
    payload = {
        "version": _QUICK_CHECK_STAMP_VERSION,
        "checked_at": time.time(),
        "fingerprint": _build_quick_check_fingerprint(path),
    }
    temp_path = stamp_path.with_name(f"{stamp_path.name}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temp_path.replace(stamp_path)
    except OSError:
        with contextlib.suppress(OSError):
            temp_path.unlink()


def _clear_quick_check_stamp(path: Path) -> None:
    with contextlib.suppress(OSError):
        _quick_check_stamp_path(path).unlink()


def _should_skip_quick_check(path: Path) -> bool:
    if os.environ.get("OPENAKITA_FORCE_SQLITE_QUICK_CHECK") == "1":
        return False
    payload = _load_quick_check_stamp(path)
    if not isinstance(payload, dict):
        return False
    if payload.get("version") != _QUICK_CHECK_STAMP_VERSION:
        return False
    return payload.get("fingerprint") == _build_quick_check_fingerprint(path)


def _quick_check_cache_state(path: Path) -> dict[str, Any]:
    payload = _load_quick_check_stamp(path) or {}
    current = _build_quick_check_fingerprint(path)
    stamp_fingerprint = payload.get("fingerprint") if isinstance(payload, dict) else None
    return {
        "force_full_check": os.environ.get("OPENAKITA_FORCE_SQLITE_QUICK_CHECK") == "1",
        "stamp_exists": bool(payload),
        "stamp_version": payload.get("version") if isinstance(payload, dict) else None,
        "stamp_main": (stamp_fingerprint or {}).get("main", {}) if isinstance(stamp_fingerprint, dict) else {},
        "current_main": current.get("main", {}),
        "matches": stamp_fingerprint == current if isinstance(stamp_fingerprint, dict) else False,
    }


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------


def quick_check_or_raise_sync(conn: sqlite3.Connection, path: Path | None = None) -> None:
    """Run ``PRAGMA quick_check`` on an open connection.

    Lifted from ``MemoryStorage.quick_check_or_raise`` so we only have
    one implementation. Existing ``MemoryStorage`` code keeps its own
    method as a thin wrapper for backwards compatibility.
    """
    _started_at = time.perf_counter()
    _path = Path(path) if path is not None else None
    _main_size = _path.stat().st_size if _path and _path.exists() else None
    _wal = Path(f"{_path}-wal") if _path else None
    _wal_size = _wal.stat().st_size if _wal and _wal.exists() else None
    _shm = Path(f"{_path}-shm") if _path else None
    _shm_size = _shm.stat().st_size if _shm and _shm.exists() else None
    # #region debug-point A:quick-check-start
    _debug_report(
        "A",
        "openakita.storage.safe_sqlite:quick_check_or_raise_sync:start",
        "[DEBUG] sqlite quick_check start",
        {
            "path": str(_path) if _path else "",
            "main_size": _main_size,
            "wal_size": _wal_size,
            "shm_size": _shm_size,
        },
    )
    # #endregion
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as e:
        # #region debug-point B:quick-check-db-error
        _debug_report(
            "B",
            "openakita.storage.safe_sqlite:quick_check_or_raise_sync:error",
            "[DEBUG] sqlite quick_check database error",
            {
                "path": str(_path) if _path else "",
                "elapsed_ms": round((time.perf_counter() - _started_at) * 1000, 2),
                "error": str(e),
            },
        )
        # #endregion
        if _looks_like_corruption(e):
            raise SQLiteUnavailable("corrupted", path=path, details=str(e)) from e
        raise
    result = str(row[0] if row else "").strip().lower()
    # #region debug-point A:quick-check-finish
    _debug_report(
        "A",
        "openakita.storage.safe_sqlite:quick_check_or_raise_sync:finish",
        "[DEBUG] sqlite quick_check finished",
        {
            "path": str(_path) if _path else "",
            "elapsed_ms": round((time.perf_counter() - _started_at) * 1000, 2),
            "result": result,
        },
    )
    # #endregion
    if result != "ok":
        raise SQLiteUnavailable("corrupted", path=path, details=result or "quick_check failed")


def safe_open_sync(
    path: str | Path,
    *,
    want_wal: bool = True,
    busy_ms: int = 30_000,
    run_quick_check: bool = True,
    foreign_keys: bool = True,
    check_same_thread: bool = False,
    windows_retry: int = 3,
    windows_retry_sleep_s: float = 0.2,
) -> sqlite3.Connection:
    """Open a SQLite database safely (synchronous flavour).

    Returns an already-pragma'd ``sqlite3.Connection``. On any failure
    detected during PRAGMA / quick_check, the partial connection is
    closed and ``SQLiteUnavailable`` is raised. Callers are responsible
    for ``conn.close()`` on success.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # #region debug-point D:safe-open-start
    _debug_report(
        "D",
        "openakita.storage.safe_sqlite:safe_open_sync:start",
        "[DEBUG] sqlite safe_open_sync start",
        {
            "path": str(p),
            "want_wal": want_wal,
            "busy_ms": busy_ms,
            "run_quick_check": run_quick_check,
            "main_exists": p.exists(),
            "main_size": p.stat().st_size if p.exists() else None,
        },
    )
    # #endregion

    if _is_sync_folder_path(p):
        raise SQLiteUnavailable("path_in_sync_folder", path=p, details=str(p))

    orphan = _hot_journal_orphan(p)
    if orphan is not None:
        main_size, side_size = orphan
        raise SQLiteUnavailable(
            "hot_journal_orphan",
            path=p,
            details=f"main={main_size}B sidecar={side_size}B",
            extra={"main_size": main_size, "sidecar_size": side_size},
        )

    last_exc: BaseException | None = None
    for _attempt in range(max(1, windows_retry)):
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(p), check_same_thread=check_same_thread)
            conn.execute(f"PRAGMA busy_timeout={int(busy_ms)}")
            if want_wal:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            if foreign_keys:
                conn.execute("PRAGMA foreign_keys=ON")
            if run_quick_check:
                if _should_skip_quick_check(p):
                    # #region debug-point D:quick-check-skip
                    _debug_report(
                        "D",
                        "openakita.storage.safe_sqlite:safe_open_sync:quick_check_skipped",
                        "[DEBUG] sqlite quick_check skipped via cache",
                        {"path": str(p)},
                    )
                    # #endregion
                else:
                    # #region debug-point D:quick-check-cache-miss
                    _debug_report(
                        "D",
                        "openakita.storage.safe_sqlite:safe_open_sync:quick_check_cache_miss",
                        "[DEBUG] sqlite quick_check cache miss",
                        {"path": str(p), **_quick_check_cache_state(p)},
                    )
                    # #endregion
                    quick_check_or_raise_sync(conn, path=p)
                    _store_quick_check_stamp(p)
            # #region debug-point D:safe-open-success
            _debug_report(
                "D",
                "openakita.storage.safe_sqlite:safe_open_sync:success",
                "[DEBUG] sqlite safe_open_sync success",
                {
                    "path": str(p),
                    "run_quick_check": run_quick_check,
                },
            )
            # #endregion
            return conn
        except SQLiteUnavailable:
            _clear_quick_check_stamp(p)
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
            raise
        except sqlite3.DatabaseError as e:
            # #region debug-point B:safe-open-db-error
            _debug_report(
                "B",
                "openakita.storage.safe_sqlite:safe_open_sync:db_error",
                "[DEBUG] sqlite safe_open_sync database error",
                {
                    "path": str(p),
                    "error": str(e),
                },
            )
            # #endregion
            _clear_quick_check_stamp(p)
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
            if _looks_like_corruption(e):
                raise SQLiteUnavailable("corrupted", path=p, details=str(e)) from e
            msg = str(e).lower()
            if "disk i/o error" in msg or "disk is full" in msg:
                raise SQLiteUnavailable("disk_full", path=p, details=str(e)) from e
            if "locked" in msg or "busy" in msg:
                last_exc = e
                time.sleep(windows_retry_sleep_s)
                continue
            raise SQLiteUnavailable("unknown_db_error", path=p, details=str(e)) from e
        except OSError as e:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
            raise SQLiteUnavailable(_classify_oserror(e), path=p, details=str(e)) from e

    assert last_exc is not None
    raise SQLiteUnavailable(
        "unknown_db_error",
        path=p,
        details=f"exhausted windows_retry: {last_exc}",
    ) from last_exc


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------


async def quick_check_or_raise_async(
    conn: aiosqlite.Connection, path: Path | None = None
) -> None:
    """Async counterpart to :func:`quick_check_or_raise_sync`."""
    try:
        cursor = await conn.execute("PRAGMA quick_check")
        row = await cursor.fetchone()
        await cursor.close()
    except sqlite3.DatabaseError as e:
        if _looks_like_corruption(e):
            raise SQLiteUnavailable("corrupted", path=path, details=str(e)) from e
        raise
    result = str(row[0] if row else "").strip().lower()
    if result != "ok":
        raise SQLiteUnavailable("corrupted", path=path, details=result or "quick_check failed")


async def safe_open_async(
    path: str | Path,
    *,
    want_wal: bool = True,
    busy_ms: int = 30_000,
    run_quick_check: bool = True,
    foreign_keys: bool = True,
    row_factory: Any = None,
    windows_retry: int = 3,
    windows_retry_sleep_s: float = 0.2,
) -> aiosqlite.Connection:
    """Open a SQLite database safely (async flavour).

    Returns an already-pragma'd ``aiosqlite.Connection`` whose ``close()``
    the caller must invoke. For ``async with`` consumers, see
    :func:`safe_open_async_ctx`.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if _is_sync_folder_path(p):
        raise SQLiteUnavailable("path_in_sync_folder", path=p, details=str(p))

    orphan = _hot_journal_orphan(p)
    if orphan is not None:
        main_size, side_size = orphan
        raise SQLiteUnavailable(
            "hot_journal_orphan",
            path=p,
            details=f"main={main_size}B sidecar={side_size}B",
            extra={"main_size": main_size, "sidecar_size": side_size},
        )

    last_exc: BaseException | None = None
    for _attempt in range(max(1, windows_retry)):
        conn: aiosqlite.Connection | None = None
        try:
            conn = await aiosqlite.connect(str(p))
            if row_factory is not None:
                conn.row_factory = row_factory
            await conn.execute(f"PRAGMA busy_timeout={int(busy_ms)}")
            if want_wal:
                await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            if foreign_keys:
                await conn.execute("PRAGMA foreign_keys=ON")
            if run_quick_check:
                if _should_skip_quick_check(p):
                    pass
                else:
                    await quick_check_or_raise_async(conn, path=p)
                    _store_quick_check_stamp(p)
            return conn
        except SQLiteUnavailable:
            _clear_quick_check_stamp(p)
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()
            raise
        except sqlite3.DatabaseError as e:
            _clear_quick_check_stamp(p)
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()
            if _looks_like_corruption(e):
                raise SQLiteUnavailable("corrupted", path=p, details=str(e)) from e
            msg = str(e).lower()
            if "disk i/o error" in msg or "disk is full" in msg:
                raise SQLiteUnavailable("disk_full", path=p, details=str(e)) from e
            if "locked" in msg or "busy" in msg:
                last_exc = e
                await asyncio.sleep(windows_retry_sleep_s)
                continue
            raise SQLiteUnavailable("unknown_db_error", path=p, details=str(e)) from e
        except OSError as e:
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()
            raise SQLiteUnavailable(_classify_oserror(e), path=p, details=str(e)) from e

    assert last_exc is not None
    raise SQLiteUnavailable(
        "unknown_db_error",
        path=p,
        details=f"exhausted windows_retry: {last_exc}",
    ) from last_exc


@contextlib.asynccontextmanager
async def safe_open_async_ctx(
    path: str | Path,
    *,
    want_wal: bool = True,
    busy_ms: int = 30_000,
    run_quick_check: bool = True,
    foreign_keys: bool = True,
    row_factory: Any = None,
) -> AsyncIterator[aiosqlite.Connection]:
    """``async with`` wrapper around :func:`safe_open_async`.

    Use this when migrating call sites that already used
    ``async with aiosqlite.connect(path) as db:`` so the diff is
    minimal. The connection is closed automatically on ``__aexit__``.
    """
    conn = await safe_open_async(
        path,
        want_wal=want_wal,
        busy_ms=busy_ms,
        run_quick_check=run_quick_check,
        foreign_keys=foreign_keys,
        row_factory=row_factory,
    )
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
