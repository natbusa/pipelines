"""Tests for the blueprint pipeline."""

import pytest
from unittest.mock import patch, MagicMock
from pipelines.blueprint import Pipeline
from tests.helpers import collect_pipe, make_body, make_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ow_body(message="hello"):
    """Build a body with __openwebui callback metadata."""
    body = make_body(message)
    body["__openwebui"] = {
        "base_url": "http://localhost:8080",
        "token": "test-token",
        "chat_id": "chat-1",
    }
    body["user"] = make_user()
    return body


def _collect_with_ow(pipeline, message="hello"):
    """Call pipe() with __openwebui context and collect results."""
    body = _ow_body(message)
    result = pipeline.pipe(
        user_message=message,
        model_id="test-model",
        messages=body["messages"],
        body=body,
        user=body["user"],
    )
    text_chunks = []
    events = []
    for chunk in result:
        if isinstance(chunk, dict):
            events.append(chunk)
        else:
            text_chunks.append(chunk)
    return text_chunks, events


def _mock_requests(mock_req, files=None, model_chunks=None, upload_id="file-123"):
    """Wire up mock responses for GET (list files) and POST (model + upload)."""
    # GET /api/v1/files/
    list_resp = MagicMock()
    list_resp.json.return_value = files or []
    list_resp.raise_for_status = MagicMock()

    mock_req.get.return_value = list_resp

    # POST responses depend on URL
    model_resp = MagicMock()
    model_resp.raise_for_status = MagicMock()
    sse_lines = []
    for c in (model_chunks or ["Hello", " world"]):
        sse_lines.append(f'data: {{"choices":[{{"delta":{{"content":"{c}"}}}}]}}')
    sse_lines.append("data: [DONE]")
    model_resp.iter_lines.return_value = sse_lines

    upload_resp = MagicMock()
    upload_resp.raise_for_status = MagicMock()
    upload_resp.json.return_value = {"id": upload_id}

    def post_side_effect(url, **kwargs):
        if "chat/completions" in url:
            return model_resp
        return upload_resp

    mock_req.post.side_effect = post_side_effect


# ---------------------------------------------------------------------------
# No callback (graceful degradation)
# ---------------------------------------------------------------------------


def test_pipe_without_callback():
    p = Pipeline()
    text, events = collect_pipe(p, "hello")
    combined = "".join(text)
    assert "not available" in combined
    assert len(events) == 0


# ---------------------------------------------------------------------------
# With callback
# ---------------------------------------------------------------------------


@patch("pipelines.blueprint.requests")
def test_pipe_lists_files(mock_req):
    files = [
        {"meta": {"name": "readme.txt"}},
        {"meta": {"name": "data.csv"}},
    ]
    _mock_requests(mock_req, files=files)

    p = Pipeline()
    text, _ = _collect_with_ow(p)
    combined = "".join(text)
    assert "readme.txt" in combined
    assert "data.csv" in combined
    assert "(2)" in combined


@patch("pipelines.blueprint.requests")
def test_pipe_lists_empty_files(mock_req):
    _mock_requests(mock_req, files=[])

    p = Pipeline()
    text, _ = _collect_with_ow(p)
    combined = "".join(text)
    assert "(0)" in combined
    assert "_(none)_" in combined


@patch("pipelines.blueprint.requests")
def test_pipe_truncates_file_list(mock_req):
    files = [{"meta": {"name": f"file{i}.txt"}} for i in range(15)]
    _mock_requests(mock_req, files=files)

    p = Pipeline()
    text, _ = _collect_with_ow(p)
    combined = "".join(text)
    assert "5 more" in combined


@patch("pipelines.blueprint.requests")
def test_pipe_streams_model_response(mock_req):
    _mock_requests(mock_req, model_chunks=["Good ", "morning"])

    p = Pipeline()
    text, _ = _collect_with_ow(p)
    combined = "".join(text)
    assert "Good " in combined
    assert "morning" in combined


@patch("pipelines.blueprint.requests")
def test_pipe_calls_configured_model(mock_req):
    _mock_requests(mock_req)

    p = Pipeline()
    p.valves = p.Valves(model="custom/model:7b")
    text, _ = _collect_with_ow(p)
    combined = "".join(text)
    assert "custom/model:7b" in combined

    # Verify the model was passed in the POST call
    call_args = [
        c for c in mock_req.post.call_args_list
        if "chat/completions" in str(c)
    ]
    assert len(call_args) == 1
    assert call_args[0].kwargs["json"]["model"] == "custom/model:7b"


@patch("pipelines.blueprint.requests")
def test_pipe_uploads_transcript(mock_req):
    _mock_requests(mock_req, upload_id="abc-123")

    p = Pipeline()
    text, _ = _collect_with_ow(p, "test message")
    combined = "".join(text)
    assert "transcript.md" in combined
    assert "abc-123" in combined

    # Verify upload was called
    upload_calls = [
        c for c in mock_req.post.call_args_list
        if "api/v1/files" in str(c)
    ]
    assert len(upload_calls) == 1


@patch("pipelines.blueprint.requests")
def test_pipe_emits_status_events(mock_req):
    _mock_requests(mock_req)

    p = Pipeline()
    _, events = _collect_with_ow(p)
    statuses = [e["event"]["data"] for e in events if e["event"]["type"] == "status"]
    assert any(not s["done"] for s in statuses), "expected in-progress status"
    assert any(s["done"] for s in statuses), "expected done status"


@patch("pipelines.blueprint.requests")
def test_pipe_transcript_contains_exchange(mock_req):
    _mock_requests(mock_req, model_chunks=["The answer is 42"])

    p = Pipeline()
    _collect_with_ow(p, "what is the meaning of life")

    # Find the upload call and check transcript content
    upload_calls = [
        c for c in mock_req.post.call_args_list
        if "api/v1/files" in str(c)
    ]
    assert len(upload_calls) == 1
    uploaded_files = upload_calls[0].kwargs["files"]
    transcript_bytes = uploaded_files["file"][1]
    transcript = transcript_bytes.decode()
    assert "what is the meaning of life" in transcript
    assert "The answer is 42" in transcript


# ---------------------------------------------------------------------------
# Valves
# ---------------------------------------------------------------------------


def test_default_valves():
    p = Pipeline()
    assert p.valves.model == "neom/gpt-oss:120b"


def test_override_valves():
    p = Pipeline()
    p.valves = p.Valves(model="other/model:7b")
    assert p.valves.model == "other/model:7b"


def test_valves_serialization():
    p = Pipeline()
    data = p.valves.model_dump()
    assert data == {"model": "neom/gpt-oss:120b"}
    restored = p.Valves(**data)
    assert restored.model == "neom/gpt-oss:120b"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_startup():
    p = Pipeline()
    await p.on_startup()  # should not raise


@pytest.mark.asyncio
async def test_on_shutdown():
    p = Pipeline()
    await p.on_shutdown()


@pytest.mark.asyncio
async def test_on_valves_updated():
    p = Pipeline()
    p.valves = p.Valves(model="test/model:1b")
    await p.on_valves_updated()
