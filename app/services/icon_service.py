from typing import List, Optional, Dict, Any, Union
import json
from uuid import UUID
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.icon import Icon, IconCategory, IconType, IconMode
from app.core.redis import get_redis

settings = get_settings()

# Redis key patterns
ICON_KEY_PREFIX = "icon:"
ICON_LIST_KEY = "icons:all"
ICON_CATEGORY_KEY_PREFIX = "icons:category:"
ICON_USER_KEY_PREFIX = "icons:user:"
ICON_TYPE_KEY_PREFIX = "icons:type:"
ICON_MODE_KEY_PREFIX = "icons:mode:"

# Cache expiration time (in seconds)
ICON_CACHE_EXPIRY = 86400  # 24 hours


async def list_icons(
    db: AsyncSession,
    icon_type: Optional[IconType] = None,
    user_id: Optional[str] = None,
) -> List[Icon]:
    """
    List icons with optional filtering and Redis caching
    """
    # Determine cache key based on filters
    cache_key = _get_list_cache_key(icon_type, user_id)
    
    # Try to get from cache first
    redis_gen = get_redis()
    redis = await anext(redis_gen)
    cached_icons = await redis.get(cache_key)
    
    if cached_icons:
        icons_data = json.loads(cached_icons)
        return [Icon(**icon_data) for icon_data in icons_data]
    
    # If not in cache, query database
    query = select(Icon)
    
    if icon_type:
        query = query.where(Icon.type == icon_type)
    if user_id and icon_type == IconType.USER:
        query = query.where(Icon.user_id == user_id)
    
    result = await db.execute(query)
    icons = result.scalars().all()
    
    # Cache the results
    await cache_icon_list(icons, cache_key)
    
    return icons


async def create_icon(db: AsyncSession, icon_data: Dict[str, Any]) -> Icon:
    """
    Create a new icon and update cache
    """
    icon = Icon(**icon_data)
    db.add(icon)
    await db.commit()
    await db.refresh(icon)
    
    # Update cache
    await cache_icon(icon)
    await invalidate_list_caches()
    
    return icon


# Cache helper functions
async def cache_icon(icon: Icon) -> None:
    """
    Cache a single icon
    """
    redis_gen = get_redis()
    redis = await anext(redis_gen)
    cache_key = f"{ICON_KEY_PREFIX}{icon.id}"
    
    # Convert icon to dict and cache
    icon_dict = {c.name: getattr(icon, c.name) for c in icon.__table__.columns}
    
    # Handle UUID serialization
    if isinstance(icon_dict["id"], UUID):
        icon_dict["id"] = str(icon_dict["id"])
    
    # Handle enum serialization
    for field in ["category", "type", "mode", "file_format"]:
        if field in icon_dict and icon_dict[field] is not None:
            icon_dict[field] = icon_dict[field].value
    
    # Handle datetime serialization
    for field in ["created_at", "updated_at"]:
        if field in icon_dict and icon_dict[field] is not None:
            # Ensure timezone awareness
            if icon_dict[field].tzinfo is None:
                icon_dict[field] = icon_dict[field].replace(tzinfo=timezone.utc)
            icon_dict[field] = icon_dict[field].isoformat()
    
    await redis.set(cache_key, json.dumps(icon_dict), ex=ICON_CACHE_EXPIRY)


async def cache_icon_list(icons: List[Icon], cache_key: str) -> None:
    """
    Cache a list of icons
    """
    if not icons:
        return
    
    redis_gen = get_redis()
    redis = await anext(redis_gen)
    
    # Convert icons to dicts
    icons_data = []
    for icon in icons:
        icon_dict = {c.name: getattr(icon, c.name) for c in icon.__table__.columns}
        
        # Handle UUID serialization
        if isinstance(icon_dict["id"], UUID):
            icon_dict["id"] = str(icon_dict["id"])
        
        # Handle enum serialization
        for field in ["category", "type", "mode", "file_format"]:
            if field in icon_dict and icon_dict[field] is not None:
                icon_dict[field] = icon_dict[field].value
        
        # Handle datetime serialization
        for field in ["created_at", "updated_at"]:
            if field in icon_dict and icon_dict[field] is not None:
                # Ensure timezone awareness
                if icon_dict[field].tzinfo is None:
                    icon_dict[field] = icon_dict[field].replace(tzinfo=timezone.utc)
                icon_dict[field] = icon_dict[field].isoformat()
        
        icons_data.append(icon_dict)
    
    await redis.set(cache_key, json.dumps(icons_data), ex=ICON_CACHE_EXPIRY)


async def invalidate_list_caches() -> None:
    """
    Invalidate all list caches
    """
    redis_gen = get_redis()
    redis = await anext(redis_gen)
    
    # Get all list cache keys
    keys = []
    
    # Get all keys with the list prefix
    cursor = 0
    while True:
        cursor, partial_keys = await redis.scan(cursor, f"{ICON_LIST_KEY}*")
        keys.extend(partial_keys)
        
        # Also get category keys
        cursor, partial_keys = await redis.scan(cursor, f"{ICON_CATEGORY_KEY_PREFIX}*")
        keys.extend(partial_keys)
        
        # Also get user keys
        cursor, partial_keys = await redis.scan(cursor, f"{ICON_USER_KEY_PREFIX}*")
        keys.extend(partial_keys)
        
        # Also get type keys
        cursor, partial_keys = await redis.scan(cursor, f"{ICON_TYPE_KEY_PREFIX}*")
        keys.extend(partial_keys)
        
        # Also get mode keys
        cursor, partial_keys = await redis.scan(cursor, f"{ICON_MODE_KEY_PREFIX}*")
        keys.extend(partial_keys)
        
        if cursor == 0:
            break
    
    # Delete all keys
    if keys:
        await redis.delete(*keys)


# Helper functions
def _get_list_cache_key(
    icon_type: Optional[IconType] = None,
    user_id: Optional[str] = None,
) -> str:
    """
    Generate a cache key for a list of icons based on filters
    """
    if icon_type and not any([user_id]):
        return f"{ICON_TYPE_KEY_PREFIX}{icon_type.value}"
    elif user_id and not any([icon_type]):
        return f"{ICON_USER_KEY_PREFIX}{user_id}"
    else:
        # Create a composite key for multiple filters
        key_parts = []
        if icon_type:
            key_parts.append(f"type:{icon_type.value}")
        if user_id:
            key_parts.append(f"user:{user_id}")
        
        if key_parts:
            return f"{ICON_LIST_KEY}:{':'.join(key_parts)}"
        else:
            return ICON_LIST_KEY
