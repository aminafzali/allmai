"""LIVE end-to-end against a RUNNING stack (real AI, real ingest).

Requires: Postgres+Redis+API+worker up, migrated, AI key configured.
Runs ONLY when RUN_LIVE_E2E=1 and the API health check passes.
Uses unique e-mails/workspace and deletes the workspace afterwards.

    RUN_LIVE_E2E=1 .venv/Scripts/python.exe -m pytest tests/test_live_e2e.py -q
"""

import os
import time
import uuid

import pytest
import httpx

API = os.environ.get("LIVE_API_BASE", "http://127.0.0.1:8000")
LIVE = os.environ.get("RUN_LIVE_E2E") == "1"


def _api_ok() -> bool:
    try:
        r = httpx.get(f"{API}/health", timeout=10)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not (LIVE and _api_ok()), reason="live stack unavailable (need RUN_LIVE_E2E=1 + running API)")


@pytest.fixture()
def session():
    tag = uuid.uuid4().hex[:8]
    email = f"live-{tag}@x.com"
    c = httpx.Client(base_url=API, timeout=120)
    assert c.post("/auth/register", json={"email": email, "password": "livepass123"}).status_code == 201
    tokens = c.post("/auth/login", json={"email": email, "password": "livepass123"}).json()
    c.headers["Authorization"] = f"Bearer {tokens['access_token']}"
    state = {"client": c, "wid": None}
    yield state
    try:
        if state["wid"]:
            c.delete(f"/workspaces/{state['wid']}")
    finally:
        c.close()


def test_live_full_flow(session):
    c = session["client"]
    # workspace + KB
    wid = c.post("/workspaces", json={"name": "live", "type": "shared"}).json()["id"]
    session["wid"] = wid
    kb = c.post(f"/workspaces/{wid}/knowledge-bases", json={"title": "LiveKB"}).json()["id"]

    # --- real audio transcription: synth 1s tone wav, no ffmpeg needed ---
    import math
    import struct
    import wave
    from io import BytesIO

    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = b"".join(
            struct.pack("<h", int(10000 * math.sin(2 * math.pi * 440 * t / 16000)))
            for t in range(16000)
        )
        w.writeframes(frames)
    audio_blob = buf.getvalue()
    src = c.post(
        f"/workspaces/{wid}/knowledge-bases/{kb}/sources",
        data={"type": "audio"},
        files={"file": ("tone.wav", audio_blob, "audio/wav")},
    ).json()
    assert src["status"] in ("pending", "processing")

    # note source (deterministic content for retrieval assertions)
    c.post(
        f"/workspaces/{wid}/knowledge-bases/{kb}/sources/link",
        json={"type": "note", "title": " fakta", "content": "The Saffron Nebula Experiment proves stellar saffron synthesis."},
    )

    # wait for worker processing (up to ~4 min for audio+note)
    deadline = time.time() + 240
    ready = failed = []
    while time.time() < deadline:
        items = c.get(f"/workspaces/{wid}/knowledge-bases/{kb}/sources").json()
        ready = [s for s in items if s["status"] == "ready"]
        failed = [s for s in items if s["status"] == "failed"]
        if len(ready) + len(failed) >= 2 and not any(s["status"] in ("pending", "processing") for s in items):
            break
        time.sleep(5)
    assert failed == [], f"ingest failures: {failed}"
    assert len(ready) == 2, f"expected 2 ready sources, got: {items}"

    # --- real retrieval over real embeddings ---
    hits = c.post(f"/workspaces/{wid}/knowledge/search",
                  json={"query": "Saffron Nebula stellar synthesis", "kb_id": kb, "top_k": 5}).json()["chunks"]
    assert any("Saffron Nebula" in h["content"] for h in hits), hits

    # --- real teacher agent (structured plan, grounded refs) ---
    ta = c.post("/workspaces/{wid}/agents",
                json={"key": "teacher_lesson_planner", "name": "T"}).json()
    plan = c.post(f"/workspaces/{wid}/agents/{ta['id']}/lesson-plans", json={
        "kb_id": kb, "chapter": "Saffron Nebula Experiment", "grade": "9th",
        "duration_minutes": 30, "teaching_style": "lecture", "instructions": "Keep it short.",
    }).json()
    assert plan["plan"]["title"]
    assert plan["plan"]["references"], "plan must carry knowledge references"
    got = c.get(f"/workspaces/{wid}/agents/{ta['id']}/lesson-plans/{plan['lesson_plan_id']}").json()
    assert got["plan"]["title"] == plan["plan"]["title"]

    # --- real student agent (profile -> plan -> chat -> progress) ---
    sa = c.post("/workspaces/{wid}/agents",
                json={"key": "student_academic_coach", "name": "S"}).json()
    c.post(f"/workspaces/{wid}/agents/{sa['id']}/profile",
           json={"facts": {"grade": "9th", "weak_subject": "physics"}})
    sp = c.post(f"/workspaces/{wid}/agents/{sa['id']}/study-plans",
                json={"goals": ["Understand the Saffron Nebula Experiment"], "weekly_hours": 4}).json()
    assert sp["plan"]["weeks"], "study plan must have weeks"
    cid = sp["conversation_id"]
    chat = c.post(f"/workspaces/{wid}/agents/{sa['id']}/coach/chat",
                  json={"message": "What should I study first?", "conversation_id": cid}).json()
    assert chat["answer"] and chat["has_plan"] is True
    task_id = sp["plan"]["weeks"][0]["tasks"][0]["id"]
    pr = c.post(f"/workspaces/{wid}/agents/{sa['id']}/progress",
                json={"conversation_id": cid, "completed_task_ids": [task_id]}).json()
    assert pr["completed_task_ids"] == [task_id]

    # --- isolation: second user sees nothing ---
    email2 = f"live2-{uuid.uuid4().hex[:8]}@x.com"
    c2 = httpx.Client(base_url=API, timeout=60)
    try:
        c2.post("/auth/register", json={"email": email2, "password": "livepass123"})
        t2 = c2.post("/auth/login", json={"email": email2, "password": "livepass123"}).json()["access_token"]
        c2.headers["Authorization"] = f"Bearer {t2}"
        assert c2.get(f"/workspaces/{wid}/knowledge-bases").status_code == 404
        assert c2.post(f"/workspaces/{wid}/knowledge/search",
                       json={"query": "Saffron"}).status_code == 404
        assert c2.post(f"/workspaces/{wid}/agents/{ta['id']}/chat",
                       json={"message": "hi"}).status_code == 404
    finally:
        c2.close()
