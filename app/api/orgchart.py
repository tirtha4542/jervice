"""Tenant hierarchy endpoints: Organization → Brand → Branch → Department → Employee."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models import Branch, Brand, Department, Employee, Organization, Role

router = APIRouter(prefix="/api/v1/org", tags=["org"])


class OrganizationOut(BaseModel):
    id: int
    name: str
    brands: list[dict] = []


class BranchOut(BaseModel):
    id: int
    brand_id: int
    name: str
    timezone: str
    departments: list[dict] = []


class EmployeeOut(BaseModel):
    id: int
    department_id: int
    role_id: int
    full_name: str
    department_name: str | None = None
    role_name: str | None = None
    permissions: list[str] = Field(default_factory=list)
    branch_id: int | None = None


class RoleOut(BaseModel):
    id: int
    name: str
    permissions: list[str]


@router.get("/hierarchy")
async def get_hierarchy(db: AsyncSession = Depends(get_db)) -> dict:
    """Full tenant tree with departments nested under each branch."""
    orgs = (
        await db.execute(
            select(Organization)
            .options(
                selectinload(Organization.brands).selectinload(Brand.branches).selectinload(Branch.departments)
            )
        )
    ).scalars().unique().all()
    return {
        "organizations": [
            {
                "id": org.id,
                "name": org.name,
                "brands": [
                    {
                        "id": brand.id,
                        "name": brand.name,
                        "branches": [
                            {
                                "id": branch.id,
                                "name": branch.name,
                                "timezone": branch.timezone,
                                "departments": [
                                    {"id": d.id, "name": d.name, "station_type": d.station_type}
                                    for d in branch.departments
                                ],
                            }
                            for branch in brand.branches
                        ],
                    }
                    for brand in org.brands
                ],
            }
            for org in orgs
        ]
    }


@router.get("/branches", response_model=list[BranchOut])
async def list_branches(
    brand_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(Branch).options(selectinload(Branch.departments))
    if brand_id is not None:
        stmt = stmt.where(Branch.brand_id == brand_id)
    rows = (await db.execute(stmt.order_by(Branch.id.asc()))).scalars().unique().all()
    return [
        {
            "id": row.id,
            "brand_id": row.brand_id,
            "name": row.name,
            "timezone": row.timezone,
            "departments": [
                {"id": d.id, "name": d.name, "station_type": d.station_type}
                for d in row.departments
            ],
        }
        for row in rows
    ]


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(db: AsyncSession = Depends(get_db)) -> list[RoleOut]:
    rows = (await db.execute(select(Role).order_by(Role.id.asc()))).scalars().all()
    return rows


@router.get("/employees", response_model=list[EmployeeOut])
async def list_employees(
    branch_id: int | None = Query(default=None, description="Scope to one branch via its departments"),
    role_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = (
        select(Employee)
        .join(Department, Employee.department_id == Department.id)
        .options(selectinload(Employee.role), selectinload(Employee.department))
    )
    if branch_id is not None:
        stmt = stmt.where(Department.branch_id == branch_id)
    if role_id is not None:
        stmt = stmt.where(Employee.role_id == role_id)
    rows = (await db.execute(stmt.order_by(Employee.id.asc()))).scalars().unique().all()
    return [
        {
            "id": row.id,
            "department_id": row.department_id,
            "role_id": row.role_id,
            "full_name": row.full_name,
            "department_name": row.department.name if row.department else None,
            "role_name": row.role.name if row.role else None,
            "permissions": row.role.permissions if row.role else [],
            "branch_id": row.department.branch_id if row.department else None,
        }
        for row in rows
    ]
