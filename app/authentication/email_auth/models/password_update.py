from pydantic import BaseModel, Field


class PasswordUpdateRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)
