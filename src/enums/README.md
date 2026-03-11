# Enums Package Documentation

## Overview

The `enums` package provides centralized error messages and status codes for the Recruitment System authentication module.

## Files

### `__init__.py`
Package initialization file that exports all enums and error classes.

### `auth_errors.py`
Contains all authentication-related enums and error handling utilities.

## Classes

### `AuthErrorMessages` (Enum)
Contains all user-facing error messages as an enum for consistency.

**Values:**
- `EMAIL_ALREADY_EXISTS` - Email already registered message
- `MISSING_REQUIRED_FIELDS` - Template for missing fields message
- `MISSING_COMPANY_NAME` - "company name"
- `MISSING_FIRST_NAME` - "first name"  
- `MISSING_LAST_NAME` - "last name"
- `EMAIL_NOT_FOUND` - No account found message
- `INCORRECT_PASSWORD` - Incorrect password message
- `SIGNUP_SERVER_ERROR` - Signup server error message
- `LOGIN_SERVER_ERROR` - Login server error message
- `INVALID_CREDENTIALS` - Invalid token message
- `COMPANY_NOT_FOUND` - Company not found message
- `INVALID_EMAIL_OR_PASSWORD` - Generic fallback message

**Methods:**
- `get_missing_fields_message(missing_fields: list) -> str` - Generates missing fields message

### `HTTPStatusCodes` (Class)
Contains HTTP status code constants for consistency.

**Values:**
- `OK = 200`
- `CREATED = 201`
- `BAD_REQUEST = 400`
- `UNAUTHORIZED = 401`
- `NOT_FOUND = 404`
- `INTERNAL_SERVER_ERROR = 500`

### `AuthErrorDetails` (Class)
Static methods that return detailed error dictionaries with suggestions.

**Methods:**
- `email_exists() -> dict` - Email already registered error with suggestion
- `missing_fields(missing_fields: list) -> dict` - Missing fields error with field list
- `email_not_found() -> dict` - Email not found error with suggestion
- `incorrect_password() -> dict` - Incorrect password error with suggestion
- `signup_server_error() -> dict` - Signup server error with suggestion
- `login_server_error() -> dict` - Login server error with suggestion
- `invalid_credentials() -> dict` - Invalid token error with suggestion

**Error Dictionary Format:**
```python
{
    "detail": "User-friendly error message",
    "error_code": "MACHINE_READABLE_CODE",
    "suggestion": "Actionable suggestion for user",
    "missing_fields": ["field1", "field2"]  # Only for missing fields error
}
```

## Usage Examples

### In Routes:
```python
from enums.auth_errors import AuthErrorMessages, HTTPStatusCodes, AuthErrorDetails

# Simple error message
raise HTTPException(
    status_code=HTTPStatusCodes.BAD_REQUEST,
    detail=AuthErrorMessages.EMAIL_ALREADY_EXISTS.value
)

# Detailed error with suggestions
raise HTTPException(
    status_code=HTTPStatusCodes.UNAUTHORIZED,
    detail=AuthErrorDetails.email_not_found()["detail"]
)
```

### In Frontend:
```javascript
// Handle detailed error responses
const handleAuthError = (error) => {
    const errorData = error.response?.data;
    
    if (errorData?.error_code === 'AUTH_EMAIL_EXISTS') {
        // Show login suggestion
        showLoginOption();
    } else if (errorData?.error_code === 'AUTH_MISSING_FIELDS') {
        // Highlight missing fields
        highlightFields(errorData.missing_fields);
    } else if (errorData?.suggestion) {
        // Show suggestion to user
        showSuggestion(errorData.suggestion);
    }
};
```

## Benefits

1. **Consistency**: All error messages centralized and consistent
2. **Maintainability**: Easy to update messages in one place
3. **Internationalization Ready**: Easy to add multi-language support
4. **Frontend Integration**: Error codes enable better frontend handling
5. **Documentation**: Self-documenting with clear enum values
6. **Testing**: Easy to mock and test error scenarios

## Future Enhancements

1. **Multi-language Support**: Add language parameter to error methods
2. **Error Categories**: Group errors by type (validation, auth, server)
3. **Logging Integration**: Add error logging to error methods
4. **Custom Exceptions**: Create custom exception classes for different error types
