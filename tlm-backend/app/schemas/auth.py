from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime

class UsuarioBase(BaseModel):
    nombre_completo: str = Field(default="Usuario Sistema", alias="nombre")
    email: str
    rol: Optional[str] = "Analista"
    activo: Optional[bool] = False

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )

class UsuarioCreate(BaseModel):
    nombre_completo: Optional[str] = Field(None, alias="nombre")
    email: str
    password: str

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )

class UsuarioResponse(BaseModel):
    id_usuario: int
    nombre_completo: str
    email: str
    rol: str
    activo: bool
    created_at: Optional[datetime] = None

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )

class LoginPayload(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    usuario_nombre: str
    rol: str