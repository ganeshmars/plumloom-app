import asyncio
import json

from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from uuid import UUID

from sqlalchemy import select, union_all, String, null
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models.document import Document
from app.models.chat_conversation import ChatConversation
from app.models.workspace import Workspace
from app.schemas.recent_items import RecentItemResponse, RecentItemsList
from app.core.redis import get_redis, get_sync_redis

from app.core.logging_config import logger

class RecentItemsService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.CACHE_TTL = 60 * 60 * 24  # 24 hours - more appropriate for "recent" items
        self.CACHE_KEY_PREFIX = "recent_items"
        self.redis = None  # Will be set in list_recent_items
        self.sync_redis = get_sync_redis()  # Get sync Redis client for event handlers
        
    def _get_sorted_set_key(self, user_id: str, item_type: Optional[str] = None) -> str:
        """Get the Redis sorted set key for a user's recent items
        
        Args:
            user_id: The user ID (which is now directly the Descope ID)
            item_type: Optional type filter ('document' or 'chat')
            
        Returns:
            Redis key string in the format 'recent_items:{user_id}[:item_type]'
        """
        key = f"{self.CACHE_KEY_PREFIX}:{user_id}"
        if item_type:
            key += f":{item_type}"
        return key
        
    def update_recent_item_sync(self, user_id: str, item_type: str, item_data: Dict[str, Any]):
        """Synchronous version of update_recent_item for event handlers"""
        try:
            logger.info(f"Updating Redis cache for user {user_id}, item type {item_type}, item id {item_data.get('item_id')}")
            
            # Check if Redis connection is working
            ping_result = self.sync_redis.ping()
            logger.info(f"Redis ping result: {ping_result}")
        
            # Process updated_at in a single operation
            updated_at = (datetime.fromisoformat(item_data['updated_at']) 
                        if isinstance(item_data['updated_at'], str) 
                        else item_data['updated_at'])
            
            # Ensure datetime has timezone info
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
                
            score = updated_at.timestamp()
            
            # Prepare item data with ISO string
            item_data_json = {**item_data, 'updated_at': updated_at.isoformat()}
            serialized_data = json.dumps(item_data_json)
            
            # Get both keys - one for item type and one for combined
            type_key = self._get_sorted_set_key(user_id, item_type)
            combined_key = self._get_sorted_set_key(user_id)
            
            # Update both sorted sets with the score and JSON data
            self.sync_redis.zadd(type_key, {serialized_data: score})
            self.sync_redis.zadd(combined_key, {serialized_data: score})
            
            # Set TTL for both keys
            self.sync_redis.expire(type_key, self.CACHE_TTL)
            self.sync_redis.expire(combined_key, self.CACHE_TTL)
            
            # Trim the sorted sets to prevent unlimited growth
            self.sync_redis.zremrangebyrank(type_key, 0, -1001)
            self.sync_redis.zremrangebyrank(combined_key, 0, -1001)
            
            # Verify data was stored
            type_count = self.sync_redis.zcard(type_key)
            combined_count = self.sync_redis.zcard(combined_key)
            logger.info(f"Redis keys after update - {type_key}: {type_count} items, {combined_key}: {combined_count} items")
            
            logger.info(f"Successfully updated Redis cache for user {user_id}, item {item_data.get('item_id')}")
        except Exception as e:
            logger.error(f"Error updating Redis cache: {str(e)}")
            
    def remove_item_sync(self, user_id: str, item_type: str, item_id: str):
        """Synchronous version of remove_item for event handlers"""
        try:
            logger.info(f"Removing item {item_id} of type {item_type} from Redis cache for user {user_id}")
            
            # Get both keys
            type_key = self._get_sorted_set_key(user_id, item_type)
            combined_key = self._get_sorted_set_key(user_id)
            
            # Get items from both sets
            type_items = self.sync_redis.zrange(type_key, 0, -1)
            combined_items = self.sync_redis.zrange(combined_key, 0, -1)
            
            # Find items to remove
            type_item = next((item for item in type_items 
                             if json.loads(item)['item_id'] == item_id), None)
            combined_item = next((item for item in combined_items 
                                if json.loads(item)['item_id'] == item_id), None)
            
            # Remove items if found
            if type_item:
                self.sync_redis.zrem(type_key, type_item)
            if combined_item:
                self.sync_redis.zrem(combined_key, combined_item)
                
            logger.info(f"Successfully removed item {item_id} from Redis cache for user {user_id}")
        except Exception as e:
            logger.error(f"Error removing item from Redis cache: {str(e)}")
            
    async def update_recent_item(self, user_id: str, item_type: str, item_data: Dict[str, Any]):
        """Async version of update_recent_item for API endpoints"""
        redis = await self._get_redis()
        # logger.info(f"Updating Redis cache for user {user_id}, item type {item_type}, item id {item_data.get('item_id')}")
        
        # Process updated_at in a single operation
        updated_at = (datetime.fromisoformat(item_data['updated_at']) 
                     if isinstance(item_data['updated_at'], str) 
                     else item_data['updated_at'])
        score = updated_at.timestamp()
        
        # Prepare item data with ISO string
        item_data_json = {**item_data, 'updated_at': updated_at.isoformat()}
        serialized_data = json.dumps(item_data_json)
        
        # Get both keys at once
        type_key = self._get_sorted_set_key(user_id, item_type)
        combined_key = self._get_sorted_set_key(user_id)
        
        # Execute Redis operations in parallel
        await redis.zadd(type_key, {serialized_data: score})
        await redis.zadd(combined_key, {serialized_data: score})
        await redis.expire(type_key, self.CACHE_TTL)
        await redis.expire(combined_key, self.CACHE_TTL)
        await redis.zremrangebyrank(type_key, 0, -1001)
        await redis.zremrangebyrank(combined_key, 0, -1001)
        
    async def _get_redis(self):
        if not self.redis:
            async for redis_client in get_redis():
                self.redis = redis_client
                break
        return self.redis

    async def list_recent_items(
        self,
        user_id: str,
        page: int = 1,
        size: int = 20,
        item_type: Optional[str] = None
    ) -> RecentItemsList:
        try:
            logger.info(f"Fetching recent items for user {user_id}, page {page}, size {size}, type {item_type}")
            redis = await self._get_redis()
            sorted_set_key = self._get_sorted_set_key(user_id, item_type)
            logger.info(f"Using Redis key: {sorted_set_key}")
            
            # # Check if Redis is working
            # ping_result = await redis.ping()
            # logger.info(f"Redis ping result: {ping_result}")
            
            # Calculate range for pagination
            start = (page - 1) * size
            end = start + size - 1
            
            # Get items from Redis sorted set
            # logger.info(f"Fetching items from Redis range {start} to {end}")
            items_data = await redis.zrevrange(sorted_set_key, start, end, withscores=True)
            total = await redis.zcard(sorted_set_key)
            # logger.info(f"Found {total} total items in Redis, retrieved {len(items_data)} items")
            
            # Parse items from JSON and convert datetime strings
            # Use a dictionary to deduplicate by item_id and prioritize items with real workspace names
            item_dict = {}
            # logger.info(f"Processing {len(items_data)} items from Redis")
            
            for i, item in enumerate(items_data):
                try:
                    item_data = json.loads(item[0])
                    item_id = item_data.get('item_id')
                    
                    # Skip if no item_id
                    if not item_id:
                        logger.warning(f"Item {i} has no item_id, skipping")
                        continue
                    
                    # Log item details for debugging
                    # logger.info(f"Processing item {i}: id={item_id}, type={item_data.get('item_type')}, "
                                # f"workspace={item_data.get('workspace_name')}")
                        
                    # Process datetime
                    updated_at = datetime.fromisoformat(item_data['updated_at'])
                    if updated_at.tzinfo is None:
                        updated_at = updated_at.replace(tzinfo=timezone.utc)
                    item_data['updated_at'] = updated_at
                    
                    # Only add if not already in dict or if this one has a better workspace name
                    current_workspace_name = item_data.get('workspace_name')
                    if item_id not in item_dict:
                        # logger.info(f"Adding new item {item_id} to results")
                        item_dict[item_id] = RecentItemResponse(**item_data)
                    elif (item_dict[item_id].workspace_name == 'Unknown Workspace' and 
                          current_workspace_name != 'Unknown Workspace'):
                        # logger.info(f"Replacing item {item_id} with better workspace name: {current_workspace_name}")
                        item_dict[item_id] = RecentItemResponse(**item_data)
                    else:
                        logger.info(f"Skipping duplicate item {item_id} (keeping existing entry)")
                except Exception as e:
                    logger.error(f"Error processing Redis item {i}: {str(e)}, item: {item[0][:100]}...")
                    
            # Convert dictionary values to list
            items = list(item_dict.values())
            
            # Log the results
            # if items:
            #     logger.info(f"Successfully retrieved {len(items)} items from Redis for user {user_id}")
            #     # Log first few items for debugging
            #     for i, item in enumerate(items[:3]):
            #         logger.info(f"Result item {i}: id={item.item_id}, type={item.item_type}, "
            #                    f"workspace={item.workspace_name}")
            # else:
            #     logger.info(f"No items found in Redis for user {user_id} after processing")
            #     logger.info(f"Original Redis data count: {len(items_data)}, Dictionary count: {len(item_dict)}")
            
            return RecentItemsList(
                items=items,
                total=total,
                page=page,
                size=size,
                total_pages=max(1, -(-total // size))  # Ceiling division for total pages
            )
        except Exception as e:
            logger.error(f"Error fetching recent items: {str(e)}")
            return RecentItemsList(
                items=[],
                total=0,
                page=page,
                size=size,
                total_pages=1  # At least one page even when empty
            )

    async def remove_item(self, user_id: str, item_type: str, item_id: str):
        """Remove an item from the user's recent items in Redis"""
        redis = await self._get_redis()
        logger.info(f"Removing item {item_id} of type {item_type} from Redis cache for user {user_id}")
        
        # Get both keys
        type_key = self._get_sorted_set_key(user_id, item_type)
        combined_key = self._get_sorted_set_key(user_id)
        
        # Get items from both sets in parallel
        type_items, combined_items = await asyncio.gather(
            redis.zrange(type_key, 0, -1),
            redis.zrange(combined_key, 0, -1)
        )
        
        # Find items to remove
        type_item = next((item for item in type_items 
                         if json.loads(item)['item_id'] == item_id), None)
        combined_item = next((item for item in combined_items 
                            if json.loads(item)['item_id'] == item_id), None)
        
        # Remove items in parallel if found
        if type_item or combined_item:
            await asyncio.gather(
                redis.zrem(type_key, type_item) if type_item else asyncio.sleep(0),
                redis.zrem(combined_key, combined_item) if combined_item else asyncio.sleep(0)
            )
