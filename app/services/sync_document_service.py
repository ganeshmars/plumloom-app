# app/services/sync_document_service.py

import json
from typing import Dict, Any, Optional, List
from uuid import UUID, uuid4
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import SQLAlchemyError

from app.models import Document, DocumentVersion, Workspace
from app.core.logging_config import logger
from app.core.storage import upload_file_to_gcs_sync, delete_file_from_gcs_sync, get_file_content_sync
# Use the new sync vector service
from app.services.weaviate.page_service_sync import PageVectorServiceSync
from app.services.weaviate.exceptions import VectorStoreOperationError
# Import constants for bucket name
from app.core.constants import GCS_DOCUMENTS_BUCKET

# Import Weaviate Filter for potential use in vector service if needed elsewhere
# from weaviate.collections.classes.filters import Filter



class SyncDocumentService:
    """
    Synchronous service for managing documents, coordinating database,
    GCS storage, and Weaviate vector operations. Designed for Celery tasks.
    """

    def __init__(self, db: Session, page_vector_service: PageVectorServiceSync):
        """
        Initializes the service.

        Args:
            db: SQLAlchemy session.
            page_vector_service: Initialized instance of PageVectorServiceSync.
        """
        self.db = db
        self.page_vector_service = page_vector_service
        # Note: GCS client is managed implicitly by functions in storage.py
        self.gcs_bucket_name = GCS_DOCUMENTS_BUCKET

    # --- Rollback Helper Functions ---

    def _rollback_gcs_files(self, paths_to_delete: List[str]):
        """Attempts to delete specified files from GCS during rollback."""
        if not paths_to_delete:
            return
        logger.warning(f"Rolling back GCS files: Attempting to delete {paths_to_delete}")
        for path in paths_to_delete:
            if not path: continue
            try:
                # Assuming delete_file_from_gcs_sync exists and takes path and bucket
                deleted = delete_file_from_gcs_sync(path, self.gcs_bucket_name)
                if deleted:
                    logger.info(f"Successfully deleted GCS file during rollback: {path}")
                else:
                    # Might happen if file wasn't created or already deleted
                    logger.warning(f"GCS file not found or failed to delete during rollback: {path}")
            except Exception as e:
                logger.error(f"Failed to delete GCS file {path} during rollback: {e}", exc_info=True)
                # Continue cleanup despite error

    def _rollback_weaviate_creation(self, tenant_id: str, doc_id: UUID):
        """Attempts to delete vectors from Weaviate during creation rollback."""
        logger.warning(f"Rolling back Weaviate creation: Attempting to delete vectors for doc {doc_id}")
        try:
            self.page_vector_service.delete_vectors(tenant_id=tenant_id, doc_id=doc_id)
            logger.info(f"Successfully deleted Weaviate vectors for doc {doc_id} during creation rollback.")
        except VectorStoreOperationError as e:
            # Log specific vector store errors during cleanup
            logger.error(f"Vector store error deleting vectors for doc {doc_id} during rollback: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Generic error deleting Weaviate vectors for doc {doc_id} during rollback: {e}",
                         exc_info=True)
            # Continue cleanup despite error

    def _rollback_weaviate_update(self, tenant_id: str, doc_id: UUID, workspace_id: UUID, title: str,
                                  previous_version_path: Optional[str]):
        """Attempts to restore Weaviate vectors to the state of the previous version."""
        logger.warning(
            f"Rolling back Weaviate update: Attempting to restore vectors for doc {doc_id} from path {previous_version_path}")
        if not previous_version_path:
            logger.error(f"Cannot rollback Weaviate update for doc {doc_id}: No previous version path provided.")
            # If no previous version, best effort is to just delete current vectors
            self._rollback_weaviate_creation(tenant_id, doc_id)
            return

        try:
            # 1. Get content of the previous version from GCS
            logger.debug(f"Fetching previous content from GCS: {previous_version_path}")
            # Assuming get_file_content_sync exists and returns bytes/str
            previous_content_bytes = get_file_content_sync(previous_version_path, self.gcs_bucket_name)
            if not previous_content_bytes:
                raise RuntimeError(f"Could not fetch previous content from {previous_version_path}")
            previous_content_str = previous_content_bytes.decode('utf-8')
            previous_content_json = json.loads(previous_content_str)
            logger.debug(f"Successfully fetched and parsed previous content for doc {doc_id}")

            # 2. Use the update_vectors method which handles diffing (it will delete current and add previous)
            #    Alternatively, could explicitly delete then create, but update handles the diff logic.
            logger.info(f"Restoring Weaviate vectors for doc {doc_id} using previous content.")
            restore_result = self.page_vector_service.update_vectors_from_content(
                tenant_id=tenant_id,
                doc_id=doc_id,
                workspace_id=workspace_id,  # Must provide workspace_id
                title=title,  # Use the title associated with the previous state
                content=previous_content_json  # Pass the dict content here
            )
            if restore_result.get("status") in ["success", "partial_success"]:
                logger.info(f"Successfully restored Weaviate vectors for doc {doc_id} using previous version content.")
            else:
                logger.error(f"Failed to fully restore Weaviate vectors for doc {doc_id}. Result: {restore_result}")
                # At this point, state might be inconsistent. Log error prominently.

        except FileNotFoundError:
            logger.error(
                f"GCS file for previous version not found during Weaviate rollback: {previous_version_path}. Deleting current vectors as fallback.",
                exc_info=True)
            self._rollback_weaviate_creation(tenant_id, doc_id)
        except json.JSONDecodeError:
            logger.error(
                f"Failed to parse JSON from previous version file {previous_version_path} during Weaviate rollback. Deleting current vectors as fallback.",
                exc_info=True)
            self._rollback_weaviate_creation(tenant_id, doc_id)
        except VectorStoreOperationError as e:
            logger.error(f"Vector store error restoring vectors for doc {doc_id} during rollback: {e}", exc_info=True)
            # Logged but continue (state might be bad)
        except Exception as e:
            logger.error(f"Generic error restoring Weaviate vectors for doc {doc_id} during rollback: {e}",
                         exc_info=True)
            # Logged but continue (state might be bad)

    # --- Main Service Methods ---

    def create_document(
            self,
            title: str,
            content: Dict[str, Any],
            user_id: str,
            tenant_id: str,
            workspace_id: UUID,
            doc_size: Optional[int] = 80,
            parent_page_id: Optional[UUID] = None,
            icon_url: Optional[str] = None,
            cover_url: Optional[str] = None,
            character_count: Optional[int] = 0,  # Consider calculating?
            block_count: Optional[int] = 0,  # Consider calculating?
    ) -> Dict[str, Any]:
        """
        Creates a new document: uploads to GCS, creates vectors, saves to DB.
        Handles rollback on failure.
        """
        doc_id = uuid4()
        logger.info(
            f"Attempting to create document {doc_id} titled '{title}' in workspace {workspace_id} by user {user_id}")

        # Define GCS paths (used for DB and GCS operations)
        base_path = f"{user_id}/{workspace_id}/{doc_id}"  # Include tenant/workspace for better GCS structure
        main_content_path = f"{base_path}/content.json"
        version_content_path = f"{base_path}/v1.json"

        gcs_paths_created = []
        vector_stored = False
        db_committed = False

        try:
            # Pre-check: Workspace exists?
            workspace = self.db.query(Workspace).filter(Workspace.workspace_id == workspace_id).first()
            if not workspace:
                logger.error(f"Workspace {workspace_id} not found.")
                raise ValueError(f"Workspace with ID {workspace_id} does not exist")

            # Step 1: Store content in GCS (Version 1 first, then Main)
            logger.debug(f"Step 1: Uploading content to GCS for doc {doc_id}")
            try:
                content_json_str = json.dumps(content)
                # Upload version 1 file
                upload_file_to_gcs_sync(content_json_str, version_content_path, self.gcs_bucket_name,
                                        'application/json')
                gcs_paths_created.append(version_content_path)
                logger.info(f"Uploaded version content to GCS: {version_content_path}")

                # Upload main content file (can be the same content initially)
                upload_file_to_gcs_sync(content_json_str, main_content_path, self.gcs_bucket_name, 'application/json')
                gcs_paths_created.append(main_content_path)
                logger.info(f"Uploaded main content to GCS: {main_content_path}")
            except Exception as gcs_error:
                logger.error(f"GCS upload failed during document creation: {gcs_error}", exc_info=True)
                # Rollback GCS files created so far
                self._rollback_gcs_files(gcs_paths_created)
                raise RuntimeError("Failed to upload content to GCS") from gcs_error

            # Step 2: Create vectors in Weaviate
            logger.debug(f"Step 2: Creating Weaviate vectors for doc {doc_id}")
            try:
                create_vector_response = self.page_vector_service.create_vectors_from_content(
                    tenant_id=tenant_id,
                    doc_id=doc_id,
                    workspace_id=workspace_id,
                    title=title,
                    content=content
                )
                # Check response status from vector service
                if create_vector_response.get("status") not in ["success",
                                                                "partial_success"] or create_vector_response.get(
                        "failed_chunks", 0) > 0:
                    # Log detailed error from vector service response
                    logger.error(
                        f"Weaviate vector creation partially or fully failed for doc {doc_id}. Response: {create_vector_response}")
                    # Treat partial failure as critical for creation? Decide based on requirements. Let's assume critical.
                    raise VectorStoreOperationError(
                        f"Vector creation failed or had errors: {create_vector_response.get('message')}")

                vector_stored = True
                logger.info(f"Successfully created Weaviate vectors for doc {doc_id}")
            except Exception as vector_error:
                logger.error(f"Weaviate vector creation failed for doc {doc_id}: {vector_error}", exc_info=True)
                # Rollback previous steps (GCS)
                self._rollback_gcs_files(gcs_paths_created)
                # No need to rollback Weaviate itself, as it failed during creation
                raise RuntimeError("Failed to create vectors in Weaviate") from vector_error

            # Step 3: Create database records
            logger.debug(f"Step 3: Creating database records for doc {doc_id}")
            try:
                current_time = datetime.now(timezone.utc)
                document = Document(
                    document_id=doc_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    title=title,
                    content_file_path=main_content_path, # Store the path
                    parent_id=parent_page_id,
                    icon_url=icon_url,
                    cover_url=cover_url,
                    created_at=current_time,
                    updated_at=current_time,
                    meta_data={
                        "version": 1,  # Initial version number
                        "vectorization_status": create_vector_response.get("status", "unknown"),
                        # Reflect vector status
                        "size": doc_size,  # Store provided size
                        "character_count": character_count,
                        "block_count": block_count,
                    }
                )

                version = DocumentVersion(
                    document_id=doc_id,
                    version_number=1,
                    content_file_path=version_content_path,  # Store the path
                    saved_at=current_time,  # Use current time for version save
                    meta_data={"initial_version": True, "size": doc_size}  # Store provided size
                )

                self.db.add(document)
                self.db.add(version)
                self.db.commit()
                db_committed = True
                logger.info(f"Successfully committed database records for doc {doc_id}")

            except SQLAlchemyError as db_error:
                logger.error(f"Database commit failed during document creation for doc {doc_id}: {db_error}",
                             exc_info=True)
                self.db.rollback()  # Rollback the failed DB transaction
                # Rollback previous steps (Weaviate, GCS)
                if vector_stored:  # Only rollback if vectors were successfully created
                    self._rollback_weaviate_creation(tenant_id, doc_id)
                self._rollback_gcs_files(gcs_paths_created)
                raise RuntimeError("Failed to save document to database") from db_error
            except Exception as e:  # Catch other potential errors during DB setup
                logger.error(f"Unexpected error during database record creation for doc {doc_id}: {e}", exc_info=True)
                self.db.rollback()
                if vector_stored:
                    self._rollback_weaviate_creation(tenant_id, doc_id)
                self._rollback_gcs_files(gcs_paths_created)
                raise RuntimeError("Failed setup database records") from e

            # --- Success ---
            logger.info(f"Successfully created document {doc_id}")
            # Return success response including GCS paths and vector info
            return {
                "status": "success",
                "message": "Document created successfully",
                "data": {
                    "document_id": str(doc_id),
                    "title": title,
                    "main_content_path": main_content_path,
                    "version_content_path": version_content_path,
                    "version_number": 1,
                    "vector_create_response": create_vector_response # Include vector response
                }
            }

        except Exception as e:
            # Catch errors raised and re-wrapped by the steps above
            logger.critical(f"Document creation failed for doc {doc_id}. Error: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"Failed to create document: {str(e)}",
                "document_id": str(doc_id),
                "data": None
            }

    def update_document(
            self,
            doc_id: UUID,
            user_id: str,
            tenant_id: str,
            content: Optional[Dict[str, Any]],
            title: Optional[str] = None,
            doc_size: Optional[int] = None,
            icon_url: Optional[str] = None,
            cover_url: Optional[str] = None,
            character_count: Optional[int] = None,
            block_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Updates an existing document: uploads new version to GCS, updates main GCS file,
        updates vectors, updates DB records. Handles rollback.
        """
        logger.info(f"Attempting to update document {doc_id} by user {user_id}")

        if content is None:  # Check if content is None, not just falsy (e.g. empty dict)
            logger.warning(f"Update requested for doc {doc_id} without new content (content is None).")
            # Depending on requirements, you might allow metadata-only updates.
            # For now, assuming content is required for a meaningful update that involves vectorization.
            return {"status": "error", "message": "Content is required for update if vectorization is expected",
                    "data": None}

        # State tracking for rollback
        new_version_gcs_path = None
        main_content_updated_in_gcs = False
        # weaviate_state_before_update = None
        previous_version_path_for_rollback = None
        # db_changes_committed = False # Not used
        original_doc_title = None
        vector_update_succeeded = False

        try:
            # Pre-fetch document and latest version for info and rollback
            document = (
                self.db.query(Document)
                .options(joinedload(Document.versions))  # Eager load versions
                .filter(Document.document_id == doc_id)
                .first()
            )

            if not document:
                logger.error(f"Document {doc_id} not found for update.")
                return {"status": "error", "message": "Document not found", "data": None}

            original_doc_title = document.title
            workspace_id = document.workspace_id

            if document.versions:
                latest_version = max(document.versions, key=lambda v: v.version_number)
                previous_version_path_for_rollback = latest_version.content_file_path
                next_version_number = latest_version.version_number + 1
            else:
                logger.warning(f"Document {doc_id} has no versions. Treating update as first version creation.")
                next_version_number = 1
                previous_version_path_for_rollback = None

            new_title = title if title is not None else document.title

            base_path = f"{user_id}/{workspace_id}/{doc_id}"
            new_main_content_path = f"{base_path}/content.json"
            new_version_gcs_path = f"{base_path}/v{next_version_number}.json"

            # content_json_str is still needed for GCS storage
            content_json_str = json.dumps(content)

            # Step 1: Upload new version content to GCS
            logger.debug(
                f"Step 1: Uploading new version content to GCS for doc {doc_id}, version {next_version_number}")
            try:
                upload_file_to_gcs_sync(content_json_str, new_version_gcs_path, self.gcs_bucket_name,
                                        'application/json')
                logger.info(f"Uploaded new version content to GCS: {new_version_gcs_path}")
            except Exception as gcs_error:
                logger.error(f"GCS upload failed for new version {next_version_number} of doc {doc_id}: {gcs_error}",
                             exc_info=True)
                raise RuntimeError("Failed to upload new version content to GCS") from gcs_error

            # Step 2: Update main content file in GCS (overwrite)
            logger.debug(f"Step 2: Updating main content file in GCS for doc {doc_id}")
            try:
                upload_file_to_gcs_sync(content_json_str, new_main_content_path, self.gcs_bucket_name,
                                        'application/json')
                main_content_updated_in_gcs = True
                logger.info(f"Updated main content file in GCS: {new_main_content_path}")
            except Exception as gcs_error:
                logger.error(f"GCS update failed for main content file of doc {doc_id}: {gcs_error}", exc_info=True)
                self._rollback_gcs_files([new_version_gcs_path])
                raise RuntimeError("Failed to update main content file in GCS") from gcs_error

            # Step 3: Update vectors in Weaviate
            update_vector_response = None  # Initialize
            logger.debug(f"Step 3: Updating Weaviate vectors for doc {doc_id}")
            try:
                # Pass the original Python dictionary `content` for correct text extraction
                update_vector_response = self.page_vector_service.update_vectors_from_content(
                    tenant_id=tenant_id,
                    doc_id=doc_id,
                    workspace_id=workspace_id,
                    title=new_title,
                    content=content  # Pass the original dict content
                )
                if update_vector_response.get("status") not in ["success", "partial_success"]:
                    logger.error(
                        f"Weaviate vector update partially or fully failed for doc {doc_id}. Response: {update_vector_response}")
                    raise VectorStoreOperationError(
                        f"Vector update failed or had errors: {update_vector_response.get('message')}")

                vector_update_succeeded = True
                logger.info(f"Successfully updated Weaviate vectors for doc {doc_id}")
            except Exception as vector_error:
                logger.error(f"Weaviate vector update failed for doc {doc_id}: {vector_error}", exc_info=True)
                try:
                    if main_content_updated_in_gcs and previous_version_path_for_rollback:
                        prev_content_bytes = get_file_content_sync(previous_version_path_for_rollback,
                                                                   self.gcs_bucket_name)
                        if prev_content_bytes:
                            upload_file_to_gcs_sync(prev_content_bytes, new_main_content_path, self.gcs_bucket_name,
                                                    'application/json')
                            logger.info(f"Restored main GCS file {new_main_content_path} from previous version.")
                        else:
                            logger.error(
                                f"Could not fetch previous content {previous_version_path_for_rollback} to restore main GCS file.")
                            delete_file_from_gcs_sync(new_main_content_path,
                                                      self.gcs_bucket_name)  # Try to delete potentially corrupted main file
                    elif main_content_updated_in_gcs:  # Main content was updated, but no previous version to restore from
                        logger.warning(
                            f"Cannot restore main GCS file for doc {doc_id}, no previous version found. Deleting potentially modified main file.")
                        delete_file_from_gcs_sync(new_main_content_path, self.gcs_bucket_name)
                except Exception as gcs_restore_err:
                    logger.error(f"Failed during GCS restore for main file {new_main_content_path}: {gcs_restore_err}",
                                 exc_info=True)
                self._rollback_gcs_files([new_version_gcs_path])
                raise RuntimeError("Failed to update vectors in Weaviate") from vector_error

            # Step 4: Create new version record and update document metadata in DB
            logger.debug(f"Step 4: Updating database records for doc {doc_id}")
            try:
                current_time = datetime.now(timezone.utc)

                version = DocumentVersion(
                    document_id=doc_id,
                    version_number=next_version_number,
                    content_file_path=new_version_gcs_path,
                    saved_at=current_time,
                    meta_data={
                        "size": doc_size if doc_size is not None else document.meta_data.get("size"),
                        "character_count": character_count if character_count is not None else document.meta_data.get(
                            "character_count"),
                        "block_count": block_count if block_count is not None else document.meta_data.get("block_count")
                    }
                )
                self.db.add(version)

                document.content_file_path = new_main_content_path
                document.updated_at = current_time
                if title is not None:
                    document.title = title
                if icon_url is not None:
                    document.icon_url = icon_url
                if cover_url is not None:
                    document.cover_url = cover_url

                meta = document.meta_data.copy()
                meta["version"] = next_version_number
                # meta["version_count"] = next_version_number # Not typically used like this, version implies count
                meta["vectorization_status"] = update_vector_response.get("status",
                                                                          "unknown") if update_vector_response else "unknown"
                if doc_size is not None: meta["size"] = doc_size
                if character_count is not None: meta["character_count"] = character_count
                if block_count is not None: meta["block_count"] = block_count

                document.meta_data = meta

                workspace = self.db.query(Workspace).filter(Workspace.workspace_id == document.workspace_id).first()
                if workspace:
                    workspace.updated_at = current_time

                self.db.commit()
                # db_changes_committed = True # Not strictly needed for logic flow
                logger.info(f"Successfully committed database updates for doc {doc_id}")

            except SQLAlchemyError as db_error:
                logger.error(f"Database commit failed during document update for doc {doc_id}: {db_error}",
                             exc_info=True)
                self.db.rollback()
                if vector_update_succeeded:
                    self._rollback_weaviate_update(tenant_id, doc_id, workspace_id, original_doc_title,
                                                   previous_version_path_for_rollback)

                try:
                    if main_content_updated_in_gcs and previous_version_path_for_rollback:
                        prev_content_bytes = get_file_content_sync(previous_version_path_for_rollback,
                                                                   self.gcs_bucket_name)
                        if prev_content_bytes:
                            upload_file_to_gcs_sync(prev_content_bytes, new_main_content_path, self.gcs_bucket_name,
                                                    'application/json')
                            logger.info(
                                f"Restored main GCS file {new_main_content_path} from previous version during DB rollback.")
                        else:  # Failed to get previous, try delete current
                            logger.error(
                                f"Could not fetch previous content {previous_version_path_for_rollback} to restore main GCS file during DB rollback. Deleting current.")
                            delete_file_from_gcs_sync(new_main_content_path, self.gcs_bucket_name)
                    elif main_content_updated_in_gcs:  # No previous version, delete current
                        delete_file_from_gcs_sync(new_main_content_path, self.gcs_bucket_name)
                except Exception as gcs_restore_err:
                    logger.error(
                        f"Failed during GCS restore for main file {new_main_content_path} during DB rollback: {gcs_restore_err}",
                        exc_info=True)
                self._rollback_gcs_files([new_version_gcs_path])
                raise RuntimeError("Failed to save document updates to database") from db_error
            except Exception as e:
                logger.error(f"Unexpected error during database record update for doc {doc_id}: {e}", exc_info=True)
                self.db.rollback()
                if vector_update_succeeded:
                    self._rollback_weaviate_update(tenant_id, doc_id, workspace_id, original_doc_title,
                                                   previous_version_path_for_rollback)
                try:  # GCS rollback
                    if main_content_updated_in_gcs and previous_version_path_for_rollback:
                        prev_content_bytes = get_file_content_sync(previous_version_path_for_rollback,
                                                                   self.gcs_bucket_name)
                        if prev_content_bytes:
                            upload_file_to_gcs_sync(prev_content_bytes, new_main_content_path, self.gcs_bucket_name,
                                                    'application/json')
                        else:
                            delete_file_from_gcs_sync(new_main_content_path, self.gcs_bucket_name)
                    elif main_content_updated_in_gcs:
                        delete_file_from_gcs_sync(new_main_content_path, self.gcs_bucket_name)
                except Exception:
                    pass
                self._rollback_gcs_files([new_version_gcs_path])
                raise RuntimeError("Failed to setup database updates") from e

            # --- Success ---
            logger.info(f"Successfully updated document {doc_id}")
            return {
                "status": "success",
                "message": "Document updated successfully",
                "data": {
                    "document_id": str(doc_id),
                    "title": new_title,
                    "main_content_path": new_main_content_path,
                    "version_content_path": new_version_gcs_path,
                    "version_number": next_version_number,
                    "vector_update_response": update_vector_response
                }
            }

        except Exception as e:
            logger.critical(f"Document update failed for doc {doc_id}. Error: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"Failed to update document: {str(e)}",
                "document_id": str(doc_id),
                "data": None
            }

    def delete_document(self, tenant_id: str, doc_id: UUID) -> Dict[str, Any]:
        logger.info(f"Attempting to delete document {doc_id}")
        try:
            document = self.db.query(Document).options(joinedload(Document.versions)).filter(
                Document.document_id == doc_id).first()
            if not document:
                logger.error(f"Document {doc_id} not found for deletion.")
                return {"status": "error", "message": "Document not found"}

            workspace_id = document.workspace_id # For GCS path structure

            # Step 1: Delete from Weaviate
            logger.debug(f"Step 1: Deleting Weaviate vectors for doc {doc_id}")
            try:
                delete_response = self.page_vector_service.delete_vectors(tenant_id=tenant_id, doc_id=doc_id)
                if delete_response.get("status") != "success":
                    logger.warning(
                        f"Weaviate deletion may have failed or partially failed for {doc_id}: {delete_response}")
            except Exception as vector_error:
                logger.error(f"Error deleting Weaviate vectors for {doc_id}: {vector_error}", exc_info=True)

            # Step 2: Delete GCS files (main and all versions)
            logger.debug(f"Step 2: Deleting GCS files for doc {doc_id}")
            paths_to_delete = [document.content_file_path] if document.content_file_path else []
            paths_to_delete.extend([v.content_file_path for v in document.versions if v.content_file_path])

            # Consider GCS prefix deletion if all files for a doc are under a common prefix like:
            # user_id/workspace_id/doc_id/
            # For now, using individual file deletion based on paths_to_delete
            self._rollback_gcs_files(paths_to_delete)  # Reusing this helper effectively deletes the files

            # Step 3: Delete from Database
            logger.debug(f"Step 3: Deleting database records for doc {doc_id}")
            try:
                # Assuming cascade delete is set up for versions and children in SQLAlchemy models
                self.db.delete(document)
                self.db.commit()
                logger.info(f"Successfully deleted database records for doc {doc_id}")
            except SQLAlchemyError as db_error:
                logger.error(f"Database commit failed during document deletion for {doc_id}: {db_error}", exc_info=True)
                self.db.rollback()
                logger.critical(f"INCONSISTENCY: DB deletion failed for {doc_id}, but GCS/Weaviate may be deleted.")
                raise RuntimeError("Failed to delete document from database") from db_error

            logger.info(f"Successfully deleted document {doc_id}")
            return {"status": "success", "message": "Document deleted successfully"}

        except Exception as e:
            logger.critical(f"Document deletion failed for doc {doc_id}. Error: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass  # Ensure rollback is attempted if possible
            return {"status": "error", "message": f"Failed to delete document: {str(e)}"}