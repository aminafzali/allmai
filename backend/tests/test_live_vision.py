"""Live opt-in Vision test (Phase 3 D verification).

Runs ONE real gapgpt-compatible vision call through the REAL worker path
(sqlite, fake embeddings). Skipped unless RUN_LIVE_VISION=1: the default
`pytest` suite performs ZERO real API calls.

Budget: exactly 1 call. Uses spike dataset figure f1 (known GT:
apple 40, orange 65, banana 25).
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import Base
from app.core.security import hash_password
from app.knowledge.models import Chunk, KnowledgeBase, Source
from app.knowledge.parsers.base import ParsedDocument, ParsedElement
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember
from workers.tasks import run_ingest

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)

FIG = r"G:\spike-tmp\spike3\f1_nocap.png"


def _live():
    if os.environ.get("RUN_LIVE_VISION") != "1":
        pytest.skip("RUN_LIVE_VISION != 1 (live vision test skipped)")
    from app.core.config import get_settings

    if not (get_settings().OPENAI_COMPAT_API_KEY or ""):
        pytest.skip("no OPENAI_COMPAT_API_KEY configured")
    if not os.path.isfile(FIG):
        pytest.skip("spike dataset figure missing")


def test_live_vision_describes_figure():
    _live()
    from app.core.config import get_settings

    s = get_settings()
    old = (s.DOCUMENT_VISION_ENABLED, s.DOC_VISION_MAX_FIGURES)
    s.DOCUMENT_VISION_ENABLED = True
    s.DOC_VISION_MAX_FIGURES = 5
    try:
        db = TestingSession()
        try:
            Base.metadata.drop_all(engine)
            Base.metadata.create_all(engine)
            pw = hash_password("password123")
            u = User(id=uuid.uuid4(), email="u@x.com", password_hash=pw)
            db.add(u)
            w = uuid.uuid4()
            db.add(Workspace(id=w, name="W", type="shared", owner_user_id=u.id))
            db.add(WorkspaceMember(workspace_id=w, user_id=u.id, role="owner"))
            kb = KnowledgeBase(id=uuid.uuid4(), workspace_id=w, title="KB",
                               scope="workspace")
            db.add(kb)
            db.commit()

            class FakeStorage:
                def __init__(self):
                    self.objects = {}

                def put(self, key, data, content_type=""):
                    self.objects[key] = data
                    return key

                def get(self, key):
                    return self.objects[key]

            with open(FIG, "rb") as f:
                blob = f.read()
            doc = ParsedDocument(
                title="live",
                elements=[ParsedElement(
                    element_id="e1", kind="image", page_no=1, reading_order=1,
                    text_markdown="[Figure on page 1: figure without caption]",
                    blob=blob, caption="",
                    metadata={"kind": "image", "element_id": "e1",
                              "needs_vision": True})])
            import workers.tasks as T

            real_parse = T.parse_source
            T.parse_source = lambda *a, **k: doc
            try:
                st = FakeStorage()
                src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id,
                             type="pdf", filename="live.pdf", storage_key="lk",
                             status="pending")
                db.add(src)
                db.commit()
                st.put("lk", b"%PDF-1.4 fake")
                res = run_ingest(
                    src.id, workspace_id=w, db=db, storage=st,
                    embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
            finally:
                T.parse_source = real_parse
            assert res["ok"] is True, res
            assert res["vision"]["processed"] == 1
            img = next(c for c in db.query(Chunk).all()
                       if c.chunk_metadata.get("kind") == "image")
            # Known GT: 40 / 65 / 25 must appear in the persisted description
            # (Latin or Persian digits — the model may use either).
            assert ("40" in img.content or "۴۰" in img.content)
            assert ("65" in img.content or "۶۵" in img.content)
            assert ("25" in img.content or "۲۵" in img.content)
            assert img.chunk_metadata.get("vision_model")
        finally:
            db.close()
    finally:
        s.DOCUMENT_VISION_ENABLED, s.DOC_VISION_MAX_FIGURES = old
