from http.client import INTERNAL_SERVER_ERROR
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required
from werkzeug.exceptions import BadRequest, NotFound, Forbidden

# Assuming TeamService in app/services/team_service.py
from app.exceptions import ServiceError
from app.services.team_service import team_service, TeamNotFoundError, TeamAccessDeniedError, TeamValidationError
from app.utils.helpers import get_current_user_doc_and_id
from app.utils.audit import audit_event

teams_bp = Blueprint('teams', __name__, url_prefix='/teams')

@teams_bp.route("/<string:team_id>/overview", methods=["GET"])
@jwt_required()
@audit_event("view_team_overview", target_arg="team_id")
def get_team_overview(team_id):
    user_doc, user_id = get_current_user_doc_and_id()
    if not user_doc:
        raise NotFound("User not found.") # Should not happen with jwt_required

    try:
        # TeamService handles permission checks based on user_id/role and team_id
        overview_data = team_service.get_team_overview(user_id, user_doc.get("role"), team_id)
        return jsonify(overview_data), 200
    except TeamNotFoundError as e:
        raise NotFound(str(e))
    except TeamAccessDeniedError as e:
        raise Forbidden(str(e))
    except Exception as e:
        current_app.logger.error(f"Error getting team overview for {team_id}: {e}", exc_info=True)
        raise # Let generic handler manage


@teams_bp.route("/my_teams", methods=["GET"])
@jwt_required()
@audit_event("list_my_teams")
def list_my_teams():
    user_doc, user_id = get_current_user_doc_and_id()
    if not user_doc:
        raise NotFound("User not found.")

    # Permission check can be inside the service or here
    # if user_doc.get("role") != "team_lead" and user_doc.get("role") != "admin":
    #     raise Forbidden("Access denied. Must be a team lead or admin.")
    # Let's assume service handles it based on isLead flag or role

    try:
        # Service needs user_id to find teams they lead
        teams = team_service.get_teams_led_by_user(user_id)
        return jsonify(teams), 200
    except Exception as e:
        current_app.logger.error(f"Error fetching 'my_teams' for user {user_id}: {e}", exc_info=True)
        raise

@teams_bp.route("/associated", methods=["GET"])
@jwt_required()
@audit_event("list_associated_teams")
def list_associated_teams():
    """
    GET /teams/associated
    Returns a list of all teams the currently authenticated user is a member of (including lead).
    """
    user_doc, user_id = get_current_user_doc_and_id()
    if not user_doc:
        raise NotFound("Current user not found.")

    try:
        associated_teams = team_service.get_user_associated_teams(user_id)
        return jsonify(associated_teams), 200
    except ServiceError as e:
         current_app.logger.error(f"ServiceError fetching associated teams for user {user_id}: {e}")
         # Now InternalServerError is defined and can be raised
    except Exception as e:
        current_app.logger.error(f"Unexpected error fetching associated teams for user {user_id}: {e}", exc_info=True)
        # Now InternalServerError is defined and can be raised

@teams_bp.route("/create", methods=["POST"]) # Changed route slightly for consistency
@jwt_required()
@audit_event("create_team")
def create_team():
    user_doc, user_id = get_current_user_doc_and_id()
    if not user_doc or user_doc.get("role") != "admin":
        raise Forbidden("Access denied. Admin privileges required.")

    data = request.json
    if not data:
        raise BadRequest("Request body must be JSON.")

    name = data.get("name")
    lead_email = data.get("lead") # Email of the lead user
    member_emails = data.get("emails", []) # List of member emails (optional)

    if not name or not lead_email:
        raise BadRequest("Missing required fields: 'name' and 'lead' email.")

    try:
        # TeamService handles finding users by email, creating team doc, setting lead status
        team_info = team_service.create_team(
            admin_id=user_id,
            team_name=name,
            lead_user_email=lead_email,
            member_user_emails=member_emails
        )
        return jsonify({"message": "Team created successfully", "team": team_info}), 201
    except TeamValidationError as e: # Catch specific validation errors from service
        raise BadRequest(str(e))
    except TeamNotFoundError as e: # Catch if lead/member user not found
         raise NotFound(str(e))
    except Exception as e:
        current_app.logger.error(f"Error creating team '{name}': {e}", exc_info=True)
        raise


@teams_bp.route("/edit", methods=["POST"]) # Using POST for edit, could be PUT/PATCH
@jwt_required()
@audit_event("edit_team") # Target ID might be implicitly logged if service handles it
def edit_team():
    user_doc, user_id = get_current_user_doc_and_id()
    if not user_doc:
        raise NotFound("User not found.")

    data = request.json
    if not data or not data.get("team_id"):
        raise BadRequest("Missing 'team_id' in request body.")

    team_id = data["team_id"]

    # Permission check should happen in the service layer
    # Service needs current user_id, user_role, and team_id to check permissions

    try:
        updated_team = team_service.edit_team(
            editor_id=user_id,
            editor_role=user_doc.get("role"),
            team_id=team_id,
            new_name=data.get("new_name"),
            new_lead_email=data.get("lead"),
            add_user_emails=data.get("add_emails", []),
            remove_user_emails=data.get("remove_emails", [])
        )
        return jsonify({"message": "Team updated successfully", "team": updated_team}), 200
    except TeamValidationError as e:
        raise BadRequest(str(e))
    except TeamNotFoundError as e:
         raise NotFound(str(e))
    except TeamAccessDeniedError as e:
         raise Forbidden(str(e))
    except Exception as e:
        current_app.logger.error(f"Error editing team {team_id}: {e}", exc_info=True)
        raise


# Combined route for getting a team by ID or Name from query params
@teams_bp.route("/details", methods=["GET"]) # Example route name
@jwt_required()
# Audit event needs careful target handling if using query params
# Maybe log the found team_id from the response or handle in service?
@audit_event("get_team") # Requires custom logic to determine target if using name
def get_team():
    user_doc, user_id = get_current_user_doc_and_id()
    if not user_doc:
         raise NotFound("User not found.")

    # Permissions checked within the service based on user role/lead status

    team_id = request.args.get("team_id")
    team_name = request.args.get("name")

    if not team_id and not team_name:
        raise BadRequest("Provide either 'team_id' or 'name' query parameter.")

    try:
        # Service handles lookup by ID or name and permission check
        team_data = team_service.get_team_details(
            requestor_id=user_id,
            requestor_role=user_doc.get("role"),
            team_id=team_id,
            team_name=team_name
        )
        # Set the target for audit log *after* finding the team
        # if team_data and team_data.get('id'):
        #     g.__audit_target = team_data['id'] # Example using g context
        return jsonify(team_data), 200
    except TeamNotFoundError as e:
        raise NotFound(str(e))
    except TeamAccessDeniedError as e:
        raise Forbidden(str(e))
    except Exception as e:
        current_app.logger.error(f"Error getting team details ({team_id=}, {team_name=}): {e}", exc_info=True)
        raise


@teams_bp.route("/<string:team_id>", methods=["DELETE"])
@jwt_required()
@audit_event("delete_team", target_arg="team_id")
def delete_team(team_id):
    user_doc, user_id = get_current_user_doc_and_id()
    if not user_doc or user_doc.get("role") != "admin":
        raise Forbidden("Access denied. Admin privileges required.")

    try:
        # Service handles the deletion logic
        success = team_service.delete_team(admin_id=user_id, team_id_to_delete=team_id)
        if success:
             return '', 204 # No Content on successful deletion
        else:
             # This might happen if the service returns False instead of raising Not Found
             raise NotFound(f"Team with ID {team_id} not found.")
    except TeamNotFoundError as e: # If service raises this specifically
        raise NotFound(str(e))
    except TeamAccessDeniedError as e: # Should be caught by role check above, but maybe other reasons
        raise Forbidden(str(e))
    except Exception as e:
        current_app.logger.error(f"Error deleting team {team_id}: {e}", exc_info=True)
        raise


@teams_bp.route("/all", methods=["GET"])
@jwt_required()
@audit_event("get_all_teams")
def get_all_teams():
    user_doc, user_id = get_current_user_doc_and_id()
    if not user_doc:
        raise NotFound("User not found.")

    is_admin = user_doc.get("role") == "admin"
    # Check lead status directly or let service handle permission
    # is_lead = user_doc.get("isLead") is True
    # if not (is_admin or is_lead):
    #      raise Forbidden("Access denied. Must be admin or team lead.")

    try:
        # Service determines which teams to return based on user's role/status
        teams = team_service.get_accessible_teams(user_id, user_doc.get("role"), user_doc.get("isLead"))
        return jsonify(teams), 200
    except TeamAccessDeniedError as e:
         raise Forbidden(str(e)) # If service explicitly denies access
    except Exception as e:
        current_app.logger.error(f"Error getting all/accessible teams for user {user_id}: {e}", exc_info=True)
        raise