"""Architecture conformance: vendors stay behind our abstractions.

- Docling/RAG-Anything/LightRAG: REMOVED from the extraction path —
  forbidden everywhere (structured extraction is Gemini Flash via
  GapGPT + deterministic local parsers).
- LangGraph: only inside app/agents/runtime/
- Zep: NOT used in MVP (interface + Postgres provider only)
- Video: audio-track transcription only (no frame analysis)
"""

from pathlib import Path

import pytest

from app.agents.registry import AGENT_CATALOG, get_agent_spec
from app.knowledge.models import SOURCE_TYPES
from app.knowledge.parsers.base import UnsupportedSourceError, validate_source_type
from app.memory.postgres_provider import PostgresMemoryProvider

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"


def _py_files(root: Path):
    return [p for p in root.rglob("*.py") if "tests" not in p.parts]


def test_no_vendor_imports_outside_adapters():
    import re

    violations = []
    for path in _py_files(APP):
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(APP).as_posix()
        # Docling/RAG-Anything/EasyOCR were deleted: any import or
        # attribute usage anywhere in app/ is a regression (the old
        # parsers/ exception is gone). Plain historical mentions in
        # comments/docstrings ("X was removed") are allowed.
        if re.search(r"(?<![\w.])(import docling|from docling|docling\.)", text):
            violations.append(f"{rel}: docling (removed)")
        if re.search(r"(?<![\w.])(import raganything|from raganything|raganything\.)", text):
            violations.append(f"{rel}: raganything (removed)")
        if re.search(r"(?<![\w.])(import easyocr|from easyocr|easyocr\.)", text):
            violations.append(f"{rel}: easyocr (removed)")
        if re.search(r"(?<![\w.])(import lightrag|from lightrag|lightrag\.)", text):
            violations.append(f"{rel}: lightrag (removed with Docling)")
        # SDK imports only (our own agents/runtime/* module path is fine)
        if re.search(r"(?<![\w.])(import langgraph|from langgraph)(?![\w.])", text):
            if not rel.startswith("agents/runtime/"):
                violations.append(f"{rel}: langgraph")
        if "import zep" in text or "from zep" in text:
            violations.append(f"{rel}: zep (MVP is Postgres-only)")
    assert violations == []


def test_video_accepted_and_audio_guarded():
    # Video = audio-track transcription only (mp4/webm/mov); unknown types
    # and mismatched extensions are still rejected loudly.
    assert "video" in SOURCE_TYPES
    assert "excel" in SOURCE_TYPES
    assert "csv" in SOURCE_TYPES
    assert validate_source_type("video", "clip.mp4") == "video"
    assert validate_source_type("excel", "data.xlsx") == "excel"
    assert validate_source_type("csv", "data.csv") == "csv"
    with pytest.raises(UnsupportedSourceError):
        validate_source_type("video", "clip.avi")
    with pytest.raises(UnsupportedSourceError):
        validate_source_type("camembert")
    with pytest.raises(UnsupportedSourceError):
        validate_source_type("audio", "voice.ogg")  # MVP: mp3/wav/m4a only
    assert validate_source_type("audio", "class1.mp3") == "audio"
    assert validate_source_type("pdf") == "pdf"


def test_agent_catalog_generic():
    # Phase 1: AGENT_CATALOG stays as the legacy fallback seed; the DB-backed
    # AgentDefinition table is the primary source. Catalog entries must keep
    # goal+tools, and the two legacy keys must still resolve.
    assert {"teacher_lesson_planner", "student_academic_coach"} <= set(AGENT_CATALOG)
    for key, spec in AGENT_CATALOG.items():
        assert spec["goal"] and spec["tools"]
    with pytest.raises(KeyError):
        get_agent_spec("nope")


def test_phase1_models_registered():
    from app.agents.definitions_models import (  # noqa: F401
        AgentDefinition,
        AgentDefinitionKnowledge,
        AgentKnowledgeAssignment,
    )
    from app.common.base import Base

    for table in ("agent_definitions", "agent_definition_knowledge",
                  "agent_kb_assignments"):
        assert table in Base.metadata.tables
    assert "scope" in Base.metadata.tables["knowledge_bases"].c
    for col in ("definition_id", "owner_user_id", "custom_instructions",
                "runtime_state"):
        assert col in Base.metadata.tables["agents"].c


def test_memory_provider_is_postgres():
    assert PostgresMemoryProvider.provider_name == "postgres"
