from .BaseController import BaseController
from .ProjectController import ProjectController
from models import ProcessingEnums
import os
from langchain_community.document_loaders import TextLoader
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
class ProcessController(BaseController):
    def __init__(self, project_id: str):
        super().__init__()

        self.project_id = project_id
        self.project_path = ProjectController().get_project_path(project_id=project_id)

    def get_file_extension(self, file_id: str):
        return os.path.splitext(file_id)[-1]

    
    def get_file_loader(self, file_id: str):
        file_extension = self.get_file_extension(file_id=file_id)
        file_path = os.path.join(self.project_path, file_id)
        
        print(f"File extension: {file_extension}")
        print(f"File path: {file_path}")
        print(f"File exists: {os.path.exists(file_path)}")
        print(f"Is file: {os.path.isfile(file_path)}")

        if file_extension == ProcessingEnums.TXT.value:
            return TextLoader(file_path, encoding="utf-8")
        elif file_extension == ProcessingEnums.PDF.value:
            try:
                return PyMuPDFLoader(file_path)
            except Exception as e:
                print(f"PyMuPDFLoader failed: {e}")
                # Try alternative PDF loader
                from langchain_community.document_loaders import PyPDFLoader
                return PyPDFLoader(file_path)
        else:
            raise ValueError(f"Unsupported file type: {file_extension}")
        
    def get_file_content(self, file_id: str):
        print(f"Looking for file: {file_id}")
        print(f"Project path: {self.project_path}")
        print(f"Full file path: {os.path.join(self.project_path, file_id)}")
        print(f"File exists: {os.path.exists(os.path.join(self.project_path, file_id))}")
        
        loader = self.get_file_loader(file_id=file_id)
        return loader.load()
    
    def process_file_content(self, file_content: list, file_id: str,
                             chunk_size: int = 100, overlap_size: int = 20):
        Text_splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size,
                                                        chunk_overlap=overlap_size,
                                                        length_function=len
                                                        )
        file_content_texts = [
            rec.page_content 
            for rec in file_content
        ]

        file_content_metadata = [
            rec.metadata 
            for rec in file_content
        ]

        chuncks = Text_splitter.create_documents(file_content_texts, metadatas = file_content_metadata)
        
        return chuncks