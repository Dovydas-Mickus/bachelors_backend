# app/services/team_service.py

import couchdb
from flask import current_app
from datetime import datetime, timezone # Needed for overview calculation

# Import db instance and custom exceptions
from app.extensions import db
from app.exceptions import (
    TeamNotFoundError, TeamAccessDeniedError, TeamValidationError,
    UserNotFoundError, ServiceError
)
# Import logging if you need it directly (though current_app.logger is preferred)
import logging

class TeamService:

    def _check_permission(self, requestor_id, requestor_role, required_role=None, team_id=None, allowed_roles=None):
        """
        Helper to check permissions. DEPRECATED (prefer checks within methods).
        Checks if requestor has required_role OR is lead of team_id OR has role in allowed_roles.
        """
        current_app.logger.warning("_check_permission helper is deprecated. Integrate checks into service methods.")
        if requestor_role == "admin":
            return True

        is_lead = False
        if team_id:
             # Avoid fetching team just for permission check if possible
             # Integrate logic into the main method
             pass

        # ... (rest of deprecated logic) ...

        raise TeamAccessDeniedError("You do not have permission to perform this action.")


    def create_team(self, admin_id: str, team_name: str, lead_user_email: str, member_user_emails: list):
        """Creates a new team (Admin only)."""
        current_app.logger.info(f"Admin {admin_id} attempting to create team '{team_name}' with lead {lead_user_email}")
        # Admin check is done in route

        if not team_name or not team_name.strip():
            raise TeamValidationError("Team name cannot be empty.")
        if not lead_user_email:
             raise TeamValidationError("Lead user email cannot be empty.")

        # Call database method to create the team document
        # Pass EMAILS as required by the database method definition
        try:
            # Database.create_team now handles user lookups and raises ValueError if not found
            team_id, _ = db.create_team(
                name=team_name.strip(),
                lead_email=lead_user_email,        # Pass lead email
                member_emails=member_user_emails   # Pass member emails
            )
            if not team_id:
                 # Should not happen if db.create_team raises on error, but defensive check
                 raise ServiceError("Database method create_team succeeded but did not return a team ID.")
            current_app.logger.info(f"Team '{team_name}' (ID: {team_id}) document created with lead {lead_user_email}.")

            # --- Need lead_user_id for set_user_lead_status ---
            # Find the lead user ID *after* successful team creation
            lead_user = db.find_user_by_email(lead_user_email)
            if not lead_user:
                 # This is unexpected if db.create_team worked, log critically
                 current_app.logger.error(f"CRITICAL: Could not find lead user {lead_user_email} by email after team {team_id} creation.")
                 # Decide how to proceed. Team exists, but lead status might be wrong.
                 # Optionally try to fetch lead_id from the newly created team doc?
                 # team_doc_check = db.get_team(team_id=team_id) # Might fail if get_team needs population
                 # lead_user_id = team_doc_check.get('lead_id') if team_doc_check else None
            else:
                 lead_user_id = lead_user.id
                 current_app.logger.info(f"Found lead user ID {lead_user_id} for email {lead_user_email}.")
                 # Update the Lead User's Status
                 try:
                     update_success = db.set_user_lead_status(lead_user_id, True)
                     if not update_success:
                          # db.set_user_lead_status logs errors internally now
                          current_app.logger.warning(f"Team '{team_name}' created, but failed to set isLead=true for user {lead_user_id}.")
                     # else: # No need to log success, db method does it
                 except Exception as e_lead_status: # Catch potential errors during status update
                      current_app.logger.error(f"Error calling set_user_lead_status for {lead_user_id} after team creation: {e_lead_status}", exc_info=True)
                      # Don't fail team creation, but log this issue

            # Return info about the created team (adjust payload as needed by frontend)
            # Fetching full details might be desired
            created_team_details = self.get_team_details(requestor_id=admin_id, requestor_role="admin", team_id=team_id)
            return created_team_details if created_team_details else {"id": team_id, "name": team_name, "lead_email": lead_user_email}

        except ValueError as ve: # Catch specific ValueError from db.create_team if user not found
             current_app.logger.warning(f"Team creation failed for '{team_name}': {ve}")
             # Raise as more specific error type for the route handler
             raise UserNotFoundError(str(ve))
        except Exception as e:
            # Catch other potential DB errors (e.g., connection, save conflict)
            current_app.logger.error(f"Database error during team creation '{team_name}': {e}", exc_info=True)
            raise ServiceError("Failed to save team creation data to the database.")


    def edit_team(self, editor_id: str, editor_role: str, team_id: str,
                  new_name: str | None, new_lead_email: str | None,
                  add_user_emails: list | None, remove_user_emails: list | None):
        """Edits an existing team (Admin or Team Lead)."""
        current_app.logger.info(f"User {editor_id} (role: {editor_role}) attempting to edit team {team_id}")

        # Fetch original team to check permissions and current state
        team = db.get_team(team_id=team_id) # Use basic fetch first if get_team does population
        if not team:
            raise TeamNotFoundError(f"Team with ID {team_id} not found.")

        # Permission Check: Admin or the actual lead of *this* specific team
        current_lead_id = team.get("lead_id") # Assuming basic get_team returns lead_id
        if not current_lead_id:
             current_app.logger.error(f"Team {team_id} found but has no lead_id field. Cannot verify permissions.")
             raise ServiceError("Team data integrity issue: missing lead ID.")

        is_current_lead = (current_lead_id == editor_id)
        if not (editor_role == "admin" or is_current_lead):
             current_app.logger.warning(f"Edit denied for team {team_id}: User {editor_id} is not admin or lead {current_lead_id}.")
             raise TeamAccessDeniedError("Only admins or the team lead can edit this team.")

        # Prepare arguments for the database edit method
        # Database.edit_team handles finding users by email now
        edit_args = {
             "team_id": team_id,
             "new_name": new_name, # Pass None if not changing
             "new_lead_email": new_lead_email, # Pass None if not changing
             "add_user_emails": add_user_emails, # Pass None or empty list if not changing
             "remove_user_emails": remove_user_emails # Pass None or empty list if not changing
        }

        # Filter out None values if db.edit_team expects only provided keys
        # filtered_args = {k: v for k, v in edit_args.items() if v is not None}
        # if not filtered_args:
        #      raise TeamValidationError("No changes provided for team edit.")

        # Call database method to apply changes
        # Database.edit_team should handle lookups, updates, lead status changes
        try:
            updated_doc_id, _ = db.edit_team(**edit_args) # Use keyword arguments

            if not updated_doc_id:
                 # This might happen if edit_team returns None/False on no changes or error
                 # Re-fetch to check or assume no change if that's the intended logic
                 current_app.logger.info(f"Edit team {team_id} resulted in no changes or db.edit_team returned falsy.")
                 # Optionally return current details if no change occurred
                 # return self.get_team_details(editor_id, editor_role, team_id=team_id)
                 return team # Return original fetched data if no change

            current_app.logger.info(f"Team {team_id} updated successfully by user {editor_id}.")
            # Fetch and return updated team details
            return self.get_team_details(editor_id, editor_role, team_id=team_id)

        except ValueError as ve: # Catch validation errors from db.edit_team
             current_app.logger.warning(f"Validation error editing team {team_id}: {ve}")
             raise TeamValidationError(str(ve))
        except couchdb.ResourceNotFound: # If team deleted between fetch and edit
            raise TeamNotFoundError(f"Team {team_id} not found during edit operation.")
        except Exception as e:
            current_app.logger.error(f"Database error editing team {team_id}: {e}", exc_info=True)
            raise ServiceError("Failed to update team data in the database.")


    def get_team_details(self, requestor_id: str, requestor_role: str, team_id: str | None = None, team_name: str | None = None):
        """Gets detailed information for a specific team (Admin, Lead, or Member)."""
        if not team_id and not team_name:
             raise TeamValidationError("Either team_id or team_name must be provided.")

        # Fetch team using the Database method which now includes user population
        team_details = db.get_team(team_id=team_id, name=team_name)
        if not team_details:
            raise TeamNotFoundError(f"Team not found with {'ID '+str(team_id) if team_id else 'name '+str(team_name)}")

        # Permission Check: Admin, the lead, or a member of *this* team
        # Need to extract lead and member IDs from the populated structure
        lead_user_info = team_details.get('lead') # From populated data
        member_user_infos = team_details.get('members', []) # From populated data

        team_lead_id = lead_user_info.get('id') if lead_user_info else None
        member_ids = {member['id'] for member in member_user_infos if member and 'id' in member}
        all_member_ids_in_team = member_ids.copy()
        if team_lead_id:
             all_member_ids_in_team.add(team_lead_id)

        is_lead = (team_lead_id == requestor_id)
        is_member = (requestor_id in all_member_ids_in_team)

        if not (requestor_role == "admin" or is_lead or is_member):
            current_app.logger.warning(f"Access denied for user {requestor_id} to view details of team {team_details.get('id')}")
            raise TeamAccessDeniedError("You must be an admin, the lead, or a member to view this team's details.")

        current_app.logger.debug(f"Access granted for user {requestor_id} to view details of team {team_details.get('id')}")
        return team_details # Return the populated structure


    def get_team_overview(self, requestor_id: str, requestor_role: str, team_id: str):
        """Calculates and returns overview statistics for a team (Admin or Lead)."""
        # Fetch basic team info first for permission check
        team = db.get_team(team_id=team_id) # Use basic fetch if possible
        if not team:
             raise TeamNotFoundError(f"Team with ID {team_id} not found.")

        # Permission Check: Admin or Lead only
        team_lead_id = team.get("lead_id") # Assumes basic fetch returns lead_id
        is_lead = (team_lead_id == requestor_id)
        if not (requestor_role == "admin" or is_lead):
             current_app.logger.warning(f"Denied overview access for team {team_id}: User {requestor_id} not admin or lead.")
             raise TeamAccessDeniedError("Only admins or the team lead can view the team overview.")

        # Fetch projects & tasks (requires these methods in Database class)
        try:
            # These methods should return lists of documents (or dicts)
            projects = db.get_projects_by_team(team_id)
            tasks = db.get_tasks_by_team(team_id)
            current_app.logger.debug(f"Team {team_id} overview: Found {len(projects)} projects, {len(tasks)} tasks.")
        except Exception as e:
             current_app.logger.error(f"Failed to fetch projects/tasks for team overview {team_id}: {e}", exc_info=True)
             raise ServiceError("Could not retrieve project/task data for team overview.")

        # Calculate stats (copied logic from original route)
        open_tasks = [t for t in tasks if isinstance(t, dict) and not t.get("completed", False)]
        completed_tasks = [t for t in tasks if isinstance(t, dict) and t.get("completed", False)]

        durations_minutes = []
        for t in completed_tasks:
            try:
                # Ensure timestamps are valid ISO strings and handle potential timezone issues
                start_str = t.get("created_at")
                end_str = t.get("completed_at")
                if not start_str or not end_str: continue # Skip if timestamps missing

                start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))

                # Ensure both are offset-aware or offset-naive before subtraction
                # Assuming ISO format implies offset-aware (often UTC)
                if start.tzinfo is None or end.tzinfo is None:
                     # Log warning if timezone info inconsistent or missing
                     current_app.logger.debug(f"Task {t.get('id','N/A')} has naive timestamp(s), assuming UTC for duration calculation.")
                     start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
                     end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end

                # Ensure end time is after start time
                if end > start:
                    duration = (end - start).total_seconds()
                    if duration >= 0:
                        durations_minutes.append(duration / 60.0)
                else:
                     current_app.logger.warning(f"Task {t.get('id','N/A')} completed_at is not after created_at. Skipping duration.")

            except (KeyError, ValueError, TypeError) as e:
                current_app.logger.warning(f"Could not calculate duration for task {t.get('id','N/A')} in team {team_id}: {e}")
                continue # Skip task if timestamps invalid

        avg_minutes = sum(durations_minutes) / len(durations_minutes) if durations_minutes else 0.0
        current_app.logger.debug(f"Team {team_id} overview calculation: Avg completion {avg_minutes:.2f} mins from {len(durations_minutes)} tasks.")

        return {
            "teamId": team_id, # Use the input team_id
            "teamName": team.get("name", "Unknown"), # Use fetched name
            "projectCount": len(projects),
            "openTasksCount": len(open_tasks),
            "completedTasksCount": len(completed_tasks),
            "avgCompletionTimeMinutes": round(avg_minutes, 2), # Round for cleaner output
        }


    def delete_team(self, admin_id: str, team_id_to_delete: str):
        """Deletes a team (Admin only)."""
        current_app.logger.warning(f"Admin {admin_id} initiating deletion of team {team_id_to_delete}")
        # Admin check done in route

        # Call database method which handles finding, deleting, and lead status update
        try:
            delete_success = db.delete_team(team_id_to_delete) # db method returns bool
            if not delete_success:
                 # This likely means team wasn't found or internal DB error occurred
                 # db.delete_team should log specifics, raise NotFoundError here
                 raise TeamNotFoundError(f"Team with ID {team_id_to_delete} not found or could not be deleted.")

            current_app.logger.info(f"Team {team_id_to_delete} deleted successfully by admin {admin_id}.")
            return True
        except TeamNotFoundError:
             raise # Propagate not found error
        except Exception as e:
             # Catch potential unexpected errors from db.delete_team
             current_app.logger.error(f"Unexpected error during service call to delete team {team_id_to_delete}: {e}", exc_info=True)
             raise ServiceError("An error occurred while attempting to delete the team.")


    def get_teams_led_by_user(self, user_id: str):
        """Gets all teams where the given user is the lead."""
        current_app.logger.debug(f"Fetching teams led by user {user_id}")
        try:
            # Database method should handle fetching and potentially populating user details
            teams = db.get_teams_by_lead(user_id)
            current_app.logger.debug(f"Found {len(teams)} teams led by user {user_id}")
            return teams
        except Exception as e:
            current_app.logger.error(f"Failed to get teams led by user {user_id}: {e}", exc_info=True)
            raise ServiceError("Could not retrieve teams led by user.")

    def get_user_associated_teams(self, user_id: str):
        """Gets all teams the specified user is associated with (lead or member)."""
        current_app.logger.debug(f"Fetching all associated teams for user {user_id}")
        try:
            # Call the new database method
            teams = db.get_teams_for_user(user_id)
            current_app.logger.debug(f"Found {len(teams)} associated teams for user {user_id} from database.")
            return teams
        except Exception as e:
            # Catch potential errors from the database layer
            current_app.logger.error(f"Service error fetching associated teams for user {user_id}: {e}", exc_info=True)
            # Re-raise as a ServiceError or return empty list depending on desired handling
            raise ServiceError("Could not retrieve teams associated with the user.")

    def get_accessible_teams(self, user_id: str, user_role: str, is_lead: bool | None):
        """Gets teams accessible to the user based on role/status."""
        # Ensure is_lead is treated as boolean
        is_actually_lead = is_lead is True

        if user_role == "admin":
            current_app.logger.debug(f"Fetching all teams for admin user {user_id}")
            try:
                 # Database method should return populated list
                 return db.get_all_teams()
            except Exception as e:
                 current_app.logger.error(f"Failed to get all teams for admin {user_id}: {e}", exc_info=True)
                 raise ServiceError("Could not retrieve list of all teams.")

        elif is_actually_lead:
            current_app.logger.debug(f"Fetching teams led by user {user_id} (isLead=True)")
            # Reuse existing method
            return self.get_teams_led_by_user(user_id)
        else:
            # Regular users (workers) or leads not marked as such
            current_app.logger.info(f"User {user_id} (role: {user_role}, lead: {is_actually_lead}) requesting team list - returning empty list (no permission).")
            # No access for non-admin/non-lead to the "all teams" style list in this implementation
            return [] # Return empty list


    def is_user_lead_of_any_team(self, user_id: str, exclude_team_id: str | None = None) -> bool:
        """Checks if a user is a lead of any team (optionally excluding one)."""
        # Delegate check to the database method
        try:
             return db.is_user_leading_any_team(user_id, exclude_team_id=exclude_team_id)
        except Exception as e:
             current_app.logger.error(f"Error checking if user {user_id} leads any team: {e}", exc_info=True)
             return False # Fail safe


# Instantiate the service
team_service = TeamService()