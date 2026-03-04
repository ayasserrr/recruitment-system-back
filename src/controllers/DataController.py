from datetime import datetime
import re
import random
import string

from .BaseController import BaseController
from .CompanyController import CompanyController
from models import ResponseSignal
from fastapi import UploadFile

class DataController(BaseController):

    def __init__(self):
        super().__init__()

    async def validate_uploaded_file(self, file: UploadFile):
        if file.content_type not in self.app_settings.FILE_ALLOWED_TYPES:
            return False, ResponseSignal.FILE_TYPE_NOT_ALLOWED.value

        if not file.filename:
            return False, ResponseSignal.FILE_TYPE_NOT_ALLOWED.value

        filename_lower = file.filename.lower()
        if not (filename_lower.endswith(".pdf") or filename_lower.endswith(".txt")):
            return False, ResponseSignal.FILE_TYPE_NOT_ALLOWED.value
            
        # Get file size by reading the file content
        file_content = await file.read()
        file_size = len(file_content)
        await file.seek(0)  # Reset file pointer
        
        if file_size > self.app_settings.FILE_MAX_SIZE * 1024 * 1024:
            return False, ResponseSignal.FILE_SIZE_EXCEEDS_LIMIT.value
        
        return True, ResponseSignal.FILE_VALIDATED_SUCCESSFULLY.value

    def generate_random_string(self, length: int = 4):
        """Generates a short random string to prevent filename collisions."""
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

    def get_clean_filename(self, filename: str):
        """Replaces non-alphanumeric characters with underscores."""
        clean_file_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
        return clean_file_name

    def candidate_has_cv(self, company_id: str, job_id: str, candidate_id: str) -> bool:
        cc = CompanyController()
        candidate_dirs = [
            cc.get_candidate_path(company_id=company_id, job_id=job_id, candidate_id=candidate_id, create=False),
            (cc.legacy_uploads_dir / company_id) / job_id / candidate_id,
        ]

        for candidate_dir in candidate_dirs:
            if not candidate_dir.exists():
                continue
            for existing in candidate_dir.iterdir():
                if existing.is_file() and existing.suffix.lower() in {".pdf", ".txt"}:
                    return True

        return False

    def delete_candidate_cv(self, company_id: str, job_id: str, candidate_id: str) -> int:
        deleted = 0
        cc = CompanyController()
        candidate_dirs = [
            cc.get_candidate_path(company_id=company_id, job_id=job_id, candidate_id=candidate_id, create=False),
            (cc.legacy_uploads_dir / company_id) / job_id / candidate_id,
        ]

        for candidate_dir in candidate_dirs:
            if not candidate_dir.exists():
                continue
            for existing in candidate_dir.iterdir():
                if existing.is_file() and existing.suffix.lower() in {".pdf", ".txt"}:
                    existing.unlink(missing_ok=True)
                    deleted += 1

        return deleted

    def generate_unique_filePath(self, original_filename: str, company_id: str, job_id: str, candidate_id: str):
        candidate_path = CompanyController().get_candidate_path(
            company_id=company_id,
            job_id=job_id,
            candidate_id=candidate_id,
        )

        clean_filename = self.get_clean_filename(filename=original_filename)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        new_filename = f"{timestamp}_{clean_filename}"
        new_file_path = candidate_path / new_filename

        while new_file_path.exists():
            random_key = self.generate_random_string(length=4)
            new_filename = f"{timestamp}_{random_key}_{clean_filename}"
            new_file_path = candidate_path / new_filename

        return str(new_file_path), new_filename