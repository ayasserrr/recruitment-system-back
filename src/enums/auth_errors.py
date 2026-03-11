"""
Error Messages Enums for Authentication System

This module contains all error messages used throughout the authentication system
to ensure consistency and maintainability.
"""
from enum import Enum


class AuthErrorMessages(str, Enum):
    """Authentication error messages enum"""
    
    # Signup Errors
    EMAIL_ALREADY_EXISTS = "This email is already registered. Please use a different email or try logging in."
    MISSING_REQUIRED_FIELDS = "Missing required fields: {}. Please provide all required information."
    MISSING_COMPANY_NAME = "company name"
    MISSING_FIRST_NAME = "first name"
    MISSING_LAST_NAME = "last name"
    
    # Login Errors
    EMAIL_NOT_FOUND = "No account found with this email address. Please check your email or sign up for a new account."
    INCORRECT_PASSWORD = "Incorrect password. Please try again or use the forgot password feature."
    
    # Server Errors
    SIGNUP_SERVER_ERROR = "An error occurred during signup. Please try again later or contact support if the problem persists."
    LOGIN_SERVER_ERROR = "Login failed due to a technical issue. Please try again later or contact support if the problem persists."
    
    # Token Errors
    INVALID_CREDENTIALS = "Could not validate credentials"
    COMPANY_NOT_FOUND = "Company not found"
    
    # Generic Messages
    INVALID_EMAIL_OR_PASSWORD = "Invalid email or password"  # Fallback for security
    
    @classmethod
    def get_missing_fields_message(cls, missing_fields: list) -> str:
        """Generate message for missing required fields"""
        fields_str = ", ".join(missing_fields)
        return cls.MISSING_REQUIRED_FIELDS.value.format(fields_str)


class HTTPStatusCodes:
    """HTTP status codes constants"""
    
    # Success
    OK = 200
    CREATED = 201
    
    # Client Errors
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    NOT_FOUND = 404
    
    # Server Errors
    INTERNAL_SERVER_ERROR = 500


class AuthErrorDetails:
    """Detailed error information for responses"""
    
    @staticmethod
    def email_exists() -> dict:
        return {
            "detail": AuthErrorMessages.EMAIL_ALREADY_EXISTS.value,
            "error_code": "AUTH_EMAIL_EXISTS",
            "suggestion": "Try logging in with this email or use a different email address"
        }
    
    @staticmethod
    def missing_fields(missing_fields: list) -> dict:
        return {
            "detail": AuthErrorMessages.get_missing_fields_message(missing_fields),
            "error_code": "AUTH_MISSING_FIELDS",
            "missing_fields": missing_fields,
            "suggestion": "Please provide all required fields to complete registration"
        }
    
    @staticmethod
    def email_not_found() -> dict:
        return {
            "detail": AuthErrorMessages.EMAIL_NOT_FOUND.value,
            "error_code": "AUTH_EMAIL_NOT_FOUND",
            "suggestion": "Check your email address or sign up for a new account"
        }
    
    @staticmethod
    def incorrect_password() -> dict:
        return {
            "detail": AuthErrorMessages.INCORRECT_PASSWORD.value,
            "error_code": "AUTH_INCORRECT_PASSWORD",
            "suggestion": "Try again or use the forgot password feature"
        }
    
    @staticmethod
    def signup_server_error() -> dict:
        return {
            "detail": AuthErrorMessages.SIGNUP_SERVER_ERROR.value,
            "error_code": "AUTH_SIGNUP_ERROR",
            "suggestion": "Try again later or contact support if the problem persists"
        }
    
    @staticmethod
    def login_server_error() -> dict:
        return {
            "detail": AuthErrorMessages.LOGIN_SERVER_ERROR.value,
            "error_code": "AUTH_LOGIN_ERROR",
            "suggestion": "Try again later or contact support if the problem persists"
        }
    
    @staticmethod
    def invalid_credentials() -> dict:
        return {
            "detail": AuthErrorMessages.INVALID_CREDENTIALS.value,
            "error_code": "AUTH_INVALID_TOKEN",
            "suggestion": "Please login again to get a new token"
        }
