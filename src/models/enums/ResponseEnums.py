from enum import Enum

class ResponseSignal(Enum):
    FILE_TYPE_NOT_ALLOWED = "File type is not allowed"
    FILE_SIZE_EXCEEDS_LIMIT = "File size exceeds the maximum allowed size"
    FILE_UPLOADED_SUCCESSFULLY = "File uploaded successfully"
    FILE_UPLOADED_FAILED = "File upload failed"
    FILE_VALIDATED_SUCCESSFULLY = "File is valid"