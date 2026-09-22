"""Menu, inventory, and recipe-BOM endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import record_audit
from app.core.database import get_db
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


class MenuItemCreate(BaseModel):
    branch_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=255, examples=["Miso Glazed Cod"])
    description: str = ""
    price: Decimal = Field(gt=0, examples=["18.50"])
    station: str = Field(default="kitchen", max_length=64)
    allergens: list[str] = Field(default_factory=list)
    recipe: list[dict[str, Any]] = Field(
        default_factory=list,
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
) -> list[dict]:
    """Menu items for a branch, each with its recipe BOM and per-portion stock."""
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
) -> dict:
    """Create a menu item and, optionally, its recipe components (BOM)."""
    await _require_branch(db, payload.branch_id)

    sku_ids = [line.get("sku_id") for line in payload.recipe if line.get("sku_id")]
    skus: dict[int, InventorySku] = {}
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
        quantity = line.get("quantity")
        if quantity is None:
            raise HTTPException(status_code=400, detail="each recipe line needs a quantity")
        db.add(
            RecipeComponent(
                menu_item_id=item.id,
                sku_id=int(line["sku_id"]),
                quantity=Decimal(str(quantity)),
            )
        )

    await record_audit(
        db,
        actor_role="manager",
        event_type="menu_item.created",
        payload={"menu_item_id": item.id, "name": item.name},
        branch_id=payload.branch_id,
    )
    await db.commit()
    await db.refresh(item)

    return {
        "id": item.id,
        "branch_id": item.branch_id,
        "name": item.name,
        "description": item.description,
        "price": item.price,
        "station": item.station,
        "allergens": item.allergens,
        "recipe": [],
    }


@router.get("/inventory", response_model=list[InventorySkuOut])
async def list_inventory(
    branch_id: int = Query(ge=1),
    below_par_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Inventory SKUs for a branch with par-level variance flags."""
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
) -> dict:
    """Set an absolute on-hand quantity (goods-in, waste, stock count)."""
    sku = await db.get(InventorySku, sku_id)
    if sku is None:
        raise HTTPException(status_code=404, detail=f"SKU {sku_id} not found")
    sku.on_hand = payload.on_hand
    await record_audit(
        db,
        actor_role="manager",
        event_type="inventory.adjusted",
        payload={"sku_id": sku.id, "sku_code": sku.sku_code, "on_hand": str(payload.on_hand)},
        branch_id=sku.branch_id,
    )
    await db.commit()
    await db.refresh(sku)
    return _sku_out(sku)


@router.get("/recipes/{menu_item_id}", response_model=list[RecipeComponentOut])
async def get_recipe(
    menu_item_id: int,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Recipe BOM for one menu item, annotated with live stock coverage."""
    exists = await db.scalar(select(MenuItem.id).where(MenuItem.id == menu_item_id))
    if exists is None:
        raise HTTPException(status_code=404, detail=f"Menu item {menu_item_id} not found")
    rows = (
        await db.execute(
            select(RecipeComponent)
            .options(selectinload(RecipeComponent.sku))
            .where(RecipeComponent.menu_item_id == menu_item_id)
        )
    ).scalars().unique().all()
    return [_recipe_out(component, component.sku) for component in rows]
