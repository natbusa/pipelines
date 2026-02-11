# How to Write and Organize Pipelines

## Pipeline layout

A single container serves every pipeline found in `PIPELINES_DIR` (default `./pipelines`). Each `.py` file or package directory with an `__init__.py` is loaded as its own pipeline, served by the same FastAPI app, and listed at `/v1/models`.

```
pipelines/
  summarizer.py            # single-file pipeline
  translator.py            # single-file pipeline
  my_agent/                # multi-file package pipeline
    __init__.py
    tools.py
    prompts.py
```

Each pipeline gets its own:

- **Pipeline ID** — from `pipeline.id` or the filename/dirname
- **Independent valves/config** — a `valves.json` is created per pipeline
- **Separate lifecycle** — `on_startup` / `on_shutdown` / `on_valves_updated`

## Single-file pipelines

The simplest form. Drop a `.py` file into `pipelines/` with a `Pipeline` class:

```python
"""
title: My Pipeline
version: 0.1.0
requirements: requests
"""

from pydantic import BaseModel, Field

class Pipeline:
    class Valves(BaseModel):
        api_key: str = Field(default="", description="API key")

    def __init__(self):
        self.name = "My Pipeline"
        self.valves = self.Valves()

    def pipe(self, user_message, model_id, messages, body):
        return f"Echo: {user_message}"
```

The frontmatter (`title`, `version`, `requirements`) is parsed at load time. Dependencies listed in `requirements` are installed automatically unless `INSTALL_FRONTMATTER_REQUIREMENTS=false`.

## Package pipelines

When a pipeline grows beyond a single file, convert it to a package — a directory with an `__init__.py` that exposes the `Pipeline` class:

```
pipelines/
  my_agent/
    __init__.py    # must contain the Pipeline class
    tools.py       # helper modules
    prompts.py
```

`__init__.py` works exactly like a single-file pipeline (same frontmatter, same `Pipeline` class). The difference is you can split logic across files and use relative imports:

```python
"""
title: My Agent
version: 0.1.0
requirements: langchain
"""

from pydantic import BaseModel, Field
from .tools import search, calculate
from .prompts import SYSTEM_PROMPT

class Pipeline:
    class Valves(BaseModel):
        api_key: str = Field(default="", description="API key")

    def __init__(self):
        self.name = "My Agent"
        self.valves = self.Valves()

    def pipe(self, user_message, model_id, messages, body):
        # use imported tools and prompts
        ...
```

## When to use packages vs single files

| Use a single file when | Use a package when |
|---|---|
| Pipeline is self-contained | Logic spans multiple modules |
| No shared helpers needed | You want to reuse tools/prompts across files |
| Quick prototype | Pipeline has its own test files or data |

## When to use separate containers

All pipelines in one container share the same Python environment. You need separate containers when:

- Pipelines have **conflicting dependencies** (e.g., different torch versions)
- You want **independent scaling** or isolation for resource-heavy pipelines
- You need **different `PIPELINES_API_KEY` values** per pipeline

## Running locally (dev mode)

Install the server dependencies and start with hot-reload:

```bash
pip install -r requirements.txt
./start.sh --reload
```

Copy a scaffold into `pipelines/` to get started:

```bash
cp examples/scaffolds/blueprint.py pipelines/
```

The server starts on `http://localhost:9099`. In Open WebUI, go to **Settings > Connections > OpenAI API** and set:

- API URL: `http://localhost:9099`
- API Key: `0p3n-w3bu!`

Your pipelines appear as model options in the UI. Edits to pipeline files are picked up automatically thanks to `--reload`.

## Deploying with Docker

### Using the stock image with your own pipelines

Mount your pipelines directory and point `PIPELINES_REQUIREMENTS_PATH` at a shared requirements file:

```yaml
pipelines:
  image: ghcr.io/open-webui/pipelines:main
  volumes:
    - ./my-pipelines:/app/pipelines
  restart: always
  environment:
    - PIPELINES_API_KEY=0p3n-w3bu!
    - PIPELINES_REQUIREMENTS_PATH=/app/pipelines/requirements.txt
```

Where `my-pipelines/` contains your pipelines and a shared requirements file:

```
my-pipelines/
  requirements.txt       # shared deps for your pipelines
  summarizer.py
  my_agent/
    __init__.py
    tools.py
```

`PIPELINES_REQUIREMENTS_PATH` runs `pip install` on every container start. Packages already installed are skipped quickly, but if startup time matters, bake the deps into a custom image instead:

### Building a custom image

```dockerfile
FROM ghcr.io/open-webui/pipelines:main
COPY my-pipelines/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
```

Then deps are installed at build time and startup is instant.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `PIPELINES_DIR` | `./pipelines` | Directory to load pipelines from |
| `PIPELINES_API_KEY` | `0p3n-w3bu!` | API key for authentication |
| `PIPELINES_URLS` | *(unset)* | Semicolon-separated URLs to download pipelines from at startup |
| `PIPELINES_REQUIREMENTS_PATH` | *(unset)* | Path to a requirements.txt to install at startup |
| `INSTALL_FRONTMATTER_REQUIREMENTS` | `false` | Install deps listed in pipeline frontmatter at startup |
| `RESET_PIPELINES_DIR` | `false` | Wipe the pipelines directory on startup |

## Testing pipelines

Install test dependencies:

```bash
pip install -r requirements-test.txt
```

Run all tests:

```bash
python -m pytest tests/ -v
```

### Test harness

`tests/helpers.py` provides three utility functions:

- `make_body(message, model, stream)` — builds a minimal OpenAI-shaped request body
- `make_user(name, user_id, role)` — builds a minimal user dict
- `collect_pipe(pipeline, message, model)` — calls `pipe()` and separates text chunks from events

### Writing tests

Tests are plain functions. Instantiate the pipeline directly, override valves with test values, and use the helpers:

```python
from pipelines.my_pipeline import Pipeline
from tests.helpers import make_body, make_user, collect_pipe

def test_pipe_returns_text():
    p = Pipeline()
    text, events = collect_pipe(p, "hello")
    assert len(text) > 0

def test_custom_valves():
    p = Pipeline()
    p.valves = p.Valves(api_key="test-key")
    text, _ = collect_pipe(p, "hello")
    assert "hello" in "".join(text)
```

### Mocking external dependencies

For pipelines that call APIs, databases, or other external services, use `unittest.mock.patch` to isolate the test:

```python
from unittest.mock import patch
from pipelines.my_api_pipeline import Pipeline
from tests.helpers import collect_pipe

def test_api_pipeline():
    p = Pipeline()
    p.valves = p.Valves(api_key="test-key", api_url="http://fake")

    with patch("pipelines.my_api_pipeline.requests.post") as mock_post:
        mock_post.return_value.json.return_value = {"result": "ok"}
        text, events = collect_pipe(p, "hello")
        assert "ok" in "".join(text)
```

The pattern is always:

1. Instantiate the `Pipeline` directly
2. Override valves with test values
3. `patch()` any external I/O the pipeline module uses
4. Call `collect_pipe()`

### Testing async lifecycle methods

Use the `@pytest.mark.asyncio` decorator:

```python
import pytest
from pipelines.my_pipeline import Pipeline

@pytest.mark.asyncio
async def test_on_startup():
    p = Pipeline()
    await p.on_startup()  # should not raise
```

## User context and callback API

Open WebUI injects user identity and callback metadata into every request sent to the pipeline server. This enables pipelines to:

- Know **who** is calling (user id, name, email, role)
- **Log and audit** pipeline usage per user
- **Call back** to Open WebUI APIs on behalf of the authenticated user (read/write files, query knowledge, invoke models)

The pipeline server is treated as a **trusted sidecar** — it runs in the same deployment as Open WebUI and is operator-deployed (no dynamic installation from untrusted sources).

### What Open WebUI sends

The `POST /chat/completions` request body includes two additional top-level fields:

```json
{
  "model": "pipeline-id",
  "messages": [{"role": "user", "content": "hello"}],
  "stream": true,

  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "Jane Doe",
    "email": "jane@example.com",
    "role": "user"
  },

  "__openwebui": {
    "base_url": "http://localhost:8080",
    "token": "eyJhbGciOiJIUzI1NiIs...",
    "chat_id": "chat-uuid-here"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `user.id` | `string` | User UUID |
| `user.name` | `string` | Display name |
| `user.email` | `string` | Email address |
| `user.role` | `string` | `"user"` or `"admin"` |
| `__openwebui.base_url` | `string` | Open WebUI API base URL |
| `__openwebui.token` | `string` | User's JWT for API callbacks |
| `__openwebui.chat_id` | `string\|null` | Current chat UUID |

### Accessing user context in `pipe()`

The pipeline server extracts `user` from the request body and passes it as a keyword argument to `pipe()`. Add `user=None` to your signature to opt in:

```python
class Pipeline:
    def pipe(self, user_message, model_id, messages, body, user=None):
        if user:
            print(f"Request from {user['name']} ({user['id']})")
        ...
```

Existing pipelines that don't declare `user` continue to work — the kwarg is simply ignored.

### Backwards compatibility

All new fields are optional with `None` defaults:

- **Existing pipelines** continue to work without changes. The `user` kwarg defaults to `None`, and `__openwebui` is simply present in `body` for pipelines that want it.
- **Non-Open WebUI callers** (direct API usage, testing) work fine — `user` will be `None` and `__openwebui` will be absent.

### Callback API usage

Pipelines can use `__openwebui` to call Open WebUI's REST API on behalf of the authenticated user. The token carries the user's permissions.

**Read a file:**

```python
import requests

def pipe(self, user_message, model_id, messages, body, user=None):
    ow = body.get("__openwebui", {})
    base_url = ow.get("base_url")
    token = ow.get("token")

    if base_url and token:
        r = requests.get(
            f"{base_url}/api/v1/files/{file_id}/content",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        file_data = r.content
```

**Upload a file:**

```python
def pipe(self, user_message, model_id, messages, body, user=None):
    ow = body.get("__openwebui", {})

    r = requests.post(
        f"{ow['base_url']}/api/v1/files/",
        headers={"Authorization": f"Bearer {ow['token']}"},
        files={"file": ("report.csv", csv_bytes, "text/csv")},
    )
    uploaded = r.json()
```

**List user's files:**

```python
def pipe(self, user_message, model_id, messages, body, user=None):
    ow = body.get("__openwebui", {})

    r = requests.get(
        f"{ow['base_url']}/api/v1/files/",
        headers={"Authorization": f"Bearer {ow['token']}"},
    )
    files = r.json()
```

### Available Open WebUI API endpoints

Any endpoint in Open WebUI's REST API is callable. Common ones:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/files/` | GET | List user's files |
| `/api/v1/files/` | POST | Upload a file |
| `/api/v1/files/{id}/content` | GET | Download file content |
| `/api/v1/files/{id}` | DELETE | Delete a file |
| `/api/v1/knowledge/` | GET | List knowledge bases |
| `/api/v1/knowledge/{id}` | GET | Get knowledge base details |
| `/api/v1/chats/` | GET | List user's chats |
| `/api/v1/models` | GET | List available models |

### `OpenWebUIClient` helper

A convenience wrapper for pipelines that frequently call back to Open WebUI:

```python
import requests

class OpenWebUIClient:
    """Helper for pipelines to access Open WebUI services."""

    def __init__(self, body: dict):
        meta = body.get("__openwebui", {})
        self.base_url = (meta.get("base_url") or "").rstrip("/")
        self.token = meta.get("token")
        self.chat_id = meta.get("chat_id")
        self.user = body.get("user")

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.token)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def get_file(self, file_id: str) -> bytes:
        r = requests.get(
            f"{self.base_url}/api/v1/files/{file_id}/content",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.content

    def upload_file(self, filename: str, content: bytes,
                    content_type: str = "application/octet-stream") -> dict:
        r = requests.post(
            f"{self.base_url}/api/v1/files/",
            headers=self._headers(),
            files={"file": (filename, content, content_type)},
        )
        r.raise_for_status()
        return r.json()

    def list_files(self) -> list:
        r = requests.get(
            f"{self.base_url}/api/v1/files/",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def delete_file(self, file_id: str) -> dict:
        r = requests.delete(
            f"{self.base_url}/api/v1/files/{file_id}",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()
```

Usage:

```python
class Pipeline:
    def pipe(self, user_message, model_id, messages, body, user=None):
        client = OpenWebUIClient(body)

        if not client.available:
            yield "Pipeline callback not available."
            return

        files = client.list_files()
        yield f"You have {len(files)} files.\n"
```

### Callback URL configuration

When the pipeline server runs in a different container or host, set `OPENWEBUI_API_URL` on the **Open WebUI** instance so pipelines receive a reachable callback URL:

```bash
# Open WebUI env
OPENWEBUI_API_URL=http://open-webui:8080
```

If unset, defaults to `request.base_url` (works when both services share the same host/network).
