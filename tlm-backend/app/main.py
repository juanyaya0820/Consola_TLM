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
# 1. ENTORNO CLOUD Y RESOLUCIÓN DE RUTAS (EJECUCIÓN RENDER + LOCAL)
# ===============================================================================
# Anclamos la raíz 'tlm-backend' y la subcarpeta 'app' en sys.path antes de importar
CURRENT_FILE = Path(__file__).resolve()
APP_DIR = CURRENT_FILE.parent                  # .../tlm-backend/app
PROJECT_ROOT = APP_DIR.parent              # .../tlm-backend

for path_str in [str(PROJECT_ROOT), str(APP_DIR)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

# Normalización de la variable de conexión a PostgreSQL (Neon / Local)
RAW_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:5432/tlm_workspace"
)

if RAW_DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = RAW_DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = RAW_DATABASE_URL

os.environ["DATABASE_URL"] = DATABASE_URL

# ===============================================================================
# 2. IMPORTACIÓN DE COMPONENTES NATIVOS DE TU PROYECTO
# ===============================================================================
from app.db.session import engine
from app.db import models
from app.api.v1.endpoints import facturas, empresas, usuarios

# Configuración del registrador de eventos
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api_orchestrator")

# ===============================================================================
# 3. INICIALIZACIÓN DEL DATA WAREHOUSE EN POSTGRESQL
# ===============================================================================
try:
    models.Base.metadata.create_all(bind=engine)
    logger.info(" Base de datos PostgreSQL sincronizada e inicializada correctamente.")
except Exception as e:
    logger.error(f" Error crítico al sincronizar tablas en PostgreSQL: {e}")

# ===============================================================================
# 4. INSTANCIA PRINCIPAL DE FASTAPI
# ===============================================================================
app = FastAPI(
    title="Consola Fiscal B2B - Analytical Engine",
    description="Motor de Ingesta ETL, Control de Accesos (RLS) y Consolidación Financiera.",
    version="1.2.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# ===============================================================================
# 5. POLÍTICAS DE SEGURIDAD Y MIDDLEWARE CORS
# ===============================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================================================================
# 6. CAPTURADORES GLOBALES DE EXCEPCIONES
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

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f" Excepción de servidor en la ruta {request.url.path}: {str(exc)}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "status": "error",
            "success": False,
            "tipo": "UnhandledException",
            "mensaje": "Error interno del servidor.",
            "detalle": str(exc)
        }
    )

# ===============================================================================
# 7. REGISTRO DE CONTROLADORES REST ORIGINALES (V1)
# ===============================================================================
app.include_router(facturas.router, prefix="/api/v1/facturas", tags=["Facturas & Motor ETL"])
app.include_router(empresas.router, prefix="/api/v1/empresas", tags=["Empresas & Clientes"])
app.include_router(usuarios.router, prefix="/api/v1", tags=["Seguridad & Control de Accesos"])

# ===============================================================================
# 8. SERVIDO DEL FRONTEND Y PUNTOS DE MONITOREO (HEALTH CHECKS)
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

@app.get("/", include_in_schema=False)
async def serve_frontend():
    """Entrega la interfaz del Dashboard / Formulario de Login en la raíz."""
    if FRONTEND_DIR:
        index_path = os.path.join(FRONTEND_DIR, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)

    fallback_index = os.path.join(GLOBAL_ROOT, "index.html")
    if os.path.exists(fallback_index):
        return FileResponse(fallback_index)

    return {
        "status": "healthy",
        "service": "Consola Fiscal B2B API Engine",
        "version": "1.2.0",
        "database": "PostgreSQL Conectado"
    }

@app.get("/health", tags=["Infraestructura"])
def health_check():
    """Endpoint de auditoría técnica SLA."""
    is_cloud_db = "neon.tech" in os.getenv("DATABASE_URL", "")
    return {
        "status": "healthy",
        "service": "Consola Fiscal B2B API Engine",
        "version": "1.2.0",
        "environment": "Production Cloud (Neon)" if is_cloud_db else "Local Development",
        "database": "PostgreSQL Conectado"
    }