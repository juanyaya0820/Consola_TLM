import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker, declarative_base

# 1. Carga de variables de entorno
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

# 2. Extracción de variables de conexión individuales
DB_USER = os.getenv("POSTGRES_USER", "postgres")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "Juan0820*")
DB_HOST = os.getenv("POSTGRES_SERVER", "127.0.0.1")
DB_PORT = int(os.getenv("POSTGRES_PORT", 5432))
DB_NAME = os.getenv("POSTGRES_DB", "tlm_workspace")

# 3. Construcción segura de la URL (Evita errores de caracteres especiales como '*')
connection_url = URL.create(
    drivername="postgresql+psycopg2",
    username=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT,
    database=DB_NAME,
)

print(f"🔗 [DB ENGINE] Conectando a {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

Base = declarative_base()

# 4. Inicialización del motor con reconexión activa y manejo de tiempos de espera
engine = create_engine(
    connection_url,
    pool_pre_ping=True,      # Valida la conexión antes de cada consulta
    pool_size=10,            # Hilos persistentes en memoria
    max_overflow=20,         # Hilos adicionales para picos de carga
    connect_args={"connect_timeout": 5} # Evita bloqueos indefinidos
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """Inyección de dependencia para endpoints de FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()