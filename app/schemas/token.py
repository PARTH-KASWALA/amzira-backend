from pydantic import BaseModel
from typing import Optional


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    user: dict


class TokenPayload(BaseModel):
    sub: Optional[int] = None
    exp: Optional[int] = None
    type: Optional[str] = None