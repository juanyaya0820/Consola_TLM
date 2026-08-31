# ===============================================================================
# ARCHIVO: tlm-backend/reset_dev_db.py
# RESTRUCTURACIÓN LIMPIA DE ESQUEMA POSTGRESQL (DROP SCHEMA PUBLIC CASCADE)
# ===============================================================================
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# 1. Carga explícita del archivo .env desde la ruta absoluta del proyecto
directorio_actual = Path(__file__).resolve().parent
env_path = directorio_actual / ".env"
load_dotenv(dotenv_path=env_path)

from sqlalchemy import text
from app.db.session import engine, Base
from app.db import models

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_reset")

def purgar_y_recrear_esquema():
    """
    Ejecuta un borrado en cascada (CASCADE) del esquema 'public' en PostgreSQL.
    Elimina todas las tablas, restricciones y objetos huérfanos antes de reconstruir.
    """
    try:
        db_host = engine.url.host or "Localhost"
        logger.info(f"🔌 Conectando al servidor PostgreSQL: [{db_host}]...")

        with engine.connect() as connection:
            with connection.begin():
                logger.info("⚠️ [RESET] Eliminando esquema public y restricciones en cascada...")
                connection.execute(text("DROP SCHEMA public CASCADE;"))
                connection.execute(text("CREATE SCHEMA public;"))
                connection.execute(text("GRANT ALL ON SCHEMA public TO public;"))
        
        logger.info("✅ [RESET] Esquema 'public' purgado con éxito.")

        logger.info("🏗️ [RESET] Reconstruyendo tablas ORM mediante SQLAlchemy...")
        Base.metadata.create_all(bind=engine)
        logger.info("✅ [RESET] Base de datos reconstruida al 100% de forma limpia.")

    except Exception as exc:
        logger.error(f"❌ Error durante la purga en cascada: {str(exc)}")

if __name__ == "__main__":
    confirmacion = input("¿Deseas PURGAR POR COMPLETO la base de datos de desarrollo? (s/n): ")
    if confirmacion.lower() == 's':
        purgar_y_recrear_esquema()
    else:
        print("Operación cancelada.")