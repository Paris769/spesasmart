from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.core.security import hash_password, verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterBody(BaseModel):
    email: EmailStr
    password: str
    full_name: str = ""


class LoginBody(BaseModel):
    email: EmailStr
    password: str


@router.post("/register", status_code=201)
async def register(body: RegisterBody, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": body.email},
    )
    if existing.first():
        raise HTTPException(status_code=400, detail="Email già registrata")

    result = await db.execute(
        text("""
            INSERT INTO users (email, password_hash, full_name, gdpr_consent_at)
            VALUES (:email, :pw, :name, NOW())
            RETURNING id, email, full_name, subscription
        """),
        {"email": body.email, "pw": hash_password(body.password), "name": body.full_name},
    )
    await db.commit()
    user = dict(result.mappings().first())
    return {"user": user, "access_token": create_access_token(str(user["id"]))}


@router.post("/login")
async def login(body: LoginBody, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT id, email, password_hash, full_name, subscription FROM users WHERE email = :email AND deleted_at IS NULL"),
        {"email": body.email},
    )
    user = result.mappings().first()
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenziali non valide")

    u = dict(user)
    u.pop("password_hash")
    return {"user": u, "access_token": create_access_token(str(u["id"]))}
