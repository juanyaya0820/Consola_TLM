# ===============================================================================
# ARCHIVO: app/main.py
# ORQUESTRADOR PRINCIPAL: PRODUCCIÓN SEGURA (MIGRACIONES NO DESTRUCTIVAS)
# ===============================================================================
import os
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api_orchestrator")

app = FastAPI(
    title="Consola TLM - Motor Fiscal & BI",
    version="2.1.0",
    description="Plataforma B2B para auditoría contable, conciliación bancaria y liquidación F350"
)

# -------------------------------------------------------------------------------
# 1. POLÍTICAS CORS
# -------------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------------------
# 2. RUTAS REST Y FRONTEND
# -------------------------------------------------------------------------------
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Seguridad & Autenticación"])
app.include_router(empresas.router, prefix="/api/v1/empresas", tags=["Gestión Multi-Tenant"])
app.include_router(facturas.router, prefix="/api/v1/facturas", tags=["Motor ETL & Auditoría"])

@app.get("/", response_class=HTMLResponse, tags=["Interfaz de Usuario"])
def servir_interfaz_cliente():
    """Entrega la SPA al acceder a la raíz del dominio."""
    base_dir = Path(__file__).resolve().parent.parent.parent
    posibles_rutas = [
        base_dir / "tlm-frontend" / "index.html",
        base_dir / "index.html",
        Path(__file__).resolve().parent / "static" / "index.html"
    ]
    for ruta in posibles_rutas:
        if ruta.exists():
            return FileResponse(ruta)

    return HTMLResponse(content="<h2>Consola TLM API Online</h2>")

# -------------------------------------------------------------------------------
# 3. MIGRACIÓN SEGURA NO DESTRUCTIVA Y AUTO-SEEDING (STARTUP)
# -------------------------------------------------------------------------------
@app.on_event("startup")
def migracion_segura_y_seeding():
    """
    Ejecuta DDL no destructivo (ALTER TABLE ADD COLUMN IF NOT EXISTS).
    Garantiza que NUNCA se borren datos existentes en Producción.
    """
    logger.info("[Startup] Verificando integridad de esquema en PostgreSQL...")
    
    try:
        with engine.connect() as conn:
            with conn.begin():
                # A. Agregar columnas faltantes en 'usuarios' sin borrar datos
                conn.execute(text("""
                    ALTER TABLE usuarios 
                    ADD COLUMN IF NOT EXISTS hashed_password VARCHAR,
                    ADD COLUMN IF NOT EXISTS rol VARCHAR DEFAULT 'Analista',
                    ADD COLUMN IF NOT EXISTS activo BOOLEAN DEFAULT FALSE;
                """))

                # B. Crear tabla asociativa de permisos si no existe
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS usuario_empresa (
                        id_usuario INTEGER REFERENCES usuarios(id_usuario) ON DELETE CASCADE,
                        id_empresa INTEGER REFERENCES empresas(id_empresa) ON DELETE CASCADE,
                        PRIMARY KEY (id_usuario, id_empresa)
                    );
                """))
                
                logger.info("✅ [Migración Segura] Estructura de tablas actualizada sin pérdida de datos.")

        # Asegurar la creación de tablas nuevas (SoportePDF, etc.)
        Base.metadata.create_all(bind=engine)

        # C. Auto-Seeding Preventivo (Solo crea si la tabla está vacía)
        db: Session = SessionLocal()
        try:
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
                logger.info("✅ [Seeding] Usuario administrador inicial verificado.")

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

            if empresa not in admin.empresas_asociadas:
                admin.empresas_asociadas.append(empresa)
                db.commit()

        except Exception as exc_db:
            db.rollback()
            logger.error(f"❌ [Seeding] Advertencia en siembra: {str(exc_db)}")
        finally:
            db.close()

    except Exception as exc_startup:
        logger.error(f"❌ [Startup] Error en verificación de esquema: {str(exc_startup)}")

@app.get("/health", tags=["Infraestructura"])
def health_check():
    return {"status": "online", "entorno": "produccion_safe"}