import asyncio
import argparse
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.document import Document
from app.models.chat_conversation import ChatConversation

async def test_timestamp_update(db: AsyncSession, document_id=None, chat_id=None):
    """Test updating timestamps directly to verify database behavior."""
    
    if document_id:
        # Get the document
        result = await db.execute(select(Document).filter(Document.document_id == document_id))
        document = result.scalar_one_or_none()
        
        if document:
            print(f"Document before update:")
            print(f"  ID: {document.document_id}")
            print(f"  Title: {document.title}")
            print(f"  Created at: {document.created_at}")
            print(f"  Updated at: {document.updated_at}")
            
            # Update the document with a new timestamp
            current_time = datetime.now(timezone.utc)
            document.updated_at = current_time
            document.title = f"Updated at {current_time.isoformat()}"
            
            # Explicitly add to session and commit
            db.add(document)
            await db.commit()
            await db.refresh(document)
            
            print(f"\nDocument after update:")
            print(f"  ID: {document.document_id}")
            print(f"  Title: {document.title}")
            print(f"  Created at: {document.created_at}")
            print(f"  Updated at: {document.updated_at}")
            
            # Verify if update was successful
            if document.updated_at != document.created_at:
                print("\n✅ SUCCESS: updated_at timestamp was changed!")
            else:
                print("\n❌ ERROR: updated_at timestamp was NOT changed!")
        else:
            print(f"Document with ID {document_id} not found")
    
    if chat_id:
        # Get the chat
        result = await db.execute(select(ChatConversation).filter(ChatConversation.conversation_id == chat_id))
        chat = result.scalar_one_or_none()
        
        if chat:
            print(f"\nChat before update:")
            print(f"  ID: {chat.conversation_id}")
            print(f"  Title: {chat.conversation_title}")
            print(f"  Started at: {chat.started_at}")
            print(f"  Updated at: {chat.updated_at}")
            
            # Update the chat with a new timestamp
            current_time = datetime.now(timezone.utc)
            chat.updated_at = current_time
            chat.conversation_title = f"Updated at {current_time.isoformat()}"
            
            # Explicitly add to session and commit
            db.add(chat)
            await db.commit()
            await db.refresh(chat)
            
            print(f"\nChat after update:")
            print(f"  ID: {chat.conversation_id}")
            print(f"  Title: {chat.conversation_title}")
            print(f"  Started at: {chat.started_at}")
            print(f"  Updated at: {chat.updated_at}")
            
            # Verify if update was successful
            if chat.updated_at != chat.started_at:
                print("\n✅ SUCCESS: updated_at timestamp was changed!")
            else:
                print("\n❌ ERROR: updated_at timestamp was NOT changed!")
        else:
            print(f"Chat with ID {chat_id} not found")

async def main():
    parser = argparse.ArgumentParser(description="Test updating timestamps in the database")
    parser.add_argument("--document-id", help="Document ID to update")
    parser.add_argument("--chat-id", help="Chat ID to update")
    
    args = parser.parse_args()
    
    if not args.document_id and not args.chat_id:
        print("Please provide either --document-id or --chat-id")
        return
    
    async for db in get_db():
        try:
            await test_timestamp_update(db, args.document_id, args.chat_id)
        except Exception as e:
            print(f"Error: {str(e)}")
            await db.rollback()
            raise

if __name__ == "__main__":
    asyncio.run(main())
