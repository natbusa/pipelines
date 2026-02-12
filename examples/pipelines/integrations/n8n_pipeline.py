"""
title: N8N Agent Pipeline
author: open-webui
version: 0.2.0
license: MIT
description: Forwards user messages to an n8n webhook workflow and returns the output
"""

from typing import Generator, Iterator, List, Optional, Union
from pydantic import BaseModel, Field
import json
import logging
import requests

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
    Forwards user messages to an n8n webhook workflow and returns the output.

    Note: n8n does not support streaming responses.
    """

    class Valves(BaseModel):
        pipeline_name: str = Field(
            default="N8N Agent Pipeline",
            description="Display name for this pipeline instance",
        )
        api_url: str = Field(
            default="https://n8n.host/webhook/myflow",
            description="n8n webhook URL for the workflow",
        )
        api_key: str = Field(
            default="",
            description="Bearer token for the n8n webhook",
        )
        verify_ssl: bool = Field(
            default=True,
            description="Verify SSL certificates when calling the webhook",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.name = self.valves.pipeline_name

    # -- Lifecycle -----------------------------------------------------------

    async def on_startup(self):
        log.info(f"on_startup: {self.name}")

    async def on_shutdown(self):
        log.info(f"on_shutdown: {self.name}")

    async def on_valves_updated(self):
        self.name = self.valves.pipeline_name
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

        yield emit_status("Calling n8n workflow")

        headers = {
            "Authorization": f"Bearer {self.valves.api_key}",
            "Content-Type": "application/json",
        }

        email = (user or {}).get("email", "unknown")
        data = {
            "inputs": {"prompt": user_message},
            "user": email,
        }

        try:
            response = requests.post(
                self.valves.api_url,
                headers=headers,
                json=data,
                verify=self.valves.verify_ssl,
            )
            response.raise_for_status()

            for line in response.iter_lines():
                if line:
                    json_data = json.loads(line.decode("utf-8"))
                    if "output" in json_data:
                        yield json_data["output"]

        except json.JSONDecodeError as e:
            log.error(f"Failed to parse JSON: {e}")
            yield "Error in JSON parsing."
        except requests.RequestException as e:
            log.error(f"Workflow request failed: {e}")
            yield f"Workflow request failed: {e}"

        yield emit_status("Done", done=True)
