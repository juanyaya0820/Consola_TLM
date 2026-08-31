from pydantic import BaseModel, ConfigDict
from typing import Optional

class FacturaBase(BaseModel):
    factura_num: Optional[str] = None
    fecha: Optional[str] = None
    fecha_vencimiento: Optional[str] = None
    forma_pago: Optional[str] = "Contado"
    medio_pago: Optional[str] = "10 - Efectivo"
    nit_tercero: Optional[str] = None
    proveedor: Optional[str] = None
    descripcion_item: Optional[str] = None
    cantidad: Optional[float] = 1.0
    valor_unitario: Optional[float] = 0.0
    subtotal: Optional[float] = 0.0
    iva: Optional[float] = 0.0
    flujo_dian: Optional[str] = None
    cuenta_gasto: Optional[str] = "51953001"
    casilla_350: Optional[int] = 64
    retencion_porc: Optional[float] = 0.0
    reteica_tarifa: Optional[float] = 0.0
    reteiva_porc: Optional[float] = 0.0
    estado_revision: Optional[str] = "PENDIENTE"

class FacturaCreate(FacturaBase):
    id_empresa: int
    cufe_hash: str
    archivo_original: str

class FacturaUpdate(BaseModel):
    cuenta_gasto: Optional[str] = None
    casilla_350: Optional[int] = None
    retencion_porc: Optional[float] = None
    reteica_tarifa: Optional[float] = None
    reteiva_porc: Optional[float] = None

class FacturaResponse(FacturaBase):
    id_factura: int
    id_empresa: int
    cufe_hash: str

    model_config = ConfigDict(from_attributes=True)