import logging
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text

# Importación de infraestructura ORM y endpoints de negocio
from app.db.session import engine, Base, get_db
from app.api.v1.endpoints import auth, empresas, facturas

# Configuración de trazabilidad para auditoría
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TLM_FINANCIAL_API")

# Generación automática de modelos en la base viva tlm_workspace
try:
    Base.metadata.create_all(bind=engine)
    logger.info("🟢 [DB SYNC] Esquema ORM sincronizado exitosamente en tlm_workspace.")
except Exception as e:
    logger.error(f"🔴 [DB ERROR] Fallo al sincronizar esquemas con PostgreSQL: {e}")

app = FastAPI(
    title="Consola Fiscal TLM - API Financiera",
    description="Backend transaccional para análisis B2B, conciliación e inteligencia de datos.",
    version="1.0.0"
)

# =====================================================================
# CONFIGURACIÓN GLOBAL DE CORS (Habilita peticiones file:// y localhost)
# =====================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# =====================================================================
# ENDPOINTS DE CONTROL DE SALUD (HEALTH CHECK)
# =====================================================================
@app.get("/", tags=["Health"])
@app.get("/health", tags=["Health"])
@app.get("/api/v1/health", tags=["Health"])
def health_check(db: Session = Depends(get_db)):
    """
    Verifica la conectividad activa con PostgreSQL tlm_workspace mediante un ping de baja latencia.
    Soporta múltiples alias para garantizar la sincronización con el frontend.
    """
    try:
        # Petición de control de salud transaccional
        db.execute(text("SELECT 1"))
        return {
            "status": "online",
            "database": "connected",
            "target_db": "tlm_workspace",
            "environment": "production"
        }
    except Exception as err:
        logger.error(f"🔴 [HEALTH CHECK FAIL] Error en motor de datos: {err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error de conectividad con PostgreSQL: {str(err)}"
        )

# =====================================================================
# REGISTRO DE ROUTERS REQUERIDOS POR EL DASHBOARD
# =====================================================================
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Autenticación"])
app.include_router(empresas.router, prefix="/api/v1/empresas", tags=["Empresas / Clientes"])
app.include_router(facturas.router, prefix="/api/v1/facturas", tags=["Facturación / PDFs"])