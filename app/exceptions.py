# app/exceptions.py

class ServiceError(Exception):
    """Base class for service layer errors."""
    def __init__(self, message="A service error occurred.", status_code=500):
        super().__init__(message)
        self.status_code = status_code # Optional: For mapping to HTTP status

class ValidationError(ServiceError):
    """Error for general validation failures (e.g., invalid input format)."""
    def __init__(self, message="Validation failed."):
        super().__init__(message, status_code=400)

class NotFoundError(ServiceError):
    """Error when a requested resource cannot be found."""
    def __init__(self, message="Resource not found."):
        super().__init__(message, status_code=404)

class DatabaseError(ServiceError): # Or inherit directly from Exception
    """Custom exception for general database operation errors."""
    def __init__(self, message="A database error occurred", status_code=500):
        super().__init__(message, status_code)

class AccessDeniedError(ServiceError):
    """Error when user lacks permission for an action."""
    def __init__(self, message="Access denied."):
        super().__init__(message, status_code=403)

class ConflictError(ServiceError):
     """Error when an action conflicts with the current state (e.g., resource already exists)."""
     def __init__(self, message="Resource conflict."):
        super().__init__(message, status_code=409)

class AuthenticationError(ServiceError):
     """Error related to failed authentication (e.g., bad password, invalid token)."""
     def __init__(self, message="Authentication failed."):
        super().__init__(message, status_code=401)

# --- Specific Not Found Errors ---
class UserNotFoundError(NotFoundError):
    """Specific error when a user resource is not found."""
    def __init__(self, message="User not found."):
        super().__init__(message)

class TeamNotFoundError(NotFoundError):
    """Specific error when a team resource is not found."""
    def __init__(self, message="Team not found."):
        super().__init__(message)

class ShareNotFoundError(NotFoundError):
    """Specific error when a share link resource is not found."""
    def __init__(self, message="Share link not found."):
        super().__init__(message)

# --- Specific Validation Errors ---
class TeamValidationError(ValidationError):
    """Specific error for team-related validation failures."""
    pass # Inherits message and status code from ValidationError

class ShareValidationError(ValidationError):
    """Specific error for share-link-related validation failures."""
    pass

class UserValidationError(ValidationError):
    """Specific error for user-related validation failures."""
    pass

# --- Specific Access Errors ---
class TeamAccessDeniedError(AccessDeniedError):
    """Specific error for denied access related to team operations."""
    pass

class ShareAccessDeniedError(AccessDeniedError):
     """Specific error for denied access related to share link operations."""
     pass

# --- File Service Specific Errors ---
# These inherit from ServiceError and potentially more specific base errors

class FileServiceError(ServiceError):
    """Base error for file service specific issues."""
    pass # Inherits default message and 500 status code

class FileServiceFileNotFoundError(FileNotFoundError, FileServiceError): # Inherit from NotFoundError too
    """Specific error when a file/directory is not found by the FileService."""
    # Inherits status_code=404 from NotFoundError
    def __init__(self, message="File or directory not found."):
        super().__init__(message) # Uses NotFoundError's init

class FileServiceAccessDeniedError(AccessDeniedError, FileServiceError): # Inherit from AccessDeniedError too
    """Specific error for denied access related to file operations (e.g., path traversal)."""
    # Inherits status_code=403 from AccessDeniedError
    def __init__(self, message="Access denied for file operation."):
        super().__init__(message) # Uses AccessDeniedError's init

class FileServiceConflictError(ConflictError, FileServiceError): # Inherit from ConflictError too
    """Specific error for conflicts during file operations (e.g., file already exists)."""
    # Inherits status_code=409 from ConflictError
    def __init__(self, message="File operation conflict."):
         super().__init__(message) # Uses ConflictError's init

# --- Specific Share Errors ---
class ShareExpiredError(ServiceError):
    """Specific error when a share link has expired."""
    def __init__(self, message="Share link has expired."):
        super().__init__(message, status_code=410) # 410 Gone