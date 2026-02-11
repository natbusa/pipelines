"""Tests for the blueprint pipeline."""

import pytest
from pipelines.blueprint import Pipeline
from tests.helpers import collect_pipe


# ---------------------------------------------------------------------------
# Pipe (streaming)
# ---------------------------------------------------------------------------


def test_pipe_yields_chunks():
    p = Pipeline()
    text, events = collect_pipe(p, "hello")
    assert len(text) > 0


def test_pipe_echoes_user_message():
    p = Pipeline()
    text, _ = collect_pipe(p, "hello world")
    combined = "".join(text)
    assert "hello world" in combined


def test_pipe_emits_start_and_done_status():
    p = Pipeline()
    _, events = collect_pipe(p, "hi")
    statuses = [
        e["event"]["data"] for e in events if e["event"]["type"] == "status"
    ]
    assert any(not s["done"] for s in statuses), "expected an in-progress status"
    assert any(s["done"] for s in statuses), "expected a done status"


def test_pipe_includes_conversation_history():
    p = Pipeline()
    text, _ = collect_pipe(p, "test message")
    combined = "".join(text)
    assert "**user**" in combined


def test_pipe_includes_stats_widget():
    p = Pipeline()
    text, _ = collect_pipe(p, "test")
    combined = "".join(text)
    assert "<details>" in combined
    assert "Statistics" in combined


def test_pipe_cool_mode_reflected():
    p_on = Pipeline()
    p_off = Pipeline()
    p_off.valves = p_off.Valves(cool=False)

    text_on, _ = collect_pipe(p_on, "x")
    text_off, _ = collect_pipe(p_off, "x")
    assert "True" in "".join(text_on)
    assert "False" in "".join(text_off)


# ---------------------------------------------------------------------------
# Valves
# ---------------------------------------------------------------------------


def test_default_valves():
    p = Pipeline()
    assert p.valves.cool is True


def test_override_valves():
    p = Pipeline()
    p.valves = p.Valves(cool=False)
    assert p.valves.cool is False


def test_valves_serialization():
    p = Pipeline()
    data = p.valves.model_dump()
    assert data == {"cool": True}
    restored = p.Valves(**data)
    assert restored.cool is True


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
    p.valves = p.Valves(cool=False)
    await p.on_valves_updated()
