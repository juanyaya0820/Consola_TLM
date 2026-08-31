import socket
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

print("\n" + "="*70)
print("   INICIANDO DIAGNÓSTICO DE INFRAESTRUCTURA - CONSOLA TLM")
print("="*70)

# 1. PRUEBA DE SOCKET TCP (Capa de Red)
host = "127.0.0.1"
port = 5432
print(f"\n[PASO 1] Probando socket TCP en {host}:{port}...")

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(3)
result = sock.connect_ex((host, port))

if result == 0:
    print("  🟢 Socket abierto: El servicio de PostgreSQL está ESCUCHANDO.")
else:
    print("  🔴 Socket cerrado: El servicio de PostgreSQL NO está respondiendo en el puerto 5432.")
    print("  ► Solución: Revisa 'services.msc' en Windows e inicia el servicio 'postgresql-x64-18'.")
    sock.close()
    exit()
sock.close()

# 2. PRUEBA DE CREDENCIALES Y BASE DE DATOS (Capa de Aplicación)
print("\n[PASO 2] Intentando autenticación contra PostgreSQL...")

connection_url = URL.create(
    drivername="postgresql+psycopg2",
    username="postgres",
    password="Juan0820*",
    host="127.0.0.1",
    port=5432,
    database="tlm_workspace",
)

try:
    engine = create_engine(connection_url, connect_args={"connect_timeout": 5})
    with engine.connect() as conn:
        res = conn.execute(text("SELECT current_database(), current_user, version();")).fetchone()
        print("  🟢 Autenticación EXITOSA.")
        print(f"     • Base de Datos Activa: {res[0]}")
        print(f"     • Usuario Autenticado : {res[1]}")
        
        # 3. VERIFICACIÓN DE TABLAS DE NEGOCIO
        tables = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public';")).fetchall()
        table_list = [t[0] for t in tables]
        print(f"  🟢 Tablas detectadas en public ({len(table_list)}): {', '.join(table_list)}")

    print("\n" + "="*70)
    print(" RESULTADO FINAL: La base de datos está 100% OPERATIVA.")
    print("="*70 + "\n")

except Exception as e:
    print("  🔴 Fallo de conexión ORM/PostgreSQL:")
    print(f"  ► Detalle del error: {e}")
    print("\n" + "="*70 + "\n")
    