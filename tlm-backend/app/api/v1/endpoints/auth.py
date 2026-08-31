import hashlib
import logging
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.session import get_db
from app.db import models

router = APIRouter()
logger = logging.getLogger(__name__)

# =====================================================================
# ESQUEMAS PYDANTIC (Contratos de Autenticación y Usuarios)
# =====================================================================
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class RegisterRequest(BaseModel):
    nombre_completo: str
    email: EmailStr
    password: str

class UsuarioResponse(BaseModel):
    id_usuario: int
    nombre_completo: str
    email: str
    rol: str
    activo: bool

    class Config:
        from_attributes = True

def hash_password(password: str) -> str:
    """Genera el hash SHA256 estándar utilizado por el sistema TLM."""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

# =====================================================================
# ENDPOINTS DE CONTROL DE ACCESO
# =====================================================================
@router.post("/login", summary="Autenticación de Usuarios")
def login(credentials: LoginRequest, db: Session = Depends(get_db)):
    """
    Valida las credenciales contra tlm_workspace y retorna un contrato
    compatible con la sesión plana requerida por el Frontend.
    """
    email_limpio = credentials.email.strip().lower()
    hashed_input = hash_password(credentials.password)

    user = db.query(models.Usuario).filter(
        func.lower(models.Usuario.email) == email_limpio,
        models.Usuario.hashed_password == hashed_input
    ).first()

    # Intento de respaldo para contraseñas sin hash
    if not user:
        user = db.query(models.Usuario).filter(
            func.lower(models.Usuario.email) == email_limpio,
            models.Usuario.hashed_password == credentials.password
        ).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Error de autenticación. Correo o contraseña incorrectos."
        )

    if not user.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Su usuario está pendiente de aprobación por parte del Administrador."
        )

    token_session = f"tlm-session-{user.id_usuario}"

    # Retornamos los atributos tanto en nivel superior como anidados para compatibilidad total
    return {
        "access_token": token_session,
        "token_type": "bearer",
        "usuario_nombre": user.nombre_completo,
        "rol": user.rol,
        "usuario": {
            "id_usuario": user.id_usuario,
            "nombre_completo": user.nombre_completo,
            "email": user.email,
            "rol": user.rol
        }
    }

@router.post("/register", status_code=status.HTTP_201_CREATED, summary="Solicitud de Registro")
def registrar_usuario(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Registra un nuevo usuario en estado inactivo hasta su aprobación."""
    email_limpio = payload.email.strip().lower()
    existente = db.query(models.Usuario).filter(func.lower(models.Usuario.email) == email_limpio).first()
    
    if existente:
        raise HTTPException(status_code=400, detail="El correo electrónico ya se encuentra registrado.")

    nuevo_usuario = models.Usuario(
        nombre_completo=payload.nombre_completo.strip(),
        email=email_limpio,
        hashed_password=hash_password(payload.password),
        rol="Analista",
        activo=False,
        created_at=datetime.now()
    )
    
    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario)
    return {"status": "success", "message": "Solicitud de registro recibida exitosamente."}

@router.get("/usuarios", response_model=List[UsuarioResponse], summary="Listar todos los usuarios (Gobernanza)")
def listar_usuarios(db: Session = Depends(get_db)):
    """Retorna la lista general de usuarios para gestión de permisos por el Administrador."""
    return db.query(models.Usuario).order_by(models.Usuario.id_usuario.asc()).all()

@router.patch("/usuarios/{id_usuario}/aprobar", summary="Aprobar acceso a usuario")
def aprobar_usuario(id_usuario: int, db: Session = Depends(get_db)):
    """Activa la cuenta de un usuario registrado."""
    usuario = db.query(models.Usuario).filter(models.Usuario.id_usuario == id_usuario).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    
    usuario.activo = True
    db.commit()
    return {"status": "success", "message": f"Usuario {usuario.email} aprobado con éxito."}

@router.delete("/usuarios/{id_usuario}", summary="Declinar / Eliminar usuario")
def eliminar_usuario(id_usuario: int, db: Session = Depends(get_db)):
    """Elimina permanentemente a un usuario del sistema."""
    usuario = db.query(models.Usuario).filter(models.Usuario.id_usuario == id_usuario).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    
    db.delete(usuario)
    db.commit()
    return {"status": "success", "message": "Usuario eliminado."}