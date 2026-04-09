"""
NEXUS Platform — Autonomous Operations for Forgewing

This is the control plane. It monitors, diagnoses, heals, and reports
on the Forgewing platform without being part of it.

Separation principle: NEXUS never imports from aria-platform.
It connects through AWS APIs, Neptune reads, and HTTP endpoints.
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nexus.config import CONSOLE_PORT, MODE
from nexus.dashboard import routes as dashboard_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("nexus")

app = FastAPI(
    title="NEXUS Platform",
    version="0.1.0",
    description="Autonomous operations system for Forgewing",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_routes.router, prefix="/api")


@app.get("/health")
async def health():
    """Liveness probe — lightweight, no external calls."""
    return {
        "status": "online",
        "mode": MODE,
        "version": "0.1.0",
        "system": "nexus-platform",
    }


@app.get("/")
async def root():
    """Root endpoint — points the operator at the dashboard API."""
    return {
        "system": "nexus-platform",
        "mode": MODE,
        "docs": "/docs",
        "health": "/health",
        "api": "/api/status",
    }


@app.on_event("startup")
async def startup():
    logger.info(
        "NEXUS Platform starting in %s mode on port %s", MODE, CONSOLE_PORT
    )
    if MODE == "production":
        logger.info("Connecting to Neptune, ECS, CloudWatch...")
    else:
        logger.info("Local mode — all external calls mocked")


@app.on_event("shutdown")
async def shutdown():
    logger.info("NEXUS Platform shutting down")
