import hashlib
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db import models

router = APIRouter()

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    usuario: dict

def hash_password(password: str) -> str:
    """Genera el hash SHA256 estándar utilizado por el sistema TLM."""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

@router.post("/login", response_model=LoginResponse)
def login(credentials: LoginRequest, db: Session = Depends(get_db)):
    """
    Valida las credenciales del usuario en la base de datos viva tlm_workspace.
    """
    hashed_input = hash_password(credentials.password)

    # Consulta directa por email y contraseña hasheada
    user = db.query(models.Usuario).filter(
        models.Usuario.email == credentials.email,
        models.Usuario.hashed_password == hashed_input
    ).first()

    if not user:
        # Intento de respaldo: verificación directa por si el hash ya está almacenado
        user = db.query(models.Usuario).filter(
            models.Usuario.email == credentials.email,
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
            detail="El usuario se encuentra inactivo en la consola fiscal."
        )

    # Generación del token de sesión para el frontend
    token_session = f"tlm-session-{user.id_usuario}"

    return {
        "access_token": token_session,
        "token_type": "bearer",
        "usuario": {
            "id_usuario": str(user.id_usuario),
            "nombre_completo": user.nombre_completo,
            "email": user.email,
            "rol": user.rol
        }
    }