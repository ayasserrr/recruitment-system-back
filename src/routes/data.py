from fastapi import APIRouter, Depends, UploadFile, status
from fastapi.responses import JSONResponse
import os
import aiofiles
import logging
from helpers import get_settings, settings
from controllers import DataController, ProjectController
from models import ResponseSignal

logger = logging.getLogger("uvicorn.error")

data_router = APIRouter(
    prefix="/api/v1/data",
    tags=["data"],
)

@data_router.post("/upload/{project_id}")
async def upload_data(project_id: str, file: UploadFile, app_settings: settings = Depends(get_settings)):
    
    # Initialize Controller
    data_controller = DataController()
    
    # 1. Validate file (type and size)
    is_valid, result_signal = data_controller.validate_uploaded_file(file=file)

    if not is_valid:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"signal": result_signal}
        )
    
    # 2. Generate the unique file path (now includes timestamp + clean name)
    file_path = data_controller.generate_unique_filename(
        original_filename=file.filename,
        project_id=project_id
    )
    
    try:
        # 3. Ensure the directory exists before saving
        # This prevents FileNotFoundError if the project folder is missing
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # 4. Save file using chunks
        async with aiofiles.open(file_path, 'wb') as f:
            while chunk := await file.read(app_settings.FILE_DEFAULT_CHUNK_SIZE):
                await f.write(chunk)
                
    except Exception as e:
        logger.error(f"Error saving file for project {project_id}: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"signal": ResponseSignal.FILE_UPLOAD_FAILED.value}
        )

    # 5. Success Response
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "signal": ResponseSignal.FILE_UPLOADED_SUCCESSFULLY.value,
            "file_name": os.path.basename(file_path) # Returns the new clean name
        }
    )