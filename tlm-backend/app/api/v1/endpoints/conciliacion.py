from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status
from fastapi.responses import Response
import pandas as pd
import io

from app.services.conciliacion import (
    reconciliar_auxiliar_vs_extracto, 
    extraer_df_desde_pdf_bancario, 
    convertir_pdf_banco_a_excel_bytes
)

router = APIRouter()

@router.post("/reconciliar", summary="Conciliar Auxiliar Siigo vs Extracto (PDF o Excel)")
async def procesar_conciliacion(
    file_siigo: UploadFile = File(...),
    file_banco: UploadFile = File(...),
    dias_tolerancia: int = Form(5)
):
    try:
        # 1. Lectura del Auxiliar de Siigo sin asumición de encabezado rígido
        content_s = await file_siigo.read()
        df_s = (
            pd.read_csv(io.BytesIO(content_s), header=None) 
            if file_siigo.filename.lower().endswith('.csv') 
            else pd.read_excel(io.BytesIO(content_s), header=None)
        )
        
        # 2. Lectura del Extracto Bancario (PDF, CSV o Excel)
        content_b = await file_banco.read()
        filename_b = file_banco.filename.lower()

        if filename_b.endswith('.pdf'):
            df_b = extraer_df_desde_pdf_bancario(content_b)
        elif filename_b.endswith('.csv'):
            df_b = pd.read_csv(io.BytesIO(content_b), header=None)
        else:
            df_b = pd.read_excel(io.BytesIO(content_b), header=None)

        # 3. Procesamiento mediante ETL Resiliente
        resultado = reconciliar_auxiliar_vs_extracto(
            df_siigo_raw=df_s, 
            df_banco_raw=df_b, 
            dias_tolerancia=dias_tolerancia
        )
        
        return resultado

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error en Conciliación: {str(e)}"
        )


@router.post("/convertir-pdf-excel", summary="Convertir Extracto Bancario en PDF a Excel (.xlsx)")
async def convertir_pdf_a_excel(file_pdf: UploadFile = File(...)):
    if not file_pdf.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="El archivo seleccionado debe ser un PDF.")

    try:
        pdf_bytes = await file_pdf.read()
        excel_bytes = convertir_pdf_banco_a_excel_bytes(pdf_bytes)

        headers = {
            'Content-Disposition': f'attachment; filename="Extracto_Convertido.xlsx"'
        }
        return Response(
            content=excel_bytes,
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers=headers
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error en conversión PDF: {str(e)}")