from fastapi import APIRouter
from app.api.v1.endpoints import auth, empresas, facturas, conciliacion, contabilidad

api_router = APIRouter()

# Registro unificado de módulos de la API
api_router.include_router(auth.router, prefix="/auth", tags=["Autenticación"])
api_router.include_router(empresas.router, prefix="/empresas", tags=["Empresas"])
api_router.include_router(facturas.router, prefix="/facturas", tags=["Facturas"])
api_router.include_router(conciliacion.router, prefix="/conciliacion", tags=["Conciliación"])
api_router.include_router(contabilidad.router, prefix="/contabilidad", tags=["Cerebro Contable"])