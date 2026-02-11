from typing import Generator, Iterator, List, Optional, Union

from pydantic import BaseModel, Field

import logging
import os
import requests
import tempfile

from utils.pipelines.openwebui import (
    emit_status,
    get_api_context,
    download_file,
    find_files_in_messages,
    upload_file,
)

from .pdf import DEFAULT_SYSTEM_PROMPT, extract_pdf, rebuild_pdf

log = logging.getLogger(__name__)


def translate_batch(texts, base_url, headers, model, system_prompt):
    """Translate a batch of texts via the Open WebUI model callback."""
    numbered = "\n".join([f"{i + 1}. {t}" for i, t in enumerate(texts)])
    user_prompt = (
        "Translate each numbered item below to Arabic. "
        "Return ONLY the Arabic translations, keeping the same numbering. "
        "One translation per line.\n\n"
        f"{numbered}"
    )

    try:
        r = requests.post(
            f"{base_url}/api/chat/completions",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
            },
        )
        r.raise_for_status()
        result = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.error(f"Translation batch failed: {e}")
        return [f"[Translation error: {e}]"] * len(texts)

    translations = []
    for line in result.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line[0].isdigit():
            parts = line.split(".", 1)
            translated = parts[1].strip() if len(parts) > 1 else line
        else:
            translated = line
        translations.append(translated)

    while len(translations) < len(texts):
        translations.append(texts[len(translations)])
    translations = translations[: len(texts)]

    return translations


class Pipeline:
    """Translates a user-uploaded PDF to Arabic and returns the result PDF."""

    class Valves(BaseModel):
        model: str = Field(
            default="neom/gpt-oss:120b",
            description="Model to use for translation (via Open WebUI)",
        )
        batch_size: int = Field(
            default=25,
            description="Text items per translation batch",
        )
        font_path: str = Field(
            default="/app/fonts/arabic.ttf",
            description="Path to Arabic TrueType font file",
        )
        system_prompt: str = Field(
            default=DEFAULT_SYSTEM_PROMPT,
            description="System prompt for the translation model",
        )

    def __init__(self):
        self.name = "PDF to PDF Translator"
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

        try:
            base_url, headers = get_api_context(body)
        except ValueError as e:
            yield f"{e}\n"
            return

        # -- 1. Find PDF attachment via _files -------------------------------
        yield emit_status("Looking for PDF attachment")

        pdfs = find_files_in_messages(messages, extension=".pdf")
        if not pdfs:
            yield "No PDF file found. Please upload a PDF document to translate.\n"
            yield emit_status("No PDF found", done=True)
            return

        file_id = pdfs[0]["id"]
        file_name = pdfs[0].get("name", "document.pdf")

        log.info(f"pipe: found PDF file_id={file_id} name={file_name}")
        yield f"Found PDF: **{file_name}**\n\n"

        # -- 2. Download the PDF ---------------------------------------------
        yield emit_status(f"Downloading {file_name}")

        pdf_bytes = download_file(base_url, headers, file_id)
        if not pdf_bytes:
            yield "Failed to download the PDF file.\n"
            yield emit_status("Download failed", done=True)
            return

        # -- 3. Extract text -------------------------------------------------
        yield emit_status("Extracting text from PDF")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            input_path = tmp.name

        try:
            texts, layout_data = extract_pdf(input_path)
        except Exception as e:
            os.unlink(input_path)
            yield f"Failed to extract text from PDF: {e}\n"
            yield emit_status("Extraction failed", done=True)
            return

        if not texts:
            os.unlink(input_path)
            yield "No text found in PDF.\n"
            yield emit_status("No text found", done=True)
            return

        yield f"Extracted **{len(texts)}** text items.\n\n"

        # -- 4. Translate in batches -----------------------------------------
        translations = []
        batch_size = self.valves.batch_size
        total_batches = (len(texts) + batch_size - 1) // batch_size

        for i in range(0, len(texts), batch_size):
            batch_num = (i // batch_size) + 1
            batch = texts[i : i + batch_size]
            pct = int((batch_num / total_batches) * 100)
            yield emit_status(
                f"Translating batch {batch_num}/{total_batches} ({pct}%)"
            )

            translated = translate_batch(
                batch, base_url, headers,
                self.valves.model, self.valves.system_prompt,
            )
            translations.extend(translated)

        yield "Translation complete.\n\n"

        # -- 5. Rebuild PDF --------------------------------------------------
        yield emit_status("Rebuilding PDF")

        font_path = self.valves.font_path
        if not os.path.isfile(font_path):
            os.unlink(input_path)
            yield (
                f"Arabic font not found at `{font_path}`. "
                "Configure the font path in Valves.\n"
            )
            yield emit_status("Font not found", done=True)
            return

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_out:
            output_path = tmp_out.name

        try:
            rebuild_pdf(
                input_path, translations, layout_data, output_path, font_path
            )
        except Exception as e:
            os.unlink(output_path)
            yield f"Failed to rebuild PDF: {e}\n"
            yield emit_status("Rebuild failed", done=True)
            return
        finally:
            if os.path.exists(input_path):
                os.unlink(input_path)

        # -- 6. Upload translated PDF ----------------------------------------
        yield emit_status("Uploading translated PDF")

        base_name = file_name.rsplit(".", 1)[0] if file_name else "translated"
        output_name = f"{base_name}_ar.pdf"

        with open(output_path, "rb") as f:
            pdf_result_bytes = f.read()
        os.unlink(output_path)

        file_url = upload_file(
            base_url, headers, output_name, pdf_result_bytes,
            content_type="application/pdf",
        )
        if file_url:
            yield f"**Translated PDF**: [{output_name}]({file_url})\n"
        else:
            yield "Failed to upload translated PDF.\n"

        yield emit_status("Done", done=True)
