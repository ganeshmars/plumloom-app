import asyncio
import argparse
import random
from datetime import datetime, timezone
from faker import Faker
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.core.database import get_db
from app.models.workspace import Workspace
from app.models.document import Document
from app.models.chat_conversation import ChatConversation

fake = Faker()

async def update_workspaces(db: AsyncSession, user_id: str, count: int = 3):
    """Update random workspaces for the specified user."""
    # Get existing workspaces for the user
    result = await db.execute(select(Workspace).filter(Workspace.user_id == user_id))
    workspaces = result.scalars().all()
    
    if not workspaces:
        print(f"No workspaces found for user {user_id}")
        return []
    
    # Select random workspaces to update
    workspaces_to_update = random.sample(workspaces, min(count, len(workspaces)))
    updated_workspaces = []
    
    for workspace in workspaces_to_update:
        # Update workspace with new data
        workspace.name = fake.company()
        workspace.description = fake.catch_phrase()
        updated_workspaces.append(workspace)
        print(f"Updated workspace: {workspace.workspace_id} with new name: {workspace.name}")
    
    await db.commit()
    return updated_workspaces

async def update_documents(db: AsyncSession, user_id: str, count: int = 5):
    """Update random documents for the specified user."""
    # Get existing documents for the user
    result = await db.execute(select(Document).filter(Document.user_id == user_id))
    documents = result.scalars().all()
    
    if not documents:
        print(f"No documents found for user {user_id}")
        return []
    
    # Select random documents to update
    docs_to_update = random.sample(documents, min(count, len(documents)))
    updated_docs = []
    
    for doc in docs_to_update:
        # Print document before update
        print(f"\nDocument before update:")
        print(f"  ID: {doc.document_id}")
        print(f"  Title: {doc.title}")
        print(f"  Created at: {doc.created_at}")
        print(f"  Updated at: {doc.updated_at}")
        
        # Update document with new data
        doc.title = fake.catch_phrase()
        doc.meta_data = {
            "tags": fake.words(3),
            "status": fake.random_element(["draft", "published", "archived"])
        }
        # Explicitly set the updated_at timestamp
        current_time = datetime.now(timezone.utc)
        doc.updated_at = current_time
        
        # Force SQLAlchemy to detect the change by explicitly marking as modified
        db.add(doc)
        updated_docs.append(doc)
    
    # Commit changes
    await db.commit()
    
    # Refresh objects to get updated values from database
    for doc in updated_docs:
        await db.refresh(doc)
        print(f"\nDocument after update:")
        print(f"  ID: {doc.document_id}")
        print(f"  Title: {doc.title}")
        print(f"  Created at: {doc.created_at}")
        print(f"  Updated at: {doc.updated_at}")
        
        # Verify if update was successful
        if doc.updated_at != doc.created_at:
            print("✅ SUCCESS: updated_at timestamp was changed!")
        else:
            print("❌ ERROR: updated_at timestamp was NOT changed!")
    
    return updated_docs

async def update_chats(db: AsyncSession, user_id: str, count: int = 5):
    """Update random chat conversations for the specified user."""
    # Get existing chats for the user
    result = await db.execute(select(ChatConversation).filter(ChatConversation.user_id == user_id))
    chats = result.scalars().all()
    
    if not chats:
        print(f"No chat conversations found for user {user_id}")
        return []
    
    # Select random chats to update
    chats_to_update = random.sample(chats, min(count, len(chats)))
    updated_chats = []
    
    for chat in chats_to_update:
        # Print chat before update
        print(f"\nChat before update:")
        print(f"  ID: {chat.conversation_id}")
        print(f"  Title: {chat.conversation_title}")
        print(f"  Started at: {chat.started_at}")
        print(f"  Updated at: {chat.updated_at}")
        
        # Update chat with new data
        chat.conversation_title = fake.sentence(nb_words=4)
        
        # Explicitly set the updated_at timestamp
        current_time = datetime.now(timezone.utc)
        chat.updated_at = current_time
        
        # Force SQLAlchemy to detect the change by explicitly marking as modified
        db.add(chat)
        updated_chats.append(chat)
    
    # Commit changes
    await db.commit()
    
    # Refresh objects to get updated values from database
    for chat in updated_chats:
        await db.refresh(chat)
        print(f"\nChat after update:")
        print(f"  ID: {chat.conversation_id}")
        print(f"  Title: {chat.conversation_title}")
        print(f"  Started at: {chat.started_at}")
        print(f"  Updated at: {chat.updated_at}")
        
        # Verify if update was successful
        if chat.updated_at != chat.started_at:
            print("✅ SUCCESS: updated_at timestamp was changed!")
        else:
            print("❌ ERROR: updated_at timestamp was NOT changed!")
    
    return updated_chats

async def main():
    parser = argparse.ArgumentParser(description="Update existing test data for a user")
    parser.add_argument("--user-id", required=True, help="User ID to update data for")
    parser.add_argument("--update-type", choices=["workspaces", "documents", "chats", "all"], 
                        default="all", help="Type of data to update")
    parser.add_argument("--count", type=int, default=3, help="Number of items to update for each type")
    
    args = parser.parse_args()
    
    async for db in get_db():
        try:
            print(f"Starting data update for user {args.user_id}")
            
            if args.update_type in ["workspaces", "all"]:
                updated_workspaces = await update_workspaces(db, args.user_id, args.count)
                print(f"Updated {len(updated_workspaces)} workspaces")
            
            if args.update_type in ["documents", "all"]:
                updated_docs = await update_documents(db, args.user_id, args.count)
                print(f"Updated {len(updated_docs)} documents")
            
            if args.update_type in ["chats", "all"]:
                updated_chats = await update_chats(db, args.user_id, args.count)
                print(f"Updated {len(updated_chats)} chats")
                
            print("Data update completed successfully")
            
        except Exception as e:
            print(f"Error updating data: {str(e)}")
            await db.rollback()
            raise

if __name__ == "__main__":
    asyncio.run(main())
