from flask import Blueprint, request, jsonify, send_file, current_app, url_for
from flask_jwt_extended import jwt_required, get_jwt_identity, verify_jwt_in_request
from werkzeug.exceptions import BadRequest, NotFound, Forbidden, Gone, InternalServerError
import werkzeug # Import werkzeug directly for exceptions
from pathlib import Path
import mimetypes


# Assuming ShareService in app/services/share_service.py
from app.services.share_service import share_service, ShareNotFoundError, ShareExpiredError, ShareAccessDeniedError, ShareValidationError
# Assuming FileService still needed to fetch the actual file based on share details
from app.services.file_service import file_service, FileNotFoundError as FileServiceFileNotFoundError, AccessDeniedError as FileServiceAccessDeniedError

from app.utils.helpers import get_current_user_doc_and_id
from app.utils.audit import audit_event, write_audit

shared_bp = Blueprint('shared', __name__) # No prefix for /shared/<token> or /share

@shared_bp.route('/share', methods=['POST'])
@jwt_required()
@audit_event("create_share_link")
def create_share_link_route():
    caller_doc, caller_id = get_current_user_doc_and_id()
    if not caller_doc:
        raise NotFound("User not found.") # Should not happen with jwt_required

    data = request.json
    if not data: raise BadRequest("Request body must be JSON.")

    # --- Input Validation (Basic) ---
    file_path_rel = data.get("file_path")
    share_type = data.get("share_type")
    target_email = data.get("target_email")
    target_team_id = data.get("target_team_id")
    duration_days = data.get("duration_days")
    allow_download = data.get("allow_download", True)

    if not file_path_rel: raise BadRequest("Missing 'file_path'")
    if share_type not in ['user', 'team', 'public']: raise BadRequest("Invalid 'share_type'")
    if share_type == 'user' and not target_email: raise BadRequest("Missing 'target_email' for user share")
    if share_type == 'team' and not target_team_id: raise BadRequest("Missing 'target_team_id' for team share")

    try:
        # ShareService handles validation, permission check (can caller share this file?), token generation, DB saving
        share_details = share_service.create_share_link(
            creator_id=caller_id,
            relative_file_path=file_path_rel,
            share_type=share_type,
            target_email=target_email,
            target_team_id=target_team_id,
            duration_days=duration_days,
            allow_download=allow_download
        )

        # Construct full URL
        # Make sure the endpoint name matches the function name used in url_for
        share_url = url_for('shared.access_shared_file_route', token=share_details['token'], _external=True)

        # Log success explicitly here if audit_event doesn't capture the target well
        # write_audit(caller_id, "create_share_link", target=share_details['token'], status="success", extra={"file": file_path_rel})

        return jsonify({"share_url": share_url, "token": share_details['token']}), 201

    except ShareValidationError as e: raise BadRequest(str(e))
    except ShareAccessDeniedError as e: raise Forbidden(str(e)) # If caller cannot share the file
    except FileServiceFileNotFoundError as e: raise NotFound(str(e)) # If the file to share doesn't exist
    except Exception as e:
        current_app.logger.error(f"Error creating share link for {file_path_rel} by {caller_id}: {e}", exc_info=True)
        raise


# NO @jwt_required() - public links must work initially
@shared_bp.route('/shared/<string:token>', methods=['GET'])
@audit_event("access_shared_file", target_arg="token", public_ok=True)
def access_shared_file_route(token):
    requester_id = None
    requester_email = None
    is_authenticated = False
    try:
        # Check if a valid access token IS present, even if not required
        verify_jwt_in_request(optional=True, locations=['cookies'])
        requester_email = get_jwt_identity()
        if requester_email:
             _, requester_id = get_current_user_doc_and_id() # Get ID if email found
             is_authenticated = True if requester_id else False
    except Exception:
         pass # Ignore JWT errors, access might still be public/allowed

    try:
        # 1. Verify share link and check permissions based on type/requester
        # Service returns necessary details if access is granted
        access_details = share_service.verify_shared_access(
            token=token,
            requester_id=requester_id,
            requester_email=requester_email,
            is_authenticated=is_authenticated
        )
        # access_details should contain: owner_id, file_path_rel, allow_download

        # 2. Fetch the actual file using FileService
        # Use owner_id and relative path from the verified share details
        file_content, mime_type = file_service.get_file_for_preview( # Use preview to handle images etc.
             user_id=access_details['owner_id'],
             relative_path=access_details['file_path_rel']
        )
        # Note: get_file_for_preview returns path or buffer

        # Determine filename for download if allowed
        download_name = Path(access_details['file_path_rel']).name

        # 3. Serve the file
        current_app.logger.info(f"Serving shared file via token {token}, download={access_details['allow_download']}")
        return send_file(
            file_content,
            mimetype=mime_type,
            as_attachment=access_details['allow_download'],
            download_name=download_name if access_details['allow_download'] else None
        )

    except ShareNotFoundError as e: raise NotFound(str(e))
    except ShareExpiredError as e: raise Gone(str(e)) # 410 Gone
    except ShareAccessDeniedError as e: raise Forbidden(str(e))
    except FileServiceFileNotFoundError as e:
        # File existed when link was created, but not now
        current_app.logger.error(f"File for valid share token {token} not found: {e}")
        raise NotFound("The file associated with this link could not be found.")
    except FileServiceAccessDeniedError as e:
         # Should not happen if share link is valid, but indicates internal issue
         current_app.logger.error(f"Access denied fetching file for valid share token {token}: {e}")
         raise InternalServerError("Error retrieving shared file.")
    except Exception as e:
        current_app.logger.error(f"Error accessing shared file for token {token}: {e}", exc_info=True)
        raise