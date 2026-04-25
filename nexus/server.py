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
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from nexus.config import CONSOLE_PORT, MODE
from nexus.dashboard import routes as dashboard_routes
from nexus.dashboard import pipeline_events_api
from nexus.dashboard import dogfood_report_api
from nexus.dashboard import v2_executions_api
from nexus.dashboard import echo_routes
from nexus.dashboard import pipeline_truth_routes
from nexus.askcustomer import api as askcustomer_api
from nexus.mechanism1 import api as classifier_api
from nexus.ontology import query_api as ontology_query_api
from nexus.ontology import routes as ontology_routes
from nexus.routes.operator_routes import router as operator_router

STATIC_DIR = Path(__file__).parent / "dashboard" / "static"

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
app.include_router(ontology_routes.router, prefix="/api/ontology")
app.include_router(ontology_query_api.router, prefix="/api/ontology")
app.include_router(pipeline_events_api.router)
app.include_router(v2_executions_api.router)
app.include_router(pipeline_truth_routes.router)
app.include_router(dogfood_report_api.router)
app.include_router(askcustomer_api.router)
app.include_router(classifier_api.router)
app.include_router(operator_router, prefix="/api/operator", tags=["operator"])
app.include_router(echo_routes.router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# overwatch-web (operator-facing /engineering UI). Mounted AFTER API
# routers so they take precedence. html=True makes StaticFiles serve
# index.html for unmatched paths under /engineering (SPA mode).
_OVERWATCH_WEB_DIST = Path(__file__).parent.parent / "overwatch-web" / "dist"
if _OVERWATCH_WEB_DIST.exists():
    app.mount(
        "/engineering",
        StaticFiles(directory=str(_OVERWATCH_WEB_DIST), html=True),
        name="overwatch_web",
    )
    logger.info("overwatch-web mounted at /engineering")
else:
    logger.warning("overwatch-web/dist/ missing; /engineering route disabled")


_COGNITO_DOMAIN = "overwatch-vaultscaler-418295677815.auth.us-east-1.amazoncognito.com"
_COGNITO_CLIENT_ID = "4ceqt9ed8esoqqnu3mao482223"
_LOGOUT_URI = "https://platform.vaultscaler.com"

# ALB session cookies: default name is AWSELBAuthSessionCookie, sharded
# by index when value exceeds one cookie. Clearing -0..-3 covers the
# common 1-4 shard range.
_ALB_AUTH_COOKIE_NAMES = (
    "AWSELBAuthSessionCookie-0",
    "AWSELBAuthSessionCookie-1",
    "AWSELBAuthSessionCookie-2",
    "AWSELBAuthSessionCookie-3",
)


@app.get("/oauth2/sign-out")
async def sign_out() -> RedirectResponse:
    """End the operator's Cognito session and rebound through ALB to sign-in.

    Browser flow:
      1. Backend returns 302 to Cognito /logout with Set-Cookie headers
         that expire the ALB session cookies in the browser.
      2. Cognito invalidates the IdP session and redirects to logout_uri
         (root of platform.vaultscaler.com).
      3. ALB sees no valid session cookie (we cleared them) and the
         authenticate-cognito action redirects to Cognito sign-in.
    """
    cognito_logout = (
        f"https://{_COGNITO_DOMAIN}/logout"
        f"?client_id={_COGNITO_CLIENT_ID}"
        f"&logout_uri={_LOGOUT_URI}"
    )
    response = RedirectResponse(url=cognito_logout, status_code=302)
    for name in _ALB_AUTH_COOKIE_NAMES:
        # Match ALB cookie attributes: path=/, host-only (no Domain),
        # Secure + HttpOnly. delete_cookie sets Max-Age=0.
        response.delete_cookie(
            key=name, path="/", secure=True, httponly=True, samesite="lax",
        )
    return response


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
    return RedirectResponse(url="/engineering", status_code=302)


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
    try:
        from nexus.capabilities.deploy_cycle import start_deploy_cycle
        start_deploy_cycle()
    except Exception:
        logger.exception("deploy_cycle scheduler failed to start")
    try:
        from nexus.overwatch_v2.tools.read_tools._registration import register_all_read_tools
        register_all_read_tools()
        logger.info("V2 read tools registered at startup")
    except Exception:
        logger.exception("V2 read tool registration failed; Echo will run toolless")


@app.on_event("shutdown")
async def shutdown():
    logger.info("Overwatch shutting down")
