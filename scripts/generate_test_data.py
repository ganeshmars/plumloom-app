import asyncio
import argparse
from faker import Faker
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4

from app.core.database import get_db
from app.models.workspace import Workspace
from app.models.document import Document
from app.models.chat_conversation import ChatConversation

fake = Faker()

async def generate_test_data(db: AsyncSession, user_id: str, num_workspaces: int = 5):
    """Generate test data for workspaces, documents, and chat conversations."""
    
    # Generate workspaces
    workspaces = []
    for _ in range(num_workspaces):
        workspace = Workspace(
            user_id=user_id,
            name=fake.company(),
            description=fake.catch_phrase(),
            meta_data={
                "size": fake.random_element(["small", "medium", "large"])
            },
            workspace_type=fake.random_element(["personal", "team", "organization"]),
            icon_url=fake.image_url(),
            cover_image_url=fake.image_url()
        )
        workspaces.append(workspace)
        db.add(workspace)
    
    await db.flush()  # Get workspace IDs
    
    # Generate documents for each workspace
    for workspace in workspaces:
        # Create parent documents
        num_parent_docs = fake.random_int(min=2, max=5)
        parent_docs = []
        for _ in range(num_parent_docs):
            doc = Document(
                workspace_id=workspace.workspace_id,
                user_id=user_id,
                title=fake.catch_phrase(),
                content_file_path=f"/path/to/content/{uuid4()}.md",
                meta_data={
                    "tags": fake.words(3),
                    "status": fake.random_element(["draft", "published", "archived"])
                }
            )
            parent_docs.append(doc)
            db.add(doc)
        
        await db.flush()  # Get parent document IDs
        
        # Create child documents
        for parent_doc in parent_docs:
            num_child_docs = fake.random_int(min=0, max=3)
            for _ in range(num_child_docs):
                child_doc = Document(
                    workspace_id=workspace.workspace_id,
                    user_id=user_id,
                    title=fake.catch_phrase(),
                    content_file_path=f"/path/to/content/{uuid4()}.md",
                    parent_id=parent_doc.document_id,
                    meta_data={
                        "tags": fake.words(2),
                        "status": fake.random_element(["draft", "published", "archived"])
                    }
                )
                db.add(child_doc)
        
        await db.flush()
        
        # Create chat conversations for some documents
        for doc in parent_docs:
            if fake.boolean(chance_of_getting_true=70):  # 70% chance of having a conversation
                conversation = ChatConversation(
                    user_id=user_id,
                    workspace_id=workspace.workspace_id,
                    conversation_title=fake.sentence(),
                    meta_data={
                        "status": fake.random_element(["active", "archived"]),
                        "tags": fake.words(2)
                    }
                )
                db.add(conversation)
    
    # Commit all changes
    await db.commit()

async def main(test_user_id: str, num_workspaces: int = 5):
    """Main function to run the test data generation."""
    print(f"Generating test data for user ID: {test_user_id} with {num_workspaces} workspaces")
    
    async for db in get_db():
        await generate_test_data(db, test_user_id, num_workspaces)
        print("Test data generated successfully!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate test data for the application')
    parser.add_argument('--user-id', type=str, required=True, help='User ID to create test data for')
    parser.add_argument('--workspaces', type=int, default=5, help='Number of workspaces to create (default: 5)')
    
    args = parser.parse_args()
    
    asyncio.run(main(args.user_id, args.workspaces))