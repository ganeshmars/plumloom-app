from typing import List, Optional, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Form, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from fastapi.responses import JSONResponse

from app.core.database import get_db
from app.models.icon import Icon, IconCategory, IconType, IconMode
from app.schemas.icon import IconResponse, GroupedIconsResponse
from app.services.icon_service import list_icons, create_icon
from app.core.storage import upload_file_to_gcs
from app.core.auth import validate_session
from app.core.constants import GCS_STORAGE_BUCKET
from app.core.logging_config import logger



router = APIRouter(prefix="/icons", tags=["icons"])

@router.get("/", response_model=GroupedIconsResponse)
async def get_icons(
    icon_type: Optional[IconType] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(validate_session),
):
    """
    Get all icons with optional filtering, grouped by type.
    
    Returns:
    - user: List of user icons
    - app: List of application icons
    """
    # Initialize the response object with empty lists
    response = GroupedIconsResponse(user=[], app=[])
    
    if icon_type == IconType.USER or icon_type is None:
        user_query = select(Icon).where(
            Icon.type == IconType.USER,
            Icon.user_id == current_user["id"]
        )
        result = await db.execute(user_query)
        user_icons = result.scalars().all()
        response.user = user_icons
    
    if icon_type == IconType.APP or icon_type is None:
        app_query = select(Icon).where(
            Icon.type == IconType.APP,
            Icon.user_id == None
        )
        result = await db.execute(app_query)
        app_icons = result.scalars().all()
        response.app = app_icons
    
    return response


@router.post("/", response_model=IconResponse)
async def create_new_icon(
    name: Optional[str] = Form(None),
    icon_type: IconType = Form(None),
    file: UploadFile = File(...),
    tags: List[str] = Form([]),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(validate_session)
):
    """Create multiple icons and upload to GCS"""
    user_id = current_user["id"]
    logger.info(f"Icon tags: {tags}")

    # Parse tags from comma-separated string to list
    tag_list = []
    if tags:
        tag_list = [tag.strip() for tag in tags[0].split(',') if tag.strip()]
    
    logger.info(f"Icon tags: {tag_list}")

    # Map extension to format
    format_mapping = {
        "svg": "svg",
        "png": "png",
        "webp": "webp",
        "jpg": "jpg",
        "jpeg": "jpg",
        "gif": "gif"
    }
    
    icon_name = name if name else file.filename.split(".")[0]
    file_extension = file.filename.split(".")[-1].lower()
    
    if file_extension not in format_mapping:
        return JSONResponse(status_code=400, content={"error": f"Unsupported file format: {file_extension} in file {file.filename}"})
    
    # Determine path and upload
    gcs_path = f"icons/user/{user_id}/{file.filename.split('.')[0]}.{file_extension}"
    content = await file.read()
    url = await upload_file_to_gcs(content, gcs_path, GCS_STORAGE_BUCKET, file.content_type)

    # Create icon in database
    icon = await create_icon(db, {
        "name": icon_name,
        "type": IconType.USER,
        "user_id": user_id,
        "mode": IconMode.LIGHT,
        "gcs_path": gcs_path,
        "url": url,
        "file_format": format_mapping[file_extension],
        "file_size": len(content),
        "meta_data": {},
        "tags": tag_list
    })
    
    return icon


@router.post("/create-app-icon", response_model=List[IconResponse])
async def create_app_icon(
        files: List[UploadFile] = File(...),
        tags: List[str] = Form([]),
        db: AsyncSession = Depends(get_db),
        current_user: dict = Depends(validate_session)
):
    """Create multiple icons and upload to GCS"""
    # user_id = current_user["id"]
    logger.info(f"Icon tags: {tags}")

    # Parse tags from comma-separated string to list
    tag_list = []
    if tags:
        tag_list = [tag.strip() for tag in tags[0].split(',') if tag.strip()]
    
    logger.info(f"Icon tags: {tag_list}")

    # Map extension to format
    format_mapping = {
        "svg": "svg",
        "png": "png",
        "webp": "webp",
        "jpg": "jpg",
        "jpeg": "jpg",
        "gif": "gif"
    }
    
    created_icons = []
    
    for file in files:
        icon_name = file.filename.split(".")[0]
        file_extension = file.filename.split(".")[-1].lower()
        
        if file_extension not in format_mapping:
            return JSONResponse(status_code=400,
                                content={"error": f"Unsupported file format: {file_extension} in file {file.filename}"})

        # Determine path and upload
        gcs_path = f"icons/app/{file.filename.split('.')[0]}.{file_extension}"
        content = await file.read()
        url = await upload_file_to_gcs(content, gcs_path, GCS_STORAGE_BUCKET, file.content_type)

        # Create icon in database
        icon = await create_icon(db, {
            "name": icon_name,
            "type": IconType.APP,
            "user_id": None,
            "mode": IconMode.LIGHT,
            "gcs_path": gcs_path,
            "url": url,
            "file_format": format_mapping[file_extension],
            "file_size": len(content),
            "meta_data": {},
            "tags": tag_list
        })
        created_icons.append(icon)
    
    return created_icons
