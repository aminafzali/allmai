from fastapi import APIRouter

router = APIRouter(prefix="/users", tags=["users"])
# Phase 1: user CRUD + RBAC endpoints.
