# Changelog

This repository is a fork of the original [Open WebUI Pipelines](https://github.com/open-webui/pipelines)
project, containing substantial modifications by Nate Busa (natalino.busa@gmail.com)
with assistance from Claude (Anthropic). All fork modifications are subject to the
same license terms as the upstream code from which they derive.

## 0.2.0

### Pipelines

- Rewrite n8n pipeline to blueprint pattern: Valves (BaseModel), emit_status, logging, user context
- Add `pipeline_name` valve for display name customization per n8n instance
- Add 8 n8n symlink slots (`n8n_p0`..`n8n_p7`) for independently-configured workflow instances
- Replace `pipelines/pdf2pdf` copy with symlink to `examples/experimental/pdf2pdf`
- `pipelines/` directory is now purely symlinks; all source code lives under `examples/`

### Docker & deployment

- Use `find -L` to follow directory symlinks when installing pipeline requirements
- Replace bash `**` glob in `start.sh` with `find -L` for portable symlink support
- Include `examples/` in Docker build context (symlink targets must be present)

## 0.1.0

Initial release of the forked pipelines server.

### Architecture

- Simplify to pipe-only: remove filter, manifold, and type system
- Add package pipeline loading (directories with `__init__.py`)
- Pass user context and `__openwebui` metadata to `pipe()`
- Separate valve storage from pipeline code (`VALVES_DIR` env var)
- Migrate legacy valves from pipeline directories on startup

### Pipelines

- Add pdf2pdf Arabic translation pipeline with layout-preserving rebuild
- Install Amiri Arabic font in Docker image (`/app/fonts/arabic.ttf`)
- Fix Arabic PDF text overflow with wider bounding boxes and font-size fallback

### Configuration

- Add `PIPELINES_DIR` env var (default `./pipelines`)
- Add `VALVES_DIR` env var (default `./valves`)
- Add `INSTALL_REQUIREMENTS` env var (default `false`) for runtime dep install
- Per-pipeline `requirements.txt` replaces frontmatter requirements
- Remove `PIPELINES_URLS`, `RESET_PIPELINES_DIR`, `INSTALL_FRONTMATTER_REQUIREMENTS`

### Docker & deployment

- Streamline Dockerfile: inline dep install at build time
- Simplify `start.sh`: remove `--mode` flag, use `INSTALL_REQUIREMENTS` instead
- Add `ship.sh` for Docker build and push with configurable tag and git hash

### Developer experience

- Add HOWTO guide for writing, organizing, and testing pipelines
- Add test scaffolding with helpers (`make_body`, `make_user`, `collect_pipe`)
- Add `openwebui.py` callback helpers (status events, file upload/download)
- Unify log format across app, uvicorn, and watchfiles
- Support `--reload` via uvicorn args passthrough
