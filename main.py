from fastapi import FastAPI, Request, Depends, status, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool


from starlette.responses import StreamingResponse, Response
from pydantic import BaseModel, ConfigDict
from typing import List, Union, Generator, Iterator


from utils.pipelines.auth import bearer_security, get_current_user
from utils.pipelines.main import get_last_user_message, stream_message_template
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from schemas import OpenAIChatCompletionForm

import shutil
import os
import importlib.util
import logging
import time
import json
import uuid
import sys

from config import API_KEY, PIPELINES_DIR, VALVES_DIR, LOG_LEVELS

if not os.path.exists(PIPELINES_DIR):
    os.makedirs(PIPELINES_DIR)

os.makedirs(VALVES_DIR, exist_ok=True)


def _migrate_valves_to_new_dir():
    """Move legacy valves.json files into VALVES_DIR.

    Handles two cases:
    1. Single-file pipelines: e.g. blueprint.py + blueprint/valves.json
    2. Package pipelines: e.g. my_agent/__init__.py + my_agent/valves.json
    """
    for entry in os.listdir(PIPELINES_DIR):
        entry_path = os.path.join(PIPELINES_DIR, entry)
        if not os.path.isdir(entry_path) or entry.startswith(".") or entry == "failed":
            continue

        old_valves = os.path.join(entry_path, "valves.json")
        if not os.path.exists(old_valves):
            continue

        new_dir = os.path.join(VALVES_DIR, entry)
        new_valves = os.path.join(new_dir, "valves.json")

        if not os.path.exists(new_valves):
            os.makedirs(new_dir, exist_ok=True)
            shutil.move(old_valves, new_valves)
            logging.info(f"Migrated valves.json: {old_valves} -> {new_valves}")
        else:
            # New location already has the file, just remove the old one
            os.remove(old_valves)
            logging.info(f"Removed duplicate legacy valves.json: {old_valves}")

        has_init = os.path.exists(os.path.join(entry_path, "__init__.py"))
        if has_init:
            # Package pipeline — leave directory intact (it still has code)
            continue

        # Single-file pipeline — clean up the now-empty directory
        remaining = [f for f in os.listdir(entry_path) if f != "__pycache__"]
        if not remaining:
            shutil.rmtree(entry_path)
            logging.info(f"Removed empty legacy directory: {entry_path}")


_migrate_valves_to_new_dir()


PIPELINES = {}
PIPELINE_MODULES = {}
PIPELINE_NAMES = {}

# Add GLOBAL_LOG_LEVEL for Pipelines
log_level = os.getenv("GLOBAL_LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(levelname)-8s [%(name)s] %(message)s"
logging.basicConfig(level=LOG_LEVELS[log_level], format=LOG_FORMAT, force=True)

# Override uvicorn log formatters to match our format
_log_formatter = logging.Formatter(LOG_FORMAT)
for _uv_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "watchfiles", "watchfiles.main"):
    for _handler in logging.getLogger(_uv_name).handlers:
        _handler.setFormatter(_log_formatter)


def get_all_pipelines():
    pipelines = {}
    for pipeline_id in PIPELINE_MODULES.keys():
        pipeline = PIPELINE_MODULES[pipeline_id]
        pipelines[pipeline_id] = {
            "module": pipeline_id,
            "id": pipeline_id,
            "name": (pipeline.name if hasattr(pipeline, "name") else pipeline_id),
            "valves": pipeline.valves if hasattr(pipeline, "valves") else None,
        }

    return pipelines


def parse_frontmatter(content):
    frontmatter = {}
    for line in content.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            frontmatter[key.strip().lower()] = value.strip()
    return frontmatter


async def load_module_from_path(module_name, module_path):

    try:
        # Load the module
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        logging.info(f"Loaded module: {module.__name__}")
        if hasattr(module, "Pipeline"):
            return module.Pipeline()
        else:
            raise Exception("No Pipeline class found")
    except Exception as e:
        logging.error(f"Error loading module: {module_name}: {e}")
    return None


async def load_package_from_directory(package_name, package_path):
    try:
        init_path = os.path.join(package_path, "__init__.py")

        # Load the package using importlib so relative imports work
        spec = importlib.util.spec_from_file_location(
            package_name,
            init_path,
            submodule_search_locations=[package_path],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module
        spec.loader.exec_module(module)
        logging.info(f"Loaded package: {package_name}")

        if hasattr(module, "Pipeline"):
            return module.Pipeline()
        else:
            raise Exception("No Pipeline class found")
    except Exception as e:
        logging.error(f"Error loading package: {package_name}: {e}")
    return None


def _register_pipeline(pipeline, module_name):
    """Register a pipeline: set up valves.json and add to global registries."""
    global PIPELINE_MODULES, PIPELINE_NAMES

    subfolder_path = os.path.join(VALVES_DIR, module_name)
    if not os.path.exists(subfolder_path):
        os.makedirs(subfolder_path)
        logging.info(f"Created subfolder: {subfolder_path}")

    valves_json_path = os.path.join(subfolder_path, "valves.json")
    if not os.path.exists(valves_json_path):
        with open(valves_json_path, "w") as f:
            json.dump({}, f)
        logging.info(f"Created valves.json in: {subfolder_path}")

    # Overwrite pipeline.valves with values from valves.json
    if os.path.exists(valves_json_path):
        with open(valves_json_path, "r") as f:
            valves_json = json.load(f)
            if hasattr(pipeline, "valves"):
                ValvesModel = pipeline.valves.__class__
                combined_valves = {
                    **pipeline.valves.model_dump(),
                    **valves_json,
                }
                valves = ValvesModel(**combined_valves)
                pipeline.valves = valves
                logging.info(f"Updated valves for module: {module_name}")

    pipeline_id = pipeline.id if hasattr(pipeline, "id") else module_name
    PIPELINE_MODULES[pipeline_id] = pipeline
    PIPELINE_NAMES[pipeline_id] = module_name
    logging.info(f"Loaded module: {module_name}")


async def load_modules_from_directory(directory):
    global PIPELINE_MODULES
    global PIPELINE_NAMES

    loaded_single_files = set()

    # Pass 1: Load single .py files (existing behavior)
    for filename in os.listdir(directory):
        if filename.endswith(".py"):
            module_name = filename[:-3]  # Remove the .py extension
            module_path = os.path.join(directory, filename)

            pipeline = await load_module_from_path(module_name, module_path)
            if pipeline:
                _register_pipeline(pipeline, module_name)
                loaded_single_files.add(module_name)
            else:
                logging.warning(f"No Pipeline class found in {module_name}")

    # Pass 2: Load package pipelines (directories with __init__.py)
    for entry in os.listdir(directory):
        if entry.startswith("."):
            continue
        entry_path = os.path.join(directory, entry)
        if not os.path.isdir(entry_path):
            continue
        if entry in loaded_single_files:
            continue
        init_path = os.path.join(entry_path, "__init__.py")
        if not os.path.exists(init_path):
            continue

        pipeline = await load_package_from_directory(entry, entry_path)
        if pipeline:
            _register_pipeline(pipeline, entry)
        else:
            logging.warning(f"No Pipeline class found in package {entry}")

    global PIPELINES
    PIPELINES = get_all_pipelines()


async def on_startup():
    await load_modules_from_directory(PIPELINES_DIR)

    for module in PIPELINE_MODULES.values():
        if hasattr(module, "on_startup"):
            await module.on_startup()


async def on_shutdown():
    for module in PIPELINE_MODULES.values():
        if hasattr(module, "on_shutdown"):
            await module.on_shutdown()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await on_startup()
    yield
    await on_shutdown()


app = FastAPI(docs_url="/docs", redoc_url=None, lifespan=lifespan)

app.state.PIPELINES = PIPELINES


origins = ["*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def check_url(request: Request, call_next):
    start_time = int(time.time())
    app.state.PIPELINES = get_all_pipelines()
    response = await call_next(request)
    process_time = int(time.time()) - start_time
    response.headers["X-Process-Time"] = str(process_time)

    return response


@app.get("/v1/models")
@app.get("/models")
async def get_models(user: str = Depends(get_current_user)):
    """
    Returns the available pipelines
    """
    app.state.PIPELINES = get_all_pipelines()
    return {
        "data": [
            {
                "id": pipeline["id"],
                "name": pipeline["name"],
                "object": "model",
                "created": int(time.time()),
                "owned_by": "openai",
                "pipeline": {
                    "valves": pipeline["valves"] != None,
                },
            }
            for pipeline in app.state.PIPELINES.values()
        ],
        "object": "list",
        "pipelines": True,
    }


@app.get("/v1")
@app.get("/")
async def get_status():
    return {"status": True}


@app.get("/v1/pipelines")
@app.get("/pipelines")
async def list_pipelines(user: str = Depends(get_current_user)):
    if user == API_KEY:
        return {
            "data": [
                {
                    "id": pipeline_id,
                    "name": PIPELINE_NAMES[pipeline_id],
                    "valves": (
                        True
                        if hasattr(PIPELINE_MODULES[pipeline_id], "valves")
                        else False
                    ),
                }
                for pipeline_id in list(PIPELINE_MODULES.keys())
            ]
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


@app.get("/v1/{pipeline_id}/valves")
@app.get("/{pipeline_id}/valves")
async def get_valves(pipeline_id: str):
    if pipeline_id not in PIPELINE_MODULES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline {pipeline_id} not found",
        )

    pipeline = PIPELINE_MODULES[pipeline_id]

    if hasattr(pipeline, "valves") is False:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Valves for {pipeline_id} not found",
        )

    return pipeline.valves


@app.get("/v1/{pipeline_id}/valves/spec")
@app.get("/{pipeline_id}/valves/spec")
async def get_valves_spec(pipeline_id: str):
    if pipeline_id not in PIPELINE_MODULES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline {pipeline_id} not found",
        )

    pipeline = PIPELINE_MODULES[pipeline_id]

    if hasattr(pipeline, "valves") is False:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Valves for {pipeline_id} not found",
        )

    return pipeline.valves.schema()


@app.post("/v1/{pipeline_id}/valves/update")
@app.post("/{pipeline_id}/valves/update")
async def update_valves(pipeline_id: str, form_data: dict):

    if pipeline_id not in PIPELINE_MODULES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline {pipeline_id} not found",
        )

    pipeline = PIPELINE_MODULES[pipeline_id]

    if hasattr(pipeline, "valves") is False:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Valves for {pipeline_id} not found",
        )

    try:
        ValvesModel = pipeline.valves.__class__
        valves = ValvesModel(**form_data)
        pipeline.valves = valves

        # Determine the directory path for the valves.json file
        valve_dir = os.path.join(VALVES_DIR, PIPELINE_NAMES[pipeline_id])
        valves_json_path = os.path.join(valve_dir, "valves.json")

        # Save the updated valves data back to the valves.json file
        with open(valves_json_path, "w") as f:
            json.dump(valves.model_dump(), f)

        if hasattr(pipeline, "on_valves_updated"):
            await pipeline.on_valves_updated()
    except Exception as e:
        logging.error(f"Error updating valves: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{str(e)}",
        )

    return pipeline.valves


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def generate_openai_chat_completion(form_data: OpenAIChatCompletionForm):
    messages = [message.model_dump() for message in form_data.messages]
    user_message = get_last_user_message(messages)

    if form_data.model not in app.state.PIPELINES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline {form_data.model} not found",
        )

    def job():
        pipeline_id = form_data.model
        pipe = PIPELINE_MODULES[pipeline_id].pipe
        body = form_data.model_dump()
        user = body.get("user")

        logging.info(
            f"chat/completions: pipeline={pipeline_id} "
            f"user={user.get('id') if user else 'anonymous'}"
        )

        if form_data.stream:

            def stream_content():
                res = pipe(
                    user_message=user_message,
                    model_id=pipeline_id,
                    messages=messages,
                    body=body,
                    user=user,
                )
                logging.debug(f"stream:true:{res}")

                if isinstance(res, str):
                    message = stream_message_template(form_data.model, res)
                    logging.debug(f"stream_content:str:{message}")
                    yield f"data: {json.dumps(message)}\n\n"

                if isinstance(res, Iterator):
                    for line in res:
                        if isinstance(line, BaseModel):
                            line = line.model_dump_json()
                            line = f"data: {line}"

                        elif isinstance(line, dict):
                            line = json.dumps(line)
                            line = f"data: {line}"

                        try:
                            line = line.decode("utf-8")
                            logging.debug(f"stream_content:Generator:{line}")
                        except:
                            pass

                        if isinstance(line, str) and line.startswith("data:"):
                            yield f"{line}\n\n"
                        else:
                            line = stream_message_template(form_data.model, line)
                            yield f"data: {json.dumps(line)}\n\n"

                if isinstance(res, str) or isinstance(res, Generator):
                    finish_message = {
                        "id": f"{form_data.model}-{str(uuid.uuid4())}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": form_data.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "logprobs": None,
                                "finish_reason": "stop",
                            }
                        ],
                    }

                    yield f"data: {json.dumps(finish_message)}\n\n"
                    yield f"data: [DONE]"

            return StreamingResponse(stream_content(), media_type="text/event-stream")
        else:
            res = pipe(
                user_message=user_message,
                model_id=pipeline_id,
                messages=messages,
                body=body,
                user=user,
            )
            logging.debug(f"stream:false:{res}")

            if isinstance(res, dict):
                return res
            elif isinstance(res, BaseModel):
                return res.model_dump()
            else:

                message = ""

                if isinstance(res, str):
                    message = res

                if isinstance(res, Generator):
                    for stream in res:
                        message = f"{message}{stream}"

                logging.debug(f"stream:false:{message}")
                return {
                    "id": f"{form_data.model}-{str(uuid.uuid4())}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": form_data.model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": message,
                            },
                            "logprobs": None,
                            "finish_reason": "stop",
                        }
                    ],
                }

    return await run_in_threadpool(job)
