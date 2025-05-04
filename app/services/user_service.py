import os
import shutil
from flask import current_app

# Import db instance and custom exceptions
from app.extensions import db
from app.exceptions import UserNotFoundError, ServiceError, UserValidationError

class UserService:

    def get_all_users(self):
        """Retrieves all user documents from the database."""
        try:
            users = db.get_all_users() # Assumes this method exists in Database class
            # The route handler should sanitize (remove password hash)
            return users
        except Exception as e:
            current_app.logger.error(f"Failed to retrieve all users: {e}", exc_info=True)
            raise ServiceError("Could not retrieve user list.")

    def find_user(self, user_id=None, email=None):
        """Finds a single user by ID or email."""
        if user_id:
             user = db.find_user_by_id(user_id)
        elif email:
             user = db.find_user_by_email(email)
        else:
             return None # Or raise ValueError?
        if not user:
             raise UserNotFoundError(f"User not found with {'ID '+user_id if user_id else 'email '+email}")
        return user

    def delete_user(self, admin_id: str, user_id_to_delete: str):
        """
        Deletes a user account, removes them from teams, and deletes their data.
        Requires admin privileges (checked in route).
        """
        current_app.logger.warning(f"Admin {admin_id} initiating deletion of user {user_id_to_delete}")

        # 1. Find the user to delete
        user_to_delete = db.find_user_by_id(user_id_to_delete)
        if not user_to_delete:
            raise UserNotFoundError(f"User to delete (ID: {user_id_to_delete}) not found.")

        # Optional: Add checks to prevent deletion of critical users if needed

        # 2. Remove user from teams (Implement this in Database class or TeamService)
        try:
            # Placeholder: Assumes a method exists. This might involve finding all teams
            # the user is in and calling edit_team to remove them.
            # team_service.remove_user_from_all_teams(user_id_to_delete)
            removed_from_teams = db.remove_user_from_all_teams(user_id_to_delete) # Example method
            current_app.logger.info(f"User {user_id_to_delete} removed from teams (Result: {removed_from_teams})")
        except Exception as e:
             # Log error but proceed with deletion? Or fail? Depends on requirements.
             current_app.logger.error(f"Error removing user {user_id_to_delete} from teams during deletion: {e}", exc_info=True)
             # raise ServiceError("Failed to remove user from teams during deletion.") # Option to fail hard

        # 3. Delete the user document
        try:
            delete_success = db.delete_user(user_id_to_delete) # Assumes this method exists and deletes the doc
            if not delete_success:
                 # This might mean the user was already gone somehow
                 current_app.logger.warning(f"db.delete_user returned false for {user_id_to_delete}, user might have been deleted concurrently.")
                 # Depending on db.delete_user impl, NotFoundError might be better here
                 raise UserNotFoundError(f"User {user_id_to_delete} could not be deleted (possibly already deleted).")
            current_app.logger.info(f"User document {user_id_to_delete} deleted successfully.")
        except Exception as e:
             current_app.logger.error(f"Failed to delete user document {user_id_to_delete}: {e}", exc_info=True)
             raise ServiceError("Database error during user document deletion.")

        # 4. Delete user's file directory (Use with caution!)
        try:
            user_root_path_str = os.path.join(current_app.config['DATABASE_FILES_DIR'], user_id_to_delete)
            user_root_path = os.path.abspath(user_root_path_str)
            if os.path.exists(user_root_path) and os.path.isdir(user_root_path):
                shutil.rmtree(user_root_path)
                current_app.logger.info(f"Deleted user data directory: {user_root_path}")
            else:
                 current_app.logger.info(f"User data directory not found or not a directory, skipping deletion: {user_root_path}")
        except Exception as e:
             # Log error but consider deletion successful overall? Or raise?
             current_app.logger.error(f"Failed to delete user directory {user_root_path} for user {user_id_to_delete}: {e}", exc_info=True)
             # Don't raise here, as the user doc is already deleted. Log it as a cleanup issue.

        current_app.logger.warning(f"User {user_id_to_delete} fully deleted by admin {admin_id}.")
        return True


# Instantiate the service
user_service = UserService()