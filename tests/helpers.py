"""Shared test helpers for pipeline tests."""


def make_body(message: str, model: str = "test-model", stream: bool = True):
    """Build a minimal OpenAI-shaped request body."""
    messages = [{"role": "user", "content": message}]
    return {
        "model": model,
        "stream": stream,
        "messages": messages,
    }


def make_user(name: str = "Test User", user_id: str = "user-1", role: str = "user"):
    """Build a minimal user dict."""
    return {"id": user_id, "name": name, "role": role}


def collect_pipe(pipeline, message: str, model: str = "test-model"):
    """
    Call pipe() and collect all yielded chunks into two lists:
    - text_chunks: str chunks (the actual streamed content)
    - events: dict chunks (status / message / citation events)
    """
    body = make_body(message, model=model)
    messages = body["messages"]

    result = pipeline.pipe(
        user_message=message,
        model_id=model,
        messages=messages,
        body=body,
    )

    text_chunks = []
    events = []

    if isinstance(result, str):
        text_chunks.append(result)
    else:
        for chunk in result:
            if isinstance(chunk, dict):
                events.append(chunk)
            else:
                text_chunks.append(chunk)

    return text_chunks, events
