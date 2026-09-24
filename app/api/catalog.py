"""Menu, inventory, and recipe-BOM endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import record_audit
from app.core.auth import (
    ActorContext,
    get_actor_context,
    require_branch_access,
    require_permission,
)
from app.core.database import get_db
from app.services.operations import invalidate_operational_cache
from app.models import Branch, InventorySku, MenuItem, RecipeComponent

router = APIRouter(prefix="/api/v1", tags=["catalog"])


class RecipeComponentOut(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    sku_name: str
    quantity: Decimal
    unit: str
    on_hand: Decimal
    par_level: Decimal
    can_make_one: bool


class MenuItemOut(BaseModel):
    id: int
    branch_id: int
    name: str
    description: str
    price: Decimal
    station: str
    allergens: list[str]
    recipe: list[RecipeComponentOut] = []


class InventorySkuOut(BaseModel):
    id: int
    branch_id: int
    sku_code: str
    name: str
    on_hand: Decimal
    par_level: Decimal
    unit: str
    below_par: bool
    severity: str | None = None


class RecipeLineCreate(BaseModel):
    sku_id: int = Field(ge=1)
    quantity: Decimal = Field(gt=0, description="Positive finite BOM quantity")

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("quantity must be finite")
        return value


class MenuItemCreate(BaseModel):
    branch_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=255, examples=["Miso Glazed Cod"])
    description: str = Field(default="", max_length=2000)
    price: Decimal = Field(gt=0, examples=["18.50"])
    station: str = Field(default="kitchen", min_length=1, max_length=64)
    allergens: list[str] = Field(default_factory=list, max_length=30)
    recipe: list[RecipeLineCreate] = Field(
        default_factory=list,
        max_length=100,
        description='[{"sku_id": 1, "quantity": "0.200"}, ...]',
    )


class InventoryAdjust(BaseModel):
    on_hand: Decimal = Field(ge=0, examples=["12.500"])


def _recipe_out(component: RecipeComponent, sku: InventorySku) -> RecipeComponentOut:
    return RecipeComponentOut(
        id=component.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        sku_name=sku.name,
        quantity=component.quantity,
        unit=sku.unit,
        on_hand=sku.on_hand,
        par_level=sku.par_level,
        can_make_one=sku.on_hand >= component.quantity,
    )


def _sku_out(sku: InventorySku) -> InventorySkuOut:
    below = sku.on_hand < sku.par_level
    return InventorySkuOut(
        id=sku.id,
        branch_id=sku.branch_id,
        sku_code=sku.sku_code,
        name=sku.name,
        on_hand=sku.on_hand,
        par_level=sku.par_level,
        unit=sku.unit,
        below_par=below,
        severity="warning" if below else None,
    )


async def _require_branch(db: AsyncSession, branch_id: int) -> None:
    if await db.scalar(select(Branch.id).where(Branch.id == branch_id)) is None:
        raise HTTPException(
            status_code=400,
            detail=f"branch_id {branch_id} does not exist. Run POST /api/v1/demo/seed first.",
        )


@router.get("/menu", response_model=list[MenuItemOut])
async def list_menu(
    branch_id: int = Query(ge=1),
    station: str | None = Query(default=None, description="Filter by prep station"),
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> list[dict]:
    """Menu items for a branch, each with its recipe BOM and per-portion stock."""
    require_branch_access(actor, branch_id)
    require_permission(actor, "menu.read")
    stmt = (
        select(MenuItem)
        .options(
            selectinload(MenuItem.recipe_components).selectinload(RecipeComponent.sku)
        )
        .where(MenuItem.branch_id == branch_id)
    )
    if station:
        stmt = stmt.where(MenuItem.station == station)
    items = (await db.execute(stmt.order_by(MenuItem.name.asc()))).scalars().unique().all()
    return [
        {
            "id": item.id,
            "branch_id": item.branch_id,
            "name": item.name,
            "description": item.description,
            "price": item.price,
            "station": item.station,
            "allergens": item.allergens,
            "recipe": [_recipe_out(c, c.sku) for c in item.recipe_components],
        }
        for item in items
    ]


@router.post("/menu", response_model=MenuItemOut, status_code=201)
async def create_menu_item(
    payload: MenuItemCreate,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    """Create a menu item and, optionally, its recipe components (BOM)."""
    require_branch_access(actor, payload.branch_id)
    require_permission(actor, "menu.update")
    await _require_branch(db, payload.branch_id)

    sku_ids = [line.sku_id for line in payload.recipe]
    skus: dict[int, InventorySku] = {}
    if len(sku_ids) != len(set(sku_ids)):
        raise HTTPException(status_code=422, detail="A recipe may contain each SKU only once")
    if sku_ids:
        rows = (
            await db.execute(
                select(InventorySku).where(
                    InventorySku.id.in_(sku_ids),
                    InventorySku.branch_id == payload.branch_id,
                )
            )
        ).scalars().all()
        skus = {row.id: row for row in rows}
        missing = [sid for sid in sku_ids if sid not in skus]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"sku_id(s) {missing} not found in branch {payload.branch_id}.",
            )

    item = MenuItem(
        branch_id=payload.branch_id,
        name=payload.name,
        description=payload.description,
        price=payload.price,
        station=payload.station,
        allergens=payload.allergens,
    )
    db.add(item)
    await db.flush()

    for line in payload.recipe:
        db.add(
            RecipeComponent(
                menu_item_id=item.id,
                sku_id=line.sku_id,
                quantity=line.quantity,
            )
        )

    await record_audit(
        db,
        actor_role=actor.role,
        event_type="menu_item.created",
        payload={"menu_item_id": item.id, "name": item.name},
        branch_id=payload.branch_id,
    )
    await db.commit()
    await invalidate_operational_cache(branch_id=item.branch_id)
    await db.refresh(item)
    recipe_rows = (
        await db.execute(
            select(RecipeComponent)
            .options(selectinload(RecipeComponent.sku))
            .where(RecipeComponent.menu_item_id == item.id)
        )
    ).scalars().unique().all()

    return {
        "id": item.id,
        "branch_id": item.branch_id,
        "name": item.name,
        "description": item.description,
        "price": item.price,
        "station": item.station,
        "allergens": item.allergens,
        "recipe": [_recipe_out(component, component.sku) for component in recipe_rows],
    }


@router.get("/inventory", response_model=list[InventorySkuOut])
async def list_inventory(
    branch_id: int = Query(ge=1),
    below_par_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> list[dict]:
    """Inventory SKUs for a branch with par-level variance flags."""
    require_branch_access(actor, branch_id)
    require_permission(actor, "inventory.read")
    stmt = select(InventorySku).where(InventorySku.branch_id == branch_id)
    rows = (await db.execute(stmt.order_by(InventorySku.sku_code.asc()))).scalars().all()
    out = [_sku_out(row) for row in rows]
    if below_par_only:
        out = [row for row in out if row.below_par]
    return out


@router.patch("/inventory/{sku_id}/on-hand", response_model=InventorySkuOut)
async def adjust_inventory(
    sku_id: int,
    payload: InventoryAdjust,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    """Set an absolute on-hand quantity (goods-in, waste, stock count)."""
    sku = (
        await db.execute(
            select(InventorySku).where(InventorySku.id == sku_id).with_for_update()
        )
    ).scalar_one_or_none()
    if sku is None:
        raise HTTPException(status_code=404, detail=f"SKU {sku_id} not found")
    require_branch_access(actor, sku.branch_id)
    require_permission(actor, "inventory.update")
    sku.on_hand = payload.on_hand
    await record_audit(
        db,
        actor_role=actor.role,
        event_type="inventory.adjusted",
        payload={"sku_id": sku.id, "sku_code": sku.sku_code, "on_hand": str(payload.on_hand)},
        branch_id=sku.branch_id,
    )
    await db.commit()
    await invalidate_operational_cache(branch_id=sku.branch_id)
    await db.refresh(sku)
    return _sku_out(sku)


@router.get("/recipes/{menu_item_id}", response_model=list[RecipeComponentOut])
async def get_recipe(
    menu_item_id: int,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> list[dict]:
    """Recipe BOM for one menu item, annotated with live stock coverage."""
    menu = await db.get(MenuItem, menu_item_id)
    if menu is None:
        raise HTTPException(status_code=404, detail=f"Menu item {menu_item_id} not found")
    require_branch_access(actor, menu.branch_id)
    require_permission(actor, "menu.read")
    rows = (
        await db.execute(
            select(RecipeComponent)
            .options(selectinload(RecipeComponent.sku))
            .where(RecipeComponent.menu_item_id == menu_item_id)
        )
    ).scalars().unique().all()
    return [_recipe_out(component, component.sku) for component in rows]
