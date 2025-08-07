import asyncio
import argparse
import json
import os
from datetime import datetime
import redis.asyncio as redis

# Redis connection settings (same as in app/core/redis.py)
REDIS_HOST = os.getenv('REDIS_HOST', 'redis')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))

async def debug_redis(user_id: str):
    """Debug Redis data for a specific user"""
    # Connect to Redis
    redis_client = redis.from_url(
        f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
    )
    
    try:
        # Check if Redis is working
        ping_result = await redis_client.ping()
        print(f"Redis ping result: {ping_result}")
        
        # Get all keys for this user
        combined_key = f"recent_items:{user_id}"
        doc_key = f"recent_items:{user_id}:document"
        chat_key = f"recent_items:{user_id}:chat"
        
        # Get counts
        combined_count = await redis_client.zcard(combined_key)
        doc_count = await redis_client.zcard(doc_key)
        chat_count = await redis_client.zcard(chat_key)
        
        print(f"\nRedis key counts:")
        print(f"- Combined key ({combined_key}): {combined_count} items")
        print(f"- Document key ({doc_key}): {doc_count} items")
        print(f"- Chat key ({chat_key}): {chat_count} items")
        
        # Get all items from the combined key
        if combined_count > 0:
            print(f"\nItems in combined key ({combined_key}):")
            items = await redis_client.zrevrange(combined_key, 0, -1, withscores=True)
            for i, (item_json, score) in enumerate(items):
                try:
                    item_data = json.loads(item_json)
                    print(f"\nItem {i+1}:")
                    print(f"  Score: {score} (timestamp: {datetime.fromtimestamp(score)})")
                    print(f"  Item ID: {item_data.get('item_id')}")
                    print(f"  Title: {item_data.get('title')}")
                    print(f"  Workspace ID: {item_data.get('workspace_id')}")
                    print(f"  Workspace Name: {item_data.get('workspace_name')}")
                    print(f"  Item Type: {item_data.get('item_type')}")
                    print(f"  Updated At: {item_data.get('updated_at')}")
                except Exception as e:
                    print(f"  Error parsing item: {str(e)}")
                    print(f"  Raw data: {item_json[:100]}...")
        else:
            print("\nNo items found in Redis for this user.")
            
        # Check for any keys with pattern
        all_keys = await redis_client.keys(f"recent_items:{user_id}*")
        if all_keys:
            print(f"\nAll keys matching pattern 'recent_items:{user_id}*':")
            for key in all_keys:
                print(f"- {key.decode('utf-8')}")
        
    finally:
        # Close Redis connection
        await redis_client.close()

async def main():
    parser = argparse.ArgumentParser(description="Debug Redis data for a specific user")
    parser.add_argument("--user-id", required=True, help="User ID to check Redis data for")
    
    args = parser.parse_args()
    await debug_redis(args.user_id)

if __name__ == "__main__":
    asyncio.run(main())
