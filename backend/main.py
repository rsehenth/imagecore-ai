"""ImageCore AI - app FastAPI local (factory, middleware de seguranca, handlers, entrypoint).

Estrutura e middleware derivados de um projeto anterior do autor (backend/main.py), projeto de
referencia pessoal: bind loopback, validacao de Host (anti DNS-rebinding), bloqueio cross-site,
validacao de Origin/Referer para metodos mutantes, cabecalhos de seguranca e CSP.
"""

import ipaddress
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api import routes_generations, routes_projects, routes_references, routes_system
from backend.core.config import PROJECT_ROOT, ConfigError, Settings, load_settings
from backend.generation.base import GenerationError, GenerationUnavailableError, VramInsufficientError
from backend.generation.registry import create_provider
from backend.jobs.manager import GenerationJobManager
from backend.storage.database import Database
from backend.storage.gallery import ConflictError, GenerationRepository, NotFoundError
from backend.storage.image_sandbox import SandboxRejected
from backend.storage.projects import ProjectRepository
from backend.storage.uploads import ReferenceRejected, ReferenceStore

logger = logging.getLogger("imagecore")

FRONTEND_DIR = PROJECT_ROOT / "frontend"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CSP_HEADER = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
)


def _split_host_port(raw: str) -> tuple[str, int | None]:
    value = raw.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return value, None
        host = value[1:end]
        rest = value[end + 1 :]
        if rest.startswith(":") and rest[1:].isdigit():
            return host, int(rest[1:])
        return host, None
    if value.count(":") == 1:
        host, _, port_str = value.partition(":")
        if port_str.isdigit():
            return host, int(port_str)
    return value, None


def _is_private_ip(value: str | None) -> bool:
    """Loopback ou rede privada. So IP literal: nomes DNS nunca passam (anti DNS rebinding)."""
    try:
        ip = ipaddress.ip_address((value or "").split("%")[0])
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _is_local_origin_url(url: str, expected_port: int | None, lan_host: str | None = None) -> bool:
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    if parsed.hostname.lower() not in _LOOPBACK_HOSTS and parsed.hostname.lower() != lan_host:
        return False
    if expected_port is not None:
        origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if origin_port != expected_port:
            return False
    return True


def _register_local_security_middleware(app: FastAPI, allow_lan: bool = False) -> None:
    """`allow_lan`: escuta na rede local; aceita apenas clientes e Hosts que sejam IPs privados literais."""

    @app.middleware("http")
    async def local_only_security(request: Request, call_next):
        host_header = request.headers.get("host", "")
        host_name, host_port = _split_host_port(host_header)
        is_testclient = bool(request.client and request.client.host == "testclient")
        lan_host = host_name if allow_lan and _is_private_ip(host_name) else None
        if allow_lan and not (is_testclient or _is_private_ip(request.client.host if request.client else None)):
            return JSONResponse(status_code=403, content={"error": "Acesso permitido apenas na rede local.", "error_code": "lan_only"})
        if host_name not in _LOOPBACK_HOSTS and lan_host is None and not (is_testclient and host_name == "testserver"):
            return JSONResponse(status_code=403, content={"error": "Host nao autorizado.", "error_code": "host_unauthorized"})

        sec_fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
        if sec_fetch_site == "cross-site":
            return JSONResponse(status_code=403, content={"error": "Requisicao cross-site bloqueada.", "error_code": "cross_site_blocked"})

        is_api_or_mutating = request.url.path.startswith("/api") or request.method in _MUTATING_METHODS
        if is_api_or_mutating:
            origin = request.headers.get("origin")
            if origin is not None:
                if not _is_local_origin_url(origin, host_port, lan_host):
                    return JSONResponse(status_code=403, content={"error": "Origem nao autorizada.", "error_code": "origin_unauthorized"})
            elif request.method in _MUTATING_METHODS:
                if sec_fetch_site and sec_fetch_site not in ("same-origin", "none"):
                    return JSONResponse(status_code=403, content={"error": "Origem de requisicao bloqueada.", "error_code": "origin_blocked"})
                referer = request.headers.get("referer")
                if referer and not _is_local_origin_url(referer, host_port, lan_host):
                    return JSONResponse(status_code=403, content={"error": "Referer nao autorizado.", "error_code": "referer_unauthorized"})

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith("/api"):
            response.headers.setdefault("Content-Security-Policy", _CSP_HEADER)
        return response


def _error_content(exc: BaseException, default_code: str | None = None) -> dict:
    """Corpo padrao de erro: mensagem tecnica (pt-BR, preservada para diagnostico) +
    `error_code` estavel e independente de idioma (Etapa 10.1) para a interface traduzir
    (frontend/js/i18n.js) + `error_details` com valores estruturados para interpolacao.
    Sem codigo conhecido, a resposta segue inalterada (fallback seguro na UI)."""
    content: dict = {"error": str(exc)}
    code = getattr(exc, "code", None) or default_code
    if code:
        content["error_code"] = code
    details = getattr(exc, "details", None)
    if isinstance(details, dict) and details:
        content["error_details"] = details
    return content


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(GenerationUnavailableError)
    async def generation_unavailable(request: Request, exc: GenerationUnavailableError):
        content = _error_content(exc, default_code="engine_unavailable")
        content["hint"] = "Inicie o motor de geracao configurado (provider.name em config/settings.yaml)."
        return JSONResponse(status_code=503, content=content)

    @app.exception_handler(VramInsufficientError)
    async def vram_insufficient(request: Request, exc: VramInsufficientError):
        return JSONResponse(status_code=503, content=_error_content(exc, default_code="vram_insufficient"))

    @app.exception_handler(GenerationError)
    async def generation_error(request: Request, exc: GenerationError):
        return JSONResponse(status_code=502, content=_error_content(exc, default_code="generation_failed"))

    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content=_error_content(exc))

    @app.exception_handler(ConflictError)
    async def conflict_error(request: Request, exc: ConflictError):
        return JSONResponse(status_code=409, content=_error_content(exc))

    @app.exception_handler(ReferenceRejected)
    async def reference_rejected(request: Request, exc: ReferenceRejected):
        return JSONResponse(status_code=exc.status, content=_error_content(exc))

    @app.exception_handler(SandboxRejected)
    async def sandbox_rejected(request: Request, exc: SandboxRejected):
        return JSONResponse(status_code=exc.status, content=_error_content(exc))

    @app.exception_handler(PermissionError)
    async def permission_error(request: Request, exc: PermissionError):
        return JSONResponse(status_code=403, content=_error_content(exc))

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content=_error_content(exc))

    @app.exception_handler(ConfigError)
    async def config_error(request: Request, exc: ConfigError):
        content = _error_content(exc)
        content["error"] = f"Configuracao invalida: {exc}"
        content["error_code"] = "config_invalid"
        return JSONResponse(status_code=500, content=content)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(level=settings.app.log_level.upper())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.storage.database_path)
        db.connect()
        data_dir = settings.storage.database_path.parent
        uploads = ReferenceStore(db, data_dir, settings.upload)
        repo = GenerationRepository(db, data_dir)
        repo.retry_file_deletions()
        projects = ProjectRepository(db, data_dir)
        provider = create_provider(settings)
        jobs = GenerationJobManager(repo, provider, uploads, gpu_cfg=settings.gpu)
        await jobs.start()

        app.state.settings = settings
        app.state.db = db
        app.state.uploads = uploads
        app.state.repo = repo
        app.state.projects = projects
        app.state.provider = provider
        app.state.jobs = jobs
        logger.info(
            "ImageCore AI iniciado | provider=%s db=%s porta=%d", provider.name, settings.storage.database_path, settings.app.port
        )
        try:
            yield
        finally:
            await jobs.stop()
            await provider.aclose()
            db.close()

    app = FastAPI(title=settings.app.name, version="0.1.0", lifespan=lifespan)
    _register_local_security_middleware(app, allow_lan=settings.app.allow_lan)
    _register_exception_handlers(app)
    app.include_router(routes_system.router, prefix="/api")
    app.include_router(routes_references.router, prefix="/api")
    app.include_router(routes_generations.router, prefix="/api")
    app.include_router(routes_projects.router, prefix="/api")
    if FRONTEND_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(FRONTEND_DIR / "index.html")

    return app


def _acquire_single_instance(host: str, port: int, lock_path: Path):
    """Garante instancia unica no workspace e detecta porta ocupada antes do uvicorn."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        raise RuntimeError(
            f"Ja existe outra instancia do ImageCore AI em execucao neste diretorio (lock: {lock_path})."
        )

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError as exc:
        lock_file.close()
        raise RuntimeError(f"A porta {port} em {host} ja esta ocupada por outro processo ({exc}).") from exc
    finally:
        probe.close()
    return lock_file


app = create_app()


if __name__ == "__main__":
    runtime_settings = load_settings()
    lock_path = runtime_settings.storage.database_path.parent / ".backend.lock"
    try:
        _lock_handle = _acquire_single_instance(runtime_settings.app.host, runtime_settings.app.port, lock_path)
    except RuntimeError as exc:
        print(f"[ImageCore AI] {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        uvicorn.run(
            app,
            host=runtime_settings.app.host,
            port=runtime_settings.app.port,
            log_level=runtime_settings.app.log_level,
        )
    finally:
        _lock_handle.close()
