"""
Multimodal ingest — turn a syllabus / assignment brief / schedule (image or PDF)
into dated task lines using Gemini, then reuse the same pipeline as text ingest.

Gemini returns natural-language task strings with the deadline inline (e.g.
"Submit lab report by Oct 14"), so they flow straight through the existing
deadline parser in `tools.add_tasks` — no separate schema to keep in sync.

Falls back gracefully: if no API key is set or the model call fails (the free
tier throws transient 503s), the caller surfaces a clear message instead of
crashing the demo.
"""

from __future__ import annotations

import json
import os
import time

_PROMPT = (
    "You are extracting deadline-bearing action items from a document image or PDF "
    "(a course syllabus, an assignment brief, a project schedule, or similar).\n"
    "Return ONLY a JSON array of short task strings. Each string must name one task "
    "and include its deadline inline in natural language, e.g.\n"
    '  ["Submit lab report by Oct 14", "Project proposal due Nov 21", '
    '"Final paper deadline Dec 3"]\n'
    "Rules: include an item ONLY if it has a concrete date or deadline. Keep each "
    "string under ~12 words. Do not invent dates. If nothing has a deadline, return []."
)

SUPPORTED = {"image/png", "image/jpeg", "image/jpg", "image/webp", "application/pdf"}


def extract_task_lines(data: bytes, mime_type: str, model: str | None = None) -> dict:
    """Extract dated task lines from raw document bytes.

    Returns {"status": "success", "lines": [...]} or {"status": "error", "message": ...}.
    """
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key or key == "your-key-here":
        return {"status": "error", "message": "GOOGLE_API_KEY not set — image ingest needs Gemini."}
    if mime_type not in SUPPORTED:
        return {"status": "error", "message": f"unsupported type {mime_type}; use PNG/JPEG/WebP/PDF."}

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        model = model or os.environ.get("MODEL", "gemini-3-flash-preview")
        part = types.Part.from_bytes(data=data, mime_type=mime_type)
        cfg = types.GenerateContentConfig(response_mime_type="application/json")

        # The free tier throws transient 503s under load — retry a few times.
        last = None
        for attempt in range(3):
            try:
                resp = client.models.generate_content(model=model, contents=[part, _PROMPT], config=cfg)
                raw = (resp.text or "").strip()
                lines = json.loads(raw) if raw else []
                if isinstance(lines, dict):  # tolerate {"tasks": [...]}
                    lines = next((v for v in lines.values() if isinstance(v, list)), [])
                lines = [str(x).strip() for x in lines if str(x).strip()]
                return {"status": "success", "lines": lines}
            except Exception as e:  # noqa: BLE001 — retry only on transient server errors
                last = e
                if "503" in str(e) or "UNAVAILABLE" in str(e) or "overloaded" in str(e).lower():
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        raise last
    except json.JSONDecodeError:
        return {"status": "error", "message": "model returned non-JSON; try a clearer image."}
    except Exception as e:  # quota, network, persistent 503
        msg = "model busy (free-tier spike) — try again in a moment." if "503" in str(e) else f"{type(e).__name__}: {e}"
        return {"status": "error", "message": f"vision extraction failed: {msg}"}
