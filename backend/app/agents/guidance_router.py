"""Career-guidance endpoints (moshaver): RIASEC quiz, profile, job match.

Workspace-member gated; profile facts are per-user (memory provider).
Job matching reads the workspace KBs + moshaver-assigned global KBs.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agents import guidance as _g
from app.auth.service import get_current_user
from app.core.database import get_db
from app.users.models import User
from app.workspaces.models import Workspace
from app.workspaces.service import resolve_workspace

router = APIRouter(tags=["guidance"])


@router.get("/workspaces/{workspace_id}/guidance/quiz")
def get_quiz(ws: Workspace = Depends(resolve_workspace),
             db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    return {"questions": _g.QUIZ, "scale": _g.SCALE,
            "types": {k: {"fa": v["fa"], "en": v["en"], "desc": v["desc"]}
                      for k, v in _g.RIASEC_TYPES.items()}}


@router.post("/workspaces/{workspace_id}/guidance/quiz")
def submit_quiz(body: dict,
                ws: Workspace = Depends(resolve_workspace),
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    from fastapi import HTTPException

    try:
        result = _g.score_quiz((body or {}).get("answers") or {})
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    saved = _g.save_guidance_profile(db, ws, user, result)
    return {**result, "saved": saved}


@router.get("/workspaces/{workspace_id}/guidance/profile")
def get_profile(ws: Workspace = Depends(resolve_workspace),
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    return _g.read_guidance_profile(db, ws, user)


@router.post("/workspaces/{workspace_id}/guidance/match")
def post_match(body: dict,
               ws: Workspace = Depends(resolve_workspace),
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    code = str((body or {}).get("code") or "")
    if not code:
        code = str(_g.read_guidance_profile(db, ws, user).get("code") or "")
    try:
        limit = int((body or {}).get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    return _g.match_jobs(db, ws, user, code, limit)
