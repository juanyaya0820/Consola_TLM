from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from pydantic import BaseModel
from typing import List, Optional
import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np
import zipfile
import base64
import io
import logging
import re
from datetime import datetime

from app.db.session import get_db
from app.db import models

router = APIRouter()
logger = logging.getLogger(__name__)

# ===============================================================================
# 1. ESQUEMAS PYDANTIC (Contratos de Datos e Interfaces de Entrada)
# ===============================================================================

class CuentaGastoUpdate(BaseModel):
    cuenta_gasto: str
    aplicar_a_proveedor: bool = False
    nit_tercero: Optional[str] = None
    id_empresa: int


class RetencionUpdate(BaseModel):
    retencion_porc: float
    aplicar_a_proveedor: bool = False
    nit_tercero: Optional[str] = None
    id_empresa: int


class SoporteExportRequest(BaseModel):
    id_empresa: int
    facturas_seleccionadas: List[str]


# ===============================================================================
# 2. FUNCIONES AUXILIARES Y EXTRACTORES (DECLARADAS PREVIAMENTE A RUTAS)
# ===============================================================================

def aplicar_filtros_tabla(
    query, 
    fecha_desde: Optional[str] = None, 
    fecha_hasta: Optional[str] = None, 
    tipo_comprobante: Optional[str] = None
):
    """
    Aplica filtros SQL dinámicos a la consulta ORM por rango de fechas 
    y naturaleza contable (Compras = Cuentas no '4%', Ventas = Cuentas '4%').
    """
    if fecha_desde:
        query = query.filter(models.Factura.fecha >= fecha_desde)
    if fecha_hasta:
        query = query.filter(models.Factura.fecha <= fecha_hasta)
    if tipo_comprobante == 'COMPRAS':
        query = query.filter(models.Factura.cuenta_gasto.notlike('4%'))
    elif tipo_comprobante == 'VENTAS':
        query = query.filter(models.Factura.cuenta_gasto.like('4%'))
    return query


def autodetectar_y_normalizar_encabezados(df: pd.DataFrame) -> pd.DataFrame:
    """
    Inspecciona las primeras 20 filas de un archivo cargado para identificar 
    dónde inician las columnas reales del reporte, omitiendo títulos de ERP.
    """
    palabras_clave = [
        'fecha', 'debito', 'débito', 'credito', 'crédito', 'valor', 
        'monto', 'concepto', 'descripcion', 'descripción', 'saldo', 
        'comprobante', 'retiro', 'consignacion', 'abono', 'cargo',
        'documento', 'tercero', 'nit', 'oficina', 'referencia', 'código', 'codigo'
    ]
    
    cols_actuales = [str(c).lower() for c in df.columns]
    if any(any(kw in c for kw in palabras_clave) for c in cols_actuales):
        return df
        
    for idx in range(min(20, len(df))):
        fila_valores = [str(v).lower() for v in df.iloc[idx].values]
        coincidencias = sum(1 for v in fila_valores if any(kw in v for kw in palabras_clave))
        
        if coincidencias >= 2:
            nuevas_columnas = [
                str(v).strip() if pd.notna(v) and str(v).strip() != 'nan' else f"Col_{i}" 
                for i, v in enumerate(df.iloc[idx].values)
            ]
            df_nuevo = df.iloc[idx + 1:].copy()
            df_nuevo.columns = nuevas_columnas
            return df_nuevo.reset_index(drop=True)
            
    return df


def clasificar_impuesto_dian(descripcion: str, tipo_persona: str, base_total: float, es_ingreso: bool) -> tuple:
    """
    Clasificador tributario heurístico: Evalúa la descripción del ítem, 
    naturaleza jurídica y monto acumulado para sugerir tarifas de RteFte (F350).
    """
    if es_ingreso:
        return (0.0, None)

    desc = descripcion.lower()
    BASE_COMPRAS = 594000.0   
    BASE_SERVICIOS = 188000.0 
    BASE_AGRICOLA = 3500000.0 

    agricola_kws = ['fruta', 'naranja', 'mango', 'agricola', 'verdura', 'hortaliza', 'limon', 'manzana', 'pera', 'uva', 'banano', 'papa', 'cebolla', 'tomate', 'agro', 'carnes', 'pollo']
    if any(kw in desc for kw in agricola_kws):
        if base_total >= BASE_AGRICOLA: 
            return (1.5, 64) 
        return (0.0, None)

    honorarios_kws = ['honorario', 'consultoria', 'asesoria', 'legal', 'abogado', 'auditoria', 'medico', 'salud']
    if any(kw in desc for kw in honorarios_kws):
        if tipo_persona == 'Persona Natural': 
            return (10.0, 63) 
        else: 
            return (11.0, 63)

    servicios_kws = ['servicio', 'mantenimiento', 'transporte', 'limpieza', 'reparacion', 'flete', 'arrendamiento', 'alquiler']
    if any(kw in desc for kw in servicios_kws):
        if base_total >= BASE_SERVICIOS:
            if tipo_persona == 'Persona Natural': 
                return (6.0, 65) 
            else: 
                return (4.0, 65)
        return (0.0, None)

    if base_total >= BASE_COMPRAS:
        if tipo_persona == 'Persona Natural': 
            return (3.5, 64) 
        else: 
            return (2.5, 64)

    return (0.0, None)


def parsear_xml_ubl(xml_bytes: bytes, nit_empresa_activa: str = "") -> Optional[dict]:
    """
    Procesa estructuras XML UBL 2.1 DIAN, clasificando ingresos vs egresos 
    según la coincidencia del NIT del emisor contra el cliente activo.
    """
    ns = {
        'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
        'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
        'ext': 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2'
    }
    try:
        try: 
            root = ET.fromstring(xml_bytes)
        except Exception:
            texto = xml_bytes.decode('utf-8', errors='ignore')
            root = ET.fromstring(texto.encode('utf-8'))

        tag_local = root.tag.split('}')[-1] if '}' in root.tag else root.tag
        if tag_local in ['ApplicationResponse', 'Event']: 
            return None

        if tag_local == 'AttachedDocument':
            invoice_node = root.find('.//ext:ExtensionContent//Invoice', ns)
            if invoice_node is None: 
                invoice_node = root.find('.//ext:ExtensionContent//CreditNote', ns)
            if invoice_node is None:
                texto_xml = xml_bytes.decode('utf-8', errors='ignore')
                if '<Invoice' in texto_xml:
                    inicio = texto_xml.find('<Invoice')
                    fin = texto_xml.rfind('</Invoice>') + 10
                    root = ET.fromstring(texto_xml[inicio:fin].encode('utf-8'))
                else: 
                    return None
            else: 
                root = invoice_node

        factura_num = root.findtext('.//cbc:ID', default="S/N", namespaces=ns)
        fecha = root.findtext('.//cbc:IssueDate', default=datetime.now().strftime("%Y-%m-%d"), namespaces=ns)
        
        cufe_base = root.findtext('.//cbc:UUID', default=None, namespaces=ns)
        if not cufe_base:
            for elem in root.iter():
                if elem.tag.endswith('UUID') and elem.text:
                    cufe_base = elem.text.strip()
                    break
        if not cufe_base: 
            return None

        supplier = root.find('.//cac:AccountingSupplierParty', ns)
        customer = root.find('.//cac:AccountingCustomerParty', ns)
        
        nit_emisor = ""
        if supplier is not None:
            el = supplier.find('.//cbc:CompanyID', ns)
            if el is not None and el.text: 
                nit_emisor = el.text.split('-')[0].strip()

        # Evaluación de naturaleza contable
        naturaleza = "EGRESO"
        nodo_tercero = supplier
        
        if nit_empresa_activa and nit_emisor == nit_empresa_activa:
            naturaleza = "INGRESO"
            nodo_tercero = customer

        nit_tercero = "0"
        tercero_nombre = "Desconocido"
        tipo_persona = "Persona Jurídica" 
        
        if nodo_tercero is not None:
            nit_elem = nodo_tercero.find('.//cbc:CompanyID', ns)
            if nit_elem is not None and nit_elem.text:
                nit_tercero = nit_elem.text.split('-')[0].strip()
            
            name_elem = nodo_tercero.find('.//cac:PartyName/cbc:Name', ns)
            if name_elem is None or not name_elem.text:
                name_elem = nodo_tercero.find('.//cac:PartyTaxScheme/cbc:RegistrationName', ns)
            if name_elem is not None and name_elem.text:
                tercero_nombre = name_elem.text

            tax_level = nodo_tercero.findtext('.//cac:PartyTaxScheme/cbc:TaxLevelCode', default="", namespaces=ns)
            add_id = nodo_tercero.findtext('.//cbc:AdditionalAccountID', default="", namespaces=ns)
            if add_id == "2" or "PN" in tax_level or "49" in tax_level:
                tipo_persona = "Persona Natural"

        forma_pago = "Contado"
        fecha_vencimiento = fecha

        payment_means = root.find('.//cac:PaymentMeans', ns)
        if payment_means is not None:
            if payment_means.findtext('./cbc:ID', namespaces=ns) == "2": 
                forma_pago = "Crédito"
            due_date = payment_means.findtext('./cbc:PaymentDueDate', namespaces=ns)
            if due_date: 
                fecha_vencimiento = due_date

        payment_terms = root.find('.//cac:PaymentTerms', ns)
        if payment_terms is not None:
            term_date = payment_terms.findtext('.//cbc:EndDate', namespaces=ns)
            if term_date:
                fecha_vencimiento = term_date
                if term_date > fecha: 
                    forma_pago = "Crédito"

        pdf_embebido_b64 = None
        attachment_node = root.find('.//cac:Attachment/cac:ExternalReference/cbc:Description', ns)
        if attachment_node is not None and attachment_node.text and len(attachment_node.text) > 100:
            pdf_embebido_b64 = attachment_node.text.strip()

        lineas_factura = []
        invoice_lines = root.findall('.//cac:InvoiceLine', ns)
        
        if invoice_lines:
            for idx, line in enumerate(invoice_lines, start=1):
                desc_elem = line.find('.//cac:Item/cbc:Description', ns)
                item_desc = desc_elem.text if desc_elem is not None and desc_elem.text else "Registro General"
                cant_elem = line.find('.//cbc:InvoicedQuantity', ns)
                cantidad = float(cant_elem.text) if cant_elem is not None and cant_elem.text else 1.0
                v_unit_elem = line.find('.//cac:Price/cbc:PriceAmount', ns)
                valor_unitario = float(v_unit_elem.text) if v_unit_elem is not None and v_unit_elem.text else 0.0
                sub_elem = line.find('.//cbc:LineExtensionAmount', ns)
                subtotal_linea = float(sub_elem.text) if sub_elem is not None and sub_elem.text else (cantidad * valor_unitario)
                
                iva_linea = 0.0
                tax_total = line.find('.//cac:TaxTotal', ns)
                if tax_total is not None:
                    iva_elem = tax_total.find('.//cbc:TaxAmount', ns)
                    if iva_elem is not None and iva_elem.text: 
                        iva_linea = float(iva_elem.text)

                lineas_factura.append({
                    "factura_num": factura_num, "fecha": fecha, "cufe_hash": f"{cufe_base}_L{idx}",
                    "nit_tercero": nit_tercero, "proveedor": tercero_nombre, "forma_pago": forma_pago,
                    "fecha_vencimiento": fecha_vencimiento, "descripcion_item": item_desc[:200],
                    "cantidad": cantidad, "valor_unitario": valor_unitario, "subtotal": subtotal_linea, 
                    "iva": iva_linea, "naturaleza": naturaleza
                })
        else:
            sub_elem = root.findtext('.//cac:LegalMonetaryTotal/cbc:LineExtensionAmount', default="0", namespaces=ns)
            iva_elem = root.findtext('.//cac:TaxTotal/cbc:TaxAmount', default="0", namespaces=ns)
            lineas_factura.append({
                "factura_num": factura_num, "fecha": fecha, "cufe_hash": f"{cufe_base}_L1",
                "nit_tercero": nit_tercero, "proveedor": tercero_nombre, "forma_pago": forma_pago,
                "fecha_vencimiento": fecha_vencimiento, "descripcion_item": "Concepto Consolidado",
                "cantidad": 1.0, "valor_unitario": float(sub_elem), "subtotal": float(sub_elem), 
                "iva": float(iva_elem), "naturaleza": naturaleza
            })

        base_total_factura = sum(l["subtotal"] for l in lineas_factura)
        for linea in lineas_factura:
            porcentaje, casilla = clasificar_impuesto_dian(linea["descripcion_item"], tipo_persona, base_total_factura, naturaleza == "INGRESO")
            linea["retencion_porc"] = porcentaje
            linea["casilla_350"] = casilla

        return {"lineas": lineas_factura, "cufe_base": cufe_base, "factura_num": factura_num, "pdf_b64": pdf_embebido_b64, "nit_tercero": nit_tercero}
    except Exception as e:
        logger.error(f"Fallo en parsing XML UBL: {str(e)}")
        raise ValueError(f"XML no válido: {str(e)}")


def parsear_puc_siigo_pyme(file_bytes: bytes) -> list:
    """Procesa e indexa el Plan Único de Cuentas (PUC)."""
    try:
        df_puc = pd.read_excel(io.BytesIO(file_bytes), header=6)
        df_puc = df_puc.dropna(axis=1, how='all')
        df_puc.columns = [str(c).strip() for c in df_puc.columns]
        df_puc = df_puc.dropna(subset=[df_puc.columns[0], df_puc.columns[1]])
        df_puc['CUENTA'] = df_puc[df_puc.columns[0]].astype(str).str.replace('-', '').str.strip()
        df_puc['NOMBRE'] = df_puc[df_puc.columns[1]].astype(str).str.strip()
        return df_puc[['CUENTA', 'NOMBRE']].to_dict(orient='records')
    except Exception as e: 
        raise ValueError(f"Fallo PUC: {str(e)}")


def parsear_balance_siigo_pyme(file_bytes: bytes) -> list:
    """Procesa e imputeriza el Balance de Prueba por Terceros."""
    try:
        df_balance = pd.read_excel(io.BytesIO(file_bytes), header=6)
        df_balance.columns = [str(c).strip() for c in df_balance.columns]
        for col in ['GRUPO', 'CUENTA', 'SUBCUENT', 'AUXILIAR', 'SUBAUXIL']:
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
        df_canonical = df_terceros[['CUENTA_FINAL', 'DESC_CUENTA_FINAL', 'NIT_CLEAN', 'DESCRIPCION', 'SALDO ANTERIOR', 'DEBITOS', 'CREDITOS', 'NUEVO SALDO']].copy()
        df_canonical.columns = ['cuenta_contable', 'nombre_cuenta', 'nit_tercero', 'nombre_tercero', 'saldo_inicial', 'debitos', 'creditos', 'saldo_final']
        for col in ['saldo_inicial', 'debitos', 'creditos', 'saldo_final']: 
            df_canonical[col] = pd.to_numeric(df_canonical[col], errors='coerce').fillna(0.0)
        return df_canonical.to_dict(orient='records')
    except Exception as e: 
        raise ValueError(f"Fallo Balance: {str(e)}")


def parsear_extracto_pdf_multibanco(pdf_bytes: bytes, banco_hint: str = "AUTO") -> pd.DataFrame:
    """
    Extrae movimientos bancarios de PDFs para Davivienda, Bancolombia, 
    Banco de Bogotá, BCS, Global66 y BBVA con control defensivo para pypdf.
    """
    try:
        import pypdf
    except ImportError:
        logger.error("Librería 'pypdf' no instalada. Ejecuta 'pip install pypdf'.")
        raise ValueError("El servidor no tiene instalada la librería 'pypdf' para leer extractos PDF. Ejecuta 'pip install pypdf'.")

    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        full_text = "\n".join([p.extract_text() for p in reader.pages if p.extract_text()])
        lines = [l.strip() for l in full_text.split('\n') if l.strip()]
        
        rows = []
        
        # 1. DAVIVIENDA
        if banco_hint == "DAVIVIENDA" or ("DAVIVIENDA" in full_text.upper() and banco_hint == "AUTO"):
            for line in lines:
                if line.startswith(('01 ', '02 ', '03 ', '04 ', '05 ', '06 ', '07 ', '08 ', '09 ', '10 ', '11 ', '12 ', '13 ', '14 ', '15 ', '16 ', '17 ', '18 ', '19 ', '20 ', '21 ', '22 ', '23 ', '24 ', '25 ', '26 ', '27 ', '28 ', '29 ', '30 ', '31 ')):
                    m_amounts = re.findall(r'\$?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)([\+\-])', line)
                    if m_amounts:
                        partes = line.split()
                        dd, mm = partes[0], partes[1]
                        fecha_str = f"2026-{mm}-{dd}"
                        v_str, v_sign = m_amounts[-2] if len(m_amounts) >= 2 else m_amounts[0]
                        val = float(v_str.replace(',', ''))
                        if v_sign == '-': val = -val
                        rows.append({"Fecha": fecha_str, "Descripción": line, "Valor": val, "Banco": "Davivienda"})

        # 2. BANCOLOMBIA
        if banco_hint == "BANCOLOMBIA" or ("BANCOLOMBIA" in full_text.upper() and banco_hint == "AUTO" and not rows):
            pat = re.compile(r'^(\d{1,2}/\d{2})\s+(.+)$')
            for l in lines:
                m = pat.match(l)
                if m:
                    fecha_raw, rest = m.groups()
                    m_nums = re.findall(r'([\-\+]?[\d,]+\.\d{2})', rest)
                    if len(m_nums) >= 1:
                        val = float(m_nums[0].replace(',', ''))
                        desc = rest[:rest.find(m_nums[0])].strip()
                        rows.append({"Fecha": fecha_raw, "Descripción": desc, "Valor": val, "Banco": "Bancolombia"})

        # 3. BANCO DE BOGOTÁ
        if banco_hint == "BANCO_BOGOTA" or (("BANCO DE BOGOTA" in full_text.upper() or "BANCO DE BOGOTÁ" in full_text.upper()) and banco_hint == "AUTO" and not rows):
            pat = re.compile(r'^(\d{2}/\d{2})\s+([A-Z0-9]{4})\s+(.+)$')
            for l in lines:
                m = pat.match(l)
                if m:
                    fecha_raw, cod, rest = m.groups()
                    m_nums = re.findall(r'([\-\+]?[\d,]+\.\d{2})', rest)
                    if len(m_nums) >= 1:
                        val = float(m_nums[-2].replace(',', '')) if len(m_nums) >= 2 else float(m_nums[0].replace(',', ''))
                        desc = rest[:rest.find(m_nums[0])].strip() if len(m_nums)>=1 else rest
                        rows.append({"Fecha": fecha_raw, "Descripción": f"{cod} {desc}", "Valor": val, "Banco": "Banco de Bogotá"})

        # 4. BANCO CAJA SOCIAL (BCS)
        if banco_hint == "BCS" or ("BANCO CAJA SOCIAL" in full_text.upper() and banco_hint == "AUTO" and not rows):
            current_date = ""
            current_desc = []
            for l in lines:
                m_date = re.match(r'^(JUL|AGO|ENE|FEB|MAR|ABR|MAY|JUN|SEP|OCT|NOV|DIC)\s+(\d{1,2})$', l)
                if not m_date: m_date = re.match(r'^(\d{1,2})\s+(JUL|AGO|ENE|FEB|MAR|ABR|MAY|JUN|SEP|OCT|NOV|DIC)$', l)
                if m_date:
                    current_date = l; current_desc = []
                    continue
                m_nums = re.findall(r'([\-\+]?[\d,]+\.\d{2})', l)
                if m_nums and current_date:
                    val_num = float(m_nums[0].replace(',', ''))
                    desc_text = " ".join(current_desc)
                    val_num = -abs(val_num) if any(k in desc_text.upper() for k in ['DEBITO', 'COMISION', 'TRASLADO', 'COMPRA', 'GRAVAMEN', 'IVA', 'CARGO']) else abs(val_num)
                    rows.append({"Fecha": current_date, "Descripción": desc_text if desc_text else "Movimiento BCS", "Valor": val_num, "Banco": "Banco Caja Social"})
                    current_desc = []
                else:
                    if current_date and not l.startswith('Pag.') and not l.startswith('Información'):
                        current_desc.append(l)

        # 5. GLOBAL66
        if banco_hint == "GLOBAL66" or ("GLOBAL66" in full_text.upper() and banco_hint == "AUTO" and not rows):
            i = 0
            while i < len(lines):
                l = lines[i]
                m_dt = re.match(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})$', l)
                if m_dt:
                    dt_str = m_dt.group(1)
                    desc_parts = []
                    val_found = None
                    i += 1
                    while i < len(lines):
                        l_sub = lines[i]
                        if re.match(r'^\d{4}-\d{2}-\d{2}', l_sub):
                            i -= 1
                            break
                        m_val = re.findall(r'\$([\d,]+\.\d{2})', l_sub)
                        if m_val:
                            val_num = float(m_val[0].replace(',', ''))
                            desc_join = " ".join(desc_parts)
                            val_found = -abs(val_num) if 'Compra' in desc_join or 'Cargo' in desc_join else val_num
                            i += 1
                            break
                        else:
                            desc_parts.append(l_sub)
                        i += 1
                    if val_found is not None:
                        rows.append({"Fecha": dt_str, "Descripción": " ".join(desc_parts), "Valor": val_found, "Banco": "Global66"})
                i += 1

        if rows:
            return pd.DataFrame(rows)
            
    except Exception as e:
        logger.error(f"Error procesando extracto bancario en PDF: {str(e)}")
        
    return pd.DataFrame()


def cargar_bytes_a_dataframe(file_bytes: bytes, filename: str, banco_hint: str = "AUTO") -> pd.DataFrame:
    """Carga archivos Excel, CSV o PDF a un DataFrame de Pandas."""
    nombre = filename.lower()
    df_raw = None
    
    if nombre.endswith('.csv'):
        try:
            df_raw = pd.read_csv(io.BytesIO(file_bytes), encoding='utf-8')
        except Exception:
            df_raw = pd.read_csv(io.BytesIO(file_bytes), encoding='latin-1', sep=';')
            
    elif nombre.endswith('.pdf'):
        df_raw = parsear_extracto_pdf_multibanco(file_bytes, banco_hint)
        if df_raw is None or df_raw.empty:
            raise ValueError("No se encontraron registros legibles en el extracto PDF. Comprueba el formato de origen.")
        return df_raw

    else:
        try:
            df_raw = pd.read_excel(io.BytesIO(file_bytes))
        except Exception:
            df_raw = pd.read_excel(io.BytesIO(file_bytes), header=1)

    return autodetectar_y_normalizar_encabezados(df_raw)


def buscar_columna_por_palabras(df: pd.DataFrame, palabras_clave: list) -> Optional[str]:
    """Busca una columna en un DataFrame que coincida con las palabras clave."""
    for col in df.columns:
        col_str = str(col).lower().replace(' ', '').replace('_', '').replace('.', '')
        if any(kw in col_str for kw in palabras_clave):
            return col
    return None


def ejecutar_cruce_algoritmico(df_libros: pd.DataFrame, df_banco: pd.DataFrame, tolerancia: float) -> dict:
    """Ejecuta el cruce de información entre los libros auxiliares y el extracto bancario."""
    
    col_fecha_libros = buscar_columna_por_palabras(df_libros, ['fecha', 'date', 'fmov', 'fechamovimiento', 'fechaelaboracion'])
    col_desc_libros = buscar_columna_por_palabras(df_libros, ['descrip', 'detalle', 'concepto', 'tercero', 'nombre', 'nombredeltercero'])
    col_debito = buscar_columna_por_palabras(df_libros, ['debito', 'débito', 'cargo', 'egreso'])
    col_credito = buscar_columna_por_palabras(df_libros, ['credito', 'crédito', 'abono', 'ingreso'])
    
    col_fecha_banco = buscar_columna_por_palabras(df_banco, ['fecha', 'date', 'transaccion', 'fechatransaccion'])
    col_desc_banco = buscar_columna_por_palabras(df_banco, ['descrip', 'detalle', 'sucursal', 'referencia', 'concepto', 'oficina', 'clasemovimiento'])
    col_valor_banco = buscar_columna_por_palabras(df_banco, ['valor', 'monto', 'cantidad', 'importe'])
    col_retiros = buscar_columna_por_palabras(df_banco, ['retiro', 'cargo', 'salida', 'egreso', 'debito'])
    col_consignaciones = buscar_columna_por_palabras(df_banco, ['consignacion', 'abono', 'deposito', 'entrada', 'ingreso', 'credito'])

    if not col_fecha_libros or (not col_debito and not col_credito):
        raise ValueError(f"No se identificaron las columnas de Fecha o Débito/Crédito en el Auxiliar de Libros. Columnas detectadas: {list(df_libros.columns[:6])}")
    
    if not col_fecha_banco or (not col_valor_banco and not (col_retiros or col_consignaciones)):
        raise ValueError(f"No se identificaron las columnas de Fecha o Montos en el Extracto Bancario. Columnas detectadas: {list(df_banco.columns[:6])}")

    df_libros['NETO'] = 0.0
    if col_debito: 
        df_libros['NETO'] += pd.to_numeric(df_libros[col_debito].astype(str).str.replace('$', '').str.replace(',', '').str.replace(' ', '').str.strip(), errors='coerce').fillna(0)
    if col_credito: 
        df_libros['NETO'] -= pd.to_numeric(df_libros[col_credito].astype(str).str.replace('$', '').str.replace(',', '').str.replace(' ', '').str.strip(), errors='coerce').fillna(0)
    
    df_banco['NETO'] = 0.0
    if col_valor_banco:
        df_banco['NETO'] = pd.to_numeric(df_banco[col_valor_banco].astype(str).str.replace('$', '').str.replace(',', '').str.replace(' ', '').str.strip(), errors='coerce').fillna(0)
    else:
        if col_consignaciones: 
            df_banco['NETO'] += pd.to_numeric(df_banco[col_consignaciones].astype(str).str.replace('$', '').str.replace(',', '').str.replace(' ', '').str.strip(), errors='coerce').fillna(0)
        if col_retiros: 
            df_banco['NETO'] -= pd.to_numeric(df_banco[col_retiros].astype(str).str.replace('$', '').str.replace(',', '').str.replace(' ', '').str.strip(), errors='coerce').fillna(0)

    libros_p = df_libros[df_libros['NETO'] != 0].copy()
    banco_p = df_banco[df_banco['NETO'] != 0].copy()

    conciliados = []
    indices_banco_cruzados = set()

    for idx_l, row_l in libros_p.iterrows():
        val_l = row_l['NETO']
        encontrado = False
        
        for idx_b, row_b in banco_p.iterrows():
            if idx_b in indices_banco_cruzados:
                continue
                
            val_b = row_b['NETO']
            dif = abs(abs(val_l) - abs(val_b))
            
            if dif <= tolerancia:
                conciliados.append({
                    "fecha_libros": str(row_l[col_fecha_libros]),
                    "desc_libros": str(row_l[col_desc_libros]) if col_desc_libros else "Libros Auxiliares",
                    "valor_libros": float(val_l),
                    "fecha_banco": str(row_b[col_fecha_banco]),
                    "desc_banco": str(row_b[col_desc_banco]) if col_desc_banco else "Extracto Bancario",
                    "valor_banco": float(val_b),
                    "diferencia": float(dif)
                })
                indices_banco_cruzados.add(idx_b)
                encontrado = True
                break
        
        if encontrado:
            libros_p.drop(idx_l, inplace=True)

    banco_p = banco_p.drop(list(indices_banco_cruzados))

    pendientes_banco = []
    for _, r in banco_p.iterrows():
        pendientes_banco.append({
            "fecha": str(r[col_fecha_banco]),
            "descripcion": str(r[col_desc_banco]) if col_desc_banco else "Movimiento Banco",
            "valor": float(r['NETO'])
        })

    pendientes_libros = []
    for _, r in libros_p.iterrows():
        pendientes_libros.append({
            "fecha": str(r[col_fecha_libros]),
            "descripcion": str(r[col_desc_libros]) if col_desc_libros else "Movimiento Libros",
            "valor": float(r['NETO'])
        })

    return {
        "resumen": {
            "total_conciliados": len(conciliados),
            "total_faltan_libros": len(pendientes_banco),
            "total_faltan_banco": len(pendientes_libros),
            "monto_conciliado": sum(c["valor_libros"] for c in conciliados),
            "monto_faltan_libros": sum(pb["valor"] for pb in pendientes_banco),
            "monto_faltan_banco": sum(pl["valor"] for pl in pendientes_libros)
        },
        "conciliados": conciliados,
        "pendientes_banco": pendientes_banco,
        "pendientes_libros": pendientes_libros
    }


# ===============================================================================
# 3. ENDPOINTS DE LA API (@router)
# ===============================================================================

@router.post("/upload", summary="Ingesta ETL de ZIP, XML y PDF")
async def procesar_comprobantes_masivos(
    id_empresa: int = Form(...),
    archivos: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    estadisticas = {"total_archivos_inspeccionados": len(archivos), "facturas_procesadas": 0, "soportes_pdf_guardados": 0, "facturas_duplicadas": 0, "errores_lectura_xml": 0}
    PREFIJOS_DEBITO_VALIDOS = ('14', '5', '6', '7')

    try:
        empresa = db.query(models.Empresa).filter(models.Empresa.id_empresa == id_empresa).first()
        if not empresa: 
            raise HTTPException(status_code=404, detail="La empresa destino no existe.")
        
        nit_empresa_limpio = empresa.nit.split('-')[0].strip()

        for archivo in archivos:
            contenido_binario = await archivo.read()
            nombre_archivo = archivo.filename.lower()

            if nombre_archivo.endswith('.zip'):
                try:
                    with zipfile.ZipFile(io.BytesIO(contenido_binario)) as zip_ref:
                        for sub_archivo in zip_ref.namelist():
                            if sub_archivo.startswith('__MACOSX') or sub_archivo.endswith('/'): 
                                continue
                            with zip_ref.open(sub_archivo) as sub_file:
                                data_sub = sub_file.read()
                                
                                if sub_archivo.lower().endswith('.pdf'):
                                    factura_num_aprox = sub_archivo.split('/')[-1].replace('.pdf', '').replace('.PDF', '').upper()
                                    pdf_b64 = base64.b64encode(data_sub).decode('utf-8')
                                    savepoint = db.begin_nested()
                                    try:
                                        if not db.query(models.SoportePDF).filter(models.SoportePDF.factura_num == factura_num_aprox).first():
                                            db.add(models.SoportePDF(factura_num=factura_num_aprox, pdf_b64=pdf_b64))
                                            savepoint.commit()
                                            estadisticas["soportes_pdf_guardados"] += 1
                                        else: 
                                            savepoint.rollback()
                                    except Exception: 
                                        savepoint.rollback()
                                    
                                elif sub_archivo.lower().endswith('.xml'):
                                    savepoint = db.begin_nested()
                                    try:
                                        resultado_parsed = parsear_xml_ubl(data_sub, nit_empresa_limpio)
                                        if resultado_parsed is None:
                                            savepoint.rollback()
                                            continue

                                        lineas_dian = resultado_parsed["lineas"]
                                        cufe_base = resultado_parsed["cufe_base"]
                                        factura_num = resultado_parsed["factura_num"]
                                        nit_tercero = resultado_parsed["nit_tercero"]

                                        if resultado_parsed["pdf_b64"]:
                                            if not db.query(models.SoportePDF).filter(models.SoportePDF.factura_num == factura_num).first():
                                                db.add(models.SoportePDF(factura_num=factura_num, pdf_b64=resultado_parsed["pdf_b64"]))
                                                estadisticas["soportes_pdf_guardados"] += 1

                                        if db.query(models.Factura).filter(models.Factura.id_empresa == id_empresa, models.Factura.cufe_hash.like(f"{cufe_base}%")).first():
                                            estadisticas["facturas_duplicadas"] += 1
                                            savepoint.rollback()
                                        else:
                                            memoria_historica = db.query(models.Factura).filter(models.Factura.id_empresa == id_empresa, models.Factura.nit_tercero == nit_tercero).order_by(models.Factura.id_factura.desc()).first()
                                            cuenta_balance = None
                                            cuenta_historia = None

                                            if memoria_historica and memoria_historica.cuenta_gasto:
                                                cta_h = str(memoria_historica.cuenta_gasto).strip()
                                                if cta_h.startswith(PREFIJOS_DEBITO_VALIDOS): 
                                                    cuenta_historia = cta_h

                                            if not cuenta_historia:
                                                try:
                                                    saldos_tercero = db.query(models.BalanceTercero).filter(models.BalanceTercero.id_empresa == id_empresa, models.BalanceTercero.nit_tercero == nit_tercero).all()
                                                    for saldo in saldos_tercero:
                                                        if saldo.cuenta_contable:
                                                            cta_b = str(saldo.cuenta_contable).strip()
                                                            if cta_b.startswith(PREFIJOS_DEBITO_VALIDOS):
                                                                cuenta_balance = cta_b
                                                                break
                                                except Exception: 
                                                    pass

                                            for linea in lineas_dian:
                                                naturaleza = linea.pop("naturaleza", "EGRESO")
                                                prefijos_validos = ('4',) if naturaleza == "INGRESO" else ('14', '5', '6', '7')
                                                cuenta_asignada = "413524" if naturaleza == "INGRESO" else "51953001"
                                                
                                                if memoria_historica and memoria_historica.cuenta_gasto and str(memoria_historica.cuenta_gasto).startswith(prefijos_validos):
                                                    cuenta_asignada = memoria_historica.cuenta_gasto
                                                    linea["retencion_porc"] = memoria_historica.retencion_porc
                                                    linea["casilla_350"] = memoria_historica.casilla_350
                                                else:
                                                    try:
                                                        saldos = db.query(models.BalanceTercero).filter(models.BalanceTercero.id_empresa == id_empresa, models.BalanceTercero.nit_tercero == nit_tercero).all()
                                                        for s in saldos:
                                                            if s.cuenta_contable and str(s.cuenta_contable).startswith(prefijos_validos):
                                                                cuenta_asignada = s.cuenta_contable
                                                                break
                                                    except Exception: 
                                                        pass

                                                db.add(models.Factura(id_empresa=id_empresa, **linea, cuenta_gasto=cuenta_asignada, estado_revision="PENDIENTE"))
                                            savepoint.commit()
                                            estadisticas["facturas_procesadas"] += 1
                                    except Exception:
                                        savepoint.rollback()
                                        estadisticas["errores_lectura_xml"] += 1
                except zipfile.BadZipFile: 
                    continue
            
            elif nombre_archivo.endswith('.xml'):
                savepoint = db.begin_nested()
                try:
                    resultado_parsed = parsear_xml_ubl(contenido_binario, nit_empresa_limpio)
                    if resultado_parsed is not None:
                        lineas_dian = resultado_parsed["lineas"]
                        cufe_base = resultado_parsed["cufe_base"]
                        nit_tercero = resultado_parsed["nit_tercero"]

                        if db.query(models.Factura).filter(models.Factura.id_empresa == id_empresa, models.Factura.cufe_hash.like(f"{cufe_base}%")).first():
                            estadisticas["facturas_duplicadas"] += 1
                            savepoint.rollback()
                        else:
                            memoria_historica = db.query(models.Factura).filter(models.Factura.id_empresa == id_empresa, models.Factura.nit_tercero == nit_tercero).order_by(models.Factura.id_factura.desc()).first()

                            for linea in lineas_dian:
                                naturaleza = linea.pop("naturaleza", "EGRESO")
                                prefijos_validos = ('4',) if naturaleza == "INGRESO" else ('14', '5', '6', '7')
                                cuenta_asignada = "413524" if naturaleza == "INGRESO" else "51953001"
                                
                                if memoria_historica and memoria_historica.cuenta_gasto and str(memoria_historica.cuenta_gasto).startswith(prefijos_validos):
                                    cuenta_asignada = memoria_historica.cuenta_gasto
                                    linea["retencion_porc"] = memoria_historica.retencion_porc
                                    linea["casilla_350"] = memoria_historica.casilla_350
                                else:
                                    try:
                                        saldos = db.query(models.BalanceTercero).filter(models.BalanceTercero.id_empresa == id_empresa, models.BalanceTercero.nit_tercero == nit_tercero).all()
                                        for s in saldos:
                                            if s.cuenta_contable and str(s.cuenta_contable).startswith(prefijos_validos):
                                                cuenta_asignada = s.cuenta_contable
                                                break
                                    except Exception: 
                                        pass

                                db.add(models.Factura(id_empresa=id_empresa, **linea, cuenta_gasto=cuenta_asignada, estado_revision="PENDIENTE"))
                            savepoint.commit()
                            estadisticas["facturas_procesadas"] += 1
                    else: 
                        savepoint.rollback()
                except Exception:
                    savepoint.rollback()
                    estadisticas["errores_lectura_xml"] += 1

            elif nombre_archivo.endswith('.pdf'):
                factura_num = nombre_archivo.replace('.pdf', '').upper()
                pdf_b64 = base64.b64encode(contenido_binario).decode('utf-8')
                savepoint = db.begin_nested()
                try:
                    if not db.query(models.SoportePDF).filter(models.SoportePDF.factura_num == factura_num).first():
                        db.add(models.SoportePDF(factura_num=factura_num, pdf_b64=pdf_b64))
                        savepoint.commit()
                        estadisticas["soportes_pdf_guardados"] += 1
                    else: 
                        savepoint.rollback()
                except Exception: 
                    savepoint.rollback()

        db.commit()
        return estadisticas
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Fallo crítico ETL: {str(e)}")


@router.post("/puc/upload", summary="Ingesta ETL Catálogo")
async def subir_puc(id_empresa: int = Form(...), archivo: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        empresa = db.query(models.Empresa).filter(models.Empresa.id_empresa == id_empresa).first()
        if not empresa: 
            raise HTTPException(status_code=404, detail="Empresa no encontrada")
        datos_puc = parsear_puc_siigo_pyme(await archivo.read())
        db.query(models.CuentaPUC).filter(models.CuentaPUC.id_empresa == id_empresa).delete(synchronize_session=False)
        for row in datos_puc: 
            db.add(models.CuentaPUC(id_empresa=id_empresa, cuenta=row['CUENTA'], nombre=row['NOMBRE']))
        db.commit()
        return {"status": "success", "registros_procesados": len(datos_puc), "mensaje": "PUC actualizado."}
    except Exception as e: 
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/balance/upload", summary="Ingesta ETL Balance")
async def subir_balance(id_empresa: int = Form(...), archivo: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        empresa = db.query(models.Empresa).filter(models.Empresa.id_empresa == id_empresa).first()
        if not empresa: 
            raise HTTPException(status_code=404, detail="Empresa no encontrada")
        datos_balance = parsear_balance_siigo_pyme(await archivo.read())
        db.query(models.BalanceTercero).filter(models.BalanceTercero.id_empresa == id_empresa).delete(synchronize_session=False)
        for row in datos_balance: 
            db.add(models.BalanceTercero(id_empresa=id_empresa, **row))
        db.commit()
        return {"status": "success", "registros_procesados": len(datos_balance), "mensaje": "Saldos actualizados."}
    except Exception as e: 
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/puc/borrar")
def purgar_puc(id_empresa: int, db: Session = Depends(get_db)):
    try:
        eliminados = db.query(models.CuentaPUC).filter(models.CuentaPUC.id_empresa == id_empresa).delete(synchronize_session=False)
        db.commit()
        return {"status": "success", "mensaje": f"Se eliminaron {eliminados} cuentas."}
    except Exception as e: 
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/balance/borrar")
def purgar_balance(id_empresa: int, db: Session = Depends(get_db)):
    try:
        eliminados = db.query(models.BalanceTercero).filter(models.BalanceTercero.id_empresa == id_empresa).delete(synchronize_session=False)
        db.commit()
        return {"status": "success", "mensaje": f"Se eliminaron {eliminados} saldos."}
    except Exception as e: 
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/", summary="Listar facturas por cliente")
def listar_facturas(
    id_empresa: int, 
    fecha_desde: Optional[str] = None, 
    fecha_hasta: Optional[str] = None, 
    tipo_comprobante: Optional[str] = None, 
    db: Session = Depends(get_db)
):
    query = db.query(models.Factura).filter(models.Factura.id_empresa == id_empresa)
    query = aplicar_filtros_tabla(query, fecha_desde, fecha_hasta, tipo_comprobante)
    return query.order_by(models.Factura.id_factura.desc()).all()


@router.delete("/{id_factura}", summary="Eliminar factura")
def eliminar_factura(id_factura: int, db: Session = Depends(get_db)):
    factura = db.query(models.Factura).filter(models.Factura.id_factura == id_factura).first()
    if not factura: 
        raise HTTPException(status_code=404, detail="Comprobante no encontrado.")
    cufe_base = factura.cufe_hash.split('_L')[0] if factura.cufe_hash else factura.cufe_hash
    db.query(models.Factura).filter(models.Factura.id_empresa == factura.id_empresa, models.Factura.cufe_hash.like(f"{cufe_base}%")).delete(synchronize_session=False)
    db.commit()
    return {"status": "success"}


@router.post("/bulk-delete", summary="Borrado masivo")
def borrado_masivo(ids: List[int], db: Session = Depends(get_db)):
    db.query(models.Factura).filter(models.Factura.id_factura.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"status": "success", "eliminados": len(ids)}


@router.patch("/{id_factura}/cuenta", summary="Reclasificación Contable")
def actualizar_cuenta_gasto(id_factura: int, payload: CuentaGastoUpdate, db: Session = Depends(get_db)):
    try:
        if payload.aplicar_a_proveedor and payload.nit_tercero:
            facturas = db.query(models.Factura).filter(models.Factura.id_empresa == payload.id_empresa, models.Factura.nit_tercero == payload.nit_tercero).all()
            for f in facturas: 
                f.cuenta_gasto = payload.cuenta_gasto.strip()
            db.commit()
            return {"status": "success"}
        else:
            factura = db.query(models.Factura).filter(models.Factura.id_factura == id_factura).first()
            if not factura: 
                raise HTTPException(status_code=404, detail="Línea no encontrada.")
            factura.cuenta_gasto = payload.cuenta_gasto.strip()
            db.commit()
            return {"status": "success"}
    except Exception as e: 
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{id_factura}/retencion", summary="Actualizar Retención")
def actualizar_retencion(id_factura: int, payload: RetencionUpdate, db: Session = Depends(get_db)):
    try:
        casilla_imputada = 64 if payload.retencion_porc > 0 else None
        if payload.aplicar_a_proveedor and payload.nit_tercero:
            facturas = db.query(models.Factura).filter(models.Factura.id_empresa == payload.id_empresa, models.Factura.nit_tercero == payload.nit_tercero).all()
            for f in facturas: 
                f.retencion_porc = payload.retencion_porc; f.casilla_350 = casilla_imputada
            db.commit()
            return {"status": "success"}
        else:
            factura = db.query(models.Factura).filter(models.Factura.id_factura == id_factura).first()
            if not factura: 
                raise HTTPException(status_code=404, detail="Línea no encontrada.")
            factura.retencion_porc = payload.retencion_porc; factura.casilla_350 = casilla_imputada
            db.commit()
            return {"status": "success"}
    except Exception as e: 
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pdf/{factura_num}", summary="Visor de PDF")
def obtener_pdf(factura_num: str, db: Session = Depends(get_db)):
    soporte = db.query(models.SoportePDF).filter(models.SoportePDF.factura_num == factura_num).first()
    if not soporte: 
        raise HTTPException(status_code=404, detail="Soporte PDF no disponible.")
    return {"factura_num": soporte.factura_num, "pdf_b64": soporte.pdf_b64}


@router.post("/export/soportes-pdf", summary="Exportar PDFs a ZIP")
def exportar_soportes_zip(payload: SoporteExportRequest, db: Session = Depends(get_db)):
    soportes = db.query(models.SoportePDF).filter(models.SoportePDF.factura_num.in_(payload.facturas_seleccionadas)).all()
    if not soportes: 
        raise HTTPException(status_code=404, detail="No hay PDFs disponibles.")
    
    facturas_meta = db.query(models.Factura).filter(models.Factura.id_empresa == payload.id_empresa, models.Factura.factura_num.in_(payload.facturas_seleccionadas)).all()
    mapa_proveedores = {}
    for f in facturas_meta:
        if f.proveedor:
            nombre_limpio = re.sub(r'[\\/*?:"<>|]', "", str(f.proveedor)).strip()
            mapa_proveedores[f.factura_num] = nombre_limpio

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for s in soportes:
            pdf_bytes = base64.b64decode(s.pdf_b64)
            razon_social = mapa_proveedores.get(s.factura_num, "Soporte_Generico")
            nombre_archivo = f"{s.factura_num} - {razon_social}.pdf"
            zip_file.writestr(nombre_archivo, pdf_bytes)
            
    zip_buffer.seek(0)
    return StreamingResponse(zip_buffer, media_type="application/zip", headers={"Content-Disposition": f"attachment; filename=Soportes_TLM_{payload.id_empresa}.zip"})


@router.get("/export/auditoria", summary="Exportar Hoja de Trabajo Multicapa")
def exportar_excel_auditoria(
    id_empresa: int, 
    fecha_desde: Optional[str] = None, 
    fecha_hasta: Optional[str] = None, 
    tipo_comprobante: Optional[str] = None, 
    db: Session = Depends(get_db)
):
    query = db.query(models.Factura).filter(models.Factura.id_empresa == id_empresa)
    query = aplicar_filtros_tabla(query, fecha_desde, fecha_hasta, tipo_comprobante)
    facturas = query.all()

    if not facturas: 
        raise HTTPException(status_code=404, detail="No hay datos registrados para exportar.")
        
    data_detalle = [{
        "Fecha Emisión": f.fecha, "Fecha Vencimiento": f.fecha_vencimiento, "Factura N°": f.factura_num, 
        "NIT Tercero": f.nit_tercero, "Razón Social": f.proveedor, "Ítem / Servicio": f.descripcion_item, 
        "Cuenta Contable": f.cuenta_gasto, "Cantidad": f.cantidad or 1.0, "Valor Unitario": f.valor_unitario or 0.0, 
        "Subtotal Línea": f.subtotal or 0.0, "IVA Línea": f.iva or 0.0, "Total Línea": (f.subtotal or 0) + (f.iva or 0), 
        "Forma de Pago": f.forma_pago, "% RteFte Asignado": f.retencion_porc or 0.0
    } for f in facturas]
    
    df_detalle = pd.DataFrame(data_detalle)
    df_consolidado = df_detalle.groupby(["Factura N°", "NIT Tercero", "Razón Social", "Fecha Emisión", "Forma de Pago", "Fecha Vencimiento"]).agg({"Subtotal Línea": "sum", "IVA Línea": "sum", "Total Línea": "sum"}).reset_index()
    df_consolidado.rename(columns={"Subtotal Línea": "Subtotal Factura", "IVA Línea": "Total IVA", "Total Línea": "Total a Pagar"}, inplace=True)
    
    output = io.BytesIO()
    try:
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_consolidado.to_excel(writer, index=False, sheet_name='Consolidado Gerencial')
            df_detalle.to_excel(writer, index=False, sheet_name='Detalle Línea a Línea')
    except Exception:
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_consolidado.to_excel(writer, index=False, sheet_name='Consolidado Gerencial')
            df_detalle.to_excel(writer, index=False, sheet_name='Detalle Línea a Línea')
    output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename=Auditoria_TLM_{id_empresa}.xlsx"})


@router.get("/export/siigo", summary="Generar Interfaz Contable (Siigo Pyme)")
def exportar_interfaz_siigo(
    id_empresa: int, 
    fecha_desde: Optional[str] = None, 
    fecha_hasta: Optional[str] = None, 
    tipo_comprobante: Optional[str] = None, 
    db: Session = Depends(get_db)
):
    query = db.query(models.Factura).filter(models.Factura.id_empresa == id_empresa)
    query = aplicar_filtros_tabla(query, fecha_desde, fecha_hasta, tipo_comprobante)
    facturas = query.all()

    if not facturas: 
        raise HTTPException(status_code=404, detail="No hay datos para exportar.")
        
    data = []
    for f in facturas:
        es_ingreso = f.cuenta_gasto and str(f.cuenta_gasto).startswith('4')
        naturaleza = "C" if es_ingreso else "D"
        data.append({"Comprobante": "P", "Consecutivo": f.factura_num, "Fecha": f.fecha, "Cuenta": f.cuenta_gasto or ("413524" if es_ingreso else "51953001"), "NIT": f.nit_tercero, "Centro_Costo": "1", "Detalle": f.descripcion_item[:50] if f.descripcion_item else "Comprobante", "Naturaleza": naturaleza, "Valor": f.subtotal})
        if f.iva and f.iva > 0: 
            cta_iva = "24080105" if es_ingreso else "24080101"
            data.append({"Comprobante": "P", "Consecutivo": f.factura_num, "Fecha": f.fecha, "Cuenta": cta_iva, "NIT": f.nit_tercero, "Centro_Costo": "1", "Detalle": "IVA Generado" if es_ingreso else "IVA Descontable", "Naturaleza": naturaleza, "Valor": f.iva})
    
    df_temp = pd.DataFrame(data)
    cabeceras = df_temp.groupby(["Consecutivo", "Fecha", "NIT"]).agg({"Valor": "sum"}).reset_index()
    for _, row in cabeceras.iterrows(): 
        data.append({"Comprobante": "P", "Consecutivo": row["Consecutivo"], "Fecha": row["Fecha"], "Cuenta": "22050101", "NIT": row["NIT"], "Centro_Costo": "1", "Detalle": "CXP Proveedores / CXC Clientes", "Naturaleza": "C", "Valor": row["Valor"]})
    df_final = pd.DataFrame(data)
    output = io.BytesIO()
    df_final.to_csv(output, sep=';', index=False, encoding='utf-8')
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=Interfaz_Siigo_{id_empresa}.csv"})


@router.get("/kpis/resumen", summary="Métricas de Estado de Resultados")
def obtener_kpis(
    id_empresa: int, 
    fecha_desde: Optional[str] = None, 
    fecha_hasta: Optional[str] = None, 
    tipo_comprobante: Optional[str] = None, 
    db: Session = Depends(get_db)
):
    query = db.query(models.Factura).filter(models.Factura.id_empresa == id_empresa)
    query = aplicar_filtros_tabla(query, fecha_desde, fecha_hasta, tipo_comprobante)
    facturas = query.all()
    
    ingresos = sum(f.subtotal for f in facturas if f.subtotal and f.cuenta_gasto and str(f.cuenta_gasto).startswith('4'))
    egresos = sum(f.subtotal for f in facturas if f.subtotal and f.cuenta_gasto and str(f.cuenta_gasto).startswith(('14', '5', '6', '7')))
    
    margen_bruto = 0
    if ingresos > 0:
        margen_bruto = round(((ingresos - egresos) / ingresos) * 100, 1)

    retefuente = sum(f.subtotal * ((f.retencion_porc or 0) / 100) for f in facturas if f.subtotal and f.cuenta_gasto and str(f.cuenta_gasto).startswith(('14', '5', '6', '7')))
    cufe_unicos = set(f.cufe_hash.split('_L')[0] for f in facturas if f.cufe_hash)
    
    return {"ingresos_totales": ingresos, "egresos_totales": egresos, "margen_bruto_porcentaje": margen_bruto, "total_retefuente_f350": retefuente, "total_comprobantes": len(cufe_unicos)}


@router.get("/impuestos/f350", summary="Liquidación Retenciones")
def obtener_f350(
    id_empresa: int, 
    fecha_desde: Optional[str] = None, 
    fecha_hasta: Optional[str] = None, 
    tipo_comprobante: Optional[str] = None, 
    db: Session = Depends(get_db)
):
    query = db.query(models.Factura).filter(models.Factura.id_empresa == id_empresa)
    query = aplicar_filtros_tabla(query, fecha_desde, fecha_hasta, tipo_comprobante)
    facturas = query.all()
    
    total_retefuente = 0.0
    casillas = {}
    for f in facturas:
        if f.cuenta_gasto and str(f.cuenta_gasto).startswith(('14', '5', '6', '7')):
            if f.retencion_porc and f.retencion_porc > 0 and f.subtotal:
                retencion = f.subtotal * (f.retencion_porc / 100)
                total_retefuente += retencion
                c_key = str(f.casilla_350 or 64)
                if c_key not in casillas: 
                    casillas[c_key] = {"concepto": f"Base Gravable Casilla {c_key}", "base": 0.0, "retencion": 0.0}
                casillas[c_key]["base"] += f.subtotal; casillas[c_key]["retencion"] += retencion
    return {"total_retefuente_nacional": total_retefuente, "total_reteica_territorial": 0.0, "total_reteiva": 0.0, "casillas_f350": casillas}


@router.post("/conciliacion/ejecutar", summary="Procesar Conciliación en Pantalla")
async def ejecutar_conciliacion_vista(
    file_siigo: UploadFile = File(...),
    file_banco: UploadFile = File(...),
    tolerancia: float = Form(100.0),
    banco_origen: str = Form("AUTO")
):
    try:
        df_libros = cargar_bytes_a_dataframe(await file_siigo.read(), file_siigo.filename, "AUTO")
        df_banco = cargar_bytes_a_dataframe(await file_banco.read(), file_banco.filename, banco_origen)
        
        resultado = ejecutar_cruce_algoritmico(df_libros, df_banco, tolerancia)
        return resultado
    except Exception as e:
        logger.error(f"Error en conciliación: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/conciliacion/descargar-excel", summary="Descargar Reporte Excel de Conciliación")
async def descargar_excel_conciliacion(
    file_siigo: UploadFile = File(...),
    file_banco: UploadFile = File(...),
    tolerancia: float = Form(100.0),
    banco_origen: str = Form("AUTO")
):
    try:
        df_libros = cargar_bytes_a_dataframe(await file_siigo.read(), file_siigo.filename, "AUTO")
        df_banco = cargar_bytes_a_dataframe(await file_banco.read(), file_banco.filename, banco_origen)
        
        res = ejecutar_cruce_algoritmico(df_libros, df_banco, tolerancia)
        
        df_c = pd.DataFrame(res["conciliados"])
        df_pb = pd.DataFrame(res["pendientes_banco"])
        df_pl = pd.DataFrame(res["pendientes_libros"])
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            pd.DataFrame([{
                "Métrica": "Partidas Conciliadas", "Cantidad": res["resumen"]["total_conciliados"], "Monto COP": res["resumen"]["monto_conciliado"]
            }, {
                "Métrica": "Pendientes por Registrar en Libros", "Cantidad": res["resumen"]["total_faltan_libros"], "Monto COP": res["resumen"]["monto_faltan_libros"]
            }, {
                "Métrica": "Pendientes de Cobro en Banco", "Cantidad": res["resumen"]["total_faltan_banco"], "Monto COP": res["resumen"]["monto_faltan_banco"]
            }]).to_excel(writer, index=False, sheet_name='Resumen Ejecutivo')
            
            (df_c if not df_c.empty else pd.DataFrame([{"Mensaje": "Sin partidas"}])).to_excel(writer, index=False, sheet_name='Conciliados')
            (df_pb if not df_pb.empty else pd.DataFrame([{"Mensaje": "Sin partidas"}])).to_excel(writer, index=False, sheet_name='Faltan en Libros')
            (df_pl if not df_pl.empty else pd.DataFrame([{"Mensaje": "Sin partidas"}])).to_excel(writer, index=False, sheet_name='Faltan en Banco')
            
        output.seek(0)
        return StreamingResponse(
            output, 
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
            headers={"Content-Disposition": f"attachment; filename=Conciliacion_Bancaria_TLM.xlsx"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/conciliacion/pdf-a-excel", summary="Convertir Extracto PDF Bancario a Excel")
async def convertir_pdf_bancario_a_excel(
    file_banco: UploadFile = File(...),
    banco_origen: str = Form("AUTO")
):
    try:
        contenido = await file_banco.read()
        df_extracto = parsear_extracto_pdf_multibanco(contenido, banco_origen)
        
        if df_extracto.empty:
            raise ValueError("No se encontraron filas de movimiento en el archivo PDF cargado.")
            
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_extracto.to_excel(writer, index=False, sheet_name='Movimientos Bancarios')
            
        output.seek(0)
        nombre_excel = file_banco.filename.replace('.pdf', '').replace('.PDF', '') + '_Convertido.xlsx'
        
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={nombre_excel}"}
        )
    except Exception as e:
        logger.error(f"Error al convertir PDF a Excel: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))