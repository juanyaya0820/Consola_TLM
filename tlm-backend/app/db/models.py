# ===============================================================================
# ARCHIVO: app/db/models.py
# MODELO DE DATOS UNIFICADO - COMPATIBILIDAD 100% CON MOTOR ETL Y AUDITORÍA
# ===============================================================================
import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Table
from sqlalchemy.orm import relationship
from app.db.session import Base

# -------------------------------------------------------------------------------
# 1. TABLA ASOCIATIVA MULTI-TENANT (JUNCTION TABLE)
# -------------------------------------------------------------------------------
usuario_empresa = Table(
    'usuario_empresa',
    Base.metadata,
    Column('id_usuario', Integer, ForeignKey('usuarios.id_usuario', ondelete="CASCADE"), primary_key=True),
    Column('id_empresa', Integer, ForeignKey('empresas.id_empresa', ondelete="CASCADE"), primary_key=True)
)

# -------------------------------------------------------------------------------
# 2. ENTIDAD: USUARIO
# -------------------------------------------------------------------------------
class Usuario(Base):
    __tablename__ = "usuarios"

    id_usuario = Column(Integer, primary_key=True, index=True)
    nombre_completo = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    rol = Column(String, default="Analista")
    activo = Column(Boolean, default=False)

    empresas_asociadas = relationship(
        "Empresa",
        secondary=usuario_empresa,
        back_populates="usuarios_autorizados",
        lazy="joined"
    )

# -------------------------------------------------------------------------------
# 3. ENTIDAD: EMPRESA
# -------------------------------------------------------------------------------
class Empresa(Base):
    __tablename__ = "empresas"

    id_empresa = Column(Integer, primary_key=True, index=True)
    nombre_comercial = Column(String, nullable=False)
    nit = Column(String, unique=True, index=True, nullable=False)
    software_erp = Column(String, default="SIIGO_NUBE")
    software_destino = Column(String, default="SIIGO_NUBE")
    logo_url = Column(String, nullable=True)

    usuarios_autorizados = relationship(
        "Usuario",
        secondary=usuario_empresa,
        back_populates="empresas_asociadas"
    )
    facturas = relationship("Factura", back_populates="empresa", cascade="all, delete-orphan")
    soportes_pdf = relationship("SoportePDF", back_populates="empresa", cascade="all, delete-orphan")
    cuentas_puc = relationship("CuentaPUC", back_populates="empresa", cascade="all, delete-orphan")
    saldos_balance = relationship("BalanceTercero", back_populates="empresa", cascade="all, delete-orphan")

# -------------------------------------------------------------------------------
# 4. ENTIDAD: FACTURA (MOTOR ETL Y AUDITORÍA UBL 2.1)
# -------------------------------------------------------------------------------
class Factura(Base):
    __tablename__ = "facturas"

    id_factura = Column(Integer, primary_key=True, index=True)
    id_empresa = Column(Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), nullable=False, index=True)
    
    factura_num = Column(String, index=True, nullable=False)
    fecha = Column(String, index=True, nullable=True)
    fecha_vencimiento = Column(String, nullable=True)
    proveedor = Column(String, index=True, nullable=True)
    nit_tercero = Column(String, index=True, nullable=True)
    descripcion_item = Column(Text, nullable=True)
    
    cantidad = Column(Float, default=1.0)
    valor_unitario = Column(Float, default=0.0)
    subtotal = Column(Float, default=0.0)
    iva = Column(Float, default=0.0)
    retencion_porc = Column(Float, default=0.0)
    retencion_valor = Column(Float, default=0.0)
    casilla_350 = Column(Integer, nullable=True)
    
    forma_pago = Column(String, default="Contado")
    cuenta_gasto = Column(String, default="51953001", index=True)
    tipo_comprobante = Column(String, default="COMPRAS")
    cufe_hash = Column(String, index=True, nullable=True)
    estado_revision = Column(String, default="PENDIENTE")
    pdf_b64 = Column(Text, nullable=True)
    fecha_cargue = Column(DateTime, default=datetime.datetime.utcnow)

    empresa = relationship("Empresa", back_populates="facturas")

# -------------------------------------------------------------------------------
# 5. ENTIDAD: SOPORTE PDF (SOPORTES DE ORIGEN Y CONTINGENCIA)
# -------------------------------------------------------------------------------
class SoportePDF(Base):
    __tablename__ = "soportes_pdf"

    id_soporte = Column(Integer, primary_key=True, index=True)
    id_empresa = Column(Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), nullable=False, index=True)
    factura_num = Column(String, index=True, nullable=False)
    pdf_b64 = Column(Text, nullable=False)

    empresa = relationship("Empresa", back_populates="soportes_pdf")

# -------------------------------------------------------------------------------
# 6. ENTIDAD: CUENTA PUC (CEREBRO CONTABLE)
# -------------------------------------------------------------------------------
class CuentaPUC(Base):
    __tablename__ = "puc_cuentas"

    id_puc = Column(Integer, primary_key=True, index=True)
    id_empresa = Column(Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), nullable=False, index=True)
    cuenta = Column(String, index=True, nullable=False)
    nombre = Column(String, nullable=False)

    empresa = relationship("Empresa", back_populates="cuentas_puc")

# -------------------------------------------------------------------------------
# 7. ENTIDAD: BALANCE TERCEROS (BALANCE DE PRUEBA)
# -------------------------------------------------------------------------------
class BalanceTercero(Base):
    __tablename__ = "balance_terceros"

    id_balance = Column(Integer, primary_key=True, index=True)
    id_empresa = Column(Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), nullable=False, index=True)
    cuenta_contable = Column(String, index=True, nullable=True)
    nombre_cuenta = Column(String, nullable=True)
    nit_tercero = Column(String, index=True, nullable=True)
    nombre_tercero = Column(String, nullable=True)
    saldo_inicial = Column(Float, default=0.0)
    debitos = Column(Float, default=0.0)
    creditos = Column(Float, default=0.0)
    saldo_final = Column(Float, default=0.0)

    empresa = relationship("Empresa", back_populates="saldos_balance")