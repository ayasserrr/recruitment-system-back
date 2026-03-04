from fastapi import APIRouter, Depends, UploadFile, status
from fastapi.responses import JSONResponse
import os
import aiofiles
import logging
from helpers import get_settings, settings
from controllers import DataController, ProjectController, ProcessController
from models import ResponseSignal
from .schemas import processRequest

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
    is_valid, result_signal = await data_controller.validate_uploaded_file(file=file)

    if not is_valid:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"signal": result_signal}
        )
    
    # 2. Generate the unique file path (now includes timestamp + clean name)
    file_path, file_id = data_controller.generate_unique_filePath(
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
            "file_id": file_id,
        }
    )

@data_router.post("/process/{project_id}")
async def process_endpoint(project_id: str, process_request: processRequest):
    
    file_id = process_request.file_id  # Use the file_id from request body
    chunk_size = process_request.chunk_size
    overlap_size = process_request.overlap_size

    process_controller = ProcessController(project_id=project_id) 

    try:
        print(f"Process endpoint called with file_id: {file_id}")
        file_content = process_controller.get_file_content(file_id=file_id)
    except Exception as e:
        logger.error(f"Error loading file {file_id}: {e}")
        print(f"Exception details: {e}")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"signal": "File not found or cannot be processed", "error": str(e)}
        )

    file_chunks = process_controller.process_file_content(file_content=file_content, file_id=file_id, chunk_size=chunk_size, overlap_size=overlap_size)

    if file_chunks is None or len(file_chunks) == 0:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"signal": ResponseSignal.PROCESSING_FAILED.value}
        )
    
    # Convert chunks to serializable format
    chunks_data = []
    for chunk in file_chunks:
        chunks_data.append({
            "content": chunk.page_content,
            "metadata": chunk.metadata
        })
    
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "signal": ResponseSignal.PROCESSING_SUCCESSFUL.value,
            "chunks": chunks_data,
            "total_chunks": len(chunks_data)
        }
    )

@data_router.get("/files/{project_id}")
async def list_files(project_id: str):
    """List all files in a project directory - for debugging"""
    project_controller = ProjectController()
    project_path = project_controller.get_project_path(project_id=project_id)
    
    try:
        files = os.listdir(project_path)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "project_path": project_path,
                "files": files,
                "total_files": len(files)
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": f"Project directory not found: {e}"}
        )