# ===============================================================================
# ARCHIVO: tlm-backend/app/main.py
# ORQUESTRADOR PRINCIPAL CON AUTOSANACIÓN DE ESQUEMA POSTGRESQL & AUTO-SEEDING
# ===============================================================================
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
    description="API REST B2B para auditoría contable, conciliación bancaria y liquidación F350"
)

# -------------------------------------------------------------------------------
# 1. POLÍTICAS CORS (CROSS-ORIGIN RESOURCE SHARING)
# -------------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
# 3. EVENTO DE ARRANQUE (STARTUP): MIGRACIÓN Y SIEMBRA EN NEON CLOUD
# -------------------------------------------------------------------------------
@app.on_event("startup")
def sincronizar_esquema_y_sembrar_datos():
    """
    Inspecciona la estructura física en PostgreSQL. Si detecta columnas ausentes,
    corrige el esquema automáticamente en Render e inyecta la semilla inicial.
    """
    logger.info("[Startup] Inspeccionando estructura física en PostgreSQL Neon...")
    
    try:
        with engine.connect() as conn:
            with conn.begin():
                # Verificar si la columna hashed_password existe físicamente en usuarios
                resultado = conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='usuarios' AND column_name='hashed_password';
                """)).fetchone()
                
                # Si la columna no existe, purgamos el esquema desalineado
                if not resultado:
                    logger.warn("⚠️ [Migración] Desfase detectado en 'usuarios'. Reconstruyendo esquema público...")
                    conn.execute(text("DROP SCHEMA public CASCADE;"))
                    conn.execute(text("CREATE SCHEMA public;"))
                    conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
                    logger.info("✅ [Migración] Esquema reconstruido correctamente.")

        # Reconstruir las tablas con la estructura limpia de models.py
        Base.metadata.create_all(bind=engine)
        logger.info("✅ [Startup] Modelos ORM sincronizados con la base de datos.")

        # Inyección de Semilla de Datos (Auto-Seeding)
        db: Session = SessionLocal()
        try:
            # 1. Sembrar Administrador Maestro
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
                logger.info("✅ [Seeding] Usuario creado: admin@tlm.com / admin123")

            # 2. Sembrar Empresa Demo
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
                logger.info("✅ [Seeding] Empresa Demo creada: TLM Consulting S.A.S. (Demo)")

            # 3. Vincular matriz de permisos Multi-Tenant
            if empresa not in admin.empresas_asociadas:
                admin.empresas_asociadas.append(empresa)
                db.commit()
                logger.info("✅ [Seeding] Permisos Multi-Tenant vinculados con éxito.")

        except Exception as exc_db:
            db.rollback()
            logger.error(f"❌ [Seeding] Error en la transacción de datos: {str(exc_db)}")
        finally:
            db.close()

    except Exception as exc_startup:
        logger.error(f"❌ [Startup] Error crítico durante la sincronización: {str(exc_startup)}")

# -------------------------------------------------------------------------------
# 4. HEALTH CHECK
# -------------------------------------------------------------------------------
@app.get("/health", tags=["Infraestructura"])
def health_check():
    return {
        "status": "online",
        "entorno": "desarrollo",
        "motor": "FastAPI + SQLAlchemy + PostgreSQL Neon"
    }