import enum


class EmploymentType(str, enum.Enum):
    FULL_TIME  = "Full-time"
    PART_TIME  = "Part-time"
    CONTRACT   = "Contract"
    INTERNSHIP = "Internship"
    FREELANCE  = "Freelance"
