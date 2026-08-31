# ===============================================================================
# ARCHIVO: app/api/v1/endpoints/auth.py
# CONTROLADOR REST: SEGURIDAD, REGISTRO Y GOBERNANZA DE USUARIOS
# ===============================================================================
import logging
import hashlib
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_db
from app.db import models

logger = logging.getLogger("api_orchestrator")
router = APIRouter()

# --- FUNCIONES DE HASHING ---
def obtener_hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def verificar_password(password_plana: str, password_hashed: str) -> bool:
    return obtener_hash_password(password_plana) == password_hashed

# --- ESQUEMAS PYDANTIC ---
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

# --- ENDPOINTS ---

@router.post("/login", summary="Iniciar Sesión")
def login(payload: LoginSchema, db: Session = Depends(get_db)):
    usuario = db.query(models.Usuario).filter(models.Usuario.email == payload.email).first()
    
    if not usuario or not verificar_password(payload.password, usuario.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas. Verifica tu correo y contraseña."
        )

    if not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta está pendiente de aprobación por el Administrador."
        )

    return {
        "access_token": f"token_bearer_{usuario.id_usuario}",
        "token_type": "bearer",
        "usuario_nombre": usuario.nombre_completo,
        "email": usuario.email,
        "rol": usuario.rol
    }


@router.post("/register", status_code=status.HTTP_201_CREATED, summary="Solicitar registro")
def registrar_usuario(payload: RegistroSchema, db: Session = Depends(get_db)):
    usuario_existente = db.query(models.Usuario).filter(models.Usuario.email == payload.email).first()
    if usuario_existente:
        raise HTTPException(status_code=400, detail="El correo ya se encuentra registrado.")

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
        return {"mensaje": "Registro solicitado correctamente.", "id_usuario": nuevo_usuario.id_usuario}
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Error al crear el usuario.")


@router.get("/usuarios", response_model=List[UsuarioResponseSchema], summary="Listar usuarios")
def listar_usuarios(db: Session = Depends(get_db)):
    """Retorna la lista de usuarios para el panel de administración."""
    try:
        return db.query(models.Usuario).all()
    except SQLAlchemyError as exc:
        logger.error(f"Error al consultar usuarios: {str(exc)}")
        raise HTTPException(status_code=500, detail="Error de base de datos al listar usuarios.")


@router.patch("/usuarios/{id_usuario}/aprobar", summary="Aprobar acceso de usuario")
def aprobar_usuario(id_usuario: int, db: Session = Depends(get_db)):
    usuario = db.query(models.Usuario).filter(models.Usuario.id_usuario == id_usuario).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    try:
        usuario.activo = True
        db.commit()
        return {"mensaje": f"Usuario {usuario.email} aprobado."}
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Error al aprobar usuario.")


@router.delete("/usuarios/{id_usuario}", summary="Eliminar usuario")
def eliminar_usuario(id_usuario: int, db: Session = Depends(get_db)):
    usuario = db.query(models.Usuario).filter(models.Usuario.id_usuario == id_usuario).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    try:
        db.delete(usuario)
        db.commit()
        return {"mensaje": "Usuario eliminado correctamente."}
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Error al eliminar usuario.")