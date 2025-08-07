# diagnostic_scripts/check_tenant_data.py

import asyncio
import logging
import sys
import os
import argparse
from uuid import UUID

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload  # selectinload is not needed for User.tenants
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.database import get_db
    from app.models.document import Document
    from app.models.users import User
except ImportError as e:
    print(f"Error importing application modules: {e}")
    print("Please ensure this script is run from a context where 'app' modules are accessible.")
    print(f"Current sys.path: {sys.path}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def check_document_tenant_info(db: AsyncSession, document_id_str: str):
    try:
        doc_id_to_check = UUID(document_id_str)
    except ValueError:
        logger.error(f"Invalid Document ID format: {document_id_str}. Must be a UUID.")
        return

    logger.info(f"Checking tenant info for Document ID: {doc_id_to_check}")

    try:
        # Eagerly load the user. User.tenants is a column property and will be loaded with the user.
        query = (
            select(Document)
            .options(
                joinedload(Document.user)  # This will load the associated User object
                # No need for .options(selectinload(User.tenants)) here
                # because User.tenants is a column on the User model itself.
            )
            .where(Document.document_id == doc_id_to_check)
        )

        result = await db.execute(query)
        existing_doc: Document | None = result.scalar_one_or_none()

        if not existing_doc:
            logger.warning(f"No Document found with ID: {doc_id_to_check}")
            return

        logger.info(f"Document Found: ID={existing_doc.document_id}, Title='{existing_doc.title}'")

        if not existing_doc.user:
            logger.warning(
                f"Document {existing_doc.document_id} has no associated User (user_id: {existing_doc.user_id}).")
            return

        user_obj = existing_doc.user  # Get the loaded User object
        logger.info(f"Associated User Found: ID={user_obj.id}")

        # --- Inspecting User's Tenants ---
        # User.tenants is directly available as an attribute of user_obj
        if not hasattr(user_obj, 'tenants'):  # Should always be true as it's a Column
            logger.error(
                f"User {user_obj.id} does NOT have an attribute named 'tenants'. This is unexpected for a Column property.")
            derived_tenant_id = "tenant-2"
            logger.info(f"Derived tenant_id (due to missing 'tenants' attribute): '{derived_tenant_id}'")
            return

        user_tenants_array = user_obj.tenants  # This will be the ARRAY(String) column value
        logger.info(f"User.tenants (column property) type: {type(user_tenants_array)}")
        logger.info(f"User.tenants (column property) content: {user_tenants_array}")

        derived_tenant_id = "tenant-2"  # Default fallback

        if user_tenants_array is None:  # The ARRAY column could be NULL
            logger.warning(f"User {user_obj.id} has 'tenants' column, but its value is None.")
        elif not user_tenants_array:  # Empty list []
            logger.warning(f"User {user_obj.id} has 'tenants' column, but it is an empty list [].")
        else:
            # user_tenants_array is expected to be a list of strings
            try:
                first_tenant_element = user_tenants_array[0]  # Get the first string from the list
                logger.info(f"First element of User.tenants array (user_tenants_array[0]): '{first_tenant_element}'")
                logger.info(f"Type of first_tenant_element: {type(first_tenant_element)}")

                # Since it's directly a string from the array, use it if it's not empty
                if isinstance(first_tenant_element, str) and first_tenant_element.strip():
                    derived_tenant_id = first_tenant_element.strip()
                else:
                    logger.warning(
                        f"The first tenant element '{first_tenant_element}' is not a valid non-empty string. Falling back.")

            except IndexError:  # Should not happen if `not user_tenants_array` was false
                logger.warning(
                    f"User.tenants array is not empty, but user_tenants_array[0] caused an IndexError. This is unexpected.")
            except Exception as e:
                logger.error(f"Unexpected error while accessing user_tenants_array[0]: {e}", exc_info=True)

        logger.info(f"Final Derived tenant_id (as would be used in sync_documents): '{derived_tenant_id}'")

    except Exception as e:
        logger.error(f"An error occurred during database query or processing: {e}", exc_info=True)


async def main():
    parser = argparse.ArgumentParser(description="Check tenant data for a specific document.")
    parser.add_argument("--document-id", required=True, help="UUID of the document to check")

    args = parser.parse_args()

    logger.info(f"Attempting to check tenant data for document: {args.document_id}")

    async for db in get_db():
        try:
            await check_document_tenant_info(db, args.document_id)
        except Exception as e:
            logger.error(f"Critical error in main execution: {str(e)}", exc_info=True)
            raise
        finally:
            logger.info("Database session automatically managed by get_db.")


if __name__ == "__main__":
    asyncio.run(main())