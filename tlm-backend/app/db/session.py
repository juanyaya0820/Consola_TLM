import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ===============================================================================
# 1. RESOLUCIÓN DE VARIABLE DE ENTORNO (NEON CLOUD / LOCAL)
# ===============================================================================
RAW_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:5432/tlm_workspace"
)

# Adecuación de esquema para SQLAlchemy (postgres:// -> postgresql://)
if RAW_DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = RAW_DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = RAW_DATABASE_URL

# Forzar requerimiento de SSL si la conexión va dirigida a la nube de Neon
if "neon.tech" in DATABASE_URL and "sslmode" not in DATABASE_URL:
    if "?" in DATABASE_URL:
        DATABASE_URL += "&sslmode=require"
    else:
        DATABASE_URL += "?sslmode=require"

# ===============================================================================
# 2. CONFIGURACIÓN DEL MOTOR SQLALCHEMY (POOL PRE-PING)
# ===============================================================================
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,       # Verifica que la conexión esté viva antes de ejecutar SQL
    pool_recycle=300,         # Recicla conexiones cada 5 minutos para evitar cortes en Neon
    pool_size=10,             # Tamaño del pool de conexiones para concurrencia B2B
    max_overflow=20
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """Generador de contexto de base de datos para inyección de dependencias."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()