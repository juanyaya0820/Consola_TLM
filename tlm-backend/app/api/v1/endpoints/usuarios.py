from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from typing import Dict, Any, List, Optional
import logging

from app.db.session import get_db
from app.db import models

# Instancia del enrutador principal de seguridad exportado a main.py
router = APIRouter()
logger = logging.getLogger("security_gateway")

# ===============================================================================
# UTILERÍAS: EXTRACCIÓN ROBUSTA Y DATA TRANSFER OBJECT (DTO ANTI-UNDEFINED)
# ===============================================================================

async def extraer_payload_completo(request: Request) -> Dict[str, Any]:
    """
    [ETL DE PETICIONES] Consolida en un único diccionario todos los parámetros
    provenientes de Query Params, cuerpos JSON o Form-Data.
    """
    data = {}
    
    # 1. Extracción de Query Parameters (?id=1&email=...)
    for key, val in request.query_params.items():
        data[key] = val

    # 2. Extracción de payload en el cuerpo de la solicitud
    try:
        json_data = await request.json()
        if isinstance(json_data, dict):
            data.update(json_data)
    except Exception:
        try:
            form_data = await request.form()
            data.update(dict(form_data))
        except Exception:
            pass

    return data


def serializar_usuario(usuario: models.Usuario) -> Dict[str, Any]:
    """
    [DATA CONTRACT] Mapeo con redundancia de claves en la raíz del JSON.
    Garantiza que el Frontend (V8 JavaScript Engine) lea los nombres de usuario,
    roles y estado de cuenta sin arrojar errores de tipo 'undefined'.
    """
    return {
        "id_usuario": usuario.id_usuario,
        "id": usuario.id_usuario,
        "idUsuario": usuario.id_usuario,
        "userId": usuario.id_usuario,
        "nombre_completo": usuario.nombre_completo,
        "nombre": usuario.nombre_completo,
        "name": usuario.nombre_completo,
        "email": usuario.email,
        "correo": usuario.email,
        "username": usuario.email,
        "rol": usuario.rol,
        "role": usuario.rol,
        "activo": usuario.activo,
        "estado": "ACTIVO" if usuario.activo else "PENDIENTE"
    }


# ===============================================================================
# 1. AUTENTICACIÓN, REGISTRO Y GESTIÓN DE SESIÓN
# ===============================================================================

@router.post("/auth/login", tags=["Autenticación & Seguridad"])
@router.post("/login", tags=["Autenticación & Seguridad"], include_in_schema=False)
async def login(request: Request, db: Session = Depends(get_db)):
    """
    Valida credenciales de acceso. Si el usuario posee el rol 'Administrador',
    se auto-asignan todas las empresas para auditoría global.
    """
    data = await extraer_payload_completo(request)
    email = str(data.get("email") or data.get("username") or data.get("correo") or "").strip().lower()
    password = str(data.get("password") or data.get("clave") or data.get("password_hash") or "").strip()

    if not email or not password:
        raise HTTPException(status_code=400, detail="Debe ingresar correo y contraseña.")

    usuario = db.query(models.Usuario).filter(models.Usuario.email == email).first()

    if not usuario:
        raise HTTPException(status_code=401, detail="El usuario ingresado no existe en el sistema.")
    if not usuario.activo:
        raise HTTPException(
            status_code=403, 
            detail="Cuenta inactiva. Requiere aprobación previa por parte de un Administrador."
        )
    if str(usuario.password_hash or "").strip() != password:
        raise HTTPException(status_code=401, detail="Contraseña incorrecta.")

    # [GOBIERNO DE DATOS] Auto-asignación de empresas para rol Administrador
    if usuario.rol and usuario.rol.lower() in ["administrador", "admin"]:
        todas_empresas = db.query(models.Empresa).all()
        usuario.empresas_asignadas = todas_empresas
        db.commit()

    usr_obj = serializar_usuario(usuario)
    logger.info(f"Sesión iniciada exitosamente: ID {usuario.id_usuario} ({usuario.email})")

    return {
        "status": "success",
        "success": True,
        "mensaje": "Autenticación exitosa",
        "access_token": f"bearer_token_{usuario.id_usuario}",
        "token_type": "bearer",
        "usuario_nombre": usuario.nombre_completo,
        "rol": usuario.rol,
        "usuario": usr_obj,
        "user": usr_obj,
        "data": usr_obj,
        **usr_obj
    }


@router.get("/auth/me", tags=["Autenticación & Seguridad"])
@router.get("/me", tags=["Autenticación & Seguridad"], include_in_schema=False)
def obtener_usuario_actual(db: Session = Depends(get_db)):
    """Retorna el perfil del usuario activo para la barra lateral del Frontend."""
    admin = db.query(models.Usuario).filter(models.Usuario.activo == True).first()
    if admin:
        usr_obj = serializar_usuario(admin)
        return {"status": "success", "success": True, "usuario": usr_obj, "user": usr_obj, **usr_obj}
    raise HTTPException(status_code=404, detail="No se encontraron usuarios activos en sesión.")


@router.post("/auth/register", tags=["Autenticación & Seguridad"])
@router.post("/register", tags=["Autenticación & Seguridad"], include_in_schema=False)
@router.post("/auth/signup", tags=["Autenticación & Seguridad"], include_in_schema=False)
async def registro_publico(request: Request, db: Session = Depends(get_db)):
    """
    [PRINCIPIO DE MENOR PRIVILEGIO]
    Registra nuevos analistas. Nacerán inactivos (activo = False) y con 0 empresas 
    asignadas (empresas_asignadas = []) hasta recibir autorización.
    """
    data = await extraer_payload_completo(request)
    nombre = str(data.get("nombre_completo") or data.get("nombre") or data.get("name") or "").strip()
    email = str(data.get("email") or data.get("correo") or data.get("username") or "").strip().lower()
    password = str(data.get("password") or data.get("clave") or data.get("password_hash") or "").strip()

    if not nombre or not email or not password:
        raise HTTPException(status_code=400, detail="Nombre, correo y contraseña son obligatorios.")
    if db.query(models.Usuario).filter(models.Usuario.email == email).first():
        raise HTTPException(status_code=400, detail="El correo electrónico ya se encuentra registrado.")

    nuevo = models.Usuario(
        nombre_completo=nombre,
        email=email,
        password_hash=password,
        rol="Analista",
        activo=False  # Requiere aprobación explícita
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)

    usr_obj = serializar_usuario(nuevo)
    logger.info(f"Nuevo registro pendiente: ID {nuevo.id_usuario} | Nombre: {nombre}")

    return {
        "status": "success",
        "success": True,
        "mensaje": "Solicitud enviada. Un Administrador debe aprobar su acceso.",
        "usuario": usr_obj,
        "user": usr_obj,
        **usr_obj
    }


# ===============================================================================
# 2. GESTIÓN ADMINISTRATIVA Y APROBACIÓN DE USUARIOS
# ===============================================================================

@router.get("/usuarios", tags=["Gestión de Usuarios"])
@router.get("/usuarios/", include_in_schema=False)
@router.get("/auth/usuarios", include_in_schema=False)
@router.get("/auth/usuarios/", include_in_schema=False)
def listar_usuarios(db: Session = Depends(get_db)):
    """Retorna la lista completa de usuarios para el panel de administración."""
    usuarios = db.query(models.Usuario).order_by(models.Usuario.id_usuario.asc()).all()
    return [serializar_usuario(u) for u in usuarios]


# --- APROBACIÓN POLIMÓRFICA CON ID EN LA RUTA (ACEPTA CUALQUIER VERBO HTTP) ---
@router.api_route("/usuarios/{id_usuario}/aprobar", methods=["GET", "POST", "PUT", "PATCH"], tags=["Gestión de Usuarios"])
@router.api_route("/auth/usuarios/{id_usuario}/aprobar", methods=["GET", "POST", "PUT", "PATCH"], include_in_schema=False)
@router.api_route("/usuarios/{id_usuario}/aceptar", methods=["GET", "POST", "PUT", "PATCH"], include_in_schema=False)
@router.api_route("/auth/usuarios/{id_usuario}/aceptar", methods=["GET", "POST", "PUT", "PATCH"], include_in_schema=False)
@router.api_route("/usuarios/{id_usuario}/estado", methods=["GET", "POST", "PUT", "PATCH"], include_in_schema=False)
@router.api_route("/auth/usuarios/{id_usuario}/estado", methods=["GET", "POST", "PUT", "PATCH"], include_in_schema=False)
async def aprobar_usuario_url(id_usuario: int, request: Request, db: Session = Depends(get_db)):
    """Procesa la aprobación cambiando la columna 'activo' a True en PostgreSQL."""
    return await ejecutar_transaccion_aprobacion(id_usuario, request, db)


# --- APROBACIÓN POLIMÓRFICA CON ID EN EL CUERPO JSON O QUERY ---
@router.api_route("/usuarios/aprobar", methods=["GET", "POST", "PUT", "PATCH"], tags=["Gestión de Usuarios"])
@router.api_route("/auth/usuarios/aprobar", methods=["GET", "POST", "PUT", "PATCH"], include_in_schema=False)
async def aprobar_usuario_body(request: Request, db: Session = Depends(get_db)):
    """Captura peticiones donde el id_usuario es enviado en el cuerpo JSON."""
    data = await extraer_payload_completo(request)
    target_id = data.get("id_usuario") or data.get("id") or data.get("idUsuario") or data.get("userId")
    
    if not target_id:
        raise HTTPException(status_code=400, detail="ID de usuario no proporcionado en la solicitud.")

    return await ejecutar_transaccion_aprobacion(int(target_id), request, db)


async def ejecutar_transaccion_aprobacion(id_usuario: int, request: Request, db: Session) -> Dict[str, Any]:
    """Transacción unificada de activación en la base de datos."""
    usr = db.query(models.Usuario).filter_by(id_usuario=id_usuario).first()
    if not usr:
        raise HTTPException(status_code=404, detail=f"No se encontró el usuario con ID {id_usuario}.")

    usr.activo = True
    db.commit()
    db.refresh(usr)

    usr_obj = serializar_usuario(usr)
    logger.info(f"Usuario aprobado en PostgreSQL: ID {usr.id_usuario} | Nombre: '{usr.nombre_completo}'")

    return {
        "status": "success",
        "success": True,
        "mensaje": "Usuario aprobado exitosamente.",
        "usuario": usr_obj,
        "user": usr_obj,
        "data": usr_obj,
        **usr_obj
    }


@router.delete("/usuarios/{id_usuario}", tags=["Gestión de Usuarios"])
@router.delete("/usuarios/{id_usuario}/", include_in_schema=False)
@router.delete("/auth/usuarios/{id_usuario}", include_in_schema=False)
@router.delete("/auth/usuarios/{id_usuario}/", include_in_schema=False)
def eliminar_usuario(id_usuario: int, db: Session = Depends(get_db)):
    """
    [BORRADO SEGURO] Disocia primero las referencias de la tabla puente
    usuario_empresa antes de borrar el registro para evitar violaciones de clave foránea.
    """
    usr = db.query(models.Usuario).filter_by(id_usuario=id_usuario).first()
    if not usr:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    # Desvincular de la tabla puente M:M
    usr.empresas_asignadas = []
    db.delete(usr)
    db.commit()
    logger.info(f"Usuario eliminado de PostgreSQL: ID {id_usuario}")
    return {"status": "success", "success": True, "mensaje": "Usuario eliminado exitosamente."}


# ===============================================================================
# 3. CONTROL DE ACCESOS POR EMPRESA (ROW-LEVEL SECURITY - RLS)
# ===============================================================================

@router.get("/usuarios/{id_usuario}/empresas", tags=["Control de Accesos (RLS)"])
@router.get("/usuarios/{id_usuario}/empresas/", include_in_schema=False)
@router.get("/auth/usuarios/{id_usuario}/empresas", include_in_schema=False)
@router.get("/auth/usuarios/{id_usuario}/empresas/", include_in_schema=False)
@router.get("/empresas/{id_usuario}/accesos", include_in_schema=False)
def ver_empresas_asignadas(id_usuario: int, db: Session = Depends(get_db)):
    """
    Retorna el catálogo exacto de empresas autorizadas para el usuario en la tabla puente.
    Si es un Analista sin asignaciones, retorna un arreglo vacío [] (Aislamiento RLS).
    """
    usr = db.query(models.Usuario).filter_by(id_usuario=id_usuario).first()
    if not usr:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    return [
        {
            "id_empresa": e.id_empresa,
            "id": e.id_empresa,
            "nombre_comercial": e.nombre_comercial,
            "razon_social": e.nombre_comercial,
            "nombre": e.nombre_comercial,
            "nit": e.nit
        } for e in usr.empresas_asignadas
    ]


@router.api_route("/usuarios/{id_usuario}/empresas", methods=["POST", "PUT", "PATCH"], tags=["Control de Accesos (RLS)"])
@router.api_route("/auth/usuarios/{id_usuario}/empresas", methods=["POST", "PUT", "PATCH"], include_in_schema=False)
@router.api_route("/usuarios/{id_usuario}/empresas/", methods=["POST", "PUT", "PATCH"], include_in_schema=False)
@router.api_route("/auth/usuarios/{id_usuario}/empresas/", methods=["POST", "PUT", "PATCH"], include_in_schema=False)
@router.api_route("/empresas/{id_usuario}/asignar", methods=["POST", "PUT", "PATCH"], include_in_schema=False)
async def asignar_empresas(id_usuario: int, request: Request, db: Session = Depends(get_db)):
    """
    Sobrescribe la relación M:M en la tabla puente usuario_empresa.
    Permite al Administrador seleccionar las empresas que auditará cada Analista.
    """
    data = await extraer_payload_completo(request)
    ids_empresas = data.get("empresas") or data.get("empresa_ids") or data.get("empresas_ids") or data.get("ids") or []

    usr = db.query(models.Usuario).filter_by(id_usuario=id_usuario).first()
    if not usr:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    # Consulta las entidades correspondientes e inyecta la nueva lista en SQLAlchemy
    empresas_db = db.query(models.Empresa).filter(models.Empresa.id_empresa.in_(ids_empresas)).all()
    usr.empresas_asignadas = empresas_db
    db.commit()

    logger.info(f"Permisos RLS actualizados: Usuario ID {id_usuario} -> {len(empresas_db)} empresa(s) asignadas.")

    return {
        "status": "success",
        "success": True,
        "mensaje": "Permisos de acceso RLS actualizados exitosamente.",
        "empresas_asignadas": [e.id_empresa for e in empresas_db]
    }