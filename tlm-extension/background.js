// Escucha peticiones enviadas directamente desde el sitio web de la Consola TLM
chrome.runtime.onMessageExternal.addListener((request, sender, sendResponse) => {
  if (request.action === "EXTRAER_DIAN") {
    ejecutarExtraccionCliente(request.idEmpresa)
      .then(res => sendResponse({ status: "OK", nuevos: res }))
      .catch(err => sendResponse({ status: "ERROR", detail: err.message }));
    return true; // Mantiene el canal abierto para respuesta asíncrona
  }
});

async function ejecutarExtraccionCliente(idEmpresa) {
  // 1. Obtener la pestaña activa donde el usuario tiene iniciada sesión en la DIAN
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url.includes("catalogo-vpfe.dian.gov.co")) {
    throw new Error("Debes tener abierta la pestaña de la DIAN en primer plano.");
  }

  // 2. Inyectar script de lectura sobre el DOM de la DIAN
  const [{ result: urls }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => Array.from(document.querySelectorAll('a[href*="/Document/Download?trackId="]')).map(a => a.href)
  });

  if (!urls || urls.length === 0) throw new Error("No se encontraron facturas en la tabla actual de la DIAN.");

  // 3. Descarga mediante Fetch heredando cookies activas y envío a FastAPI
  const formData = new FormData();
  formData.append("id_empresa", idEmpresa);

  let enviadas = 0;
  for (let i = 0; i < urls.length; i++) {
    const res = await fetch(urls[i]);
    const blob = await res.blob();
    if (blob.size > 1000) {
      formData.append("archivos", blob, `dian_ext_${i}_${Date.now()}.zip`);
      enviadas++;
    }
  }

  const backendRes = await fetch("http://127.0.0.1:8000/api/v1/facturas/upload", {
    method: "POST",
    body: formData
  });

  if (!backendRes.ok) throw new Error("Fallo de comunicación con la API de PostgreSQL.");
  return enviadas;
}