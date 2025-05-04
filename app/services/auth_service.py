import bcrypt
import os
from flask import current_app
from flask_jwt_extended import create_access_token, create_refresh_token
from datetime import datetime, timezone # Make sure timezone is imported

# Import db instance and custom exceptions
from app.extensions import db
from app.exceptions import ValidationError, AuthenticationError, UserNotFoundError, ServiceError

class AuthService:

    ALLOWED_ROLES = ["admin", "team_lead", "worker"]

    def register_user(self, first_name, last_name, email, password, role):
        """Registers a new user."""
        current_app.logger.info(f"Attempting registration for email: {email}")

        # Validation
        if not email or not password or not first_name or not last_name:
            raise ValidationError("Missing required fields for registration.")
        if role not in self.ALLOWED_ROLES:
            raise ValidationError(f"Invalid role specified: {role}. Allowed roles: {self.ALLOWED_ROLES}")

        # Check if user already exists
        existing_user = db.find_user_by_email(email)
        if existing_user:
            current_app.logger.warning(f"Registration failed: Email already exists - {email}")
            raise ValidationError("Email address is already registered.") # Or ConflictError

        # Hash password
        try:
            hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        except Exception as e:
            current_app.logger.error(f"Password hashing failed for {email}: {e}")
            raise ServiceError("Failed to secure password during registration.")

        # Add user to database (Database class handles document creation)
        try:
            user_id = db.add_user(
                first_name=first_name,
                last_name=last_name,
                email=email,
                password_hash=hashed_password, # Pass the hash
                role=role
            )
            if not user_id:
                 raise ServiceError("Database failed to return a user ID after insertion.")

            current_app.logger.info(f"User registered successfully: {email} (ID: {user_id})")
            # Fetch the newly created user doc to return info (optional)
            # user_doc = db.find_user_by_id(user_id)
            # return user_doc.copy() if user_doc else {"id": user_id, "email": email, "role": role} # Return basic info
            return {"id": user_id, "email": email, "role": role, "first_name": first_name, "last_name": last_name}

        except Exception as e:
            current_app.logger.error(f"Database error during registration for {email}: {e}", exc_info=True)
            # Clean up potentially created user? Difficult without transactions.
            raise ServiceError("Failed to save user data during registration.")


    def login_user(self, email, password):
        """Authenticates a user and returns tokens and user info."""
        current_app.logger.info(f"Login attempt for email: {email}")

        user_doc = db.find_user_by_email(email)

        if not user_doc:
            current_app.logger.warning(f"Login failed: User not found - {email}")
            raise AuthenticationError("Invalid email or password.") # Use AuthenticationError

        # Verify password
        stored_hash = user_doc.get("password_hash")
        if not stored_hash or not bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
            current_app.logger.warning(f"Login failed: Invalid password for user {email} (ID: {user_doc.id})")
            raise AuthenticationError("Invalid email or password.")

        # --- Optional: Create user directory on first login ---
        try:
            user_id = user_doc.id
            user_root_path_str = os.path.join(current_app.config['DATABASE_FILES_DIR'], user_id)
            user_root_path = os.path.abspath(user_root_path_str)
            if not os.path.exists(user_root_path):
                 os.makedirs(user_root_path, exist_ok=True)
                 os.chmod(user_root_path, 0o755) # Set appropriate permissions
                 # Store initial access control in DB
                 db.set_user_access(user_id, user_root_path, [{"path": user_root_path, "permissions": ["read", "write"]}])
                 current_app.logger.info(f"Created initial directory and access for user {user_id} at {user_root_path}")
        except Exception as e:
            current_app.logger.error(f"Failed to create directory/access for user {user_doc.id} on login: {e}", exc_info=True)
            # Decide if login should fail here. Probably not, but log it seriously.

        # Generate JWT Tokens
        try:
            identity = user_doc["email"] # Use email as identity
            access_token = create_access_token(identity=identity)
            refresh_token = create_refresh_token(identity=identity)
        except Exception as e:
             current_app.logger.error(f"JWT token generation failed for {email}: {e}")
             raise ServiceError("Failed to generate authentication tokens.")

        current_app.logger.info(f"User login successful: {email} (ID: {user_doc.id})")
        # Return tokens and basic user info (excluding hash)
        user_info = {
            "id": user_doc.id,
            "email": user_doc.get("email"),
            "role": user_doc.get("role"),
            "first_name": user_doc.get("first_name"),
            "last_name": user_doc.get("last_name"),
            "isLead": user_doc.get("isLead", False),
        }
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": user_info
        }

    def refresh_access_token(self, identity):
        """Generates a new access token for a given identity."""
        # Optional: Check if the user identity still exists in the DB
        user = db.find_user_by_email(identity)
        if not user:
            current_app.logger.warning(f"Refresh token attempt for non-existent user: {identity}")
            raise AuthenticationError("User associated with refresh token not found.")

        current_app.logger.debug(f"Generating new access token for identity: {identity}")
        new_access_token = create_access_token(identity=identity)
        return new_access_token

# Instantiate the service
auth_service = AuthService()