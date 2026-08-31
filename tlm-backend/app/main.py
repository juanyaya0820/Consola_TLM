# ===============================================================================
# ARCHIVO: tlm-backend/app/main.py
# ORQUESTRADOR PRINCIPAL: SERVICIO DE INTERFAZ SPA, MIGRACIÓN & AUTO-SEEDING
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
# 1. POLÍTICAS CORS (ACCESO MULTI-ORIGEN)
# -------------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------------------
# 2. VINCULACIÓN DE CONTROLADORES REST
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
    Entrega el cliente Single Page Application (SPA) al acceder a la URL principal.
    Busca la plantilla index.html en las rutas estándar del proyecto.
    """
    base_dir = Path(__file__).resolve().parent.parent.parent  # Raíz del repositorio
    
    posibles_rutas = [
        base_dir / "tlm-frontend" / "index.html",
        base_dir / "index.html",
        Path(__file__).resolve().parent / "static" / "index.html"
    ]

    for ruta in posibles_rutas:
        if ruta.exists():
            return FileResponse(ruta)

    # Fallback dinámico si el archivo HTML no se encuentra en el contenedor
    return HTMLResponse(content="""
        <html>
            <head><title>Consola TLM - Estado de Servidor</title></head>
            <body style="font-family:sans-serif; text-align:center; padding:50px; background:#F8F9FA;">
                <h1 style="color:#271A82;">🛡️ Consola TLM - API Backend Activa</h1>
                <p style="color:#666;">El motor de base de datos y la API están operativos.</p>
                <p><a href="/docs" style="color:#F37A20; font-weight:bold;">Ver Documentación Swagger API (/docs)</a></p>
            </body>
        </html>
    """)

# -------------------------------------------------------------------------------
# 4. EVENTO DE ARRANQUE (STARTUP): MIGRACIÓN Y SIEMBRA EN NEON CLOUD
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
                resultado = conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='usuarios' AND column_name='hashed_password';
                """)).fetchone()
                
                if not resultado:
                    logger.warning("⚠️ [Migración] Desfase detectado en 'usuarios'. Reconstruyendo esquema público...")
                    conn.execute(text("DROP SCHEMA public CASCADE;"))
                    conn.execute(text("CREATE SCHEMA public;"))
                    conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
                    logger.info("✅ [Migración] Esquema reconstruido correctamente.")

        Base.metadata.create_all(bind=engine)
        logger.info("✅ [Startup] Modelos ORM sincronizados con la base de datos.")

        # Inyección de Semilla de Datos
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
                logger.info("✅ [Seeding] Usuario creado: admin@tlm.com / admin123")

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
# 5. ENDPOINT DE SALUD
# -------------------------------------------------------------------------------
@app.get("/health", tags=["Infraestructura"])
def health_check():
    return {
        "status": "online",
        "entorno": "desarrollo",
        "motor": "FastAPI + SQLAlchemy + PostgreSQL Neon"
    }