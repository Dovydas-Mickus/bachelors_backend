from flask import Blueprint, request, jsonify, make_response, current_app
from flask_jwt_extended import (
    create_access_token, create_refresh_token, jwt_required,
    get_jwt_identity, set_access_cookies, set_refresh_cookies,
    unset_jwt_cookies, verify_jwt_in_request
)
from werkzeug.exceptions import BadRequest, Unauthorized, NotFound

from app.services.auth_service import auth_service
from app.utils.audit import audit_event
from app.utils.helpers import get_current_user_doc_and_id

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

@auth_bp.route("/register", methods=["POST"])
@audit_event("register_user")
def register():
    data = request.json
    required_fields = ["first_name", "last_name", "email", "password", "role"]
    if not data or not all(field in data for field in required_fields):
        raise BadRequest("Missing required fields for registration.")

    try:
        # AuthService handles hashing and adding user to DB
        user_info = auth_service.register_user(
            first_name=data["first_name"],
            last_name=data["last_name"],
            email=data["email"],
            password=data["password"],
            role=data["role"]
        )
        # Exclude password hash from response
        user_info.pop("password_hash", None)
        return jsonify({"message": "User registered successfully", "user": user_info}), 201
    except ValueError as e: # Catch validation errors from service
        raise BadRequest(str(e))
    except Exception as e:
        current_app.logger.error(f"Registration error: {e}", exc_info=True)
        # Let the generic error handler manage this, audit log will capture 'error' status
        raise # Re-raise for central handler


@auth_bp.route("/login", methods=["POST"])
@audit_event("login", public_ok=True) # Public attempt, audit logs success/failure
def login():
    data = request.json
    if not data or "email" not in data or "password" not in data:
        raise BadRequest("Missing email or password.")

    try:
        # AuthService handles user lookup, password check, token generation
        # It might also handle initial folder setup if needed
        login_result = auth_service.login_user(data["email"], data["password"])

        if not login_result: # Service indicates login failure (user not found or wrong password)
             raise Unauthorized("Invalid email or password.")

        access_token = login_result["access_token"]
        refresh_token = login_result["refresh_token"]
        user_info = login_result["user"] # Service should return basic user info

        response = make_response(jsonify({
            "message": "Login successful",
            "user": user_info # Send back basic user details
        }))
        set_access_cookies(response, access_token)
        set_refresh_cookies(response, refresh_token)
        current_app.logger.info(f"User logged in: {data['email']}")
        return response

    except Unauthorized as e:
        raise e
    except Exception as e:
        current_app.logger.error(f"Login error for {data.get('email', 'N/A')}: {e}", exc_info=True)
        raise


@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
@audit_event("refresh_access_token")
def refresh():
    try:
        identity = get_jwt_identity()
        if not identity:
             raise Unauthorized("Invalid refresh token claims.")

        new_access_token = auth_service.refresh_access_token(identity)

        response = jsonify({"message": "Access token refreshed"})
        set_access_cookies(response, new_access_token)
        current_app.logger.debug(f"Access token refreshed for user: {identity}")
        return response

    except Unauthorized as e:
         raise e
    except Exception as e:
        current_app.logger.error(f"Token refresh error for {get_jwt_identity()}: {e}", exc_info=True)
        raise


@auth_bp.route("/profile", methods=["GET"])
@jwt_required()
@audit_event("view_profile")
def profile():
    user_doc, user_id = get_current_user_doc_and_id()

    if not user_doc:
        raise NotFound("User profile not found.")

    profile_data = {
        "id": user_id,
        "first_name": user_doc.get("first_name"),
        "last_name": user_doc.get("last_name"),
        "email": user_doc.get("email"),
        "role": user_doc.get("role"),
        "isLead": user_doc.get("isLead", False),
        "created_at": user_doc.get("created_at"),
    }
    return jsonify(profile_data), 200


@auth_bp.route("/logout", methods=["POST"])
@audit_event("logout", public_ok=True)
def logout():

    identity = None
    try:
       verify_jwt_in_request(optional=True, locations=['cookies'])
       identity = get_jwt_identity()
    except Exception:
        pass

    response = make_response(jsonify({"message": "Logout successful"}))
    unset_jwt_cookies(response)
    current_app.logger.info(f"Logout processed for user: {identity or 'Unknown/Expired'}")

    return response, 200