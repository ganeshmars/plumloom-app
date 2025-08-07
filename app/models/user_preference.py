from datetime import datetime
from sqlalchemy import Column, String, DateTime, Enum, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB
import enum
from app.models.base import Base

class ThemeMode(enum.Enum):
    LIGHT = "light"
    DARK = "dark"

class UserPreference(Base):
    __tablename__ = "user_preferences"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    view_mode = Column(
        Enum(ThemeMode),
        nullable=False,
        server_default=ThemeMode.LIGHT.name,
        comment='User preferred theme mode'
    )
    display_options = Column(JSONB, nullable=False, default={}, comment='Display settings like theme, density, font size')
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    meta_data = Column(JSONB, nullable=False, default={}, comment='Additional metadata for user preferences')
    
    __table_args__ = (
        Index('ix_user_preferences_user_id', 'user_id'),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "view_mode": self.view_mode.value,
            "display_options": self.display_options,
            "meta_data": self.meta_data,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
    
    def get_compressed_dict(self):
        return {
            "view_mode": self.view_mode.value,
            "display_options": self.display_options,
            "meta_data": self.meta_data,
        }

    def __repr__(self):
        return f"<UserPreference(id={self.id}, user_id={self.user_id})>"
