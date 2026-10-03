from __future__ import annotations

from fastapi import APIRouter

from finance_context.api import documents, jobs, slices, trace_routes

router = APIRouter()
router.include_router(jobs.router)
router.include_router(documents.router)
router.include_router(trace_routes.router)
router.include_router(slices.router)
