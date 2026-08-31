import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ===============================================================================
# 1. NORMALIZACIÓN DE VARIABLE DE ENTORNO (POSTGRESQL NEON CLOUD / LOCAL)
# ===============================================================================
RAW_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:5432/tlm_workspace"
)

# Compatibilidad de protocolo para SQLAlchemy (postgres:// -> postgresql://)
if RAW_DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = RAW_DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = RAW_DATABASE_URL

os.environ["DATABASE_URL"] = DATABASE_URL

# ===============================================================================
# 2. IMPORTACIÓN DE CONTROLADORES REST (Soporte Híbrido Render / Local)
# ===============================================================================
try:
    # Invocación estándar de paquete cuando Uvicorn ejecuta 'app.main:app'
    from app.facturas import router as facturas_router
    from app.empresas import router as empresas_router
    from app.auth import router as auth_router
except ImportError:
    # Fallback relativo para ejecuciones locales de script directo
    from .facturas import router as facturas_router
    from .empresas import router as empresas_router
    from .auth import router as auth_router

# ===============================================================================
# 3. INICIALIZACIÓN DEL NÚCLEO API ENGINE
# ===============================================================================
app = FastAPI(
    title="Consola Fiscal B2B API Engine",
    version="1.2.0",
    description="Motor Backend para procesamiento fiscal, RLS y analítica contable TLM."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================================================================
# 4. RESOLUCIÓN DE RUTAS DEL FRONTEND Y ARCHIVOS ESTÁTICOS
# ===============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))               # /tlm-backend/app
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))        # /tlm-backend
GLOBAL_ROOT = os.path.abspath(os.path.join(PROJECT_ROOT, ".."))     # Raíz del repositorio

POSSIBLE_FRONTEND_PATHS = [
    os.path.join(GLOBAL_ROOT, "tlm-frontend"),
    os.path.join(PROJECT_ROOT, "tlm-frontend"),
    os.path.join(BASE_DIR, "static"),
]

FRONTEND_DIR = next((path for path in POSSIBLE_FRONTEND_PATHS if os.path.isdir(path)), None)

if FRONTEND_DIR:
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/", include_in_schema=False)
async def serve_frontend():
    """Entrega la interfaz del Dashboard Ejecutivo directamente en la raíz."""
    if FRONTEND_DIR:
        index_path = os.path.join(FRONTEND_DIR, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)

    fallback_index = os.path.join(GLOBAL_ROOT, "index.html")
    if os.path.exists(fallback_index):
        return FileResponse(fallback_index)

    return {"status": "online", "message": "Consola Fiscal B2B API Engine operando. Frontend no montado."}

# ===============================================================================
# 5. MONITOREO DE INFRAESTRUCTURA (HEALTH CHECK)
# ===============================================================================
@app.get("/health", tags=["Infraestructura"])
def health_check():
    """Endpoint de auditoría técnica."""
    is_cloud_db = "neon.tech" in os.getenv("DATABASE_URL", "")
    return {
        "status": "healthy",
        "service": "Consola Fiscal B2B API Engine",
        "version": "1.2.0",
        "environment": "Production Cloud (Neon)" if is_cloud_db else "Local Development",
        "database": "PostgreSQL Conectado"
    }

# ===============================================================================
# 6. REGISTRO DE CONTROLADORES REST (V1)
# ===============================================================================
app.include_router(facturas_router, prefix="/api/v1/facturas", tags=["Facturas & Motor ETL"])
app.include_router(empresas_router, prefix="/api/v1/empresas", tags=["Empresas & Clientes"])
app.include_router(auth_router, prefix="/api/v1", tags=["Seguridad & Control de Accesos"])