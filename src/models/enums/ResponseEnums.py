from enum import Enum

class ResponseSignal(Enum):
    FILE_TYPE_NOT_ALLOWED = "File type is not allowed"
    FILE_SIZE_EXCEEDS_LIMIT = "File size exceeds the maximum allowed size"
    FILE_ALREADY_EXISTS = "Candidate already has a CV for this job"
    FILE_UPLOADED_SUCCESSFULLY = "File uploaded successfully"
    FILE_UPLOADED_FAILED = "File upload failed"
    FILE_VALIDATED_SUCCESSFULLY = "File is valid"
    PROCESSING_FAILED = "File processing failed"
    PROCESSING_SUCCESSFUL = "File processing successful"