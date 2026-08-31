import os
import sys
from pathlib import Path
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
import logging

# ===============================================================================
# 1. RESOLUCIÓN DE RUTAS Y NORMALIZACIÓN SYS.PATH
# ===============================================================================
CURRENT_FILE = Path(__file__).resolve()
APP_DIR = CURRENT_FILE.parent               # .../tlm-backend/app
PROJECT_ROOT = APP_DIR.parent              # .../tlm-backend

for path_str in [str(PROJECT_ROOT), str(APP_DIR)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

# ===============================================================================
# 2. NORMALIZACIÓN DE DATABASE_URL (POSTGRESQL NEON CLOUD)
# ===============================================================================
RAW_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:5432/tlm_workspace"
)

# Adecuación de protocolo para SQLAlchemy (postgres:// -> postgresql://)
if RAW_DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = RAW_DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = RAW_DATABASE_URL

# Forzar parametrización SSL para clústeres Neon Cloud
if "neon.tech" in DATABASE_URL and "sslmode" not in DATABASE_URL:
    DATABASE_URL += "&sslmode=require" if "?" in DATABASE_URL else "?sslmode=require"

os.environ["DATABASE_URL"] = DATABASE_URL

# ===============================================================================
# 3. IMPORTACIÓN DE MÓDULOS DE NÚCLEO Y CONTROLADORES REST
# ===============================================================================
from app.db.session import engine
from app.db import models
from app.api.v1.endpoints import facturas, empresas, usuarios

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api_orchestrator")

# Sincronización de esquemas en PostgreSQL con captura de excepciones
try:
    models.Base.metadata.create_all(bind=engine)
    logger.info(" Base de datos PostgreSQL sincronizada e inicializada correctamente.")
except Exception as e:
    logger.error(f" Error crítico al sincronizar tablas en PostgreSQL: {e}")

# ===============================================================================
# 4. INICIALIZACIÓN DE FASTAPI Y POLÍTICAS CORS
# ===============================================================================
app = FastAPI(
    title="Consola Fiscal B2B - Analytical Engine",
    description="Motor de Ingesta ETL, Control de Accesos (RLS) y Consolidación Financiera.",
    version="1.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================================================================
# 5. MANEJADORES GLOBALES DE EXCEPCIONES
# ===============================================================================
@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    logger.error(f" Excepción de SQL en la ruta {request.url.path}: {str(exc)}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "status": "error",
            "success": False,
            "tipo": "DatabaseError",
            "mensaje": "Ocurrió una inconsistencia en PostgreSQL.",
            "detalle": str(exc.orig) if hasattr(exc, 'orig') else str(exc)
        }
    )

# ===============================================================================
# 6. REGISTRO DE CONTROLADORES REST (V1)
# ===============================================================================
app.include_router(facturas.router, prefix="/api/v1/facturas", tags=["Facturas & Motor ETL"])
app.include_router(empresas.router, prefix="/api/v1/empresas", tags=["Empresas & Clientes"])
app.include_router(usuarios.router, prefix="/api/v1/auth", tags=["Seguridad & Control de Accesos"])

# ===============================================================================
# 7. SERVIDO DEL FRONTEND Y HEALTH CHECKS (COMPATIBILIDAD GET / HEAD)
# ===============================================================================
GLOBAL_ROOT = PROJECT_ROOT.parent

POSSIBLE_FRONTEND_PATHS = [
    os.path.join(GLOBAL_ROOT, "tlm-frontend"),
    os.path.join(PROJECT_ROOT, "tlm-frontend"),
    os.path.join(APP_DIR, "static"),
]

FRONTEND_DIR = next((path for path in POSSIBLE_FRONTEND_PATHS if os.path.isdir(path)), None)

if FRONTEND_DIR:
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

# Soporte explícito de métodos GET y HEAD para evitar el error 405 en Render
@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_frontend(request: Request):
    """Entrega el archivo index.html deshabilitando caché para actualizar el cliente."""
    if FRONTEND_DIR:
        index_path = os.path.join(FRONTEND_DIR, "index.html")
        if os.path.exists(index_path):
            return FileResponse(
                index_path,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
            )

    fallback_index = os.path.join(GLOBAL_ROOT, "index.html")
    if os.path.exists(fallback_index):
        return FileResponse(fallback_index)

    return {"status": "healthy", "service": "Consola Fiscal B2B API Engine"}

@app.api_route("/health", methods=["GET", "HEAD"], tags=["Infraestructura"])
def health_check(request: Request):
    """Endpoint de auditoría técnica SLA."""
    is_cloud_db = "neon.tech" in os.getenv("DATABASE_URL", "")
    return {
        "status": "healthy",
        "service": "Consola Fiscal B2B API Engine",
        "version": "1.2.0",
        "environment": "Production Cloud (Neon)" if is_cloud_db else "Local Development",
        "database": "PostgreSQL Conectado"
    }