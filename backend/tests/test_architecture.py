"""Architecture conformance: vendors stay behind our abstractions.

- RAG-Anything: importable only inside app/knowledge/parsers/
- LangGraph: only inside app/agents/runtime/
- Zep: NOT used in MVP (interface + Postgres provider only)
- Video: rejected, never processed
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
        if "raganything" in text and not rel.startswith("knowledge/parsers/"):
            violations.append(f"{rel}: raganything")
        if "raganything" in text and rel.startswith("knowledge/parsers/"):
            # Full-engine USAGE (imports/calls) is forbidden; merely naming
            # these paths in docstrings/comments is allowed and intended.
            for banned in ("raganything.raganything",
                           "from raganything import RAGAnything",
                           "process_document_complete(",
                           "insert_content_list(", "ainsert(", "aquery("):
                if banned in text:
                    violations.append(f"{rel}: full-engine {banned}")
        if re.search(r"(?<![\w.])(import lightrag|from lightrag|lightrag\.)", text):
            violations.append(f"{rel}: lightrag (Phase 2 is parse-only)")
        if re.search(r"(?<![\w.])(import docling|from docling)(?![\w.])", text):
            if not rel.startswith("knowledge/parsers/"):
                violations.append(f"{rel}: docling")
        # SDK imports only (our own agents/runtime/* module path is fine)
        if re.search(r"(?<![\w.])(import langgraph|from langgraph)(?![\w.])", text):
            if not rel.startswith("agents/runtime/"):
                violations.append(f"{rel}: langgraph")
        if "import zep" in text or "from zep" in text:
            violations.append(f"{rel}: zep (MVP is Postgres-only)")
    assert violations == []


def test_video_rejected_and_audio_guarded():
    assert "video" not in SOURCE_TYPES
    with pytest.raises(UnsupportedSourceError):
        validate_source_type("video")
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
