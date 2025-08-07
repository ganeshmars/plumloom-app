# import logging
# from typing import Set
# from uuid import UUID
# from sqlalchemy import event, create_engine
# from sqlalchemy.orm import mapper, sessionmaker, Session
# from app.models.document import Document
# from app.models.chat_conversation import ChatConversation
# from app.models.chat_message import ChatMessage
# from app.models.workspace import Workspace
# from app.models.users import User
# from app.services.recent_items_service import RecentItemsService
# from app.core.config import get_settings
#
# settings = get_settings()
#
# # Create a dedicated sync engine for background tasks
# background_engine = create_engine(
#     f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}",
#     pool_pre_ping=True,
#     pool_size=5,
#     max_overflow=10
# )
#
# # Create a session factory for background tasks
# background_session_factory = sessionmaker(
#     background_engine,
#     class_=Session,
#     expire_on_commit=False
# )
#
# logger = logging.getLogger(__name__)
#
# # Configure logging
# logger = logging.getLogger(__name__)
#
#
# def _update_document_in_redis(session: Session, document: Document):
#     """Helper function to update/add document in Redis"""
#     # Get the workspace name
#     workspace = session.get(Workspace, document.workspace_id)
#
#     logger.info(f"Updating Redis for document {document.document_id}, user_id: {document.user_id}")
#
#     # Prepare item data
#     item_data = {
#         'item_id': str(document.document_id),
#         'title': document.title or 'Untitled Document',
#         'workspace_id': str(document.workspace_id),
#         'workspace_name': workspace.name if workspace else 'Unknown Workspace',
#         'parent_id': str(document.parent_id) if document.parent_id else None,
#         'updated_at': document.updated_at.isoformat(),
#         'item_type': 'Page'
#     }
#
#     # Update in Redis using user_id
#     service = RecentItemsService(session)
#     service.update_recent_item_sync(document.user_id, 'document', item_data)
#
#
# def _update_chat_in_redis(session: Session, chat: ChatConversation):
#     """Helper function to update/add chat in Redis"""
#     # Get the workspace name
#     workspace = session.get(Workspace, chat.workspace_id)
#
#     logger.info(
#         f"Updating Redis for chat {chat.conversation_id}, user_id: {chat.user_id}, updated_at: {chat.updated_at.isoformat()}")
#
#     # Prepare item data
#     item_data = {
#         'item_id': str(chat.conversation_id),
#         'title': chat.conversation_title or 'Untitled Chat',
#         'workspace_id': str(chat.workspace_id),
#         'workspace_name': workspace.name if workspace else 'Unknown Workspace',
#         'parent_id': None,
#         'updated_at': chat.updated_at.isoformat(),
#         'item_type': 'Conversation'
#     }
#
#     # Update in Redis using user_id
#     service = RecentItemsService(session)  # This service uses sync_redis internally for its sync methods
#     service.update_recent_item_sync(chat.user_id, 'chat', item_data)
#
#
# def _update_workspace_items_in_redis(session: Session, workspace: Workspace):
#     """Update all items in Redis related to a workspace when the workspace is updated"""
#     logger.info(f"Updating Redis items for workspace {workspace.workspace_id}")
#
#     # Get all documents in this workspace
#     documents = session.query(Document).filter(Document.workspace_id == workspace.workspace_id).all()
#     for doc in documents:
#         _update_document_in_redis(session, doc)
#         logger.info(f"Updated Redis for document {doc.document_id} due to workspace change")
#
#     # Get all chats in this workspace
#     chats = session.query(ChatConversation).filter(ChatConversation.workspace_id == workspace.workspace_id).all()
#     for chat in chats:
#         _update_chat_in_redis(session, chat)
#         logger.info(f"Updated Redis for chat {chat.conversation_id} due to workspace change")
#
#     logger.info(
#         f"Completed Redis updates for workspace {workspace.workspace_id} with {len(documents)} documents and {len(chats)} chats")
#
#
# # Track documents that need Redis updates per session
# class SessionRedisTracker:
#     def __init__(self):
#         self.pending_updates = {}
#
#     def track_document(self, session_id: int, doc_id: UUID):
#         """Track a document for Redis update in a specific session"""
#         if session_id not in self.pending_updates:
#             self.pending_updates[session_id] = set()
#         self.pending_updates[session_id].add(doc_id)
#
#     def get_documents(self, session_id: int) -> Set[UUID]:
#         """Get documents to update for a session"""
#         return self.pending_updates.get(session_id, set())
#
#     def clear_session(self, session_id: int):
#         """Clear tracked documents for a session"""
#         self.pending_updates.pop(session_id, None)
#
#
# _redis_tracker = SessionRedisTracker()
# _workspace_tracker = SessionRedisTracker()
#
#
# def track_document_changes(session):
#     """Track document changes to update Redis after commit"""
#     session_id = id(session)
#     # Track inserted and updated documents
#     for obj in session.new | session.dirty:
#         if isinstance(obj, Document):
#             _redis_tracker.track_document(session_id, obj.document_id)
#
#
# def track_workspace_changes(session):
#     """Track workspace changes to update Redis after commit"""
#     session_id = id(session)
#     # Track updated workspaces
#     for obj in session.new | session.dirty:
#         if isinstance(obj, Workspace):
#             _workspace_tracker.track_document(session_id, obj.workspace_id)
#
#
# # Listen for after_flush on Session
# @event.listens_for(Session, 'after_flush')
# def handle_session_flush(session, flush_context):
#     """After flush, track which documents need Redis updates"""
#     track_document_changes(session)
#     track_workspace_changes(session)
#
#
# @event.listens_for(Session, 'after_commit')
# def handle_session_commit(session):
#     """Handle session commit - updates Redis after successful database commit"""
#     session_id = id(session)
#     pending_doc_updates = _redis_tracker.get_documents(session_id)
#     pending_workspace_updates = _workspace_tracker.get_documents(session_id)
#
#     # Process document updates
#     if pending_doc_updates:
#         try:
#             with background_session_factory() as redis_session:
#                 # Get all documents that were modified
#                 documents = redis_session.query(Document).filter(
#                     Document.document_id.in_(pending_doc_updates)
#                 ).all()
#
#                 # Update Redis for each document
#                 for doc in documents:
#                     _update_document_in_redis(redis_session, doc)
#                     logger.info(f"Successfully updated document {doc.document_id} in Redis after DB commit")
#                 redis_session.commit()
#
#                 # Clear the pending updates for this session
#                 _redis_tracker.clear_session(session_id)
#         except Exception as e:
#             logger.error(f"Error updating documents in Redis after commit: {str(e)}")
#         finally:
#             # Always clear session tracking to prevent memory leaks
#             _redis_tracker.clear_session(session_id)
#
#     # Process workspace updates
#     if pending_workspace_updates:
#         try:
#             with background_session_factory() as redis_session:
#                 # Get all workspaces that were modified
#                 workspaces = redis_session.query(Workspace).filter(
#                     Workspace.workspace_id.in_(pending_workspace_updates)
#                 ).all()
#
#                 # For each updated workspace, update all related items in Redis
#                 for workspace in workspaces:
#                     _update_workspace_items_in_redis(redis_session, workspace)
#                 redis_session.commit()
#
#                 # Clear the pending updates for this session
#                 _workspace_tracker.clear_session(session_id)
#         except Exception as e:
#             logger.error(f"Error updating workspace-related items in Redis after commit: {str(e)}")
#         finally:
#             # Always clear session tracking to prevent memory leaks
#             _workspace_tracker.clear_session(session_id)
#
#
# # Document event handlers
# @event.listens_for(Document, 'after_insert')
# def handle_document_insert(mapper, connection, target):
#     """Handle document insert"""
#     # This can be handled by handle_session_commit and _redis_tracker
#     # or by a direct call like handle_document_update if specific immediate action is needed.
#     # For now, let _redis_tracker handle general document inserts/updates.
#     pass
#
#
# # Workspace event handlers
# @event.listens_for(Workspace, 'after_update')
# def handle_workspace_update(mapper, connection, target):
#     """Handle workspace update - this will update all related items in Redis"""
#     try:
#         with background_session_factory() as session:
#             refreshed_workspace = session.get(Workspace, target.workspace_id)
#             if refreshed_workspace:
#                 _update_workspace_items_in_redis(session, refreshed_workspace)
#                 session.commit()
#                 logger.info(f"Successfully processed workspace update for {refreshed_workspace.workspace_id} in Redis")
#             else:
#                 logger.warning(
#                     f"Workspace {target.workspace_id} not found in background session for update processing.")
#     except Exception as e:
#         logger.error(f"Error updating workspace-related items in Redis: {str(e)}")
#
#
# @event.listens_for(Document, 'after_update')
# def handle_document_update(mapper, connection, target):
#     """Handle document update"""
#
#     try:
#         with background_session_factory() as session:
#             refreshed_document = session.get(Document, target.document_id)
#             if refreshed_document:
#                 _update_document_in_redis(session, refreshed_document)
#                 session.commit()
#                 logger.info(f"Successfully updated document {refreshed_document.document_id} in Redis")
#             else:
#                 logger.warning(f"Document {target.document_id} not found in background session for update processing.")
#     except Exception as e:
#         logger.error(f"Error updating document {target.document_id} in Redis: {str(e)}")
#
#
# @event.listens_for(Document, 'after_delete')
# def handle_document_delete(mapper, connection, target):
#     """Handle document delete"""
#
#     try:
#         with background_session_factory() as session:
#             service = RecentItemsService(session)
#             service.remove_item_sync(target.user_id, 'document', str(target.document_id))
#             session.commit()
#             logger.info(f"Successfully removed document {target.document_id} from Redis")
#     except Exception as e:
#         logger.error(f"Error removing document {target.document_id} from Redis: {str(e)}")
#
#
# # Chat conversation event handlers
# @event.listens_for(ChatConversation, 'after_insert')
# def handle_chat_insert(mapper, connection, target: ChatConversation):
#     """Handle chat insert"""
#     try:
#         with background_session_factory() as session:
#             # Re-fetch in new session to ensure all relationships and defaults are loaded if needed by _update_chat_in_redis
#             # Though for insert, target should be quite complete.
#             inserted_chat = session.get(ChatConversation, target.conversation_id)
#             if inserted_chat:
#                 _update_chat_in_redis(session, inserted_chat)
#                 session.commit()
#                 logger.info(f"Successfully processed chat insert for {inserted_chat.conversation_id} in Redis")
#             else:
#                 logger.warning(f"ChatConversation {target.conversation_id} (insert) not found in background session.")
#     except Exception as e:
#         logger.error(f"Error processing chat insert for {target.conversation_id} in Redis: {str(e)}")
#
#
# @event.listens_for(ChatConversation, 'after_update')
# def handle_chat_update(mapper, connection, target: ChatConversation):
#     """Handle chat update (e.g., title change, status change, or explicit updated_at)"""
#     try:
#         with background_session_factory() as session:
#             refreshed_chat = session.get(ChatConversation, target.conversation_id)
#             if refreshed_chat:
#                 _update_chat_in_redis(session, refreshed_chat)
#                 session.commit()
#                 logger.info(f"Successfully processed chat update for {refreshed_chat.conversation_id} in Redis")
#             else:
#                 logger.warning(f"ChatConversation {target.conversation_id} (update) not found in background session.")
#     except Exception as e:
#         logger.error(f"Error processing chat update for {target.conversation_id} in Redis: {str(e)}")
#
#
# @event.listens_for(ChatConversation, 'after_delete')
# def handle_chat_delete(mapper, connection, target: ChatConversation):
#     """Handle chat delete"""
#     try:
#         with background_session_factory() as session:
#             service = RecentItemsService(session)
#             # target.user_id should still be accessible even if the object is being deleted
#             service.remove_item_sync(target.user_id, 'chat', str(target.conversation_id))
#             session.commit()
#             logger.info(f"Successfully removed chat {target.conversation_id} from Redis")
#     except Exception as e:
#         logger.error(f"Error removing chat {target.conversation_id} from Redis: {str(e)}")
#
#
# # ChatMessage event handler
# @event.listens_for(ChatMessage, 'after_insert')
# def handle_chat_message_insert(mapper, connection, target: ChatMessage):
#     """Handle chat message insert, then update parent conversation in Redis."""
#     logger.info(
#         f"New ChatMessage {target.message_id} inserted for conversation {target.conversation_id}. Triggering Redis update for conversation.")
#     try:
#         with background_session_factory() as session:
#             # Fetch the parent ChatConversation
#             parent_conversation = session.get(ChatConversation, target.conversation_id)
#             if parent_conversation:
#                 # The ChatService should have already updated parent_conversation.updated_at
#                 # This fetch ensures we have the latest state from DB.
#                 _update_chat_in_redis(session, parent_conversation)
#                 session.commit()
#                 logger.info(
#                     f"Successfully updated Redis for conversation {parent_conversation.conversation_id} due to new message {target.message_id}")
#             else:
#                 logger.warning(
#                     f"Parent ChatConversation {target.conversation_id} not found for new message {target.message_id} during Redis update.")
#     except Exception as e:
#         logger.error(
#             f"Error updating Redis for conversation {target.conversation_id} after new message {target.message_id}: {str(e)}")
