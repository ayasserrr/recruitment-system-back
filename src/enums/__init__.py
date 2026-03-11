"""
Enums package for the Recruitment System

This package contains all enums and constants used throughout the application
for better code organization and maintainability.
"""

from .auth_errors import AuthErrorMessages, HTTPStatusCodes, AuthErrorDetails

__all__ = [
    "AuthErrorMessages",
    "HTTPStatusCodes", 
    "AuthErrorDetails"
]
