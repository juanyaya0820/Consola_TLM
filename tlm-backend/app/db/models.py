from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Table
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

# Instancia base de SQLAlchemy ORM
Base = declarative_base()

# ===============================================================================
# TABLA PUENTE: ROW-LEVEL SECURITY (RLS)
# Define la relación M:M entre Usuarios (Analistas) y Empresas (Clientes)
# ===============================================================================
usuario_empresa_asoc = Table(
    "usuario_empresa",
    Base.metadata,
    Column("id_usuario", Integer, ForeignKey("usuarios.id_usuario", ondelete="CASCADE"), primary_key=True),
    Column("id_empresa", Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), primary_key=True)
)

# ===============================================================================
# DIMENSIÓN: EMPRESAS (CATÁLOGO DE CLIENTES Y CONFIGURACIÓN ERP)
# ===============================================================================
class Empresa(Base):
    __tablename__ = "empresas"

    id_empresa = Column(Integer, primary_key=True, index=True)
    nombre_comercial = Column(String(255), nullable=False)
    nit = Column(String(50), unique=True, index=True, nullable=False)
    software_erp = Column(String(100), default="SIIGO_NUBE")
    software_destino = Column(String(100), default="SIIGO_NUBE")
    logo_url = Column(String(500), nullable=True)
    fecha_registro = Column(DateTime, default=datetime.utcnow)

    # Relaciones relacionales con borrado en cascada
    facturas = relationship("Factura", back_populates="empresa", cascade="all, delete-orphan")
    soportes_pdf = relationship("SoportePDF", back_populates="empresa", cascade="all, delete-orphan")
    cuentas_puc = relationship("CuentaPUC", back_populates="empresa", cascade="all, delete-orphan")
    saldos_balance = relationship("BalanceTercero", back_populates="empresa", cascade="all, delete-orphan")

# ===============================================================================
# DIMENSIÓN: USUARIOS (CONTROL DE AUTENTICACIÓN Y ROLES)
# ===============================================================================
class Usuario(Base):
    __tablename__ = "usuarios"

    id_usuario = Column(Integer, primary_key=True, index=True)
    nombre_completo = Column(String(255), nullable=False)
    email = Column(String(150), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    rol = Column(String(50), default="Analista")
    activo = Column(Boolean, default=False)  # Inactivo por defecto para requerir aprobación
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

    # Vinculación M:M con la tabla puente para filtrado de empresas asignadas
    empresas_asignadas = relationship("Empresa", secondary=usuario_empresa_asoc, backref="analistas_asignados")

# ===============================================================================
# TABLA DE HECHOS (FACT TABLE): FACTURACIÓN UBL 2.1
# ===============================================================================
class Factura(Base):
    __tablename__ = "facturas"

    id_factura = Column(Integer, primary_key=True, index=True)
    id_empresa = Column(Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), nullable=False, index=True)
    factura_num = Column(String(100), nullable=False, index=True)
    fecha = Column(String(20), nullable=False, index=True)
    fecha_vencimiento = Column(String(20), nullable=True)
    cufe_hash = Column(String(255), nullable=False, index=True)
    nit_tercero = Column(String(50), nullable=False, index=True)
    proveedor = Column(String(255), nullable=False)
    forma_pago = Column(String(50), default="Contado")
    descripcion_item = Column(String(500), nullable=True)
    cantidad = Column(Float, default=1.0)
    valor_unitario = Column(Float, default=0.0)
    subtotal = Column(Float, default=0.0)
    iva = Column(Float, default=0.0)
    cuenta_gasto = Column(String(20), default="51953001", index=True)
    retencion_porc = Column(Float, default=0.0)
    casilla_350 = Column(Integer, nullable=True)
    estado_revision = Column(String(50), default="PENDIENTE")
    
    empresa = relationship("Empresa", back_populates="facturas")

# ===============================================================================
# DIMENSIONES AUXILIARES: PDFS, PLAN DE CUENTAS (PUC) Y BALANCES
# ===============================================================================
class SoportePDF(Base):
    __tablename__ = "soportes_pdf"

    id_soporte = Column(Integer, primary_key=True, index=True)
    id_empresa = Column(Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), nullable=False, index=True)
    factura_num = Column(String(100), nullable=False, index=True)
    pdf_b64 = Column(Text, nullable=False)

    empresa = relationship("Empresa", back_populates="soportes_pdf")

class CuentaPUC(Base):
    __tablename__ = "cuentas_puc"

    id_cuenta = Column(Integer, primary_key=True, index=True)
    id_empresa = Column(Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), nullable=False, index=True)
    cuenta = Column(String(20), nullable=False, index=True)
    nombre = Column(String(255), nullable=False)

    empresa = relationship("Empresa", back_populates="cuentas_puc")

class BalanceTercero(Base):
    __tablename__ = "balance_terceros"

    id_balance = Column(Integer, primary_key=True, index=True)
    id_empresa = Column(Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), nullable=False, index=True)
    cuenta_contable = Column(String(20), nullable=False, index=True)
    nombre_cuenta = Column(String(255), nullable=True)
    nit_tercero = Column(String(50), nullable=False, index=True)
    nombre_tercero = Column(String(255), nullable=True)
    saldo_inicial = Column(Float, default=0.0)
    debitos = Column(Float, default=0.0)
    creditos = Column(Float, default=0.0)
    saldo_final = Column(Float, default=0.0)

    empresa = relationship("Empresa", back_populates="saldos_balance")