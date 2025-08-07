from datetime import datetime
from enum import Enum
from uuid import uuid4
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Index, Integer, Boolean, Enum as SQLAlchemyEnum, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base


class IconCategory(str, Enum):
    EMOJI = "emoji"
    REACTION = "reaction"
    STICKER = "sticker"
    FILE = "file"
    AVATAR = "avatar"
    SYSTEM = "system"
    OTHER = "other"


class IconType(str, Enum):
    APP = "app"
    USER = "user"


class IconMode(str, Enum):
    LIGHT = "light"
    DARK = "dark"
    UNIVERSAL = "universal"


class IconFormat(str, Enum):
    SVG = "svg"
    PNG = "png"
    WEBP = "webp"
    JPG = "jpg"
    GIF = "gif"


class Icon(Base):
    __tablename__ = "icons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(255), nullable=False)
    type = Column(SQLAlchemyEnum(IconType), nullable=False)
    user_id = Column(String, ForeignKey('users.id'), nullable=True)  # Foreign key to users table
    mode = Column(SQLAlchemyEnum(IconMode), nullable=False, default=IconMode.LIGHT)
    gcs_path = Column(String(500), nullable=False)
    url = Column(String(500), nullable=False)
    file_format = Column(SQLAlchemyEnum(IconFormat), nullable=False)
    file_size = Column(Integer, nullable=True)  # Size in bytes
    meta_data = Column(JSONB, nullable=True, default={})
    tags = Column(JSONB, nullable=True, default=[])  # Array of tags for easier searching
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", backref="icons")

    # Indexes for better query performance
    __table_args__ = (
        Index('ix_icons_type_user_id', 'type', 'user_id'),
        Index('ix_icons_mode', 'mode')
    )

    def __repr__(self):
        return f"<Icon(id={self.id}, name={self.name}, type={self.type})>"
