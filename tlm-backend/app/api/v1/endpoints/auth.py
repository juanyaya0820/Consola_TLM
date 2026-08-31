# ===============================================================================
# ARCHIVO: app/api/v1/endpoints/auth.py
# CONTROLADOR REST: AUTENTICACIÓN, REGISTRO Y GOBERNANZA DE USUARIOS
# ===============================================================================
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
import hashlib

from app.db.session import get_db
from app.db import models

logger = logging.getLogger("api_orchestrator")
router = APIRouter()

# -------------------------------------------------------------------------------
# FUNCIONES AUXILIARES DE SEGURIDAD (HASHING DE CONTRASEÑAS)
# -------------------------------------------------------------------------------
def obtener_hash_password(password: str) -> str:
    """Genera un hash SHA-256 seguro para almacenar la contraseña."""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def verificar_password(password_plana: str, password_hashed: str) -> bool:
    """Valida si la contraseña ingresada coincide con el hash almacenado."""
    return obtener_hash_password(password_plana) == password_hashed

# -------------------------------------------------------------------------------
# ESQUEMAS PYDANTIC (DTOs)
# -------------------------------------------------------------------------------
class LoginSchema(BaseModel):
    email: EmailStr
    password: str

class RegistroSchema(BaseModel):
    nombre_completo: str
    email: EmailStr
    password: str

class UsuarioResponseSchema(BaseModel):
    id_usuario: int
    nombre_completo: str
    email: str
    rol: str
    activo: bool

    class Config:
        from_attributes = True

# -------------------------------------------------------------------------------
# ENDPOINTS REST
# -------------------------------------------------------------------------------

@router.post("/login", summary="Iniciar Sesión en la Consola")
def login(payload: LoginSchema, db: Session = Depends(get_db)):
    """Valida las credenciales del usuario y genera la respuesta de autenticación."""
    usuario = db.query(models.Usuario).filter(models.Usuario.email == payload.email).first()
    
    if not usuario or not verificar_password(payload.password, usuario.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas. Verifica tu correo y contraseña."
        )

    if not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta está pendiente de aprobación por parte del Administrador."
        )

    return {
        "access_token": f"token_bearer_{usuario.id_usuario}",
        "token_type": "bearer",
        "usuario_nombre": usuario.nombre_completo,
        "email": usuario.email,
        "rol": usuario.rol
    }


@router.post("/register", status_code=status.HTTP_201_CREATED, summary="Solicitar registro de usuario")
def registrar_usuario(payload: RegistroSchema, db: Session = Depends(get_db)):
    """Registra una nueva cuenta de analista en estado pendiente (`activo=False`)."""
    usuario_existente = db.query(models.Usuario).filter(models.Usuario.email == payload.email).first()
    if usuario_existente:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe un usuario registrado con este correo electrónico."
        )

    nuevo_usuario = models.Usuario(
        nombre_completo=payload.nombre_completo,
        email=payload.email,
        hashed_password=obtener_hash_password(payload.password),
        rol="Analista",
        activo=False
    )

    try:
        db.add(nuevo_usuario)
        db.commit()
        db.refresh(nuevo_usuario)
        return {"mensaje": "Solicitud de registro enviada con éxito.", "id_usuario": nuevo_usuario.id_usuario}
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(f"Error al registrar usuario: {str(exc)}")
        raise HTTPException(status_code=500, detail="Error transaccional al crear la cuenta.")


@router.get("/usuarios", response_model=List[UsuarioResponseSchema], summary="Listar todos los usuarios para gobernanza")
def listar_usuarios(db: Session = Depends(get_db)):
    """Retorna la lista completa de usuarios para el panel de administración."""
    try:
        usuarios = db.query(models.Usuario).all()
        return usuarios
    except SQLAlchemyError as exc:
        logger.error(f"Error al consultar usuarios: {str(exc)}")
        raise HTTPException(status_code=500, detail="Error al consultar la lista de usuarios.")


@router.patch("/usuarios/{id_usuario}/aprobar", summary="Aprobar acceso a un usuario")
def aprobar_usuario(id_usuario: int, db: Session = Depends(get_db)):
    """Activa la cuenta de un usuario para permitir su acceso."""
    usuario = db.query(models.Usuario).filter(models.Usuario.id_usuario == id_usuario).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    try:
        usuario.activo = True
        db.commit()
        return {"mensaje": f"Usuario {usuario.email} aprobado exitosamente."}
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Error al actualizar el estado del usuario.")


@router.delete("/usuarios/{id_usuario}", summary="Eliminar usuario")
def eliminar_usuario(id_usuario: int, db: Session = Depends(get_db)):
    """Elimina la cuenta de un usuario y sus relaciones de la base de datos."""
    usuario = db.query(models.Usuario).filter(models.Usuario.id_usuario == id_usuario).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    try:
        db.delete(usuario)
        db.commit()
        return {"mensaje": "Usuario eliminado correctamente."}
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Error al eliminar el usuario.")