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
| Quick prototype or simple filter | Pipeline has its own test files or data |

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
4. Call `collect_pipe()` / `inlet()` / `outlet()`

### Testing async methods (inlet, outlet, lifecycle)

Use the `@pytest.mark.asyncio` decorator:

```python
import pytest
from pipelines.my_pipeline import Pipeline
from tests.helpers import make_body, make_user

@pytest.mark.asyncio
async def test_inlet():
    p = Pipeline()
    body = make_body("hello")
    result = await p.inlet(body, make_user())
    assert result["messages"][0]["role"] == "system"
```
