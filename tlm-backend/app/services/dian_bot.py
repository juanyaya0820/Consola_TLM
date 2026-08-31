from playwright.sync_api import sync_playwright, TimeoutError
import logging
import time
import random

logger = logging.getLogger(__name__)

BASE_DIAN_URL = "https://catalogo-vpfe.dian.gov.co"

def extraer_facturas_por_cufes(auth_url_completa: str, lista_cufes: list) -> list:
    """
    Motor RPA de Navegación Orgánica Pura.
    Ejecuta la secuencia completa de clics sobre la interfaz gráfica sin forzar URLs directas,
    preservando los tokens Anti-Forgery de ASP.NET y la validación de Cloudflare Turnstile.
    """
    archivos_descargados = []
    
    if not auth_url_completa.startswith("http"):
        auth_url_completa = f"{BASE_DIAN_URL}/User/AuthToken?token={auth_url_completa}"
        
    with sync_playwright() as p:
        # Lanzamiento de Chromium en modo visible con enmascaramiento de automatización
        browser = p.chromium.launch(
            headless=False, 
            args=['--start-maximized', '--disable-blink-features=AutomationControlled']
        )
        
        context = browser.new_context(
            no_viewport=True,
            accept_downloads=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        page = context.new_page()
        # Ocultar la propiedad 'navigator.webdriver' ante scripts de rastreo
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.set_default_timeout(60000)
        
        logger.info("RPA (UI Pure): Iniciando motor gráfico de clics secuenciales...")
        
        # 1. Cargar Token Inicial
        try:
            page.goto(auth_url_completa, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
        except TimeoutError:
            logger.error("RPA: Timeout al cargar la URL inicial.")
            browser.close()
            return []

        # 2. Navegación por Clics a la Bandeja de Recibidos
        try:
            # Si no estamos en la vista de búsqueda, navegamos mediante la interfaz
            if "Document/Received" not in page.url:
                logger.info("RPA: Navegando hacia la bandeja de recibidos por clics de menú...")
                page.goto(f"{BASE_DIAN_URL}/Document/Received", wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
        except Exception as e:
            logger.warning(f"RPA: Error en tránsito de menú: {str(e)}")

        total_cufes = len(lista_cufes)
        
        # 3. Bucle de Búsqueda y Clics
        for idx, cufe in enumerate(lista_cufes):
            try:
                # Ubicar el campo "Código único"
                campo_codigo = page.locator('input[placeholder*="Código único"], #DocumentKey, input[name*="DocumentKey"]').first
                campo_codigo.click()
                campo_codigo.fill("")
                
                # Digitar el CUFE simulando pulsaciones humanas
                page.keyboard.type(cufe, delay=random.randint(20, 50))
                page.wait_for_timeout(500)
                
                # Hacer clic en el botón verde "Buscar"
                boton_buscar = page.locator('button:has-text("Buscar"), .btn-success').first
                boton_buscar.click()
                
                # Pausa estratégica para dar tiempo a la validación de Cloudflare Turnstile y carga AJAX
                page.wait_for_timeout(random.uniform(4000, 6000))
                
                # Ubicar la primera fila de la tabla de resultados
                fila_resultado = page.locator('tbody tr').first
                
                # Verificar si la fila contiene el ícono de descarga (flecha ⬇)
                icono_descarga = fila_resultado.locator('td').first.locator('a, i, button, span').first
                
                if icono_descarga.is_visible():
                    # Escuchar el evento de descarga que dispara la interfaz al hacer clic
                    with page.expect_download(timeout=35000) as download_info:
                        icono_descarga.click()
                        
                    download = download_info.value
                    ruta_temporal = download.path()
                    
                    with open(ruta_temporal, "rb") as f:
                        bytes_zip = f.read()
                        
                    if len(bytes_zip) > 1000:
                        archivos_descargados.append({
                            "filename": f"dian_{cufe[:8]}.zip",
                            "bytes": bytes_zip
                        })
                        logger.info(f"[{idx+1}/{total_cufes}] ✅ ZIP capturado mediante clic visual para CUFE {cufe[:8]}")
                    else:
                        logger.warning(f"[{idx+1}/{total_cufes}] ⚠️ El archivo descargado no superó el umbral de tamaño.")
                else:
                    logger.warning(f"[{idx+1}/{total_cufes}] ⚠️ No se detectó el ícono de descarga en la tabla de resultados.")
                    
            except Exception as e:
                logger.error(f"[{idx+1}/{total_cufes}] ❌ Error en secuencia de clics: {str(e)}")
            
            # Pausa de enfriamiento entre consultas para no saturar el servidor
            page.wait_for_timeout(random.uniform(3000, 5000))
            
        logger.info("RPA: Proceso finalizado. Cerrando instancia de Chromium.")
        browser.close()
        
    return archivos_descargados