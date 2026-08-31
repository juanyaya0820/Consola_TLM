import xml.etree.ElementTree as ET
import logging
import re
import html
import hashlib
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def _remover_namespaces(root: ET.Element) -> ET.Element:
    """Elimina dinámicamente prefijos de espacios de nombres (cac:, cbc:) en todo el árbol XML."""
    for elem in root.iter():
        if '}' in elem.tag:
            elem.tag = elem.tag.split('}', 1)[1]
    return root

def _obtener_raiz_ubl_limpia(xml_bytes: bytes) -> ET.Element:
    """
    Extrae la estructura raíz <Invoice> o <CreditNote> comercial real,
    desenrollando bloques CDATA de contenedores externos <AttachedDocument>.
    """
    try:
        xml_str = xml_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        xml_str = xml_bytes.decode('latin-1', errors='replace')

    xml_str = html.unescape(xml_str)
    if "&lt;" in xml_str or "&gt;" in xml_str:
        xml_str = html.unescape(xml_str)

    match_ubl = re.search(
        r'<(?:[a-zA-Z0-9_\-]+:)?(Invoice|CreditNote)[\s>].*?</(?:[a-zA-Z0-9_\-]+:)?\1>',
        xml_str,
        re.DOTALL | re.IGNORECASE
    )

    if match_ubl:
        xml_str = match_ubl.group(0)

    root = ET.fromstring(xml_str.strip())
    root = _remover_namespaces(root)

    if root.tag == "AttachedDocument":
        for desc_nodo in root.findall('.//Description'):
            if desc_nodo.text and ("<Invoice" in desc_nodo.text or "<CreditNote" in desc_nodo.text):
                inner_str = html.unescape(desc_nodo.text.strip())
                match_inner = re.search(
                    r'<(?:[a-zA-Z0-9_\-]+:)?(Invoice|CreditNote)[\s>].*?</(?:[a-zA-Z0-9_\-]+:)?\1>',
                    inner_str,
                    re.DOTALL | re.IGNORECASE
                )
                if match_inner:
                    inner_root = ET.fromstring(match_inner.group(0).strip())
                    return _remover_namespaces(inner_root)

    return root

def _extraer_proveedor(root: ET.Element) -> tuple:
    """Extrae Razón Social y NIT del emisor explorando estructuras UBL 2.1."""
    supplier = root.find('.//AccountingSupplierParty') or root.find('.//SenderParty') or root

    nombre, nit = "", ""

    for tag in ['RegistrationName', 'Name']:
        for node in supplier.findall(f'.//{tag}'):
            if node.text and len(node.text.strip()) > 1 and not node.text.strip().isdigit():
                nombre = node.text.strip()
                break
        if nombre: break

    for tag in ['CompanyID', 'ID']:
        for node in supplier.findall(f'.//{tag}'):
            if node.text:
                clean = re.sub(r'[^\d]', '', node.text.split('-')[0])
                if len(clean) >= 6 and clean != "800000000":
                    nit = clean
                    break
        if nit: break

    if not nombre:
        for node in root.findall('.//RegistrationName'):
            if node.text and len(node.text.strip()) > 1 and not node.text.strip().isdigit():
                nombre = node.text.strip()
                break

    if not nit:
        for node in root.findall('.//CompanyID'):
            if node.text:
                clean = re.sub(r'[^\d]', '', node.text.split('-')[0])
                if len(clean) >= 6:
                    nit = clean
                    break

    return nombre or "PROVEEDOR DESCONOCIDO", nit or "800000000"

def _extraer_condiciones_pago(root: ET.Element, fecha_emision: str, es_nota_credito: bool) -> tuple:
    """Extrae Forma de Pago (1=Contado, 2=Crédito), Medio de Pago y Fecha de Vencimiento."""
    if es_nota_credito:
        return "N/A (Nota Crédito)", "47 - Transferencia / Ajuste", fecha_emision

    forma_pago = "Contado"
    medio_pago = "10 - Efectivo"
    fecha_vencimiento = fecha_emision

    nodo_pago = root.find('.//PaymentMeans')
    if nodo_pago is not None:
        id_pago = nodo_pago.find('.//ID')
        if id_pago is not None and id_pago.text:
            val_id = id_pago.text.strip()
            if val_id == "2":
                forma_pago = "Crédito"
            elif val_id == "1":
                forma_pago = "Contado"

        code_pago = nodo_pago.find('.//PaymentMeansCode')
        if code_pago is not None and code_pago.text:
            medio_pago = code_pago.text.strip()

        due_nodo = nodo_pago.find('.//PaymentDueDate')
        if due_nodo is not None and due_nodo.text:
            fecha_vencimiento = due_nodo.text.strip()

    if fecha_vencimiento == fecha_emision:
        terms_nodo = root.find('.//PaymentTerms')
        if terms_nodo is not None:
            due_terms = terms_nodo.find('.//PaymentDueDate')
            if due_terms is not None and due_terms.text:
                fecha_vencimiento = due_terms.text.strip()
                forma_pago = "Crédito"

    return forma_pago, medio_pago, fecha_vencimiento

def _extraer_cantidad_linea(linea: ET.Element) -> float:
    """Extrae la cantidad con chequeo explícito 'is not None' para evitar trampas booleanas."""
    etiquetas = ['InvoicedQuantity', 'CreditedQuantity', 'BaseQuantity', 'Quantity']

    for tag in etiquetas:
        nodo = linea.find(f'.//{tag}')
        if nodo is None:
            nodo = linea.find(tag)

        if nodo is not None and nodo.text and nodo.text.strip():
            try:
                val_clean = nodo.text.strip().replace(',', '.')
                return float(val_clean)
            except ValueError:
                continue

    return 1.0

def parsear_xml_ubl(xml_bytes: bytes, nombre_archivo: str = "", nit_empresa_activa: str = None) -> List[Dict[str, Any]]:
    """Engine de parsing financiero con extracción garantizada de cantidades y flujo comercial."""
    try:
        root = _obtener_raiz_ubl_limpia(xml_bytes)
    except Exception as e:
        logger.error(f"Error procesando {nombre_archivo}: {str(e)}")
        return []

    es_nota_credito = (root.tag == "CreditNote")

    # 1. ENCABEZADOS
    id_nodo = root.find('.//ID')
    factura_num = id_nodo.text.strip() if (id_nodo is not None and id_nodo.text) else "FE-SIN-NUMERO"
    if len(factura_num) > 35 or factura_num.startswith("http"):
        factura_num = re.sub(r'[^\w\-]', '', nombre_archivo.split('.')[0]) or "FE-SIN-NUMERO"

    fecha_nodo = root.find('.//IssueDate')
    fecha_emision = fecha_nodo.text.strip() if (fecha_nodo is not None and fecha_nodo.text) else "2026-01-01"

    uuid_nodo = root.find('.//UUID')
    cufe_raw = uuid_nodo.text.strip() if (uuid_nodo is not None and uuid_nodo.text) else ""
    if not cufe_raw:
        cufe_raw = hashlib.md5(xml_bytes).hexdigest()

    proveedor_nombre, nit_tercero = _extraer_proveedor(root)
    forma_pago, medio_pago, fecha_vencimiento = _extraer_condiciones_pago(root, fecha_emision, es_nota_credito)

    flujo_dian = "Egreso (Compra/Gasto)"
    if nit_empresa_activa and nit_tercero == nit_empresa_activa:
        flujo_dian = "Ingreso (Venta)"

    lineas_xml = root.findall('.//InvoiceLine') or root.findall('.//CreditNoteLine')
    resultados = []
    file_hash = hashlib.md5(nombre_archivo.encode()).hexdigest()[:6]

    # 2. FACTURA SIN LÍNEAS DETALLADAS
    if not lineas_xml:
        monetary = root.find('.//LegalMonetaryTotal')
        subtotal = 0.0
        if monetary is not None:
            sub_node = monetary.find('.//LineExtensionAmount')
            if sub_node is not None and sub_node.text:
                subtotal = float(sub_node.text.strip())

        resultados.append({
            "factura_num": factura_num,
            "fecha": fecha_emision,
            "fecha_vencimiento": fecha_vencimiento,
            "forma_pago": forma_pago,
            "medio_pago": medio_pago,
            "cufe_hash": f"{cufe_raw[:20]}_{file_hash}_L1",
            "proveedor": proveedor_nombre,
            "nit_tercero": nit_tercero,
            "descripcion_item": "Gasto/Servicio General (Resumen Consolidado)",
            "cantidad": 1.0,
            "valor_unitario": subtotal,
            "subtotal": subtotal,
            "iva": 0.0,
            "flujo_dian": flujo_dian,
            "cuenta_gasto": "51953001",
            "casilla_350": 64,
            "retencion_porc": 2.5 if subtotal >= 1189000 else 0.0,
            "reteica_tarifa": 0.0,
            "reteiva_porc": 0.0,
            "estado_revision": "PENDIENTE"
        })
        return resultados

    # 3. EXTRACCIÓN LÍNEA POR LÍNEA
    for idx, linea in enumerate(lineas_xml):
        desc_node = linea.find('.//Description')
        descripcion = desc_node.text.strip() if (desc_node is not None and desc_node.text) else f"Ítem Contable #{idx+1}"

        cant = _extraer_cantidad_linea(linea)

        sub_node = linea.find('.//LineExtensionAmount')
        subtotal_linea = float(sub_node.text.strip()) if (sub_node is not None and sub_node.text) else 0.0

        price_node = linea.find('.//PriceAmount')
        val_unitario = float(price_node.text.strip()) if (price_node is not None and price_node.text) else (subtotal_linea / cant if cant > 0 else subtotal_linea)

        tax_node = linea.find('.//TaxTotal')
        iva_linea = 0.0
        if tax_node is not None:
            tax_amount_node = tax_node.find('.//TaxAmount')
            if tax_amount_node is not None and tax_amount_node.text:
                iva_linea = float(tax_amount_node.text.strip())

        casilla = 64
        ret_porc = 2.5 if subtotal_linea >= 1189000 else 0.0

        desc_lower = descripcion.lower()
        if "honorario" in desc_lower or "asesor" in desc_lower:
            casilla = 61
            ret_porc = 10.0
        elif "servicio" in desc_lower or "mantenimiento" in desc_lower:
            casilla = 62
            ret_porc = 4.0 if subtotal_linea >= 176000 else 0.0

        resultados.append({
            "factura_num": factura_num,
            "fecha": fecha_emision,
            "fecha_vencimiento": fecha_vencimiento,
            "forma_pago": forma_pago,
            "medio_pago": medio_pago,
            "cufe_hash": f"{cufe_raw[:20]}_{file_hash}_L{idx+1}",
            "proveedor": proveedor_nombre,
            "nit_tercero": nit_tercero,
            "descripcion_item": descripcion,
            "cantidad": cant,
            "valor_unitario": val_unitario,
            "subtotal": subtotal_linea,
            "iva": iva_linea,
            "flujo_dian": flujo_dian,
            "cuenta_gasto": "51953001",
            "casilla_350": casilla,
            "retencion_porc": ret_porc,
            "reteica_tarifa": 0.0,
            "reteiva_porc": 0.0,
            "estado_revision": "PENDIENTE"
        })

    return resultados