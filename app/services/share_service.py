import secrets
from datetime import datetime, timedelta, timezone
from flask import current_app

# Import db instance and custom exceptions
from app.extensions import db
from app.exceptions import (
    ShareNotFoundError, ShareExpiredError, ShareAccessDeniedError, ShareValidationError,
    ServiceError, UserNotFoundError, TeamNotFoundError
)
# Need FileService to check if file exists/is accessible by creator
from .file_service import file_service, FileNotFoundError as FileServiceFileNotFoundError, AccessDeniedError as FileServiceAccessDeniedError


class ShareService:

    def create_share_link(self, creator_id: str, relative_file_path: str, share_type: str,
                          target_email: str | None, target_team_id: str | None,
                          duration_days: int | None, allow_download: bool):
        """Creates a share link document in the database."""
        current_app.logger.info(f"User {creator_id} attempting to share file '{relative_file_path}' (type: {share_type})")

        # 1. Validate Inputs
        if share_type not in ['user', 'team', 'public']:
            raise ShareValidationError(f"Invalid share_type: {share_type}")
        if share_type == 'user' and not target_email:
            raise ShareValidationError("Missing 'target_email' for user share.")
        if share_type == 'team' and not target_team_id:
            raise ShareValidationError("Missing 'target_team_id' for team share.")

        # Optional: Validate email format, team existence
        if share_type == 'user':
             # Check if target user exists? Maybe optional for flexibility.
             # target_user = db.find_user_by_email(target_email)
             # if not target_user: raise UserNotFoundError(f"Target user '{target_email}' not found.")
             pass
        if share_type == 'team':
             team = db.get_team(team_id=target_team_id)
             if not team: raise TeamNotFoundError(f"Target team ID '{target_team_id}' not found.")

        # 2. Check Creator's Permission to Access the File
        try:
            # Use FileService internal method to check path validity and existence
            # This implicitly checks if the file is within the creator's root
            absolute_path = file_service._resolve_and_check_path(creator_id, relative_file_path)
            if not absolute_path.is_file():
                 raise FileServiceFileNotFoundError(f"File not found at path: {relative_file_path}")
            current_app.logger.debug(f"File access verified for creator {creator_id} and path '{relative_file_path}'")
        except (FileServiceFileNotFoundError, FileServiceAccessDeniedError) as e:
             current_app.logger.warning(f"Share creation failed: Creator {creator_id} cannot access path '{relative_file_path}'. Reason: {e}")
             raise ShareAccessDeniedError(f"You do not have permission to share the file at '{relative_file_path}' or it does not exist.")
        except Exception as e:
             current_app.logger.error(f"Unexpected error checking file access for share by {creator_id}: {e}", exc_info=True)
             raise ServiceError("Could not verify file access for sharing.")

        # 3. Generate Share Details
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at_iso = None
        if isinstance(duration_days, int) and duration_days > 0:
            try:
                expires_at = now + timedelta(days=duration_days)
                expires_at_iso = expires_at.isoformat()
            except OverflowError:
                 current_app.logger.warning(f"Share duration {duration_days} days resulted in overflow, setting no expiry.")
                 expires_at_iso = None # Or raise validation error?

        # 4. Prepare Share Document
        share_doc_data = {
            "type": "share_link", # Add a type for easier querying
            "token": token,
            "owner_id": creator_id,
            "file_path": relative_file_path, # Store relative path
            "share_type": share_type,
            "allow_download": bool(allow_download),
            "created_at": now.isoformat(),
            "expires_at": expires_at_iso,
            # Add target info based on type
            "target_email": target_email if share_type == 'user' else None,
            "target_team_id": target_team_id if share_type == 'team' else None,
        }

        # 5. Save to Database
        try:
            doc_id, _ = db.create_share_link(share_doc_data) # Assumes this method exists
            if not doc_id:
                 raise ServiceError("Database failed to return document ID after creating share link.")
            current_app.logger.info(f"Share link created with token {token} (Doc ID: {doc_id}) for file '{relative_file_path}' by user {creator_id}")
            # Return key details including the token
            return {"token": token, "doc_id": doc_id, "expires_at": expires_at_iso}
        except Exception as e:
            current_app.logger.error(f"Database error creating share link for token {token}: {e}", exc_info=True)
            raise ServiceError("Failed to save share link to database.")


    def verify_shared_access(self, token: str, requester_id: str | None, requester_email: str | None, is_authenticated: bool):
        """
        Verifies a share token, checks expiry and permissions.
        Returns share details needed to fetch the file if access is granted.
        """
        current_app.logger.debug(f"Verifying access for share token {token}. Requester ID: {requester_id}, Email: {requester_email}, Auth: {is_authenticated}")

        share_doc = db.find_share_link_by_token(token) # Assumes this method exists
        if not share_doc:
            raise ShareNotFoundError("Share link not found or invalid.")

        # Check Expiry
        expires_at_str = share_doc.get("expires_at")
        if expires_at_str:
            try:
                # Handle potential 'Z' Zulu suffix for UTC
                if expires_at_str.endswith('Z'):
                    expires_at_str = expires_at_str[:-1] + '+00:00'
                expires_at = datetime.fromisoformat(expires_at_str)
                # Ensure comparison is timezone-aware
                if expires_at.tzinfo is None:
                     # If stored time is naive, assume UTC? Or fail? Assume UTC for now.
                     expires_at = expires_at.replace(tzinfo=timezone.utc)

                if datetime.now(timezone.utc) > expires_at:
                    current_app.logger.warning(f"Share link expired for token: {token} (Expired at: {expires_at_str})")
                    raise ShareExpiredError("This share link has expired.")
            except ValueError:
                current_app.logger.error(f"Invalid expires_at format in share doc {share_doc.id}: {expires_at_str}")
                raise ServiceError("Invalid share link data (expiry format).") # Internal error

        # Check Permissions based on share_type
        share_type = share_doc.get("share_type")
        access_granted = False

        if share_type == 'public':
            access_granted = True
            current_app.logger.debug(f"Public access granted for token {token}")
        elif share_type == 'user':
            target_email = share_doc.get('target_email')
            if is_authenticated and requester_email and requester_email.lower() == target_email.lower():
                 access_granted = True
                 current_app.logger.debug(f"User share access granted for token {token} to {requester_email}")
            else:
                 current_app.logger.warning(f"User share access denied for token {token}. Target: {target_email}, Requester: {requester_email}")
        elif share_type == 'team':
            target_team_id = share_doc.get('target_team_id')
            if is_authenticated and requester_id and target_team_id:
                 # Requires a method in Database class to check membership
                 is_member = db.is_user_in_team(requester_id, target_team_id)
                 if is_member:
                     access_granted = True
                     current_app.logger.debug(f"Team share access granted for token {token} to user {requester_id} in team {target_team_id}")
                 else:
                     current_app.logger.warning(f"Team share access denied for token {token}. User {requester_id} not in team {target_team_id}.")
            else:
                current_app.logger.warning(f"Team share access denied for token {token}. Requester not authenticated or missing ID/target team.")
        else:
            current_app.logger.error(f"Invalid share_type '{share_type}' encountered in share doc {share_doc.id} for token {token}")
            raise ServiceError("Invalid share link type encountered.")

        if not access_granted:
            raise ShareAccessDeniedError("You do not have permission to access this shared link.")

        # Return necessary details if access granted
        owner_id = share_doc.get("owner_id")
        file_path_rel = share_doc.get("file_path")
        allow_download = share_doc.get("allow_download", True)

        if not owner_id or not file_path_rel:
             current_app.logger.error(f"Share doc {share_doc.id} (token {token}) is missing owner_id or file_path.")
             raise ServiceError("Incomplete share link data found.")

        return {
            "owner_id": owner_id,
            "file_path_rel": file_path_rel,
            "allow_download": allow_download
        }


# Instantiate the service
share_service = ShareService()