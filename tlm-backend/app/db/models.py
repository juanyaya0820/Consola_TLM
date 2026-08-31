# ===============================================================================
# ARCHIVO: app/db/models.py
# MODELADO DE DATOS ORM COMPLETO: GOBERNANZA, ETL FISCAL Y CEREBRO CONTABLE
# ===============================================================================
import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Table
from sqlalchemy.orm import relationship
from app.db.session import Base

# -------------------------------------------------------------------------------
# 1. TABLA ASOCIATIVA MULTI-TENANT (JUNCTION TABLE)
# -------------------------------------------------------------------------------
# Asocia usuarios con empresas autorizadas para aislamiento lógico en PostgreSQL
usuario_empresa = Table(
    'usuario_empresa',
    Base.metadata,
    Column('id_usuario', Integer, ForeignKey('usuarios.id_usuario', ondelete="CASCADE"), primary_key=True),
    Column('id_empresa', Integer, ForeignKey('empresas.id_empresa', ondelete="CASCADE"), primary_key=True)
)

# -------------------------------------------------------------------------------
# 2. ENTIDAD: USUARIOS (Analistas, Auditores y Administradores)
# -------------------------------------------------------------------------------
class Usuario(Base):
    __tablename__ = "usuarios"

    id_usuario = Column(Integer, primary_key=True, index=True)
    nombre_completo = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    rol = Column(String, default="Analista")  # "Administrador" o "Analista"
    activo = Column(Boolean, default=False)
    fecha_creacion = Column(DateTime, default=datetime.datetime.utcnow)

    # Relación Many-to-Many con Empresa mediante la tabla asociativa
    empresas_asociadas = relationship(
        "Empresa",
        secondary=usuario_empresa,
        back_populates="usuarios_autorizados",
        lazy="joined"  # Carga los permisos inmediatamente en el login
    )

# -------------------------------------------------------------------------------
# 3. ENTIDAD: EMPRESAS (Clientes / Entidades Auditables)
# -------------------------------------------------------------------------------
class Empresa(Base):
    __tablename__ = "empresas"

    id_empresa = Column(Integer, primary_key=True, index=True)
    nombre_comercial = Column(String, nullable=False)
    nit = Column(String, unique=True, index=True, nullable=False)
    software_erp = Column(String, default="SIIGO_NUBE")
    software_destino = Column(String, default="SIIGO_NUBE")
    logo_url = Column(String, nullable=True)
    fecha_creacion = Column(DateTime, default=datetime.datetime.utcnow)

    # Relaciones inversas y borrado en cascada
    usuarios_autorizados = relationship(
        "Usuario",
        secondary=usuario_empresa,
        back_populates="empresas_asociadas"
    )
    facturas = relationship("Factura", back_populates="empresa", cascade="all, delete-orphan")
    cuentas_puc = relationship("PUCModel", back_populates="empresa", cascade="all, delete-orphan")
    saldos_balance = relationship("BalancePruebaModel", back_populates="empresa", cascade="all, delete-orphan")

# -------------------------------------------------------------------------------
# 4. ENTIDAD: FACTURAS (Motor ETL, Auditoría Fiscal F350 y Soportes XML/PDF)
# -------------------------------------------------------------------------------
class Factura(Base):
    __tablename__ = "facturas"

    id_factura = Column(Integer, primary_key=True, index=True)
    id_empresa = Column(Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), nullable=False, index=True)
    
    factura_num = Column(String, index=True, nullable=False)
    fecha = Column(String, index=True, nullable=True)  # YYYY-MM-DD
    proveedor = Column(String, index=True, nullable=True)
    nit_tercero = Column(String, index=True, nullable=True)
    descripcion_item = Column(Text, nullable=True)
    
    cantidad = Column(Float, default=1.0)
    valor_unitario = Column(Float, default=0.0)
    subtotal = Column(Float, default=0.0)
    iva = Column(Float, default=0.0)
    retencion_porc = Column(Float, default=0.0)
    retencion_valor = Column(Float, default=0.0)
    
    forma_pago = Column(String, default="Contado")  # "Contado" o "Crédito"
    cuenta_gasto = Column(String, default="51953001", index=True)
    tipo_comprobante = Column(String, default="COMPRAS")  # "COMPRAS" o "VENTAS"
    pdf_b64 = Column(Text, nullable=True)
    fecha_cargue = Column(DateTime, default=datetime.datetime.utcnow)

    empresa = relationship("Empresa", back_populates="facturas")

# -------------------------------------------------------------------------------
# 5. ENTIDAD: CEREBRO CONTABLE - CATÁLOGO DE CUENTAS (PUC)
# -------------------------------------------------------------------------------
class PUCModel(Base):
    __tablename__ = "puc_cuentas"

    id_puc = Column(Integer, primary_key=True, index=True)
    id_empresa = Column(Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), nullable=False, index=True)
    codigo_cuenta = Column(String, index=True, nullable=False)
    nombre_cuenta = Column(String, nullable=False)
    nivel = Column(Integer, default=5)
    permite_movimiento = Column(Boolean, default=True)

    empresa = relationship("Empresa", back_populates="cuentas_puc")

# -------------------------------------------------------------------------------
# 6. ENTIDAD: CEREBRO CONTABLE - BALANCE DE PRUEBA HISTÓRICO
# -------------------------------------------------------------------------------
class BalancePruebaModel(Base):
    __tablename__ = "balance_prueba"

    id_balance = Column(Integer, primary_key=True, index=True)
    id_empresa = Column(Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), nullable=False, index=True)
    codigo_cuenta = Column(String, index=True, nullable=False)
    nombre_cuenta = Column(String, nullable=False)
    saldo_inicial = Column(Float, default=0.0)
    debitos = Column(Float, default=0.0)
    creditos = Column(Float, default=0.0)
    saldo_final = Column(Float, default=0.0)

    empresa = relationship("Empresa", back_populates="saldos_balance")