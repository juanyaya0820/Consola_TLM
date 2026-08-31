from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class EmpresaBase(BaseModel):
    nombre_comercial: str
    nit: str
    software_erp: Optional[str] = "SIIGO_NUBE"

    model_config = ConfigDict(from_attributes=True)

class EmpresaCreate(BaseModel):
    nombre_comercial: str
    nit: str
    software_erp: Optional[str] = "SIIGO_NUBE"
    software_destino: Optional[str] = None  # Absorbe la propiedad enviada desde la interfaz

class EmpresaResponse(EmpresaBase):
    id_empresa: int
    software_erp: str = "SIIGO_NUBE"
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)