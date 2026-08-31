# ===============================================================================
# ARCHIVO: tlm-backend/seed_dev_db.py
# REESTABLECIMIENTO DE CREDENCIALES Y SIEMBRA DIRECTA EN NEON CLOUD
# ===============================================================================
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# 1. Carga explícita del archivo de variables de entorno (.env)
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

from sqlalchemy.orm import Session
from app.db.session import engine, Base, SessionLocal
from app.db import models
from app.api.v1.endpoints.auth import obtener_hash_password

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_seeder")

def sembrar_entorno_desarrollo():
    """
    Garantiza la creación del esquema ORM e inyecta las credenciales maestras
    y la empresa activa directamente en la base de datos en la nube.
    """
    try:
        db_host = engine.url.host or "Localhost"
        logger.info(f"🔌 Conectando a Neon Cloud: [{db_host}]...")

        # A. Creación de la estructura física de tablas
        logger.info("🏗️ Generando tablas ORM en PostgreSQL...")
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Estructura DDL verificada y creada.")

        db: Session = SessionLocal()

        try:
            # B. Inyección o reactivación del Usuario Administrador
            admin = db.query(models.Usuario).filter(models.Usuario.email == "admin@tlm.com").first()
            if not admin:
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
                logger.info("✅ Usuario 'admin@tlm.com' sembrado con éxito.")
            else:
                admin.activo = True
                admin.hashed_password = obtener_hash_password("admin123")
                db.commit()
                logger.info("✅ Usuario 'admin@tlm.com' reactivado con contraseña 'admin123'.")

            # C. Inyección o actualización de la Empresa Demo
            empresa = db.query(models.Empresa).filter(models.Empresa.nit == "901234567-8").first()
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
                logger.info("✅ Empresa 'TLM Consulting S.A.S. (Demo)' sembrada con éxito.")

            # D. Vinculación de la matriz de permisos Multi-Tenant (usuario_empresa)
            if empresa not in admin.empresas_asociadas:
                admin.empresas_asociadas.append(empresa)
                db.commit()
                logger.info("✅ Permisos Multi-Tenant vinculados correctamente.")

            print("\n" + "=" * 65)
            print("🚀 BASE DE DATOS RESTABLECIDA Y OPERATIVA EN LA NUBE")
            print("=" * 65)
            print(" Credenciales de Acceso Validadas:")
            print("   • Correo:     admin@tlm.com")
            print("   • Contraseña: admin123")
            print("   • Cliente:    TLM Consulting S.A.S. (Demo) [NIT: 901234567-8]")
            print("=" * 65 + "\n")

        except Exception as exc_inner:
            db.rollback()
            logger.error(f"❌ Error en la transacción de datos: {str(exc_inner)}")
        finally:
            db.close()

    except Exception as exc_outer:
        logger.error(f"❌ Error crítico de conexión: {str(exc_outer)}")

if __name__ == "__main__":
    sembrar_entorno_desarrollo()