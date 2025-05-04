# app/database.py

from datetime import datetime, timezone
import couchdb
import logging

from app.exceptions import ServiceError, TeamNotFoundError

# Configure logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__) # Use a specific logger for this module

class Database:
    # Connection details (Consider moving to config/env vars)
    COUCHDB_URL = "http://admin:dovydas994@0.0.0.0:5984/"
    DB_NAME = "nas_db"

    # --- Initialization and Indexing ---

    def __init__(self):
        """Initialize database connection."""
        self.couch = None
        self.db = None # Initialize db attribute
        log.info("Attempting to connect to CouchDB...")
        try:
            self.couch = couchdb.Server(self.COUCHDB_URL, session=couchdb.Session(timeout=20)) # Added timeout
            if self.DB_NAME not in self.couch:
                self.db = self.couch.create(self.DB_NAME)
                log.info(f"Database '{self.DB_NAME}' created.")
            else:
                self.db = self.couch[self.DB_NAME]
                log.info(f"Connected to existing database '{self.DB_NAME}'.")
            # Ensure indexes exist AFTER successful connection
            if self.db:
                 self._ensure_indexes()
            else:
                 log.critical(f"Database object self.db is None after connection attempt!")

        except couchdb.http.ServerError as e:
             log.error(f"❌ CouchDB ServerError connecting to {self.COUCHDB_URL}: {e} (Status: {e.args[0] if e.args else 'N/A'})")
        except couchdb.http.Unauthorized as e:
             log.error(f"❌ CouchDB Unauthorized error connecting to {self.COUCHDB_URL.replace('admin:dovydas994@', 'admin:****@')}: {e}")
        except Exception as e:
            log.error(f"❌ Failed to connect/initialize CouchDB at {self.COUCHDB_URL}: {e}", exc_info=True)
            # Ensure db is None if connection failed
            self.couch = None
            self.db = None
        log.info("Database __init__ finished.") # Confirm init completes


    def _ensure_indexes(self):
        """Creates necessary Mango indexes if they don't exist."""
        if not self.db:
            log.warning("Cannot ensure indexes: Database connection not established.")
            return
        log.info("Ensuring database indexes...")

        indexes_to_create = [
            {"index": {"fields": ["type", "email"]}, "name": "idx-user-type-email", "type": "json"},
            {"index": {"fields": ["type"]}, "name": "idx-team-type", "type": "json"},
            {"index": {"fields": ["type", "lead_id"]}, "name": "idx-team-type-lead", "type": "json"},
            {"index": {"fields": ["type", "team_id"]}, "name": "idx-project-type-team", "type": "json"},
            {"index": {"fields": ["type", "team_id"]}, "name": "idx-task-type-team", "type": "json"},
            {"index": {"fields": ["type", "user_id"]}, "name": "idx-access-type-user", "type": "json"},
            {"index": {"fields": ["type", "token"]}, "name": "idx-share-type-token", "type": "json"},
            {"index": {"fields": ["type", "user_ids"]}, "name": "idx-team-type-user_ids", "type": "json"} # Index for team membership
        ]

        try:
            existing_indexes = self.db.index().get('indexes', [])
            existing_index_names = {idx.get('name') for idx in existing_indexes}
            log.debug(f"Existing indexes: {existing_index_names}")

            for index_def in indexes_to_create:
                index_name = index_def.get("name")
                ddoc_id = f"_design/{index_name}" # Design doc name based on index name
                if index_name not in existing_index_names:
                    log.info(f"Creating index '{index_name}'...")
                    try:
                        # Use fields, name, and ddoc parameters
                        self.db.create_index(index_def["index"]["fields"], name=index_name, ddoc=ddoc_id)
                        log.info(f"Successfully created index '{index_name}'.")
                    except Exception as ie:
                         log.error(f"❌ Error creating index '{index_name}': {ie}", exc_info=True)
                # else: # Optional: log that index already exists
                #    log.debug(f"Index '{index_name}' already exists.")
            log.info("Index check complete.")
        except Exception as e:
            log.error(f"❌ Error ensuring indexes: {e}", exc_info=True)

    # --- User Methods ---

    def find_user_by_id(self, user_id: str) -> couchdb.Document | None:
        """Finds a user document directly by its ID."""
        if not self.db:
            log.error("Database connection not available for find_user_by_id.")
            return None
        try:
            user_doc = self.db.get(user_id)
            if user_doc and user_doc.get("type") == "user":
                return user_doc
            elif user_doc:
                log.warning(f"Document found for ID {user_id}, but it's not type 'user'.")
                return None
            else:
                return None # Should be caught by ResourceNotFound
        except couchdb.ResourceNotFound:
            log.debug(f"User document with ID {user_id} not found.")
            return None
        except Exception as e:
            log.error(f"❌ Error fetching user by ID {user_id}: {e}", exc_info=True)
            return None

    def find_user_by_email(self, email):
        if not self.db: return None
        selector = {'type': 'user', 'email': email}
        try:
            results = list(self.db.find({
                'selector': selector,
                'limit': 1,
                'use_index': '_design/idx-user-type-email/json'
            }))
            if results:
                return results[0] # Return the couchdb Document object
            return None
        except Exception as e:
            log.error(f"Error finding user by email {email}: {e}", exc_info=True)
            return None

    def add_user(self, first_name, last_name, email, password_hash, role):
        if not self.db: return None
        user_doc = {
            "_id": f"user:{email}", # Example: Use email for potentially predictable ID
            "type": "user",
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "password_hash": password_hash,
            "role": role,
            "isLead": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            doc_id, rev = self.db.save(user_doc)
            log.info(f"User '{email}' added with ID: {doc_id}")
            return doc_id
        except couchdb.ResourceConflict:
             log.warning(f"User add conflict for email {email} (ID: user:{email}). User might already exist.")
             # Optionally try to fetch the existing user here?
             return f"user:{email}" # Return potential existing ID
        except Exception as e:
            log.error(f"Failed to add user {email}: {e}", exc_info=True)
            return None

    def set_user_lead_status(self, user_id, is_lead_status: bool):
        """Sets or unsets the 'isLead' field on a user document."""
        if not self.db:
            log.error("Database connection not available for set_user_lead_status.")
            return False
        try:
            user_doc = self.db.get(user_id)
            if not user_doc or user_doc.get("type") != "user":
                 log.warning(f"Attempted to set lead status on non-user/missing document: {user_id}")
                 return False

            # Only save if the status actually changes
            current_status = user_doc.get("isLead", False)
            new_status = bool(is_lead_status)
            if current_status != new_status:
                user_doc["isLead"] = new_status
                self.db.save(user_doc)
                log.info(f"Successfully updated isLead={new_status} for user {user_id}")
            else:
                 log.debug(f"isLead status for user {user_id} is already {new_status}. No update needed.")
            return True
        except couchdb.ResourceNotFound:
            log.error(f"User document with ID {user_id} not found during lead status update.")
            return False
        except Exception as e:
            log.error(f"❌ Error updating lead status for user {user_id}: {e}", exc_info=True)
            return False

    def get_all_users(self):
        if not self.db: return []
        users = []
        try:
            selector = {'type': 'user'}
            all_user_docs = list(self.db.find({
                'selector': selector,
                 'use_index': '_design/idx-user-type-email/json' # Can use this index
            }))
            for user_doc in all_user_docs:
                 users.append({
                    # Use _id here for consistency if models expect it
                    "id": user_doc.id, # Or "_id": user_doc.id
                    "first_name": user_doc.get("first_name"),
                    "last_name": user_doc.get("last_name"),
                    "email": user_doc.get("email"),
                    "role": user_doc.get("role"),
                    "isLead": user_doc.get("isLead", False)
                })
            log.debug(f"Fetched {len(users)} users.")
            return users
        except Exception as e:
            log.error(f"❌ Error in get_all_users: {e}", exc_info=True)
            return []

    def delete_user(self, user_id: str) -> bool:
        """Deletes a user document and their access control document."""
        if not self.db:
            log.error(f"Attempted to delete user {user_id} but database is not connected.")
            return False
        user_deleted = False
        try:
            user_doc = self.db.get(user_id)
            if user_doc and user_doc.get("type") == "user":
                log.info(f"Found user document {user_id} for deletion.")
                self.db.delete(user_doc)
                log.info(f"Successfully deleted user document {user_id}.")
                user_deleted = True
            else:
                log.warning(f"User document {user_id} not found or not type 'user'.")
                # Still proceed to delete access doc if user doc was missing
                user_deleted = False # Explicitly false if not found

            # Attempt to delete associated access control doc regardless
            access_doc_id = f"access_{user_id}"
            try:
                 access_doc = self.db.get(access_doc_id)
                 if access_doc:
                      self.db.delete(access_doc)
                      log.info(f"Deleted associated access document {access_doc_id}.")
            except couchdb.ResourceNotFound:
                 log.debug(f"No associated access document {access_doc_id} found to delete.")
            except Exception as e_acc:
                 log.error(f"Error deleting access doc {access_doc_id} for user {user_id}: {e_acc}")

            return user_deleted # Return true only if user doc was found and deleted

        except couchdb.ResourceNotFound:
             log.warning(f"User document {user_id} not found (caught during get).")
             # Still try deleting access doc
             try:
                 access_doc = self.db.get(f"access_{user_id}")
                 if access_doc: self.db.delete(access_doc)
             except: pass # Ignore errors here
             return False
        except Exception as e:
            log.error(f"Error deleting user {user_id}: {e}", exc_info=True)
            return False

    # --- Team Methods ---

    def create_team(self, name, lead_email, member_emails):
        if not self.db: raise ConnectionError("Database not connected")
        lead_user_doc = self.find_user_by_email(lead_email)
        if not lead_user_doc:
            raise ValueError(f"Lead user with email '{lead_email}' not found")
        lead_id = lead_user_doc.id

        member_ids = []
        processed_member_emails = set()
        for email in member_emails:
            if email not in processed_member_emails and email != lead_email:
                member_doc = self.find_user_by_email(email)
                if member_doc:
                    member_ids.append(member_doc.id)
                else:
                    log.warning(f"Member email '{email}' not found, skipping for team '{name}'.")
                processed_member_emails.add(email)

        team_doc = {
            "type": "team",
            "name": name,
            "lead_id": lead_id,
            "user_ids": list(set([lead_id] + member_ids)), # Include lead
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            doc_id, rev = self.db.save(team_doc)
            log.info(f"Team '{name}' created with ID: {doc_id}")
            return doc_id, rev
        except Exception as e:
            log.error(f"Failed to create team '{name}': {e}", exc_info=True)
            raise

    def edit_team(self, team_id, new_name=None, new_lead_email=None, add_user_emails=None, remove_user_emails=None):
        if not self.db: raise ConnectionError("Database not connected")
        try:
            team_doc = self.db[team_id]
            if team_doc.get("type") != "team":
                raise ValueError("Document is not a team")

            updated = False
            original_lead_id = team_doc.get("lead_id") # Store before potential changes

            if new_name is not None and team_doc.get("name") != new_name:
                team_doc["name"] = new_name
                updated = True

            current_user_ids = set(team_doc.get("user_ids", []))
            original_user_ids = current_user_ids.copy()

            # Add users
            if add_user_emails:
                for email in set(add_user_emails): # Use set to avoid duplicate lookups
                    user_doc = self.find_user_by_email(email)
                    if user_doc:
                        if user_doc.id not in current_user_ids:
                             current_user_ids.add(user_doc.id)
                             # updated = True # Adding users counts as update below
                    else:
                        log.warning(f"Edit team '{team_id}': User to add '{email}' not found, skipping.")

            # Remove users
            ids_to_remove = set()
            if remove_user_emails:
                for email in set(remove_user_emails):
                    user_doc = self.find_user_by_email(email)
                    if user_doc:
                        ids_to_remove.add(user_doc.id)
                    else:
                        log.warning(f"Edit team '{team_id}': User to remove '{email}' not found, skipping.")

                # Check if trying to remove the current lead without replacement
                if original_lead_id in ids_to_remove and not new_lead_email:
                     log.error(f"Edit team '{team_id}': Cannot remove the current lead '{original_lead_id}' without assigning a new lead via 'new_lead_email'.")
                     raise ValueError("Cannot remove the current team lead without assigning a new one.")

                current_user_ids.difference_update(ids_to_remove)

            if current_user_ids != original_user_ids:
                 team_doc["user_ids"] = list(current_user_ids)
                 updated = True

            # Handle changing the lead
            new_lead_id = None
            if new_lead_email:
                lead_user_doc = self.find_user_by_email(new_lead_email)
                if not lead_user_doc:
                     log.error(f"Edit team '{team_id}': New lead email '{new_lead_email}' not found. Lead not changed.")
                     # Optionally raise ValueError here if lead must exist
                     # raise ValueError(f"New lead user with email '{new_lead_email}' not found.")
                else:
                    new_lead_id = lead_user_doc.id
                    if new_lead_id != original_lead_id:
                        team_doc["lead_id"] = new_lead_id
                        # Ensure the new lead is also in the user_ids list
                        if new_lead_id not in team_doc["user_ids"]:
                             team_doc["user_ids"].append(new_lead_id) # Should be list now
                        updated = True
                        # --- Update isLead status ---
                        if original_lead_id:
                             is_still_a_lead = self.is_user_leading_any_team(original_lead_id, exclude_team_id=team_id)
                             if not is_still_a_lead:
                                self.set_user_lead_status(original_lead_id, False)
                        self.set_user_lead_status(new_lead_id, True)

            if updated:
                log.info(f"Saving updated team document {team_id}...")
                doc_id, rev = self.db.save(team_doc)
                log.info(f"Team '{team_id}' updated successfully.")
                return doc_id, rev
            else:
                log.info(f"Team '{team_id}' edit requested, but no effective changes detected.")
                return team_doc.id, team_doc.rev

        except couchdb.ResourceNotFound:
            raise TeamNotFoundError(f"Team with ID {team_id} not found.") # Use specific exception
        except ValueError as ve: # Catch validation errors (like removing lead)
             raise ve
        except Exception as e:
            log.error(f"Error editing team {team_id}: {e}", exc_info=True)
            raise ServiceError(f"Failed to edit team {team_id}.") from e # Wrap generic errors


    def is_user_leading_any_team(self, user_id, exclude_team_id=None):
        """Checks if a user is the lead of any team, optionally excluding one."""
        if not self.db: return False
        selector = {"type": "team", "lead_id": user_id}
        try:
             results = list(self.db.find({
                 "selector": selector,
                 "fields": ["_id"], # Only need ID to check count
                 "use_index": "_design/idx-team-type-lead/json"
                 }))
             if not results: return False # No teams found where user is lead

             if exclude_team_id:
                 # Check if there's any result *other* than the excluded team
                 return any(team['_id'] != exclude_team_id for team in results)
             else:
                 # If not excluding, any result means they lead at least one
                 return True
        except Exception as e:
             log.error(f"Error checking if user {user_id} leads any team: {e}", exc_info=True)
             return False

    # --- THIS IS THE HELPER METHOD THAT WAS MISSING ---
    def _populate_team_users(self, team_doc):
        """Helper to replace user IDs with basic user info,
           separating lead and members. Returns a DICT, not a couchdb.Document."""
        if not self.db:
            log.error("Cannot populate team users: Database not connected.")
            # Return a basic dict representation if possible
            return dict(team_doc) if team_doc else None
        if not team_doc or not isinstance(team_doc, (dict, couchdb.Document)): # Allow dict or Document
            log.warning(f"Cannot populate non-dict/non-doc team_doc: {type(team_doc)}")
            return dict(team_doc) if team_doc else None

        # Work with a dictionary copy
        team_data = dict(team_doc)
        team_id = team_data.get("_id", "N/A") # Use _id for logging
        lead_id = team_data.get('lead_id')
        user_ids = team_data.get('user_ids', [])

        if not user_ids:
            log.debug(f"Team {team_id} has no users listed in user_ids.")
            team_data['lead'] = None
            team_data['members'] = []
            # Remove potentially confusing keys before returning
            team_data.pop('user_ids', None)
            team_data.pop('lead_id', None)
            return team_data

        member_list = []
        lead_data = None
        user_docs_map = {}

        try:
            valid_user_ids = [uid for uid in user_ids if isinstance(uid, str)]
            if valid_user_ids:
                 fetched_users = self.db.view('_all_docs', keys=valid_user_ids, include_docs=True)
                 user_docs_map = {
                     row.id: row.doc
                     for row in fetched_users
                     if row.doc and not row.doc.get('error') and row.doc.get("type") == "user"
                 }
                 log.debug(f"Bulk fetched {len(user_docs_map)} valid user docs for team {team_id}.")
            else:
                 log.warning(f"No valid string user IDs found for team {team_id}.")

        except Exception as e:
            log.error(f"❌ Error bulk fetching users for team {team_id}: {e}", exc_info=True)

        for user_id in valid_user_ids:
            user_doc = user_docs_map.get(user_id)
            if user_doc:
                # Construct user info dict - use the ID from the user doc itself (_id)
                user_info = {
                    "id": user_doc.id, # Use the actual document ID (_id)
                    "first_name": user_doc.get("first_name", "N/A"),
                    "last_name": user_doc.get("last_name", ""),
                    "email": user_doc.get("email", "N/A"),
                    "role": user_doc.get("role", "N/A"),
                    "isLeadStatus": user_doc.get("isLead", False)
                }
                # Identify lead based on team's lead_id field
                if user_id == lead_id:
                    lead_data = user_info
                # Add to members list ONLY if NOT the lead
                # elif user_id != lead_id: # This logic is correct
                #    member_list.append(user_info)
            else:
                 log.warning(f"User document {user_id} referenced in team {team_id} but not found/invalid during detail fetch.")

        # Add the separate 'lead' and 'members' keys
        team_data['lead'] = lead_data
        # Rebuild member list from map, excluding the lead
        team_data['members'] = [
             {
                "id": uid,
                "first_name": udoc.get("first_name", "N/A"),
                "last_name": udoc.get("last_name", ""),
                "email": udoc.get("email", "N/A"),
                "role": udoc.get("role", "N/A"),
                "isLeadStatus": udoc.get("isLead", False)
             }
             for uid, udoc in user_docs_map.items() if uid != lead_id # Exclude lead
        ]


        # Remove original fields to avoid confusion in the final API response
        team_data.pop('users', None) # Remove if it existed
        team_data.pop('user_ids', None)
        team_data.pop('lead_id', None)
        # Keep _id and _rev if needed, or remove them too if API should hide them
        # team_data.pop('_rev', None)

        # RENAME _id to id for the final API response consistency
        if "_id" in team_data:
            team_data["id"] = team_data.pop("_id")

        return team_data # Return the processed dictionary
    # --- END OF _populate_team_users METHOD ---

    def get_team(self, team_id=None, name=None):
        """Fetches a single team and populates user details."""
        if not self.db: return None
        team_doc = None
        try:
            if team_id:
                team_doc = self.db.get(team_id)
            elif name:
                selector = {'type': 'team', 'name': name}
                # Add index usage if you have one for name
                results = list(self.db.find({'selector': selector, 'limit': 1}))
                if results: team_doc = results[0]
            else:
                raise ValueError("Either team_id or name must be provided")

            if not team_doc or team_doc.get("type") != "team":
                log.debug(f"Team not found or not type 'team' for id={team_id}, name={name}")
                return None

            # Call the populator which now returns the desired dictionary structure
            populated_team_dict = self._populate_team_users(team_doc)
            return populated_team_dict

        except couchdb.ResourceNotFound:
            log.debug(f"Team ResourceNotFound for id={team_id}, name={name}")
            return None
        except ValueError as ve:
             log.warning(f"ValueError in get_team: {ve}")
             raise ve # Re-raise validation errors
        except Exception as e:
            log.error(f"Error getting team (id={team_id}, name={name}): {e}", exc_info=True)
            return None # Return None on other errors


    def get_users_details(self, user_ids: list, current_lead_id=None):
        """
        Helper to fetch details for a list of user IDs.
        DEPRECATED in favor of bulk fetch within _populate_team_users.
        Kept for reference if needed elsewhere, but should be removed if unused.
        """
        log.warning("get_users_details is deprecated. Use bulk fetch within _populate_team_users.")
        if not self.db or not user_ids: return []
        users_data = []
        for user_id in user_ids:
            user_doc = self.find_user_by_id(user_id) # Calls the new find_user_by_id
            if user_doc:
                 users_data.append({
                    "id": user_doc.id, # Use .id which gets _id
                    "first_name": user_doc.get("first_name"),
                    "last_name": user_doc.get("last_name"),
                    "email": user_doc.get("email"),
                    "role": user_doc.get("role"),
                    "is_lead": user_id == current_lead_id, # is_lead relative to this team context
                    "isLeadStatus": user_doc.get("isLead", False) # Actual status on user doc
                })
            else:
                 log.warning(f"(Deprecated get_users_details) User document {user_id} not found.")
        return users_data


    def get_all_teams(self):
        """Fetches all teams and populates user details."""
        if not self.db: return []
        teams_data = []
        try:
            selector = {'type': 'team'}
            all_team_docs = list(self.db.find({
                'selector': selector,
                'use_index': '_design/idx-team-type/json'
            }))
            log.debug(f"get_all_teams: Found {len(all_team_docs)} raw team docs.")

            for team_doc in all_team_docs:
                 populated_team = self._populate_team_users(team_doc) # Use helper
                 if populated_team:
                    teams_data.append(populated_team)

            # --- Optional: Logging raw output ---
            # import json
            # print("--- START /get_all_teams API Raw Output ---")
            # try: print(json.dumps(teams_data, indent=2, default=str))
            # except Exception as json_err: print(f"JSON dump failed: {json_err}")
            # print("--- END /get_all_teams API Raw Output ---")
            # --- End Logging ---

            return teams_data

        except Exception as e:
            log.error(f"❌ Error in get_all_teams: {e}", exc_info=True)
            return []

    def get_teams_by_lead(self, lead_user_id: str) -> list[dict]:
        """Fetches teams led by a specific user and populates user details."""
        if not self.db: return []
        try:
            team_docs_raw = list(self.db.find({ # Fetch raw docs first
                "selector": {"type": "team", "lead_id": lead_user_id},
                "use_index": "_design/idx-team-type-lead/json"
            }))
            # --- ADD LOGGING HERE ---
            log.debug(f"get_teams_by_lead: Raw results for lead {lead_user_id}: {team_docs_raw}")
            # --- END LOGGING ---

            teams = []
            for team_doc in team_docs_raw: # Iterate over raw results
                populated_team = self._populate_team_users(team_doc)
                if populated_team:
                    teams.append(populated_team)
            return teams
        except Exception as e:
            log.error(f"Error getting teams for lead {lead_user_id}: {e}", exc_info=True)
            return []

    # --- Access Control Methods ---
    def set_user_root_folder(self, user_email, root_path, access=None):
        # This seems redundant if set_user_access is preferred
        log.warning("set_user_root_folder is deprecated, use set_user_access directly.")
        if not self.db: raise ConnectionError("Database not connected")
        user_doc = self.find_user_by_email(user_email)
        if not user_doc:
            raise ValueError(f"User with email '{user_email}' not found")
        user_id = user_doc.id
        if access is None:
            access = [{"path": root_path, "permissions": ["read", "write"]}]
        return self.set_user_access(user_id, root_path, access)

    def set_user_access(self, user_id, root_path, access_list):
        if not self.db: raise ConnectionError("Database not connected")
        # Use a predictable ID for the access doc
        access_doc_id = f"access:{user_id}"
        try:
             # Try to fetch existing doc to update it (get _rev)
             doc = self.db.get(access_doc_id)
             log.debug(f"Updating existing access doc {access_doc_id}")
        except couchdb.ResourceNotFound:
             # Create new doc if it doesn't exist
             doc = {"_id": access_doc_id, "type": "access_control", "user_id": user_id}
             log.debug(f"Creating new access doc {access_doc_id}")

        # Update fields
        doc["root_path"] = root_path
        doc["access"] = access_list
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            doc_id, rev = self.db.save(doc)
            log.info(f"Access control saved for user {user_id} (Doc ID: {doc_id})")
            return doc_id, rev
        except Exception as e:
            log.error(f"Failed to save access control for user {user_id}: {e}", exc_info=True)
            return None, None

    def get_user_access(self, user_id):
        if not self.db: return []
        try:
            # Use the specific index for access control docs by user_id
            results = list(self.db.find({
                "selector": {"type": "access_control", "user_id": user_id},
                "use_index": "_design/idx-access-type-user/json"
            }))
            # Typically expect only one access doc per user with this design
            return results # Return list of matching docs
        except Exception as e:
            log.error(f"Failed to get access control for user {user_id}: {e}", exc_info=True)
            return []

    # --- Share Link Methods ---
    def create_share_link(self, share_data: dict) -> tuple[str | None, str | None]:
        if not self.db: log.error("DB not connected for create_share_link."); return None, None
        try:
            share_data.setdefault("type", "share_link")
            share_data.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            doc_id, rev = self.db.save(share_data)
            log.info(f"Created share link document with ID: {doc_id}")
            return doc_id, rev
        except Exception as e:
            log.error(f"❌ Error saving share link document: {e}", exc_info=True)
            return None, None

    def find_share_link_by_token(self, token: str) -> couchdb.Document | None:
        if not self.db: log.error("DB not connected for find_share_link_by_token."); return None
        selector = {'type': 'share_link', 'token': token}
        try:
            results = list(self.db.find({
                'selector': selector, 'limit': 1,
                'use_index': '_design/idx-share-type-token/json'
            }))
            if results: return results[0]
            log.info(f"No share link found for token: {token}")
            return None
        except Exception as e:
            log.error(f"❌ Error finding share link by token {token}: {e}", exc_info=True)
            return None

    # --- Team Membership Check ---
    def is_user_in_team(self, user_id: str, team_id: str) -> bool:
        if not self.db: log.error("DB not connected for is_user_in_team."); return False
        try:
            team_doc = self.db.get(team_id)
            if team_doc and team_doc.get("type") == "team":
                return user_id in team_doc.get("user_ids", [])
            return False
        except couchdb.ResourceNotFound: return False
        except Exception as e:
            log.error(f"❌ Error checking team membership user {user_id}/team {team_id}: {e}", exc_info=True)
            return False

    # --- Project/Task Methods (Placeholders - Implement as needed) ---
    def get_projects_by_team(self, team_id: str) -> list[dict]:
        if not self.db: return []
        log.debug(f"Fetching projects for team {team_id}")
        try:
            return list(self.db.find({
                "selector": {"type": "project", "team_id": team_id},
                "use_index": "_design/idx-project-type-team/json"
            }))
        except Exception as e:
            log.error(f"Error getting projects for team {team_id}: {e}", exc_info=True)
            return []

    def get_tasks_by_team(self, team_id: str) -> list[dict]:
        if not self.db: return []
        log.debug(f"Fetching tasks for team {team_id}")
        try:
            # Assuming tasks link directly to teams. Adjust if they link via projects.
            return list(self.db.find({
                "selector": {"type": "task", "team_id": team_id},
                 "use_index": "_design/idx-task-type-team/json"
            }))
        except Exception as e:
            log.error(f"Error getting tasks for team {team_id}: {e}", exc_info=True)
            return []

    def remove_user_from_all_teams(self, user_id):
        """Finds all teams user is a member of (NOT lead) and removes them."""
        if not self.db: return False
        log.warning(f"Attempting to remove user {user_id} from all teams they are a member of.")
        teams_member_of = self.get_teams_for_user(user_id) # Get all teams user is associated with
        removed_count = 0
        success = True
        for team_data in teams_member_of: # team_data is now a dict from _populate
             team_id = team_data.get("id") # Use 'id' key
             lead_info = team_data.get("lead")
             team_lead_id = lead_info.get("id") if lead_info else None

             if team_id and user_id != team_lead_id: # Only remove if NOT the lead
                 log.info(f"Removing user {user_id} from team {team_id} (they are not lead).")
                 try:
                      # Call edit_team, providing only the necessary args
                      doc_id, rev = self.edit_team(
                          team_id=team_id,
                          remove_user_emails=[user_doc['email'] for user_doc in [self.find_user_by_id(user_id)] if user_doc] # Need email for edit_team
                          )
                      if doc_id:
                           removed_count += 1
                      else:
                           log.warning(f"edit_team call seemed to fail or not update for user {user_id} / team {team_id}")
                           success = False # Mark overall operation as potentially failed
                 except Exception as e:
                      log.error(f"Failed removing user {user_id} from team {team_id} during cleanup: {e}")
                      success = False # Mark overall operation as failed
        log.info(f"Finished removing user {user_id} from teams. Removed from: {removed_count}. Overall Success: {success}")
        return success # Return True only if all removals succeeded (or no removals needed)

    def get_teams_for_user(self, user_id: str) -> list[dict]:
        """Finds all teams where the given user_id is listed in the user_ids array."""
        if not self.db:
            log.error("Database connection not available for get_teams_for_user.") # Use log if defined, otherwise logging.error
            return []
        try:
            selector = {
                "type": "team",
                "user_ids": {"$all": [user_id]} # Check if user_id exists in the array
            }
            log.debug(f"Finding teams for user {user_id} with selector: {selector}") # Use log
            # Ensure the index exists and is named correctly
            team_docs = list(self.db.find({
                "selector": selector,
                "use_index": "_design/idx-team-type-user_ids/json" # Use the team membership index
            }))

            log.info(f"Found {len(team_docs)} raw team docs associated with user {user_id}.") # Use log

            # Populate user details for each found team
            populated_teams = []
            for team_doc in team_docs:
                # Ensure _populate_team_users exists and is called correctly
                populated = self._populate_team_users(team_doc)
                if populated:
                    populated_teams.append(populated)

            log.debug(f"Returning {len(populated_teams)} populated teams for user {user_id}.")
            return populated_teams

        except Exception as e:
            log.error(f"❌ Error finding teams for user {user_id}: {e}", exc_info=True) # Use log
            return []

# --- End of Database Class ---