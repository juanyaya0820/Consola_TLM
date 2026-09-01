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
    """
    Servicio dinámico del cliente Single Page Application (SPA).
    Evalúa rutas en el árbol del repositorio para prevenir respuestas 404 en la raíz.
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
# 4. EVENTO STARTUP: MIGRACIÓN SEGURA NO DESTRUCTIVA Y AUTO-SEEDING
# -------------------------------------------------------------------------------
@app.on_event("startup")
def migracion_segura_y_seeding():
    """
    Ejecuta alteraciones DDL no destructivas (ALTER TABLE IF NOT EXISTS),
    elimina restricciones NOT NULL de columnas heredadas ('password_hash') y
    garantiza la siembra inicial de credenciales y empresa demo sin pérdida de datos.
    """
    logger.info("[Startup] Verificando integridad de esquema e infraestructura en PostgreSQL Neon...")

    try:
        with engine.connect() as conn:
            with conn.begin():
                # A. Extensión de columnas en la tabla 'usuarios'
                conn.execute(text("""
                    ALTER TABLE usuarios 
                    ADD COLUMN IF NOT EXISTS hashed_password VARCHAR,
                    ADD COLUMN IF NOT EXISTS rol VARCHAR DEFAULT 'Analista',
                    ADD COLUMN IF NOT EXISTS activo BOOLEAN DEFAULT FALSE;
                """))

                # B. Resolución del bloqueo por columna heredada 'password_hash' (Remover NOT NULL)
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

                # C. Extensión de columnas de contacto y fiscalidad en la tabla 'facturas'
                conn.execute(text("""
                    ALTER TABLE facturas 
                    ADD COLUMN IF NOT EXISTS telefono VARCHAR,
                    ADD COLUMN IF NOT EXISTS direccion TEXT,
                    ADD COLUMN IF NOT EXISTS correo VARCHAR,
                    ADD COLUMN IF NOT EXISTS responsabilidad_fiscal VARCHAR,
                    ADD COLUMN IF NOT EXISTS fecha_vencimiento VARCHAR,
                    ADD COLUMN IF NOT EXISTS casilla_350 INTEGER,
                    ADD COLUMN IF NOT EXISTS cufe_hash VARCHAR,
                    ADD COLUMN IF NOT EXISTS estado_revision VARCHAR DEFAULT 'PENDIENTE';
                """))

                # D. Creación condicional de la matriz asociativa de accesos Multi-Tenant
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS usuario_empresa (
                        id_usuario INTEGER REFERENCES usuarios(id_usuario) ON DELETE CASCADE,
                        id_empresa INTEGER REFERENCES empresas(id_empresa) ON DELETE CASCADE,
                        PRIMARY KEY (id_usuario, id_empresa)
                    );
                """))

                logger.info("✅ [Migración Segura] Estructura DDL y restricciones sincronizadas correctamente.")

        # Creación de tablas ORM faltantes mediante metadatos de SQLAlchemy
        Base.metadata.create_all(bind=engine)

        # E. Siembra de Datos Maestros (Auto-Seeding Idempotente)
        db: Session = SessionLocal()
        try:
            # 1. Administrador Maestro
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
                logger.info("✅ [Seeding] Usuario Administrador sembrado: admin@tlm.com / admin123")

            # 2. Empresa Demo
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
                logger.info("✅ [Seeding] Empresa Demo sembrada: TLM Consulting S.A.S. (Demo)")

            # 3. Asignación de Permisos Multi-Tenant
            if empresa and admin and (empresa not in admin.empresas_asociadas):
                admin.empresas_asociadas.append(empresa)
                db.commit()
                logger.info("✅ [Seeding] Permisos Multi-Tenant vinculados con éxito.")

        except Exception as exc_db:
            db.rollback()
            logger.error(f"❌ [Seeding] Advertencia transaccional durante la siembra: {str(exc_db)}")
        finally:
            db.close()

    except Exception as exc_startup:
        logger.error(f"❌ [Startup] Error crítico en verificación de infraestructura: {str(exc_startup)}")

# -------------------------------------------------------------------------------
# 5. MONITOREO DE SALUD DE INFRAESTRUCTURA
# -------------------------------------------------------------------------------
@app.get("/health", tags=["Infraestructura"])
def health_check():
    return {
        "status": "online",
        "entorno": "produccion_safe",
        "motor": "FastAPI + SQLAlchemy + PostgreSQL Neon Cloud"
    }