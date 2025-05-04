from flask import Blueprint, request, jsonify, current_app, send_file
from flask_jwt_extended import jwt_required
from werkzeug.exceptions import BadRequest, NotFound, Forbidden, InternalServerError
import os

# Assuming UserService in app/services/user_service.py
from app.services.user_service import user_service, UserNotFoundError, UserValidationError
# Assuming TeamService might be needed if deleting users requires team checks
# from app.services.team_service import team_service

from app.utils.helpers import get_current_user_doc_and_id
from app.utils.audit import audit_event, write_audit # write_audit might be needed for manual logging

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# --- Decorator for Admin Check ---
from functools import wraps
def admin_required(fn):
    @wraps(fn)
    @jwt_required() # Ensure user is logged in first
    def wrapper(*args, **kwargs):
        user_doc, user_id = get_current_user_doc_and_id()
        if not user_doc or user_doc.get("role") != "admin":
            # Log attempt before raising Forbidden
            write_audit(user_id, f"admin_access_denied:{fn.__name__}", status="denied")
            raise Forbidden("Access denied. Admin privileges required.")
        # Inject admin user info if needed by the route, or just proceed
        # kwargs['admin_user_doc'] = user_doc
        # kwargs['admin_user_id'] = user_id
        return fn(*args, **kwargs)
    return wrapper


@admin_bp.route('/audit_log', methods=['GET'])
@admin_required # Use the custom decorator
@audit_event("download_audit_log") # Audit the attempt (success logged automatically)
def download_audit_log_route():
    # admin_required ensures only admins reach here
    audit_log_file = current_app.config.get('AUDIT_LOG_FILE')
    if not audit_log_file or not os.path.exists(audit_log_file):
        # Log this specific failure case manually if needed
        # write_audit(get_current_user_doc_and_id()[1], "download_audit_log", status="error", extra={"detail": "Log file not found"})
        raise NotFound("Audit log file not found on the server.")

    try:
        return send_file(
            audit_log_file,
            mimetype='text/plain',
            as_attachment=True,
            download_name=os.path.basename(audit_log_file)
        )
    except Exception as e:
        current_app.logger.error(f"Error sending audit log file: {e}", exc_info=True)
        # Audit event decorator will catch this and log 'error'
        raise InternalServerError("An error occurred while accessing the audit log.")


@admin_bp.route("/users", methods=["GET"]) # Renamed from get_users for RESTfulness
@admin_required
@audit_event("get_all_users")
def get_all_users():
    try:
        users = user_service.get_all_users()
        # Exclude sensitive fields like password hashes before sending
        sanitized_users = [
            {k: v for k, v in user.items() if k != 'password_hash'}
            for user in users
        ]
        return jsonify(sanitized_users), 200
    except Exception as e:
        current_app.logger.error(f"Error fetching all users: {e}", exc_info=True)
        raise


@admin_bp.route('/users/<string:user_id_to_delete>', methods=['DELETE'])
@admin_required
# Target ID is correctly captured by audit_event
@audit_event("delete_user", target_arg="user_id_to_delete")
def delete_user_route(user_id_to_delete):
    # Get admin user ID for safety check and logging
    _, admin_user_id = get_current_user_doc_and_id()

    # Prevent self-deletion
    if admin_user_id == user_id_to_delete:
        raise BadRequest("Admin cannot delete their own account.")

    try:
        # UserService handles finding user, removing from teams (maybe?), deleting doc
        success = user_service.delete_user(admin_id=admin_user_id, user_id_to_delete=user_id_to_delete)
        if success:
            return '', 204 # No Content
        else:
            # Should ideally be raised as UserNotFoundError by the service
            raise NotFound(f"User with ID {user_id_to_delete} not found.")
    except UserNotFoundError as e:
        raise NotFound(str(e))
    except UserValidationError as e: # e.g., trying to delete last admin?
        raise BadRequest(str(e))
    except Exception as e:
        current_app.logger.error(f"Error deleting user {user_id_to_delete}: {e}", exc_info=True)
        raise