from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
import logging

from app.db.session import engine
from app.db import models
from app.api.v1.endpoints import facturas, empresas, usuarios

# Configuración del registrador de eventos
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api_orchestrator")

# ===============================================================================
# 1. INICIALIZACIÓN DEL DATA WAREHOUSE EN POSTGRESQL
# ===============================================================================
try:
    models.Base.metadata.create_all(bind=engine)
    logger.info(" Base de datos PostgreSQL sincronizada e inicializada correctamente.")
except Exception as e:
    logger.error(f" Error crítico al sincronizar tablas en PostgreSQL: {e}")

# ===============================================================================
# 2. INSTANCIA PRINCIPAL DE FASTAPI
# ===============================================================================
app = FastAPI(
    title="Consola Fiscal B2B - Analytical Engine",
    description="Motor de Ingesta ETL, Control de Accesos (RLS) y Consolidación Financiera.",
    version="1.2.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# ===============================================================================
# 3. POLÍTICAS DE SEGURIDAD Y MIDDLEWARE CORS
# ===============================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================================================================
# 4. CAPTURADORES GLOBALES DE EXCEPCIONES
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
# 5. REGISTRO DE CONTROLADORES REST (V1)
# ===============================================================================
app.include_router(facturas.router, prefix="/api/v1/facturas", tags=["Facturas & Motor ETL"])
app.include_router(empresas.router, prefix="/api/v1/empresas", tags=["Empresas & Clientes"])
app.include_router(usuarios.router, prefix="/api/v1", tags=["Seguridad & Control de Accesos"])

# ===============================================================================
# 6. MONITOREO Y DISPONIBILIDAD (HEALTH CHECKS)
# ===============================================================================
@app.get("/", tags=["Infraestructura"])
@app.get("/health", tags=["Infraestructura"])
def health_check():
    return {
        "status": "healthy",
        "service": "Consola Fiscal B2B API Engine",
        "version": "1.2.0",
        "database": "PostgreSQL Conectado"
    }