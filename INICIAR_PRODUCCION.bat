@echo off
title TLM - Servidor de Produccion Activo
color 0B

echo ===================================================================
echo      SISTEMA DE GESTION FISCAL TLM - INICIO DE PRODUCCION
echo ===================================================================
echo.
echo [1/2] Iniciando el motor de Base de Datos y Backend (FastAPI)...

:: %~dp0 captura la ruta exacta donde esta el .bat (evita errores con tildes)
cd /d "%~dp0\tlm-backend"

:: START abre una nueva ventana de consola independiente (cmd /k la mantiene abierta)
start "TLM - Backend Produccion" cmd /k ".\.venv\Scripts\activate && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"

:: TIMEOUT da un margen de 3 segundos para asegurar que el backend levante antes del tunel
timeout /t 3 >nul

echo [2/2] Levantando tunel seguro (Ngrok) para el equipo...

:: Inicia Ngrok apuntando a tu dominio estatico y al puerto 8000
start "TLM - Enlace Ngrok" cmd /k "ngrok http --domain=unless-playback-rerun.ngrok-free.dev 8000"

echo.
echo ===================================================================
echo  [EXITO] EL ENTORNO DE PRODUCCION ESTA EN LINEA Y OPERATIVO.
echo  El equipo ya puede acceder al dashboard financiero.
echo ===================================================================
echo.
echo Puedes presionar cualquier tecla para cerrar esta ventana principal.
echo (Las ventanas del servidor seguiran operando en segundo plano).
pause >nul