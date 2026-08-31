from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
import pandas as pd
import numpy as np
import io
import logging

from app.db.session import get_db
from app.db import models

router = APIRouter()
logger = logging.getLogger(__name__)

# ===============================================================================
# TRANSFORMADORES ETL SIIGO PYME
# ===============================================================================

def parsear_puc_siigo_pyme(file_bytes: bytes) -> list:
    """Extrae y normaliza el Catálogo de Cuentas (PUC) de Siigo Pyme."""
    try:
        df_puc = pd.read_excel(io.BytesIO(file_bytes), header=6)
        df_puc = df_puc.dropna(axis=1, how='all')
        df_puc.columns = [str(c).strip() for c in df_puc.columns]
        
        col_cuenta = df_puc.columns[0]
        col_desc = df_puc.columns[1]
        
        df_puc = df_puc.dropna(subset=[col_cuenta, col_desc])
        df_puc['CUENTA'] = df_puc[col_cuenta].astype(str).str.replace('-', '').str.strip()
        df_puc['NOMBRE'] = df_puc[col_desc].astype(str).str.strip()
        
        return df_puc[['CUENTA', 'NOMBRE']].to_dict(orient='records')
    except Exception as e:
        raise ValueError(f"Fallo al leer la estructura del PUC de Siigo Pyme: {str(e)}")

def parsear_balance_siigo_pyme(file_bytes: bytes) -> list:
    """
    Normaliza el Balance de Prueba por Terceros mediante imputación analítica (Forward Fill)
    para mapear cuentas jerárquicas a cada NIT.
    """
    try:
        df_balance = pd.read_excel(io.BytesIO(file_bytes), header=6)
        df_balance.columns = [str(c).strip() for c in df_balance.columns]
        
        columnas_jerarquia = ['GRUPO', 'CUENTA', 'SUBCUENT', 'AUXILIAR', 'SUBAUXIL']
        for col in columnas_jerarquia:
            if col in df_balance.columns:
                df_balance[col] = df_balance[col].astype(str).str.strip().replace('nan', '')
            else:
                df_balance[col] = ''
                
        df_balance['CUENTA_COMPLETA'] = df_balance['GRUPO'] + df_balance['CUENTA'] + df_balance['SUBCUENT'] + df_balance['AUXILIAR'] + df_balance['SUBAUXIL']
        df_balance['CUENTA_COMPLETA'] = df_balance['CUENTA_COMPLETA'].replace('', np.nan)
        
        df_balance['NIT_CLEAN'] = df_balance['NIT'].astype(str).str.strip().replace('nan', '')
        
        filas_cuenta = (df_balance['CUENTA_COMPLETA'].notna()) & (df_balance['NIT_CLEAN'] == '')
        df_balance['CUENTA_FINAL'] = df_balance['CUENTA_COMPLETA'].where(filas_cuenta).ffill()
        df_balance['DESC_CUENTA_FINAL'] = df_balance['DESCRIPCION'].where(filas_cuenta).ffill()
        
        df_terceros = df_balance[df_balance['NIT_CLEAN'] != ''].copy()
        
        df_canonical = df_terceros[[
            'CUENTA_FINAL', 'DESC_CUENTA_FINAL', 'NIT_CLEAN', 'DESCRIPCION', 
            'SALDO ANTERIOR', 'DEBITOS', 'CREDITOS', 'NUEVO SALDO'
        ]].copy()
        
        df_canonical.columns = [
            'cuenta_contable', 'nombre_cuenta', 'nit_tercero', 'nombre_tercero', 
            'saldo_inicial', 'debitos', 'creditos', 'saldo_final'
        ]
        
        for col in ['saldo_inicial', 'debitos', 'creditos', 'saldo_final']:
            df_canonical[col] = pd.to_numeric(df_canonical[col], errors='coerce').fillna(0.0)
            
        return df_canonical.to_dict(orient='records')
    except Exception as e:
        raise ValueError(f"Estructura de Balance Siigo Pyme inválida: {str(e)}")

# ===============================================================================
# ENDPOINTS API REST
# ===============================================================================

@router.post("/puc/upload", summary="Ingesta ETL Catálogo de Cuentas")
async def subir_puc(
    id_empresa: int = Form(...),
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    try:
        empresa = db.query(models.Empresa).filter(models.Empresa.id_empresa == id_empresa).first()
        if not empresa:
            raise HTTPException(status_code=404, detail="Empresa no encontrada")
            
        contenido = await archivo.read()
        datos_puc = parsear_puc_siigo_pyme(contenido)
        
        return {"status": "success", "registros_procesados": len(datos_puc), "mensaje": "PUC Sincronizado correctamente."}
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/balance/upload", summary="Ingesta ETL Balance de Prueba")
async def subir_balance(
    id_empresa: int = Form(...),
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    try:
        empresa = db.query(models.Empresa).filter(models.Empresa.id_empresa == id_empresa).first()
        if not empresa:
            raise HTTPException(status_code=404, detail="Empresa no encontrada")
            
        contenido = await archivo.read()
        datos_balance = parsear_balance_siigo_pyme(contenido)
        
        return {"status": "success", "registros_procesados": len(datos_balance), "mensaje": "Saldos del Balance normalizados exitosamente."}
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))