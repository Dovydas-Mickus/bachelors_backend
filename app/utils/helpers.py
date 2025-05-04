from flask import current_app
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from werkzeug.exceptions import Forbidden, NotFound, Unauthorized

# Import your Database instance from extensions
from app.extensions import db
# Import custom exceptions if needed for type hinting or specific checks
from app.exceptions import UserNotFoundError, AccessDeniedError, ServiceError

def get_current_user_doc_and_id():
    """
    Gets the user document and ID for the currently authenticated JWT identity.
    Requires that verify_jwt_in_request() was called successfully beforehand
    (usually via @jwt_required or the audit decorator).
    Returns (user_doc, user_id) or raises NotFound/Unauthorized.
    """
    try:
        user_email = get_jwt_identity()
        if not user_email:
            # This should ideally not happen if @jwt_required is used correctly
            current_app.logger.warning("get_current_user_doc_and_id called without JWT identity.")
            raise Unauthorized("No user identity found in token.")

        # Use Database method
        user_doc = db.find_user_by_email(user_email)
        if not user_doc:
            # User existed when token was issued, but not anymore
            current_app.logger.warning(f"User document not found for valid JWT identity: {user_email}")
            raise NotFound("User associated with token not found.") # Or Unauthorized? NotFound seems appropriate.

        return (user_doc, user_doc.id)

    except Exception as e:
        # Catch potential DB errors or unexpected issues
        current_app.logger.error(f"Error fetching user for JWT identity: {e}", exc_info=True)
        # Re-raise as a generic server error or specific exception
        raise ServiceError("Failed to retrieve current user information.") from e


def get_user_doc(user_id: str):
    """Fetches a user document by ID using the database module."""
    user_doc = db.find_user_by_id(user_id) # Use Database method
    if not user_doc:
         raise UserNotFoundError(f"User with ID {user_id} not found.")
    return user_doc


def is_lead_of(current_user_id: str, target_user_id: str) -> bool:
    """Checks if current user leads a team containing the target user."""
    # This logic is better inside TeamService or Database class for encapsulation
    try:
        teams_led = db.get_teams_by_lead(current_user_id)
        # --- ADD LOGGING HERE ---
        current_app.logger.debug(f"is_lead_of: Teams led by {current_user_id}: {[t.get('id', t.get('_id')) for t in teams_led]}") # Log IDs/names
        # --- END LOGGING ---
        for team_data in teams_led: # team_data is now a dict from populate
            # --- ADD MORE LOGGING ---
            team_id_for_log = team_data.get('id', 'N/A')
            # Check if members list exists and target is in member IDs
            members = team_data.get('members', [])
            member_ids_in_team = {m.get('id') for m in members if m and m.get('id')}
            current_app.logger.debug(f"is_lead_of: Checking team {team_id_for_log}. Target: {target_user_id}. Member IDs in this team: {member_ids_in_team}")
            # --- END MORE LOGGING ---

            # Original check was on user_ids, now check populated members
            if any(member.get('id') == target_user_id for member in members):
                current_app.logger.debug(f"is_lead_of: Target user {target_user_id} FOUND in members of team {team_id_for_log}.")
                return True # Found a team they lead containing the target

            # ALSO check if the target user IS the lead themselves (edge case?)
            lead_info = team_data.get('lead')
            if lead_info and lead_info.get('id') == target_user_id:
                current_app.logger.debug(f"is_lead_of: Target user {target_user_id} IS the lead of team {team_id_for_log}.")
                # Decide if this counts. Usually, you check if lead can access member,
                # not if lead can access lead. Let's assume this should return True too.
                return True

        current_app.logger.debug(f"is_lead_of: Target user {target_user_id} NOT FOUND in any team led by {current_user_id}.")
        return False
    except Exception as e:
        current_app.logger.error(f"Error checking lead status for {current_user_id} over {target_user_id}: {e}")
        return False


def resolve_target_user(requested_id: str | None) -> tuple[str, dict]:
    """
    Resolves the target user ID and document, checking caller's permissions.
    Raises werkzeug exceptions (Forbidden, NotFound, Unauthorized) on failure.
    Returns: (target_user_id, target_user_doc)
    """
    try:
        # Get the currently authenticated caller
        caller_doc, caller_id = get_current_user_doc_and_id()
        # caller_doc could be None if get_current_user_doc_and_id raises NotFound/Unauthorized

    except (NotFound, Unauthorized) as e:
         # If the caller themselves isn't found or authenticated
         raise Forbidden("Authentication required to perform this action.") from e

    # Default to caller if no specific user requested
    if not requested_id or requested_id == caller_id:
        return caller_id, caller_doc

    # Fetch the target user document
    try:
        target_doc = get_user_doc(requested_id) # Raises UserNotFoundError if not found
        if not target_doc.get("type") == "user": # Basic type check
             raise NotFound("Target ID does not correspond to a user.")
    except UserNotFoundError as e:
         raise NotFound(str(e)) # Propagate as werkzeug NotFound

    # Authorization Check: Admin or Lead of the target user's team?
    caller_role = caller_doc.get("role")
    is_authorized = False

    if caller_role == "admin":
        is_authorized = True
    # Check if caller is team lead *and* leads the target user
    elif caller_role == "team_lead":
         # This check might be expensive if called frequently
         if is_lead_of(caller_id, requested_id):
              is_authorized = True

    # Add other roles/checks if necessary

    if is_authorized:
        current_app.logger.debug(f"User {caller_id} authorized to act as user {requested_id}.")
        return requested_id, target_doc
    else:
        current_app.logger.warning(f"User {caller_id} (role: {caller_role}) DENIED action on behalf of user {requested_id}.")
        raise Forbidden("Not authorized to access this user's data.")