"""
title: Pipeline Blueprint
author: Demo
version: 0.2.0
license: MIT
description: Demonstrates user context, file operations, and model callbacks via Open WebUI
"""

from typing import Generator, Iterator, List, Optional, Union
from pydantic import BaseModel, Field
import json
import logging
import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event helpers – yield these from pipe() to send structured events to the UI
# ---------------------------------------------------------------------------

def emit_status(
    description: str = "Unknown state",
    status: str = "in_progress",
    done: bool = False,
):
    """Return a status event dict (yield it from pipe())."""
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
# Pipeline
# ---------------------------------------------------------------------------

class Pipeline:
    """
    Demonstrates callback API capabilities:

    - Reading user files from Open WebUI
    - Uploading a file and linking to it in the chat
    - Calling a model (neom/gpt-oss:120b) through Open WebUI
    """

    class Valves(BaseModel):
        model: str = Field(
            default="neom/gpt-oss:120b",
            description="Model to call via Open WebUI",
        )

    def __init__(self):
        self.name = "Pipeline Blueprint"
        self.valves = self.Valves()

    # -- Lifecycle -----------------------------------------------------------

    async def on_startup(self):
        log.info(f"on_startup: {self.name}")

    async def on_shutdown(self):
        log.info(f"on_shutdown: {self.name}")

    async def on_valves_updated(self):
        log.info(f"on_valves_updated: {self.valves}")

    # -- Pipe ----------------------------------------------------------------

    def pipe(
        self,
        user_message: str,
        model_id: str,
        messages: List[dict],
        body: dict,
        user: Optional[dict] = None,
    ) -> Union[str, Generator, Iterator]:
        log.info(f"pipe: {len(messages)} messages, model={model_id}")

        ow = body.get("__openwebui", {})
        base_url = (ow.get("base_url") or "").rstrip("/")
        token = ow.get("token")

        if not (base_url and token):
            yield "Open WebUI callback not available. "
            yield "Send requests through Open WebUI to enable file and model access.\n"
            return

        headers = {"Authorization": f"Bearer {token}"}

        # -- 1. List user files ------------------------------------------------
        yield emit_status("Listing your files")

        files = self._list_files(base_url, headers)

        yield f"**Your files** ({len(files)}):\n\n"
        if files:
            for f in files[:10]:
                name = f.get("meta", {}).get("name", f.get("filename", "unknown"))
                yield f"- `{name}`\n"
            if len(files) > 10:
                yield f"- ... and {len(files) - 10} more\n"
        else:
            yield "- _(none)_\n"
        yield "\n"

        # -- 2. Call model -----------------------------------------------------
        yield emit_status(f"Calling {self.valves.model}")
        yield f"**{self.valves.model}** says:\n\n"

        assistant_reply = ""
        try:
            r = requests.post(
                f"{base_url}/api/chat/completions",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "model": self.valves.model,
                    "messages": messages,
                    "stream": True,
                },
                stream=True,
            )
            r.raise_for_status()

            for raw_line in r.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.removeprefix("data: ").strip()
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        assistant_reply += content
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
        except Exception as e:
            yield f"\n\nModel call failed: {e}\n"

        yield "\n\n"

        # -- 3. Upload transcript file -----------------------------------------
        yield emit_status("Saving transcript")

        transcript = (
            f"User: {user_message}\n\n"
            f"Assistant ({self.valves.model}):\n{assistant_reply}\n"
        )

        file_url = self._upload_transcript(base_url, headers, transcript)
        if file_url:
            yield f"**Transcript saved**: [transcript.md]({file_url})\n"
        else:
            yield "Failed to save transcript.\n"

        yield emit_status("Done", done=True)

    # -- Helpers -------------------------------------------------------------

    def _list_files(self, base_url: str, headers: dict) -> list:
        try:
            r = requests.get(f"{base_url}/api/v1/files/", headers=headers)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"Failed to list files: {e}")
            return []

    def _upload_transcript(
        self, base_url: str, headers: dict, content: str
    ) -> Optional[str]:
        try:
            r = requests.post(
                f"{base_url}/api/v1/files/",
                headers=headers,
                files={
                    "file": ("transcript.md", content.encode(), "text/markdown")
                },
            )
            r.raise_for_status()
            file_id = r.json()["id"]
            return f"{base_url}/api/v1/files/{file_id}/content"
        except Exception as e:
            log.error(f"Failed to upload transcript: {e}")
            return None
