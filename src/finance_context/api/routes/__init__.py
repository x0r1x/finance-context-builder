from __future__ import annotations

from fastapi import APIRouter

from finance_context.api.routes import documents, jobs, slices, trace

router = APIRouter()
router.include_router(jobs.router)
router.include_router(documents.router)
router.include_router(trace.router)
router.include_router(slices.router)

__all__ = ["router"]
