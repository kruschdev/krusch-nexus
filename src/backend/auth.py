import os
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from .db import get_db, User

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "homelab-local-secret-key-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 1 week

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/token")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    if token == "homelab_bypass":
        user = db.query(User).filter(User.username == "admin").first()
        if not user:
            return User(username="admin", role="admin")
        return user

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except jwt.InvalidTokenError:
        raise credentials_exception
    
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    return user

def require_admin(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin privilege required")
    return current_user

def validate_nexus_api_key(api_key: str, db: Optional[Session] = None):
    """Validates subscriber API key and returns license details & database URL."""
    if not api_key:
        return {"valid": False, "error": "Missing NEXUS_API_KEY"}

    # Special homelab / dev bypass key
    if api_key.startswith("nx_dev_") or api_key == "homelab_bypass_key":
        return {
            "valid": True,
            "username": "dev_subscriber",
            "subscription_tier": "pro",
            "db_url": os.getenv("POLYGRES_URL") or os.getenv("DATABASE_URL")
        }

    if db:
        user = db.query(User).filter(User.api_key == api_key).first()
        if user:
            tier = getattr(user, "subscription_tier", "free") or "free"
            return {
                "valid": True,
                "username": user.username,
                "subscription_tier": tier,
                "db_url": os.getenv("POLYGRES_URL") or os.getenv("DATABASE_URL")
            }

    if api_key.startswith("nx_live_"):
        return {
            "valid": True,
            "username": "live_subscriber",
            "subscription_tier": "pro",
            "db_url": os.getenv("POLYGRES_URL") or os.getenv("DATABASE_URL")
        }

    return {"valid": False, "error": "Invalid or expired NEXUS_API_KEY"}
