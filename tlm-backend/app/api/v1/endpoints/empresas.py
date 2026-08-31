# ===============================================================================
# ARCHIVO: app/api/v1/endpoints/empresas.py
# CONTROLADOR REST: GESTIÓN DE PORTAFOLIOS Y MATRIZ DE ACCESOS
# ===============================================================================
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_db
from app.db import models

logger = logging.getLogger("api_orchestrator")
router = APIRouter()

# -------------------------------------------------------------------------------
# DTOs (Data Transfer Objects)
# -------------------------------------------------------------------------------
class EmpresaCreateSchema(BaseModel):
    nombre_comercial: str = Field(..., example="Empresa Demo S.A.S.")
    nit: str = Field(..., example="900123456-1")
    software_erp: Optional[str] = "SIIGO_NUBE"
    software_destino: Optional[str] = "SIIGO_NUBE"
    logo_url: Optional[str] = None

class AsignarEmpresasSchema(BaseModel):
    empresa_ids: List[int] = Field(..., example=[1, 2, 3])

class EmpresaResponseSchema(BaseModel):
    id_empresa: int
    nombre_comercial: str
    nit: str
    logo_url: Optional[str] = None
    software_erp: Optional[str] = None

    class Config:
        from_attributes = True

# -------------------------------------------------------------------------------
# ENDPOINTS REST
# -------------------------------------------------------------------------------

@router.get("/", response_model=List[EmpresaResponseSchema], summary="Listar empresas según permisos del usuario")
def listar_empresas(
    email: Optional[str] = Query(None, description="Email del usuario autenticado"),
    rol: Optional[str] = Query(None, description="Rol activo (Administrador / Analista)"),
    db: Session = Depends(get_db)
):
    """
    Retorna el portafolio de empresas autorizadas:
    - Administrador: Visibilidad global de todas las entidades.
    - Analista: Filtrado estricto según la tabla asociativa 'usuario_empresa'.
    """
    try:
        if rol == "Administrador" or not email:
            return db.query(models.Empresa).all()

        usuario = db.query(models.Usuario).filter(models.Usuario.email == email).first()
        if not usuario:
            return []

        if usuario.rol == "Administrador":
            return db.query(models.Empresa).all()

        return usuario.empresas_asociadas

    except SQLAlchemyError as exc:
        logger.error(f"Error de lectura en base de datos: {str(exc)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al consultar el portafolio de empresas."
        )


@router.post("/", response_model=EmpresaResponseSchema, status_code=status.HTTP_201_CREATED, summary="Registrar nuevo cliente")
def crear_empresa(
    payload: EmpresaCreateSchema,
    creador_email: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Crea un nuevo entorno de empresa y auto-asigna privilegios al usuario creador.
    """
    try:
        empresa_existente = db.query(models.Empresa).filter(models.Empresa.nit == payload.nit).first()
        if empresa_existente:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ya existe un cliente registrado con el NIT {payload.nit}"
            )

        nueva_empresa = models.Empresa(
            nombre_comercial=payload.nombre_comercial,
            nit=payload.nit,
            software_erp=payload.software_erp,
            software_destino=payload.software_destino,
            logo_url=payload.logo_url
        )
        db.add(nueva_empresa)
        db.commit()
        db.refresh(nueva_empresa)

        # Autovinculación de permisos al creador
        if creador_email:
            usuario = db.query(models.Usuario).filter(models.Usuario.email == creador_email).first()
            if usuario:
                usuario.empresas_asociadas.append(nueva_empresa)
                db.commit()

        return nueva_empresa

    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(f"Error en creación de empresa: {str(exc)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error transaccional al crear la empresa."
        )


@router.get("/{id_usuario}/accesos", response_model=List[int], summary="Obtener IDs de empresas asignadas a un usuario")
def obtener_accesos_usuario(
    id_usuario: int,
    db: Session = Depends(get_db)
):
    """
    Devuelve la lista pura de IDs (`id_empresa`) asignados al usuario.
    """
    usuario = db.query(models.Usuario).filter(models.Usuario.id_usuario == id_usuario).first()
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Usuario con ID {id_usuario} no encontrado."
        )

    return [emp.id_empresa for emp in usuario.empresas_asociadas]


@router.post("/{id_usuario}/asignar", summary="Actualizar matriz de permisos de un usuario")
def guardar_asignacion_accesos(
    id_usuario: int,
    payload: AsignarEmpresasSchema,
    db: Session = Depends(get_db)
):
    """
    Sincroniza masivamente la tabla asociativa de permisos para el usuario.
    """
    usuario = db.query(models.Usuario).filter(models.Usuario.id_usuario == id_usuario).first()
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Usuario con ID {id_usuario} no encontrado."
        )

    try:
        empresas_nuevas = db.query(models.Empresa).filter(
            models.Empresa.id_empresa.in_(payload.empresa_ids)
        ).all() if payload.empresa_ids else []

        # Sobreescritura directa de la relación ORM
        usuario.empresas_asociadas = empresas_nuevas
        db.commit()
        db.refresh(usuario)

        logger.info(f"Permisos sincronizados para {usuario.email}: {[e.id_empresa for e in empresas_nuevas]}")

        return {
            "status": "success",
            "mensaje": f"Se actualizaron los permisos de {usuario.nombre_completo}.",
            "empresas_asignadas": len(empresas_nuevas)
        }

    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(f"Error en sincronización de permisos: {str(exc)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Fallo de persistencia al actualizar la matriz de accesos."
        )