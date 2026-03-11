"""
Authentication Error Messages Documentation
====================================

This document provides a comprehensive overview of all error messages
used in the authentication system for better user experience.

SIGNUP ENDPOINT (/api/v1/auth/signup)
========================================

1. Email Already Exists
   - Status: 400 Bad Request
   - Message: "This email is already registered. Please use a different email or try logging in."
   - When: User tries to signup with an email that's already in the system

2. Missing Required Fields
   - Status: 400 Bad Request  
   - Message: "Missing required fields: [field names]. Please provide all required information."
   - When: User doesn't provide name, first_name, or last_name
   - Examples:
     * "Missing required fields: company name. Please provide all required information."
     * "Missing required fields: first name, last name. Please provide all required information."

3. Server Error During Signup
   - Status: 500 Internal Server Error
   - Message: "An error occurred during signup. Please try again later or contact support if the problem persists."
   - When: Database error, validation error, or other technical issues

LOGIN ENDPOINT (/api/v1/auth/login)
======================================

1. Email Doesn't Exist
   - Status: 401 Unauthorized
   - Message: "No account found with this email address. Please check your email or sign up for a new account."
   - When: User tries to login with an email not in the system

2. Incorrect Password
   - Status: 401 Unauthorized
   - Message: "Incorrect password. Please try again or use the forgot password feature."
   - When: User provides wrong password for existing email

3. Server Error During Login
   - Status: 500 Internal Server Error
   - Message: "Login failed due to a technical issue. Please try again later or contact support if the problem persists."
   - When: Database error, token generation error, or other technical issues

CURRENT USER ENDPOINT (/api/v1/auth/me)
========================================

1. Invalid Token
   - Status: 401 Unauthorized
   - Message: "Could not validate credentials"
   - When: JWT token is invalid, expired, or malformed

2. Company Not Found
   - Status: 404 Not Found
   - Message: "Company not found"
   - When: Token is valid but company was deleted from database

BENEFITS OF THESE ERROR MESSAGES
================================

1. User-Friendly Language
   - Clear, simple language that users can understand
   - Actionable guidance on what to do next

2. Security-Conscious
   - Login errors don't reveal whether email exists or password is wrong
   - Prevents email enumeration attacks

3. Helpful Guidance
   - Suggests alternative actions (try logging in, sign up, contact support)
   - Provides specific instructions for different scenarios

4. Consistent Experience
   - Similar error message format across all endpoints
   - Professional and supportive tone

FRONTEND INTEGRATION EXAMPLES
=============================

React/JavaScript Error Handling:
```javascript
const handleAuthError = (error) => {
  const status = error.response?.status;
  const message = error.response?.data?.detail;
  
  switch(status) {
    case 400:
      // Handle signup validation errors
      if (message.includes('already registered')) {
        // Show login suggestion
        showLoginOption();
      } else if (message.includes('Missing required fields')) {
        // Highlight missing fields
        highlightRequiredFields(message);
      }
      break;
      
    case 401:
      // Handle login errors
      if (message.includes('No account found')) {
        // Show signup option
        showSignupOption();
      } else if (message.includes('Incorrect password')) {
        // Show password reset option
        showPasswordReset();
      }
      break;
      
    case 500:
      // Show server error with retry option
      showServerErrorWithRetry();
      break;
  }
  
  showErrorMessage(message);
};
```

This approach ensures users get helpful, actionable feedback for every authentication scenario!
