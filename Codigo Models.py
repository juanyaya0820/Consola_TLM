from sqlalchemy import Table, Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.db.session import Base

# Tabla intermedia para permisos de acceso (Muchos a Muchos)
usuario_empresa = Table(
    "usuario_empresa",
    Base.metadata,
    Column("id_usuario", Integer, ForeignKey("usuarios.id_usuario", ondelete="CASCADE"), primary_key=True),
    Column("id_empresa", Integer, ForeignKey("empresas.id_empresa", ondelete="CASCADE"), primary_key=True)
)

class Usuario(Base):
    __tablename__ = "usuarios"

    id_usuario = Column(Integer, primary_key=True, index=True)
    nombre_completo = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    rol = Column(String(50), default="Analista")
    activo = Column(Boolean, default=False)  # Control de Seguridad: Requiere aprobación del Admin
    created_at = Column(DateTime)

    empresas_asignadas = relationship("Empresa", secondary=usuario_empresa, back_populates="usuarios_gestores")


class Empresa(Base):
    __tablename__ = "empresas"

    id_empresa = Column(Integer, primary_key=True, index=True)
    nombre_comercial = Column(String(255), nullable=False)
    nit = Column(String(50), unique=True, index=True, nullable=False)
    software_erp = Column(String(50), default="SIIGO_NUBE")
    created_at = Column(DateTime)

    facturas = relationship("Factura", back_populates="empresa", cascade="all, delete-orphan")
    usuarios_gestores = relationship("Usuario", secondary=usuario_empresa, back_populates="empresas_asignadas")


class Factura(Base):
    __tablename__ = "facturas"

    id_factura = Column(Integer, primary_key=True, index=True)
    id_empresa = Column(Integer, ForeignKey("empresas.id_empresa"), nullable=False)
    
    factura_num = Column(String(100), index=True)
    fecha = Column(String(20))
    archivo_original = Column(String(255))
    cufe_hash = Column(String(255), unique=True, index=True)
    flujo_dian = Column(String(50))
    
    nit_tercero = Column(String(50), index=True)
    proveedor = Column(String(255))
    
    descripcion_item = Column(Text)
    cantidad = Column(Float, default=1.0)
    valor_unitario = Column(Float, default=0.0)
    subtotal = Column(Float, default=0.0)
    iva = Column(Float, default=0.0)
    
    forma_pago = Column(String(50), default="Contado")
    medio_pago = Column(String(100), default="10 - Efectivo")
    fecha_vencimiento = Column(String(20))
    
    cuenta_gasto = Column(String(50), default="51953001")
    casilla_350 = Column(Integer, default=64)
    retencion_porc = Column(Float, default=0.0)
    reteica_tarifa = Column(Float, default=0.0)
    reteiva_porc = Column(Float, default=0.0)
    estado_revision = Column(String(50), default="PENDIENTE")

    empresa = relationship("Empresa", back_populates="facturas")


class SoportePDF(Base):
    __tablename__ = "soportes_pdf"
    factura_num = Column(String(100), primary_key=True, index=True, nullable=False)
    pdf_b64 = Column(Text, nullable=False)