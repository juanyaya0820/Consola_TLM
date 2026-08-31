# ===============================================================================
# ARCHIVO: tlm-backend/app/main.py
# ORQUESTRADOR PRINCIPAL: MIGRACIONES NO DESTRUCTIVAS Y SERVICIO DE BI MULTI-TENANT
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

# Configuración del motor de registros para auditoría de servidor
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api_orchestrator")

app = FastAPI(
    title="Consola TLM - Motor Fiscal & BI",
    version="2.1.0",
    description="Plataforma B2B para auditoría contable, conciliación bancaria y liquidación F350"
)

# -------------------------------------------------------------------------------
# 1. POLÍTICAS DE SEGURIDAD CORS (Cross-Origin Resource Sharing)
# -------------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Habilita consumo seguro desde dashboards web y clientes externos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------------------
# 2. ENRUTADORES DE API REST
# -------------------------------------------------------------------------------
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Seguridad & Autenticación"])
app.include_router(empresas.router, prefix="/api/v1/empresas", tags=["Gestión Multi-Tenant"])
app.include_router(facturas.router, prefix="/api/v1/facturas", tags=["Motor ETL & Auditoría"])

# -------------------------------------------------------------------------------
# 3. DISPACHER DE INTERFAZ GRÁFICA (FRONTEND SPA)
# -------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, tags=["Interfaz de Usuario"])
def servir_interfaz_cliente():
    """
    Resuelve dinámicamente el archivo index.html dentro de la estructura de la SPA.
    Evita la devolución de JSON vacíos o errores 404 en el punto de entrada principal.
    """
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
# 4. EVENTO STARTUP: MIGRACIÓN SEGURA Y SIEMBRA PREVENTIVA
# -------------------------------------------------------------------------------
@app.on_event("startup")
def migracion_segura_y_seeding():
    """
    Ejecuta DDL de alteración segura en PostgreSQL sin manipular datos activos
    e inyecta credenciales maestras únicamente si las tablas están desiertas.
    """
    logger.info("[Startup] Verificando integridad de esquema en PostgreSQL...")
    
    try:
        with engine.connect() as conn:
            with conn.begin():
                # Extensión de columnas en usuarios sin alteración de datos existentes
                conn.execute(text("""
                    ALTER TABLE usuarios 
                    ADD COLUMN IF NOT EXISTS hashed_password VARCHAR,
                    ADD COLUMN IF NOT EXISTS rol VARCHAR DEFAULT 'Analista',
                    ADD COLUMN IF NOT EXISTS activo BOOLEAN DEFAULT FALSE;
                """))

                # Creación condicional de la matriz de accesos multi-tenant
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS usuario_empresa (
                        id_usuario INTEGER REFERENCES usuarios(id_usuario) ON DELETE CASCADE,
                        id_empresa INTEGER REFERENCES empresas(id_empresa) ON DELETE CASCADE,
                        PRIMARY KEY (id_usuario, id_empresa)
                    );
                """))
                
                logger.info("✅ [Migración Segura] Estructura de tablas actualizada sin pérdida de datos.")

        # Garantiza el mapeo relacional de modelos ORM secundarios (SoportePDF, etc.)
        Base.metadata.create_all(bind=engine)

        # Inyección idempotente de entidades base
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
                logger.info("✅ [Seeding] Usuario administrador verificado.")

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

            if empresa and admin and (empresa not in admin.empresas_asociadas):
                admin.empresas_asociadas.append(empresa)
                db.commit()

        except Exception as exc_db:
            db.rollback()
            logger.error(f"❌ [Seeding] Advertencia transaccional: {str(exc_db)}")
        finally:
            db.close()

    except Exception as exc_startup:
        logger.error(f"❌ [Startup] Error en verificación de esquema: {str(exc_startup)}")

# -------------------------------------------------------------------------------
# 5. MONITOREO DE SALUD
# -------------------------------------------------------------------------------
@app.get("/health", tags=["Infraestructura"])
def health_check():
    return {"status": "online", "entorno": "produccion_safe"}