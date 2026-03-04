from datetime import datetime
import os
import re
import string
import random
from .BaseController import BaseController
from .ProjectController import ProjectController
from models import ResponseSignal
from fastapi import UploadFile

class DataController(BaseController):

    def __init__(self):
        super().__init__()

    def validate_uploaded_file(self, file: UploadFile):
        if file.content_type not in self.app_settings.FILE_ALLOWED_TYPES:
            return False, ResponseSignal.FILE_TYPE_NOT_ALLOWED.value
            
        if file.size > self.app_settings.FILE_MAX_SIZE * 1024 * 1024:
            return False, ResponseSignal.FILE_SIZE_EXCEEDS_LIMIT.value
        
        return True, ResponseSignal.FILE_VALIDATED_SUCCESSFULLY.value

    def generate_random_string(self, length: int = 4):
        """Generates a short random string to prevent filename collisions."""
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

    def get_clean_filename(self, filename: str):
        """Replaces non-alphanumeric characters with underscores."""
        clean_file_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
        return clean_file_name

    def generate_unique_filePath(self, original_filename: str, project_id: str):
        project_path = ProjectController().get_project_path(project_id=project_id)
        clean_filename = self.get_clean_filename(filename=original_filename)
        
        # Adding timestamp for chronological sorting (YearMonthDay_HourMinute)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        
        # Initial filename format: 20260302_2256_data
        new_filename = f"{timestamp}_{clean_filename}"
        new_file_path = os.path.join(project_path, new_filename)

        # If file exists, append a short random key for uniqueness
        while os.path.exists(new_file_path):
            random_key = self.generate_random_string(length=4)
            new_filename = f"{timestamp}_{random_key}_{clean_filename}"
            new_file_path = os.path.join(project_path, new_filename)

        return new_file_path, new_filename