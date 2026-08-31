import pandas as pd
import numpy as np
import pdfplumber
import io
import re
import unicodedata
from datetime import datetime

# Mapeo de meses hispanos a formato numérico
MESES_NUM = {
    'enero': '01', 'febrero': '02', 'marzo': '03', 'abril': '04',
    'mayo': '05', 'junio': '06', 'julio': '07', 'agosto': '08',
    'septiembre': '09', 'octubre': '10', 'noviembre': '11', 'diciembre': '12',
    'ene': '01', 'feb': '02', 'mar': '03', 'abr': '04',
    'may': '05', 'jun': '06', 'jul': '07', 'ago': '08',
    'sep': '09', 'oct': '10', 'nov': '11', 'dic': '12'
}

def quitar_acentos(texto) -> str:
    """Elimina acentos y tildes para evitar errores de coincidencia en columnas."""
    if not isinstance(texto, str):
        return str(texto) if pd.notna(texto) else ""
    texto_norm = unicodedata.normalize('NFD', texto)
    return ''.join(c for c in texto_norm if unicodedata.category(c) != 'Mn')


# ==============================================================================
# 1. PARSER TEXTUAL DAVIVIENDA (EXTRACTO PDF)
# ==============================================================================
def procesar_pdf_davivienda(pdf_bytes: bytes) -> pd.DataFrame:
    """
    Lee linea a linea el PDF de Davivienda capturando la fecha (DD MM),
    el concepto completo y el signo del movimiento (- es Salida/Crédito, + es Entrada/Débito).
    """
    filas = []
    anio_informe = str(datetime.now().year)

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        texto_completo = ""
        for page in pdf.pages:
            t = page.extract_text() or ""
            texto_completo += "\n" + t

        # Capturar año del informe (ej. INFORME DEL MES: JUNIO /2025)
        match_anio = re.search(r'INFORME\s+DEL\s+MES\s*:\s*([A-ZÁÉÍÓÚa-záéíóú]+)\s*/\s*(\d{4})', texto_completo, re.IGNORECASE)
        if match_anio:
            anio_informe = match_anio.group(2)

        # Patrón Regex para la línea Davivienda: DD MM Concepto $Valor[-+] $Saldo[+-]
        patron_davivienda = r'(\d{2})\s+(\d{2})\s+(.*?)\s+\$?([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)([-+])\s+\$?([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)([-+]?)'
        matches = re.findall(patron_davivienda, texto_completo, re.DOTALL)
        
        for m in matches:
            dia, mes, concepto, valor_str, signo, saldo_str, saldo_signo = m
            
            concepto_clean = " ".join(concepto.split())
            fecha_str = f"{anio_informe}-{mes}-{dia}"
            v_num = float(valor_str.replace(',', ''))
            
            if signo == '-':
                monto_firmado = -v_num  # Salida (Crédito en libros)
                debito = 0.0
                credito = v_num
            else:
                monto_firmado = v_num   # Entrada (Débito en libros)
                debito = v_num
                credito = 0.0

            filas.append({
                'Fecha': fecha_str,
                'Concepto': concepto_clean if concepto_clean else "Transacción Bancaria Davivienda",
                'Monto_Neto': monto_firmado,
                'Débito': debito,
                'Crédito': credito
            })

    if not filas:
        return pd.DataFrame()

    df = pd.DataFrame(filas)
    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    return df.dropna(subset=['Fecha'])


# ==============================================================================
# 2. PARSER GENÉRICO OTROS BANCOS
# ==============================================================================
def extraer_df_desde_pdf_bancario(pdf_bytes: bytes) -> pd.DataFrame:
    try:
        df_davi = procesar_pdf_davivienda(pdf_bytes)
        if not df_davi.empty and len(df_davi) >= 1:
            return df_davi
    except Exception as e:
        print(f"Fallback parser genérico: {e}")

    filas = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tablas = page.extract_tables()
            if tablas:
                for tabla in tablas:
                    for fila in tabla:
                        if fila and any(fila):
                            fila_limpia = [str(c).replace('\n', ' ').strip() if c is not None else "" for c in fila]
                            if any(fila_limpia):
                                filas.append(fila_limpia)

    return pd.DataFrame(filas)


# ==============================================================================
# 3. IDENTIFICADOR Y NORMALIZADOR DE SIIGO (EXCEL)
# ==============================================================================
def recortar_metadatos_y_encontrar_encabezado(df_raw: pd.DataFrame) -> pd.DataFrame:
    if df_raw.empty:
        return df_raw

    palabras_clave = [
        'fecha', 'fec', 'date', 'comprobante', 'concepto', 'descripcion', 
        'debito', 'credito', 'monto', 'valor', 'saldo', 'retiro', 'deposito', 
        'ingreso', 'egreso', 'f.mov', 'elaboracion', 'movimiento'
    ]

    max_coincidencias = -1
    fila_encabezado_idx = 0

    for i in range(min(25, len(df_raw))):
        celdas_str = [quitar_acentos(str(val)).lower() for val in df_raw.iloc[i].tolist()]
        fila_texto = " ".join(celdas_str)
        
        coincidencias = sum(1 for p in palabras_clave if p in fila_texto)
        if coincidencias > max_coincidencias:
            max_coincidencias = coincidencias
            fila_encabezado_idx = i

    if max_coincidencias >= 1:
        nuevos_encabezados = [quitar_acentos(str(val)).strip().lower() if pd.notna(val) else f"col_{idx}" 
                              for idx, val in enumerate(df_raw.iloc[fila_encabezado_idx].tolist())]
        df_recortado = df_raw.iloc[fila_encabezado_idx + 1:].copy()
        df_recortado.columns = nuevos_encabezados
        return df_recortado.reset_index(drop=True)

    return df_raw


def extraer_fecha_valida(val):
    if pd.isna(val) or val is None:
        return np.nan
    s = str(val).strip()
    dt = pd.to_datetime(s, dayfirst=True, errors='coerce')
    if pd.notna(dt) and 2000 <= dt.year <= 2100:
        return dt
    return np.nan


def normalizar_df_financiero(df_input: pd.DataFrame) -> pd.DataFrame:
    # Si viene procesado del extractor Davivienda
    if isinstance(df_input, pd.DataFrame) and 'Monto_Neto' in df_input.columns and 'Fecha' in df_input.columns:
        if pd.api.types.is_datetime64_any_dtype(df_input['Fecha']):
            df_input = df_input.dropna(subset=['Fecha', 'Monto_Neto'])
            df_input = df_input[df_input['Monto_Neto'] != 0]
            if 'Concepto' not in df_input.columns:
                df_input['Concepto'] = "Movimiento Bancario"
            return df_input[['Fecha', 'Concepto', 'Débito', 'Crédito', 'Monto_Neto']]

    df_clean = recortar_metadatos_y_encontrar_encabezado(df_input)

    # Buscar columna de fecha explícita 'fecha elaboracion' o 'fecha'
    col_fecha = None
    for c in df_clean.columns:
        if any(p in str(c) for p in ['fecha elaboracion', 'fecha elaboraci', 'fecha', 'fec', 'date']):
            col_fecha = c
            break

    if not col_fecha:
        max_fechas = 0
        for col in df_clean.columns:
            fechas_test = df_clean[col].apply(extraer_fecha_valida)
            validos = fechas_test.notna().sum()
            if validos > max_fechas:
                max_fechas = validos
                col_fecha = col

    if col_fecha is None:
        raise ValueError("No se identificó la columna de Fechas en el reporte contable.")

    # Construcción de concepto unificado en SIIGO
    def construir_concepto_siigo(row):
        partes = []
        for c in ['nombre del tercero', 'tercero', 'comprobante', 'descripcion', 'detalle', 'concepto']:
            for col_real in df_clean.columns:
                if c in col_real and pd.notna(row[col_real]):
                    v = str(row[col_real]).strip()
                    if v and v != '-' and v not in partes:
                        partes.append(v)
        return " - ".join(partes[:3]) if partes else "Movimiento Contable SIIGO"

    cols_debito = [c for c in df_clean.columns if any(p in str(c) for p in ['debito', 'ingreso', 'entrada'])]
    cols_credito = [c for c in df_clean.columns if any(p in str(c) for p in ['credito', 'egreso', 'retiro', 'salida'])]

    def limpiar_num(val):
        if pd.isna(val) or val is None: return 0.0
        try: return float(str(val).replace('$', '').replace(',', '').strip())
        except: return 0.0

    df_res = pd.DataFrame()
    df_res['Fecha'] = df_clean[col_fecha].apply(extraer_fecha_valida)
    df_res['Concepto'] = df_clean.apply(construir_concepto_siigo, axis=1)

    if cols_debito and cols_credito:
        deb = df_clean[cols_debito[0]].apply(limpiar_num).abs()
        cred = df_clean[cols_credito[0]].apply(limpiar_num).abs()
        df_res['Débito'] = deb
        df_res['Crédito'] = cred
        df_res['Monto_Neto'] = deb - cred  # Débito (+), Crédito (-)
    else:
        col_monto = [c for c in df_clean.columns if 'monto' in str(c) or 'valor' in str(c)][0]
        neto = df_clean[col_monto].apply(limpiar_num)
        df_res['Monto_Neto'] = neto
        df_res['Débito'] = neto.apply(lambda x: x if x > 0 else 0.0)
        df_res['Crédito'] = neto.apply(lambda x: abs(x) if x < 0 else 0.0)

    df_res = df_res.dropna(subset=['Fecha', 'Monto_Neto'])
    df_res = df_res[df_res['Monto_Neto'] != 0]

    return df_res


# ==============================================================================
# 4. RECONCILIACIÓN Y EXPORTACIÓN A EXCEL
# ==============================================================================
def convertir_pdf_banco_a_excel_bytes(pdf_bytes: bytes) -> bytes:
    df_raw = extraer_df_desde_pdf_bancario(pdf_bytes)
    df_final = normalizar_df_financiero(df_raw)
    
    df_final['Fecha'] = df_final['Fecha'].dt.strftime('%Y-%m-%d')
    
    df_export = pd.DataFrame({
        'Fecha': df_final['Fecha'],
        'Descripción / Concepto': df_final['Concepto'],
        'Entradas (Débito)': df_final['Débito'],
        'Salidas (Crédito)': df_final['Crédito'],
        'Monto Neto': df_final['Monto_Neto']
    })
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_export.to_excel(writer, index=False, sheet_name='Extracto_Convertido')
    return output.getvalue()


def reconciliar_auxiliar_vs_extracto(
    df_siigo_raw: pd.DataFrame, 
    df_banco_raw: pd.DataFrame, 
    dias_tolerancia: int = 5
) -> dict:
    df_s = normalizar_df_financiero(df_siigo_raw)
    df_b = normalizar_df_financiero(df_banco_raw)

    df_s['Estado'] = 'Pendiente en Libros'
    df_b['Estado'] = 'Pendiente en Banco'
    
    coincidencias = 0
    b_usados = set()

    for idx_s, row_s in df_s.iterrows():
        monto_s = round(float(row_s['Monto_Neto']), 2)
        fecha_s = row_s['Fecha']

        for idx_b, row_b in df_b.iterrows():
            if idx_b in b_usados:
                continue
            
            monto_b = round(float(row_b['Monto_Neto']), 2)
            fecha_b = row_b['Fecha']
            
            # Cruce por Monto Exacto con Tolerancia de Días de Tránsito
            if monto_s == monto_b and abs((fecha_s - fecha_b).days) <= dias_tolerancia:
                b_usados.add(idx_b)
                df_s.at[idx_s, 'Estado'] = 'Conciliado'
                df_b.at[idx_b, 'Estado'] = 'Conciliado'
                coincidencias += 1
                break

    monto_libros_pend = df_s[df_s['Estado'] != 'Conciliado']['Monto_Neto'].sum()
    monto_banco_pend = df_b[df_b['Estado'] != 'Conciliado']['Monto_Neto'].sum()

    df_s['Fecha'] = df_s['Fecha'].dt.strftime('%Y-%m-%d')
    df_b['Fecha'] = df_b['Fecha'].dt.strftime('%Y-%m-%d')

    return {
        "resumen": {
            "total_registros_libros": len(df_s),
            "total_registros_banco": len(df_b),
            "coincidencias_exitosas": coincidencias,
            "diferencia_cuadre_cop": float(monto_libros_pend - monto_banco_pend)
        },
        "pendientes_libros": df_s[df_s['Estado'] != 'Conciliado'].to_dict(orient="records"),
        "pendientes_banco": df_b[df_b['Estado'] != 'Conciliado'].to_dict(orient="records")
    }