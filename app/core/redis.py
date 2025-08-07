import redis.asyncio as aioredis
import redis
import os

# Redis connection settings
REDIS_HOST = os.getenv('REDIS_HOST', 'redis')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))

# Create Redis pools for sync and async connections
async_redis_pool = aioredis.ConnectionPool(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=0,
    decode_responses=True,
    max_connections=10
)

sync_redis_pool = redis.ConnectionPool(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=0,
    decode_responses=True,
    max_connections=10
)

# Sync Redis client for use in synchronous code
sync_redis = redis.Redis(connection_pool=sync_redis_pool)

async def get_redis():
    """Get async Redis connection from pool"""
    client = aioredis.Redis(connection_pool=async_redis_pool)
    try:
        yield client
    finally:
        await client.aclose()

def get_sync_redis():
    """Get synchronous Redis connection"""
    return sync_redis
