from pydantic import BaseModel
from typing import Optional

class processRequest(BaseModel):
    file_id: str  # The file_id returned from upload endpoint
    chunk_size: Optional[int] = 100
    overlap_size: Optional[int] = 20
    do_reset: Optional[int] = 0
    

