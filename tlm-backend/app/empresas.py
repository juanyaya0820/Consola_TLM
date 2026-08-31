from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.orm import Session
from typing import Dict, Any, List, Optional

from app.db.session import get_db
from app.db import models

router = APIRouter()

# ===============================================================================
# UTILERÍAS DE EXTRACCIÓN Y SERIALIZACIÓN
# ===============================================================================

async def extraer_payload(request: Request) -> Dict[str, Any]:
    """Extrae payloads en formato JSON o Form-Data de manera resiliente."""
    try:
        return await request.json()
    except Exception:
        try:
            return dict(await request.form())
        except Exception:
            return {}


def serializar_empresa(e: models.Empresa) -> Dict[str, Any]:
    """DTO plano para representación de la dimensión Empresas."""
    return {
        "id_empresa": e.id_empresa,
        "id": e.id_empresa,
        "nombre_comercial": e.nombre_comercial,
        "razon_social": e.nombre_comercial,
        "nombre": e.nombre_comercial,
        "nit": e.nit,
        "logo_url": e.logo_url,
        "software_erp": e.software_erp,
        "software_destino": e.software_destino
    }


# ===============================================================================
# ENDPOINTS DE GESTIÓN DE EMPRESAS CON FILTRADO RLS
# ===============================================================================

@router.get("", tags=["Empresas"])
@router.get("/", include_in_schema=False)
@router.get("/auth/empresas", include_in_schema=False)
@router.get("/auth/empresas/", include_in_schema=False)
def listar_empresas(
    email: Optional[str] = Query(None),
    rol: Optional[str] = Query(None),
    creador_email: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    [ROW-LEVEL SECURITY - RLS INTELIGENTE]
    1. Si se envía email y el rol es 'Analista': Retorna SOLO las empresas autorizadas.
    2. Si se envía email y el rol es 'Administrador' OR no se envía email (Consulta de Catálogo Maestro para Modales):
       Retorna la totalidad de empresas creadas en PostgreSQL.
    """
    user_email = str(email or creador_email or "").strip().lower()
    user_rol = str(rol or "").strip().lower()

    if user_email:
        usr = db.query(models.Usuario).filter(models.Usuario.email == user_email).first()
        if usr:
            # Si el usuario es Administrador, retorna el catálogo corporativo completo
            if (usr.rol and usr.rol.lower() in ["administrador", "admin"]) or (user_rol in ["administrador", "admin"]):
                empresas = db.query(models.Empresa).order_by(models.Empresa.id_empresa.asc()).all()
                return [serializar_empresa(e) for e in empresas]
            
            # Si es Analista, filtra estrictamente su portafolio autorizado
            return [serializar_empresa(e) for e in usr.empresas_asignadas]

    # [SOLUCIÓN AL MODAL DE ASIGNACIÓN]
    # Si la petición viene sin email (ej. consulta administrativa global), retorna el catálogo maestro
    todas_las_empresas = db.query(models.Empresa).order_by(models.Empresa.id_empresa.asc()).all()
    return [serializar_empresa(e) for e in todas_las_empresas]


@router.post("", tags=["Empresas"])
@router.post("/", include_in_schema=False)
@router.post("/auth/empresas", include_in_schema=False)
async def crear_empresa(
    request: Request, 
    creador_email: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Crea una nueva empresa en la base de datos y la vincula automáticamente
    al usuario que la creó para que tenga acceso inmediato a ella.
    """
    data = await extraer_payload(request)

    razon_social = str(
        data.get("razon_social") or 
        data.get("nombre_comercial") or 
        data.get("nombre") or ""
    ).strip()
    
    nit = str(data.get("nit") or "").strip()
    logo_url = str(data.get("logo_url") or data.get("logo") or "").strip()
    
    software = str(
        data.get("software_erp") or 
        data.get("software_destino") or 
        data.get("software") or "SIIGO NUBE"
    ).strip()

    if not razon_social or not nit:
        raise HTTPException(status_code=400, detail="La Razón Social y el NIT son obligatorios.")

    if db.query(models.Empresa).filter(models.Empresa.nit == nit).first():
        raise HTTPException(status_code=400, detail=f"El NIT {nit} ya se encuentra registrado.")

    nueva = models.Empresa(
        nombre_comercial=razon_social,
        nit=nit,
        logo_url=logo_url if logo_url else None,
        software_erp=software,
        software_destino=software
    )

    db.add(nueva)
    db.commit()
    db.refresh(nueva)

    # AUTO-VINCULACIÓN RLS: Asignar la nueva empresa al usuario creador
    email_target = str(creador_email or data.get("creador_email") or data.get("email") or "").strip().lower()
    if email_target:
        usr = db.query(models.Usuario).filter(models.Usuario.email == email_target).first()
        if usr and nueva not in usr.empresas_asignadas:
            usr.empresas_asignadas.append(nueva)
            db.commit()

    emp_payload = serializar_empresa(nueva)
    print(f"\n---> [EMPRESA REGISTRADA Y VINCULADA] ID: {nueva.id_empresa} | Cliente: '{razon_social}'")

    return {
        "status": "success",
        "success": True,
        "mensaje": "Empresa creada e integrada a su portafolio exitosamente.",
        "empresa": emp_payload,
        "data": emp_payload,
        "cliente": emp_payload,
        **emp_payload
    }


@router.delete("/{id_empresa}", tags=["Empresas"])
@router.delete("/{id_empresa}/", include_in_schema=False)
@router.delete("/auth/empresas/{id_empresa}", include_in_schema=False)
def eliminar_empresa(id_empresa: int, db: Session = Depends(get_db)):
    """Elimina la empresa y sus registros contables en cascada."""
    emp = db.query(models.Empresa).filter_by(id_empresa=id_empresa).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")
    
    db.delete(emp)
    db.commit()
    print(f"\n---> [EMPRESA ELIMINADA] ID: {id_empresa}")
    return {"status": "success", "success": True, "mensaje": "Empresa eliminada exitosamente."}