"""
OVERWATCH — Autonomous Platform Engineering for Forgewing

This is the external control plane. It monitors, diagnoses, heals,
and reports on the Forgewing platform without being part of it.
"NEXUS" remains the daemon's internal identity; "Overwatch" is the
operator-facing brand at platform.vaultscaler.com.

Separation principle: Overwatch never imports from aria-platform.
It connects through AWS APIs, Neptune reads, and HTTP endpoints.
"""
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from nexus.config import CONSOLE_PORT, MODE
from nexus.dashboard import routes as dashboard_routes

STATIC_DIR = Path(__file__).parent / "dashboard" / "static"
INDEX_FILE = STATIC_DIR / "index.html"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("nexus")

app = FastAPI(
    title="Overwatch",
    version="0.2.0",
    description="Autonomous platform engineering for Forgewing",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_routes.router, prefix="/api")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health():
    """Liveness probe — lightweight, no external calls."""
    import os
    return {
        "status": "online",
        "mode": MODE,
        "version": "0.2.0",
        "system": "overwatch",
        "commit": os.environ.get("GIT_SHA", "unknown"),
    }


@app.get("/")
async def root():
    """Serve the operator dashboard SPA, or a JSON pointer if it's missing."""
    if INDEX_FILE.exists():
        return FileResponse(str(INDEX_FILE))
    return JSONResponse(
        {
            "system": "overwatch",
            "mode": MODE,
            "docs": "/docs",
            "health": "/health",
            "api": "/api/status",
        }
    )


@app.on_event("startup")
async def startup():
    logger.info(
        "Overwatch starting in %s mode on port %s", MODE, CONSOLE_PORT
    )
    if MODE == "production":
        logger.info("Connecting to Neptune, ECS, CloudWatch...")
    else:
        logger.info("Local mode — all external calls mocked")
    try:
        from nexus.capabilities.scheduled_diagnosis import start_scheduler
        start_scheduler()
    except Exception:
        logger.exception("scheduled diagnosis scheduler failed to start")
    try:
        from nexus.capabilities.ci_cycle import start_ci_cycle
        start_ci_cycle()
    except Exception:
        logger.exception("ci_cycle scheduler failed to start")


@app.on_event("shutdown")
async def shutdown():
    logger.info("Overwatch shutting down")
