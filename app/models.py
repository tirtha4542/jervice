from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domain.state_machines import (
    GuestSessionStatus,
    OrderItemStatus,
    OrderStatus,
    PaymentStatus,
    ReservationStatus,
    TableSessionStatus,
    TableStatus,
)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brands: Mapped[list["Brand"]] = relationship(back_populates="organization")


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    organization: Mapped[Organization] = relationship(back_populates="brands")
    branches: Mapped[list["Branch"]] = relationship(back_populates="brand")


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    brand: Mapped[Brand] = relationship(back_populates="branches")
    departments: Mapped[list["Department"]] = relationship(back_populates="branch")
    tables: Mapped[list["DiningTable"]] = relationship(back_populates="branch")


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    station_type: Mapped[str] = mapped_column(String(64), default="floor")
    branch: Mapped[Branch] = relationship(back_populates="departments")
    employees: Mapped[list["Employee"]] = relationship(back_populates="department")


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[Department] = relationship(back_populates="employees")
    role: Mapped[Role] = relationship()


class DiningTable(Base):
    __tablename__ = "dining_tables"
    __table_args__ = (UniqueConstraint("branch_id", "code", name="uq_branch_table_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    qr_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=TableStatus.AVAILABLE.value)
    capacity: Mapped[int] = mapped_column(Integer, default=4)
    branch: Mapped[Branch] = relationship(back_populates="tables")
    sessions: Mapped[list["TableSession"]] = relationship(back_populates="table")


class TableSession(Base):
    __tablename__ = "table_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("dining_tables.id"), nullable=False)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=TableSessionStatus.OPEN.value)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    table: Mapped[DiningTable] = relationship(back_populates="sessions")
    guests: Mapped[list["GuestSession"]] = relationship(back_populates="table_session")
    orders: Mapped[list["Order"]] = relationship(back_populates="table_session")


class GuestSession(Base):
    __tablename__ = "guest_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_session_id: Mapped[int] = mapped_column(ForeignKey("table_sessions.id"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), default="Guest")
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    otp_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default=GuestSessionStatus.AUTHENTICATING.value)
    dietary_preferences: Mapped[list[str]] = mapped_column(JSON, default=list)
    table_session: Mapped[TableSession] = relationship(back_populates="guests")


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    station: Mapped[str] = mapped_column(String(64), default="kitchen")
    allergens: Mapped[list[str]] = mapped_column(JSON, default=list)
    recipe_components: Mapped[list["RecipeComponent"]] = relationship(back_populates="menu_item")


class InventorySku(Base):
    __tablename__ = "inventory_skus"
    __table_args__ = (UniqueConstraint("branch_id", "sku_code", name="uq_branch_sku"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), nullable=False)
    sku_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    on_hand: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=0)
    par_level: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=0)
    unit: Mapped[str] = mapped_column(String(32), default="unit")


class RecipeComponent(Base):
    __tablename__ = "recipe_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"), nullable=False)
    sku_id: Mapped[int] = mapped_column(ForeignKey("inventory_skus.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    menu_item: Mapped[MenuItem] = relationship(back_populates="recipe_components")
    sku: Mapped[InventorySku] = relationship()


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_session_id: Mapped[int] = mapped_column(ForeignKey("table_sessions.id"), nullable=False)
    guest_session_id: Mapped[int | None] = mapped_column(ForeignKey("guest_sessions.id"), nullable=True)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default=OrderStatus.DRAFT.value)
    table_session: Mapped[TableSession] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    modifiers: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default=OrderItemStatus.QUEUED.value)
    station: Mapped[str] = mapped_column(String(64), default="kitchen")
    order: Mapped[Order] = relationship(back_populates="items")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    guest_session_id: Mapped[int | None] = mapped_column(ForeignKey("guest_sessions.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=PaymentStatus.UNPAID.value)
    method: Mapped[str] = mapped_column(String(32), default="card")
    order: Mapped[Order] = relationship(back_populates="payments")


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), nullable=False)
    table_id: Mapped[int | None] = mapped_column(ForeignKey("dining_tables.id"), nullable=True)
    guest_name: Mapped[str] = mapped_column(String(255), nullable=False)
    party_size: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(32), default=ReservationStatus.REQUESTED.value)
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    actor_role: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
