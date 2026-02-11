"""Open WebUI callback helpers for pipelines.

Provides utilities for pipelines that call back to Open WebUI via the
``__openwebui`` metadata injected into pipeline requests:

- Status events for the chat UI
- File download / upload via the Open WebUI file API
- Finding file references in ``_files`` message metadata
"""

import logging
from typing import List, Optional

import requests

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status events
# ---------------------------------------------------------------------------


def emit_status(
    description: str = "Unknown state",
    status: str = "in_progress",
    done: bool = False,
):
    """Return a status-event dict (yield it from ``pipe()``)."""
    return {
        "event": {
            "type": "status",
            "data": {
                "status": status,
                "description": description,
                "done": done,
            },
        }
    }


# ---------------------------------------------------------------------------
# API context
# ---------------------------------------------------------------------------


def get_api_context(body: dict) -> tuple[str, dict]:
    """Extract Open WebUI *base_url* and auth *headers* from ``__openwebui``.

    Returns ``(base_url, headers)`` or raises :class:`ValueError`.
    """
    ow = body.get("__openwebui", {})
    base_url = (ow.get("base_url") or "").rstrip("/")
    token = ow.get("token")

    if not (base_url and token):
        raise ValueError(
            "Open WebUI callback not available. "
            "Send requests through Open WebUI to enable file and model access."
        )

    return base_url, {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------


def download_file(
    base_url: str, headers: dict, file_id: str
) -> Optional[bytes]:
    """Download file content by *file_id* from Open WebUI."""
    try:
        r = requests.get(
            f"{base_url}/api/v1/files/{file_id}/content", headers=headers
        )
        r.raise_for_status()
        return r.content
    except Exception as e:
        log.error(f"Failed to download file {file_id}: {e}")
        return None


def upload_file(
    base_url: str,
    headers: dict,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> Optional[str]:
    """Upload a file to Open WebUI and return its content URL."""
    try:
        r = requests.post(
            f"{base_url}/api/v1/files/",
            headers=headers,
            files={"file": (filename, content, content_type)},
        )
        r.raise_for_status()
        file_id = r.json()["id"]
        return f"{base_url}/api/v1/files/{file_id}/content"
    except Exception as e:
        log.error(f"Failed to upload file: {e}")
        return None


# ---------------------------------------------------------------------------
# Message file helpers
# ---------------------------------------------------------------------------


def find_files_in_messages(
    messages: List[dict],
    *,
    content_type: Optional[str] = None,
    extension: Optional[str] = None,
) -> list[dict]:
    """Return all ``_files`` entries from *messages* matching the filter.

    Searches messages newest-first.  Each returned dict has at least
    ``id``, ``name``, and ``content_type``.

    Pass *content_type* (substring match, case-insensitive) and/or
    *extension* (e.g. ``".pdf"``, case-insensitive) to filter.
    """
    ext = extension.lower() if extension else None
    ct = content_type.lower() if content_type else None

    results: list[dict] = []
    for msg in reversed(messages):
        for f in msg.get("_files", []):
            if not f.get("id"):
                continue
            name = f.get("name", "")
            fct = f.get("content_type", "")
            if ext and not name.lower().endswith(ext):
                continue
            if ct and ct not in fct.lower():
                continue
            results.append(f)
    return results
