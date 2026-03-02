from .BaseController import BaseController
from .ProjectController import ProjectController
from models import ResponseSignal
from fastapi import UploadFile
import os
import re

class DataController(BaseController):

    def __init__(self):
        super().__init__()

    def validate_uploaded_file(self, file: UploadFile):
        if file.content_type not in self.app_settings.FILE_ALLOWED_TYPES:
            return False, ResponseSignal.FILE_TYPE_NOT_ALLOWED.value
        if file.size > self.app_settings.FILE_MAX_SIZE * 1024 * 1024:
            return False, ResponseSignal.FILE_SIZE_EXCEEDS_LIMIT.value
        
        return True, ResponseSignal.FILE_VALIDATED_SUCCESSFULLY.value
    
    def generate_unique_filename(self, original_filename: str, project_id: str):
        
        random_key = self.generate_random_string()
        project_path = ProjectController().get_project_path(project_id = project_id )
        clean_filename = self.get_clean_filename(filename=original_filename)

        new_file_path = os.path.join(project_path, f"{random_key}_{clean_filename}")

        while os.path.exists(new_file_path):
            random_key = self.generate_random_string()
            new_file_path = os.path.join(project_path, f"{random_key}_{clean_filename}")

        return new_file_path    


    def get_clean_filename(self, filename: str):
        
        clean_file_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)

        return clean_file_name