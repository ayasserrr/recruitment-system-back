from helpers import get_settings, settings
import os
import random
import string
from pathlib import Path

class BaseController:
    def __init__(self):
        self.app_settings = get_settings()
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.files_dir = os.path.join(self.base_dir,"assets","files")
        self.uploads_dir = Path(self.base_dir) / "assets" / "uploads"
        self.legacy_uploads_dir = Path(self.base_dir).parent / "uploads"

    def generate_random_string(self, length: int = 8):
        return ''.join(random.choices(string.ascii_letters + string.digits, k=length))
        
