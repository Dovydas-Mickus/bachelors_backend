from flask import Blueprint, request, jsonify, send_file, current_app
from flask_jwt_extended import jwt_required
from werkzeug.exceptions import BadRequest, NotFound, Forbidden, Conflict, InternalServerError
import werkzeug # Import werkzeug directly for exceptions
import os

# Assuming FileService in app/services/file_service.py
# Import specific exceptions if defined there
from app.services.file_service import file_service, FileServiceError, FileNotFoundError, AccessDeniedError, ConflictError
from app.utils.helpers import resolve_target_user # Use helper for permission checks
from app.utils.audit import audit_event

files_bp = Blueprint('files', __name__, url_prefix='/files')

# --- Helper to get target user ID safely (copied from previous example) ---
def _get_target_user_id_from_request():
    """Gets target user_id from query args and resolves permissions."""
    requested_user_id = request.args.get("user_id")
    try:
        target_user_id, _ = resolve_target_user(requested_user_id)
        return target_user_id
    except (NotFound, Forbidden) as e:
        raise e # Let the calling route's error handler catch it

# --- Route Definitions (using the structure from previous example) ---

@files_bp.route("/cloud", methods=["GET"])
@jwt_required()
@audit_event("list_cloud")
def list_cloud_contents():
    """ GET /files/cloud?path=<subfolder>&user_id=<optional> """
    try:
        target_user_id = _get_target_user_id_from_request()
        relative_path = request.args.get("path", "")
        items = file_service.list_directory(target_user_id, relative_path)
        return jsonify({"cloud_contents": items}), 200
    except (FileNotFoundError, AccessDeniedError) as e:
        status_code = 404 if isinstance(e, FileNotFoundError) else 403
        raise werkzeug.exceptions.HTTPException(description=str(e), response=jsonify({"error": str(e)}), code=status_code)
    except FileServiceError as e:
        current_app.logger.error(f"FileServiceError listing cloud: {e}")
        raise InternalServerError(str(e))
    except (NotFound, Forbidden) as e: # From resolve_target_user
         raise e


@files_bp.route("/upload", methods=["POST"])
@jwt_required()
@audit_event("upload_file")
def upload_file():
    """ POST /files/upload?user_id=<optional> - Form data: 'file', 'path' (optional subdir) """
    try:
        target_user_id = _get_target_user_id_from_request()
        target_rel_path = request.form.get("path", "").strip("/")

        if "file" not in request.files: raise BadRequest("No file part in the request.")
        file = request.files["file"]
        if not file or file.filename == '': raise BadRequest("No selected file or empty filename.")

        result = file_service.save_uploaded_file(target_user_id, file, target_rel_path)
        return jsonify(result), 201
    except BadRequest as e:
         raise e
    except (FileNotFoundError, AccessDeniedError, ConflictError) as e:
        status_code = 404 if isinstance(e, FileNotFoundError) else \
                      403 if isinstance(e, AccessDeniedError) else 409
        raise werkzeug.exceptions.HTTPException(description=str(e), response=jsonify({"error": str(e)}), code=status_code)
    except FileServiceError as e:
         current_app.logger.error(f"FileServiceError uploading file: {e}")
         raise InternalServerError(str(e))
    except (NotFound, Forbidden) as e:
         raise e


@files_bp.route("/download", methods=["GET"])
@jwt_required()
@audit_event("download_file")
def download_file():
    """ GET /files/download?path=<file_path>&user_id=<optional> """
    try:
        target_user_id = _get_target_user_id_from_request()
        relative_path = request.args.get("path")
        if not relative_path: raise BadRequest("Missing required query parameter 'path'.")

        target_file, mime_type = file_service.get_file_for_download(target_user_id, relative_path)
        return send_file(target_file, mimetype=mime_type, as_attachment=True, download_name=target_file.name)
    except BadRequest as e: raise e
    except FileNotFoundError as e: raise NotFound(str(e))
    except AccessDeniedError as e: raise Forbidden(str(e))
    except FileServiceError as e:
        current_app.logger.error(f"FileServiceError downloading file: {e}")
        raise InternalServerError(str(e))
    except (NotFound, Forbidden) as e: raise e


@files_bp.route("/preview", methods=["GET"])
@jwt_required()
@audit_event("preview_file")
def preview_file():
    """ GET /files/preview?path=<file_path>&user_id=<optional> """
    try:
        target_user_id = _get_target_user_id_from_request()
        relative_path = request.args.get("path")
        if not relative_path: raise BadRequest("Missing required query parameter 'path'.")

        file_content, mime_type = file_service.get_file_for_preview(target_user_id, relative_path)
        return send_file(file_content, mimetype=mime_type, as_attachment=False)
    except BadRequest as e: raise e
    except FileNotFoundError as e: raise NotFound(str(e))
    except AccessDeniedError as e: raise Forbidden(str(e))
    except FileServiceError as e:
        status_code = getattr(e, 'status_code', 500)
        raise werkzeug.exceptions.HTTPException(description=str(e), response=jsonify({"error": str(e)}), code=status_code)
    except (NotFound, Forbidden) as e: raise e


@files_bp.route("/rename", methods=["POST"])
@jwt_required()
@audit_event("rename_file_or_folder")
def rename_item():
    """ POST /files/rename?user_id=<optional> - JSON: {"old_path": "...", "new_name": "..."} """
    data = request.get_json()
    if not data or "old_path" not in data or "new_name" not in data:
        raise BadRequest("Missing 'old_path' or 'new_name' in JSON body.")
    try:
        target_user_id = _get_target_user_id_from_request()
        result = file_service.rename_item(target_user_id, data["old_path"], data["new_name"])
        return jsonify(result), 200
    except BadRequest as e: raise e
    except (FileNotFoundError, AccessDeniedError, ConflictError) as e:
        status_code = 404 if isinstance(e, FileNotFoundError) else \
                      403 if isinstance(e, AccessDeniedError) else 409
        raise werkzeug.exceptions.HTTPException(description=str(e), response=jsonify({"error": str(e)}), code=status_code)
    except FileServiceError as e:
        current_app.logger.error(f"FileServiceError renaming item: {e}")
        raise InternalServerError(str(e))
    except (NotFound, Forbidden) as e: raise e


@files_bp.route("/mkdir", methods=["POST"])
@jwt_required()
@audit_event("create_folder")
def create_folder():
    """ POST /files/mkdir?user_id=<optional> - JSON: {"path": "new/folder/path"} """
    data = request.get_json()
    if not data or "path" not in data: raise BadRequest("Missing 'path' in JSON body.")
    try:
        target_user_id = _get_target_user_id_from_request()
        result = file_service.create_directory(target_user_id, data["path"])
        return jsonify(result), 201
    except BadRequest as e: raise e
    except (AccessDeniedError, ConflictError, FileNotFoundError) as e: # Add FNF from _get_user_root
        status_code = 403 if isinstance(e, AccessDeniedError) else \
                      409 if isinstance(e, ConflictError) else 404
        raise werkzeug.exceptions.HTTPException(description=str(e), response=jsonify({"error": str(e)}), code=status_code)
    except FileServiceError as e:
        current_app.logger.error(f"FileServiceError creating directory: {e}")
        raise InternalServerError(str(e))
    except (NotFound, Forbidden) as e: raise e # From resolve_target_user


@files_bp.route("/delete", methods=["DELETE"])
@jwt_required()
@audit_event("delete_file_or_folder")
def delete_item():
    """ DELETE /files/delete?user_id=<optional> - JSON: {"path": "path/to/delete"} """
    data = request.get_json()
    if not data or "path" not in data: raise BadRequest("Missing 'path' in JSON body.")
    try:
        target_user_id = _get_target_user_id_from_request()
        result = file_service.delete_item(target_user_id, data["path"])
        return jsonify(result), 200 # Or 204 No Content
    except BadRequest as e: raise e
    except (FileNotFoundError, AccessDeniedError) as e:
        status_code = 404 if isinstance(e, FileNotFoundError) else 403
        raise werkzeug.exceptions.HTTPException(description=str(e), response=jsonify({"error": str(e)}), code=status_code)
    except FileServiceError as e:
        current_app.logger.error(f"FileServiceError deleting item: {e}")
        raise InternalServerError(str(e))
    except (NotFound, Forbidden) as e: raise e


@files_bp.route("/search", methods=["GET"])
@jwt_required()
@audit_event("search_files")
def search_files():
    """ GET /files/search?q=<query>&user_id=<optional> """
    query = request.args.get("q", "").strip()
    if not query:
        raise BadRequest("Search query parameter 'q' is required.")
    try:
        target_user_id = _get_target_user_id_from_request()
        # Assuming a search method in file_service
        matches = file_service.search_files(target_user_id, query)
        return jsonify({"results": matches})
    except BadRequest as e: raise e
    except (FileNotFoundError, AccessDeniedError) as e: # e.g. user storage not found
        status_code = 404 if isinstance(e, FileNotFoundError) else 403
        raise werkzeug.exceptions.HTTPException(description=str(e), response=jsonify({"error": str(e)}), code=status_code)
    except FileServiceError as e:
        current_app.logger.error(f"FileServiceError searching files: {e}")
        raise InternalServerError(str(e))
    except (NotFound, Forbidden) as e: raise e