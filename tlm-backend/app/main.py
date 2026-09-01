# ===============================================================================
# ARCHIVO: tlm-backend/app/main.py
# PROYECTO: CONSOLA TLM - MOTOR FISCAL, ETL & BUSINESS INTELLIGENCE
# ROL: ORQUESTRADOR PRINCIPAL CON MIGRACIONES DDL NO DESTRUCTIVAS Y AUTO-SEEDING
# ===============================================================================
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import engine, Base, SessionLocal
from app.db import models
from app.api.v1.endpoints import facturas, empresas, auth

# Configuración de logs ejecutivos de infraestructura
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api_orchestrator")

app = FastAPI(
    title="Consola TLM - Motor Fiscal & BI",
    version="2.1.0",
    description="Plataforma B2B para auditoría contable, conciliación bancaria y liquidación F350"
)

# -------------------------------------------------------------------------------
# 1. POLÍTICAS DE SEGURIDAD Y ORIGEN CRUZADO (CORS)
# -------------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------------------
# 2. ENRUTADORES REST (API ENDPOINTS)
# -------------------------------------------------------------------------------
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Seguridad & Autenticación"])
app.include_router(empresas.router, prefix="/api/v1/empresas", tags=["Gestión Multi-Tenant"])
app.include_router(facturas.router, prefix="/api/v1/facturas", tags=["Motor ETL & Auditoría"])

# -------------------------------------------------------------------------------
# 3. ENRUTAMIENTO DE INTERFAZ GRÁFICA (FRONTEND SPA)
# -------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, tags=["Interfaz de Usuario"])
def servir_interfaz_cliente():
    """Servicio dinámico del cliente SPA."""
    base_dir = Path(__file__).resolve().parent.parent.parent
    posibles_rutas = [
        base_dir / "tlm-frontend" / "index.html",
        base_dir / "index.html",
        Path(__file__).resolve().parent / "static" / "index.html"
    ]
    for ruta in posibles_rutas:
        if ruta.exists():
            return FileResponse(ruta)

    return HTMLResponse(content="<h2 style='font-family:sans-serif; text-align:center;'>Consola TLM - API Online</h2>")

# -------------------------------------------------------------------------------
# 4. EVENTO STARTUP: MIGRACIÓN SEGURA NO DESTRUCTIVA Y AUTO-SEEDING
# -------------------------------------------------------------------------------
@app.on_event("startup")
def migracion_segura_y_seeding():
    logger.info("[Startup] Iniciando verificación de infraestructura de Base de Datos...")

    # =========================================================================
    # FASE 1: CREACIÓN DE TABLAS (Asegura arranque en DEV y DBs limpias)
    # =========================================================================
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("✅ [FASE 1] Modelo ORM sincronizado y tablas base aseguradas.")
    except Exception as e:
        logger.error(f"❌ [FASE 1] Fallo al crear tablas base: {e}")

    # =========================================================================
    # FASE 2: MIGRACIONES NO DESTRUCTIVAS (Inyecta columnas en PRODUCCIÓN)
    # =========================================================================
    try:
        with engine.connect() as conn:
            with conn.begin():
                # Solo ejecutamos comandos avanzados DDL si estamos en PostgreSQL (Neon Cloud)
                if engine.dialect.name == "postgresql":
                    # Actualización de columnas de seguridad
                    conn.execute(text("""
                        ALTER TABLE usuarios 
                        ADD COLUMN IF NOT EXISTS hashed_password VARCHAR,
                        ADD COLUMN IF NOT EXISTS rol VARCHAR DEFAULT 'Analista',
                        ADD COLUMN IF NOT EXISTS activo BOOLEAN DEFAULT FALSE;
                    """))

                    # Actualización de columnas financieras en facturas
                    conn.execute(text("""
                        ALTER TABLE facturas 
                        ADD COLUMN IF NOT EXISTS telefono VARCHAR,
                        ADD COLUMN IF NOT EXISTS direccion TEXT,
                        ADD COLUMN IF NOT EXISTS correo VARCHAR,
                        ADD COLUMN IF NOT EXISTS responsabilidad_fiscal VARCHAR,
                        ADD COLUMN IF NOT EXISTS fecha_vencimiento VARCHAR,
                        ADD COLUMN IF NOT EXISTS retencion_porc DOUBLE PRECISION DEFAULT 0.0,
                        ADD COLUMN IF NOT EXISTS retencion_valor DOUBLE PRECISION DEFAULT 0.0,
                        ADD COLUMN IF NOT EXISTS casilla_350 INTEGER,
                        ADD COLUMN IF NOT EXISTS cufe_hash VARCHAR,
                        ADD COLUMN IF NOT EXISTS estado_revision VARCHAR DEFAULT 'PENDIENTE',
                        ADD COLUMN IF NOT EXISTS pdf_b64 TEXT;
                    """))

                    # Prevención de fallos por llaves NOT NULL heredadas
                    conn.execute(text("""
                        DO $$ 
                        BEGIN 
                            IF EXISTS (
                                SELECT 1 FROM information_schema.columns 
                                WHERE table_name='usuarios' AND column_name='password_hash'
                            ) THEN
                                ALTER TABLE usuarios ALTER COLUMN password_hash DROP NOT NULL;
                            END IF;
                        END $$;
                    """))
                    logger.info("✅ [FASE 2] Columnas financieras y metadatos actualizados en Producción.")
    except Exception as exc:
        logger.warning(f"⚠️ [FASE 2] Advertencia DDL (Normal en bases de datos SQLite efímeras de pruebas): {exc}")

    # =========================================================================
    # FASE 3: SIEMBRA DE DATOS MAESTROS (AUTO-SEEDING)
    # =========================================================================
    db: Session = SessionLocal()
    try:
        # Administrador Maestro
        admin = db.query(models.Usuario).filter(models.Usuario.email == "admin@tlm.com").first()
        if not admin:
            from app.api.v1.endpoints.auth import obtener_hash_password
            admin = models.Usuario(
                nombre_completo="Administrador Maestro TLM",
                email="admin@tlm.com",
                hashed_password=obtener_hash_password("admin123"),
                rol="Administrador",
                activo=True
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)

        # Empresa Demo
        empresa = db.query(models.Empresa).first()
        if not empresa:
            empresa = models.Empresa(
                nombre_comercial="TLM Consulting S.A.S. (Demo)",
                nit="901234567-8",
                software_erp="SIIGO_NUBE",
                software_destino="SIIGO_NUBE"
            )
            db.add(empresa)
            db.commit()
            db.refresh(empresa)

        # Matriz de permisos
        if empresa and admin and (empresa not in admin.empresas_asociadas):
            admin.empresas_asociadas.append(empresa)
            db.commit()

        logger.info("✅ [FASE 3] Datos maestros y permisos garantizados.")
    except Exception as exc_db:
        db.rollback()
        logger.error(f"❌ [FASE 3] Advertencia transaccional: {str(exc_db)}")
    finally:
        db.close()

# -------------------------------------------------------------------------------
# 5. MONITOREO DE SALUD DE INFRAESTRUCTURA
# -------------------------------------------------------------------------------
@app.get("/health", tags=["Infraestructura"])
def health_check():
    return {"status": "online", "entorno": "produccion_safe"}