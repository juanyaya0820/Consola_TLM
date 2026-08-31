import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_db
from app.db import models

# Configuración del registrador de eventos
logger = logging.getLogger("api_orchestrator")

router = APIRouter()

# ===============================================================================
# ESQUEMAS PYDANTIC (DTOs DE ENTRADA Y SALIDA)
# ===============================================================================
class EmpresaCreateSchema(BaseModel):
    nombre_comercial: str = Field(..., example="Empresa Demo S.A.S.")
    nit: str = Field(..., example="900123456-1")
    software_erp: Optional[str] = Field(default="SIIGO_NUBE", example="SIIGO_NUBE")
    software_destino: Optional[str] = Field(default="SIIGO_NUBE", example="SIIGO_NUBE")
    logo_url: Optional[str] = Field(default=None, example="https://dominio.com/logo.png")

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

# ===============================================================================
# ENDPOINTS REST (GESTIÓN MULTI-TENANT Y GOBERNANZA DE ACCESOS)
# ===============================================================================

@router.get("/", response_model=List[EmpresaResponseSchema], summary="Listar empresas por permisos de usuario")
def listar_empresas(
    email: Optional[str] = Query(None, description="Email del usuario autenticado"),
    rol: Optional[str] = Query(None, description="Rol del usuario (Administrador / Analista)"),
    db: Session = Depends(get_db)
):
    """
    Retorna el portafolio de empresas auditables según el rol del usuario:
    - Administrador: Acceso global a todas las empresas activas.
    - Analista: Acceso filtrado por la matriz de permisos (Tabla asociativa).
    """
    try:
        if rol == "Administrador" or not email:
            empresas = db.query(models.Empresa).all()
            return empresas

        # Búsqueda de usuario para evaluar permisos específicos
        usuario = db.query(models.Usuario).filter(models.Usuario.email == email).first()
        if not usuario:
            return []

        # Si el usuario es Administrador por BD
        if getattr(usuario, 'rol', '') == "Administrador":
            return db.query(models.Empresa).all()

        # Filtrar empresas asociadas mediante la relación de accesos
        if hasattr(usuario, 'empresas_asociadas') and usuario.empresas_asociadas:
            return usuario.empresas_asociadas

        return []

    except SQLAlchemyError as exc:
        logger.error(f"Error al consultar lista de empresas: {str(exc)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inconsistencia de lectura en la base de datos de empresas."
        )


@router.post("/", response_model=EmpresaResponseSchema, status_code=status.HTTP_201_CREATED, summary="Crear un nuevo cliente")
def crear_empresa(
    payload: EmpresaCreateSchema,
    creador_email: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Registra una nueva entidad contable/cliente y vincula automáticamente
    al usuario creador como gestor autorizado.
    """
    try:
        # Verificar duplicidad por NIT
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

        # Autovinculación del creador
        if creador_email:
            usuario = db.query(models.Usuario).filter(models.Usuario.email == creador_email).first()
            if usuario and hasattr(usuario, 'empresas_asociadas'):
                usuario.empresas_asociadas.append(nueva_empresa)
                db.commit()

        return nueva_empresa

    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(f"Error al crear empresa: {str(exc)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error en la transacción de creación de empresa."
        )


@router.get("/{id_usuario}/accesos", response_model=List[int], summary="Obtener IDs de empresas asignadas")
def obtener_accesos_usuario(
    id_usuario: int,
    db: Session = Depends(get_db)
):
    """
    Retorna la lista pura de IDs (`id_empresa`) a las que un usuario
    tiene permiso de auditoría y gestión.
    """
    usuario = db.query(models.Usuario).filter(models.Usuario.id_usuario == id_usuario).first()
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Usuario con ID {id_usuario} no encontrado."
        )

    try:
        if hasattr(usuario, 'empresas_asociadas') and usuario.empresas_asociadas:
            return [emp.id_empresa for emp in usuario.empresas_asociadas]
        return []
    except Exception as exc:
        logger.error(f"Error al obtener accesos de id_usuario={id_usuario}: {str(exc)}")
        return []


@router.post("/{id_usuario}/asignar", summary="Actualizar matriz de permisos multi-tenant")
def guardar_asignacion_accesos(
    id_usuario: int,
    payload: AsignarEmpresasSchema,
    db: Session = Depends(get_db)
):
    """
    Sincroniza masivamente la tabla asociativa de permisos para el usuario especificado.
    """
    usuario = db.query(models.Usuario).filter(models.Usuario.id_usuario == id_usuario).first()
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Usuario con ID {id_usuario} no encontrado."
        )

    try:
        # Obtener las instancias de las empresas seleccionadas
        empresas_seleccionadas = db.query(models.Empresa).filter(
            models.Empresa.id_empresa.in_(payload.empresa_ids)
        ).all() if payload.empresa_ids else []

        # Actualización de la relación de la entidad de usuario
        if hasattr(usuario, 'empresas_asociadas'):
            usuario.empresas_asociadas = empresas_seleccionadas
            db.commit()

        return {
            "status": "success",
            "mensaje": f"Se actualizaron los permisos del usuario {usuario.nombre_completo}.",
            "empresas_asignadas": len(empresas_seleccionadas)
        }

    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(f"Error al asignar empresas a id_usuario={id_usuario}: {str(exc)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al guardar la reasignación de permisos en la base de datos."
        )