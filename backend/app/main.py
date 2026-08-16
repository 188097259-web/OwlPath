import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from .api import router
from .config import Settings
from .db import Database
from .engine import RunEngine
from .errors import APIError
from .providers import ProviderClient
from .security import SecretStore


logger = logging.getLogger("owlpath")


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Dict[str, Any]) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 404 and "." not in Path(path).name:
            return await super().get_response("index.html", scope)
        return response


def _error(request: Request, status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(status_code=status_code, content={
        "error": {"code": code, "message": message, "details": details, "request_id": request_id}
    })


def create_app(settings: Settings = None, provider_client: ProviderClient = None) -> FastAPI:
    effective = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        db = Database(effective.database_path)
        db.initialize()
        secrets = SecretStore(effective.database_path, effective.master_key)
        client = provider_client or ProviderClient(
            timeout_seconds=effective.provider_timeout_seconds,
            max_response_bytes=effective.max_provider_response_bytes,
        )
        engine = RunEngine(db, secrets, client)
        engine.recover_interrupted()
        application.state.settings = effective
        application.state.db = db
        application.state.secrets = secrets
        application.state.provider_client = client
        application.state.engine = engine
        yield
        await engine.shutdown()

    application = FastAPI(
        title="OwlPath（鸮径）",
        description="Research-only multi-model pathogen hypothesis agent. Not clinically validated.",
        version="0.1.0-research",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=effective.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Actor", "Last-Event-ID", "X-OwlPath-Admin-Token"],
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Response:
        request.state.request_id = request.headers.get("X-Request-ID", uuid4().hex)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-OwlPath-Clinical-Status"] = "research-only-not-validated"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Swagger/ReDoc load their own documented CDN assets; keep CSP focused
        # on the clinical web application and JSON API so developer docs remain
        # usable without weakening the application's policy.
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
                "object-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; font-src 'self' data:; "
                "connect-src 'self' http://127.0.0.1:* http://localhost:*"
            )
        if request.url.path.startswith("/api/"):
            # Clinical and provider configuration responses must not be cached by
            # browsers or shared intermediaries, even in this local research build.
            response.headers["Cache-Control"] = "no-store"
        elif response.headers.get("content-type", "").startswith("text/html"):
            # The SPA shell references content-hashed assets. Always revalidate the
            # shell itself so a newly built frontend cannot be hidden by a stale
            # browser-cached index.html.
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        elif request.url.path.startswith("/assets/"):
            # Vite assets are content hashed, so they are safe to cache forever.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @application.exception_handler(APIError)
    async def api_error(request: Request, exc: APIError) -> JSONResponse:
        return _error(request, exc.status_code, exc.code, exc.message, exc.details)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Deliberately omit raw `input` values so provider keys or clinical text
        # cannot leak through validation errors or logs.
        details = [{
            "loc": list(item.get("loc", [])), "message": item.get("msg"), "type": item.get("type")
        } for item in exc.errors()]
        return _error(request, 422, "validation_error", "Request validation failed", details)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return _error(request, exc.status_code, "http_error", str(exc.detail))

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled OwlPath request error id=%s", getattr(request.state, "request_id", "unknown"))
        return _error(request, 500, "internal_error", "An unexpected server error occurred")

    application.include_router(router, prefix="/api")

    frontend_dist = effective.base_dir.parent / "frontend" / "dist"
    if frontend_dist.is_dir() and (frontend_dist / "index.html").exists():
        application.mount("/", SPAStaticFiles(directory=str(frontend_dist), html=True), name="frontend")
    else:
        @application.get("/", include_in_schema=False)
        async def service_root() -> Dict[str, Any]:
            return {
                "service": "OwlPath（鸮径）", "version": "0.1.0-research",
                "status": "research-only-not-clinically-validated", "docs": "/docs",
            }
    return application


app = create_app()
