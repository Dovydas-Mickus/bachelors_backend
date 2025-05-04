import json
import logging
import os
from logging.handlers import RotatingFileHandler
from functools import wraps
from datetime import datetime, timezone
from flask import request, g, current_app, jsonify # Import current_app
import werkzeug
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity, get_jwt # Added get_jwt

# Configure audit logger (called during app creation)
audit_logger = logging.getLogger("audit")
audit_logger.propagate = False # Prevent propagation to root logger

def setup_audit_logging(app):
    """Initializes the audit logger handlers based on app config."""
    log_file = app.config.get('AUDIT_LOG_FILE', 'audit.log')
    max_bytes = app.config.get('AUDIT_LOG_MAX_BYTES', 10_000_000)
    backup_count = app.config.get('AUDIT_LOG_BACKUP_COUNT', 5)

    # Ensure log directory exists
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir)
            app.logger.info(f"Created log directory: {log_dir}")
        except OSError as e:
            app.logger.error(f"Failed to create log directory {log_dir}: {e}")
            # Decide how to handle this - maybe log to stderr?
            return # Cannot set up file handler

    # Remove existing handlers if any (e.g., during reload)
    for handler in audit_logger.handlers[:]:
        handler.close()
        audit_logger.removeHandler(handler)

    # Add the file handler
    try:
        file_handler = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8'
        )
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        audit_logger.addHandler(file_handler)
        audit_logger.setLevel(logging.INFO)
        app.logger.info(f"Audit logger configured for file: {log_file}")
    except Exception as e:
         app.logger.error(f"Failed to configure audit log handler for {log_file}: {e}", exc_info=True)


def write_audit(user_id: str | None,
                action: str,
                *,
                target: str | None = None,
                status: str = "success",
                extra: dict | None = None) -> None:
    """Persist a single audit record as JSON."""
    ip_address = "N/A (No Request Context)"
    # Ensure we have request context for IP address
    if request:
        # Use X-Forwarded-For if behind a proxy, fallback to remote_addr
        if request.headers.getlist("X-Forwarded-For"):
             ip_address = request.headers.getlist("X-Forwarded-For")[0]
        else:
             ip_address = request.remote_addr
    elif current_app and not current_app.config.get("TESTING"):
        # Log error only if not testing, as tests might run outside context
        current_app.logger.error("Attempted to write audit log outside request context.")


    record = {
        "ts":      datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ip":      ip_address,
        "uid":     user_id,
        "act":     action,
        "target":  target,
        "status":  status,
        "extra":   extra or {}
    }
    # Use json.dumps for consistent formatting
    try:
         log_line = json.dumps(record, separators=(",", ":"))
         audit_logger.info(log_line)
    except Exception as e:
         # Log error if JSON serialization fails or logger fails
         fallback_logger = logging.getLogger('fallback_audit')
         fallback_logger.error(f"Failed to write audit log: {e}. Record was: {record}", exc_info=True)


# Convenience decorator
def audit_event(action_name: str,
                *,
                target_arg: str | None = None,
                public_ok: bool = False):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            uid = None
            jwt_claims = None
            try:
                # Always try to verify, even if optional, to get identity if present
                verify_jwt_in_request(optional=True, locations=['cookies']) # Check cookies
                identity = get_jwt_identity()
                if identity:
                     uid = identity # Use email or whatever identity is
                     # Optionally fetch user ID from DB if needed consistently?
                     # _, uid = get_current_user_doc_and_id() # Requires DB lookup
                     jwt_claims = get_jwt() # Get claims if needed for extra logging
                elif not public_ok:
                    # If no identity found and public access is NOT okay, raise Unauthorized manually
                    # This ensures consistent 401 handling if verify_jwt_in_request(optional=True) doesn't raise
                    raise werkzeug.exceptions.Unauthorized("Authentication required.")

            except werkzeug.exceptions.Unauthorized as auth_err:
                 # This handles cases where verify_jwt_in_request raises 401 directly
                 # Or our manual raise above
                 if not public_ok:
                     # Log denied access for non-public endpoints
                     write_audit(uid, action_name, target=kwargs.get(target_arg), status="denied", extra={"http_status": 401, "detail": auth_err.description})
                     raise # Re-raise the exception to be handled by Flask error handlers
                 # If public_ok, we just proceed with uid = None
            except Exception as jwt_exc:
                # Catch other potential JWT errors (e.g., malformed token)
                current_app.logger.warning(f"JWT related error during audit check: {jwt_exc}")
                if not public_ok:
                    # Treat other JWT errors as Unauthorized for protected routes
                    write_audit(uid, action_name, target=kwargs.get(target_arg), status="denied", extra={"http_status": 401, "detail": "Invalid token"})
                    raise werkzeug.exceptions.Unauthorized("Invalid token.")


            # Determine target before calling the view
            tgt = kwargs.get(target_arg) if target_arg else None
            # Allow view to override target using g context
            g.__audit_target = None

            try:
                resp = view(*args, **kwargs)

                # Determine status code from response
                status_code = 500 # Default assumption
                if isinstance(resp, tuple) and len(resp) > 1 and isinstance(resp[1], int):
                     status_code = resp[1] # e.g., return '', 204
                elif hasattr(resp, 'status_code'):
                     status_code = resp.status_code # e.g., response = make_response(...)
                elif hasattr(resp, 'status'): # e.g. response = jsonify(...) -> status is '200 OK'
                     try: status_code = int(resp.status.split()[0])
                     except: pass # Keep default 500 if split/int fails

                # Determine final audit status
                status = "success"
                if status_code >= 400:
                     status = "denied" if status_code in (401, 403) else "error"

                # Get target potentially set by the view
                final_tgt = g.get("__audit_target", tgt)

                write_audit(uid, action_name, target=final_tgt, status=status, extra={"http_status": status_code})
                return resp

            except werkzeug.exceptions.HTTPException as he:
                # Handle exceptions raised explicitly by the view or Flask
                status = "denied" if he.code in (401, 403) else "error"
                final_tgt = g.get("__audit_target", tgt) # Check g context even on error
                write_audit(uid, action_name,
                            target=final_tgt,
                            status=status,
                            extra={"http_status": he.code, "detail": he.description})
                raise # Re-raise the HTTP exception
            except Exception as exc:
                # Catch unexpected errors in the view
                current_app.logger.error(f"Unexpected error in audited view '{action_name}': {exc}", exc_info=True)
                final_tgt = g.get("__audit_target", tgt)
                write_audit(uid, action_name,
                            target=final_tgt,
                            status="error",
                            extra={"err_type": type(exc).__name__, "err_msg": str(exc), "http_status": 500})
                # Let the global error handler manage the response
                raise
        return wrapped
    return decorator