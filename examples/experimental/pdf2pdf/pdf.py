"""pdf2pdf - PDF text extraction and Arabic rebuild helpers."""

import arabic_reshaper
import fitz  # PyMuPDF
from bidi.algorithm import get_display

# ============================================================
# DEFAULT SYSTEM PROMPT
# ============================================================

DEFAULT_SYSTEM_PROMPT = """You are a professional Arabic translator from english to arabic.

DIALECT: Modern Standard Arabic (MSA) only. Gulf-appropriate formality.
- NO Egyptian dialect markers
- NO Levantine forms

NUMBER FORMATTING:
- Numbers 3-9: write in Arabic words
- Numbers 10-9999: write as numerals (10, 500, 4800)
- Numbers 10000+: mixed format (14 alf, 70 milyun)
- Use Arabic numerals (1,2,3) NOT Hindi numerals
- Gregorian dates only
- Keep currency symbols and % as-is

MEASUREMENTS: Metric system only. Temperature in Celsius only.

TONE: Formal professional tone, suitable for a corporate audience in Saudi Arabia.

OUTPUT: Provide ONLY the Arabic translation. No explanations, no notes, no English."""


# ============================================================
# PDF TEXT EXTRACTION
# ============================================================


def extract_pdf(filepath):
    """
    Extract text from PDF with layout metadata using PyMuPDF.
    Returns (texts, layout_data).
    """
    doc = fitz.open(filepath)
    texts = []
    layout_data = {
        "pdf_width": doc[0].rect.width,
        "pdf_height": doc[0].rect.height,
        "pages": [],
    }

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_data = {"page_num": page_num, "lines": []}
        blocks = page.get_text("dict")["blocks"]

        for block in blocks:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                parts = []
                first_span = None
                for span in line["spans"]:
                    if span["text"].strip():
                        parts.append(span["text"])
                        if first_span is None:
                            first_span = {
                                "size": span["size"],
                                "font": span["font"],
                                "color": span["color"],
                            }
                if not parts:
                    continue
                line_text = " ".join(parts)
                page_data["lines"].append({
                    "text_index": len(texts),
                    "original_text": line_text,
                    "bbox": list(line["bbox"]),
                    "span_format": first_span,
                })
                texts.append(line_text)

        layout_data["pages"].append(page_data)

    doc.close()
    return texts, layout_data


# ============================================================
# PDF REBUILD (PDF -> PDF)
# ============================================================


def shape_arabic(text):
    """Reshape Arabic glyphs and apply bidi reordering for PDF rendering."""
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)


def rebuild_pdf(input_path, translations, layout_data, output_path, font_path):
    """
    Rebuild PDF with translated Arabic text using redact-and-overlay.

    Strategy:
    1. For each page, redact original text areas (fill with sampled bg color)
    2. Insert translated Arabic text at the same positions
    """
    doc = fitz.open(input_path)
    font_name = "arabic"

    # Register the Arabic font once
    page0 = doc[0]
    page0.insert_font(fontname=font_name, fontfile=font_path)

    for page_data in layout_data["pages"]:
        page_num = page_data["page_num"]
        page = doc[page_num]

        # Register font on this page
        if page_num > 0:
            page.insert_font(fontname=font_name, fontfile=font_path)

        # Render page at 2x for background color sampling
        sample_pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))

        # Step 1: Add redaction annotations over original text
        for line_entry in page_data["lines"]:
            bbox = line_entry["bbox"]
            rect = fitz.Rect(bbox)

            # Sample background color from just left of text
            sx = max(0, int(rect.x0 * 2) - 10)
            sy = int((rect.y0 + rect.height / 2) * 2)
            sx = min(sx, sample_pix.width - 1)
            sy = min(sy, sample_pix.height - 1)

            try:
                pixel = sample_pix.pixel(sx, sy)
                bg_r = pixel[0] / 255.0
                bg_g = pixel[1] / 255.0
                bg_b = pixel[2] / 255.0
            except Exception:
                bg_r, bg_g, bg_b = 1.0, 1.0, 1.0

            expanded = rect + (-4, -2, 4, 2)
            page.add_redact_annot(expanded, fill=(bg_r, bg_g, bg_b))

        page.apply_redactions()

        # Step 2: Insert translated text
        for line_entry in page_data["lines"]:
            text_idx = line_entry["text_index"]
            if text_idx < len(translations):
                text = translations[text_idx]
            else:
                text = line_entry.get("original_text", "")

            if not text.strip():
                continue

            text = shape_arabic(text)

            bbox = line_entry["bbox"]
            fmt = line_entry.get("span_format", {})
            font_size = fmt.get("size", 12)

            # Text color
            color_int = fmt.get("color", 0)
            r = ((color_int >> 16) & 0xFF) / 255.0
            g = ((color_int >> 8) & 0xFF) / 255.0
            b = (color_int & 0xFF) / 255.0

            # RTL layout: mirror the original left margin to the right,
            # extend the box leftward for Arabic text width
            page_width = layout_data["pdf_width"]
            orig_rect = fitz.Rect(bbox)
            left_margin = orig_rect.x0
            right_edge = page_width - left_margin  # mirror left margin
            width = max(orig_rect.width * 2.0, right_edge)
            height = max(orig_rect.height * 2.0, font_size * 3.0)
            insert_rect = fitz.Rect(
                max(0, right_edge - width),
                orig_rect.y0,
                right_edge,
                orig_rect.y0 + height,
            )

            rc = page.insert_textbox(
                insert_rect,
                text,
                fontname=font_name,
                fontfile=font_path,
                fontsize=font_size,
                color=(r, g, b),
                align=fitz.TEXT_ALIGN_RIGHT,
            )
            if rc < 0:
                # Text overflowed — retry with smaller font to fit the box
                smaller = font_size * 0.7
                page.insert_textbox(
                    insert_rect,
                    text,
                    fontname=font_name,
                    fontfile=font_path,
                    fontsize=smaller,
                    color=(r, g, b),
                    align=fitz.TEXT_ALIGN_RIGHT,
                )

    doc.save(output_path, garbage=4, deflate=True)
    doc.close()
