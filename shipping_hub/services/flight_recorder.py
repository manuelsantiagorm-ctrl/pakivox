from __future__ import annotations

import hashlib
import json
import threading
from contextlib import suppress
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone


_local = threading.local()
_write_lock = threading.Lock()

SENSITIVE_KEYS = {
    "authorization",
    "access_token",
    "refresh_token",
    "token",
    "client_id",
    "client_secret",
    "api_key",
    "apikey",
    "password",
    "secret",
    "signature",
    "stripe_signature",
}

PERSONAL_KEYS = {
    "name",
    "first_name",
    "last_name",
    "company",
    "email",
    "phone",
    "telephone",
    "mobile",
    "street",
    "street1",
    "street2",
    "complete_street",
    "address",
    "address_line1",
    "address_line2",
    "reference",
    "references",
    "neighborhood",
    "colony",
    "city",
    "municipality",
    "state",
    "province",
    "postal_code",
    "zip",
    "zipcode",
}


def _enabled() -> bool:
    return bool(
        getattr(
            settings,
            "SHIPPING_HUB_FLIGHT_RECORDER_ENABLED",
            True,
        )
    )


def _directory() -> Path:
    configured = getattr(
        settings,
        "SHIPPING_HUB_FLIGHT_RECORDER_DIR",
        "",
    )
    path = (
        Path(configured)
        if configured
        else Path(settings.BASE_DIR)
        / "runtime"
        / "shipping_flight_recorder"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _hash(value: Any) -> str:
    text = str(value or "")
    return hashlib.sha256(
        text.encode("utf-8", errors="replace")
    ).hexdigest()[:16]


def _key(value: Any) -> str:
    return str(value or "").strip().lower()


def sanitize(
    value: Any, *, key: str = "", _depth: int = 0, _seen: set | None = None
) -> Any:
    if _seen is None:
        _seen = set()

    normalized_key = _key(key)

    if normalized_key in SENSITIVE_KEYS:
        return "[REDACTED_SECRET]"

    if normalized_key in PERSONAL_KEYS:
        return {
            "redacted": True,
            "fingerprint": _hash(value),
            "present": bool(str(value or "").strip()),
        }

    if value is None or isinstance(
        value,
        (bool, int, float),
    ):
        return value

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "length": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }

    if isinstance(value, str):
        if len(value) > 12000:
            return {
                "type": "truncated_string",
                "length": len(value),
                "prefix": value[:12000],
            }
        return value

    # Protección contra recursión infinita (referencias circulares
    # entre objetos, ej. instancias de modelo de Django que se
    # referencian mutuamente vía FKs cacheadas).
    if _depth >= 6:
        return {
            "type": "max_depth_reached",
            "repr": str(value)[:200],
        }

    object_id = id(value)
    if object_id in _seen:
        return {"type": "circular_reference"}

    if isinstance(value, dict):
        _seen = _seen | {object_id}
        sanitized = {}
        for nested_key, nested_value in value.items():
            sanitized[str(nested_key)] = sanitize(
                nested_value,
                key=str(nested_key),
                _depth=_depth + 1,
                _seen=_seen,
            )
        return sanitized

    if isinstance(value, (list, tuple, set)):
        _seen = _seen | {object_id}
        return [
            sanitize(item, _depth=_depth + 1, _seen=_seen) for item in list(value)[:500]
        ]

    with suppress(Exception):
        _seen = _seen | {object_id}
        return sanitize(vars(value), _depth=_depth + 1, _seen=_seen)

    return str(value)[:2000]


def set_flight_context(**values: Any) -> None:
    _local.context = sanitize(values)


def clear_flight_context() -> None:
    if hasattr(_local, "context"):
        delattr(_local, "context")


def get_flight_context() -> dict[str, Any]:
    value = getattr(_local, "context", {})
    return dict(value) if isinstance(value, dict) else {}


def _event_path(context: dict[str, Any]) -> Path:
    purchase_id = context.get("purchase_id") or "unknown"
    day = timezone.localdate().isoformat()
    return _directory() / (
        f"purchase_{purchase_id}_{day}.jsonl"
    )


def record_flight_event(
    event_type: str,
    payload: Any = None,
) -> None:
    if not _enabled():
        return

    context = get_flight_context()
    event = {
        "recorded_at": timezone.now().isoformat(),
        "event_type": str(event_type),
        "context": context,
        "payload": sanitize(payload),
    }
    line = json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )

    with _write_lock:
        path = _event_path(context)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def record_provider_exchange(
    *,
    provider: str,
    phase: str,
    method: str,
    path: str,
    environment: str = "",
    status_code: int | None = None,
    duration_ms: int | None = None,
    request_payload: Any = None,
    response_payload: Any = None,
    response_headers: Any = None,
    exception_type: str = "",
    exception_message: str = "",
) -> None:
    record_flight_event(
        "provider_exchange",
        {
            "provider": provider,
            "phase": phase,
            "method": str(method or "").upper(),
            "path": path,
            "environment": environment,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "request_payload": request_payload,
            "response_payload": response_payload,
            "response_headers": response_headers,
            "exception_type": exception_type,
            "exception_message": exception_message,
        },
    )
