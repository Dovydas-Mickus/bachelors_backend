from datetime import datetime, timezone
import logging
import os
import io
import shutil
import mimetypes
from pathlib import Path
import subprocess
from PIL import Image
from werkzeug.utils import secure_filename
# Removed: from flask import current_app

# Import db instance and custom exceptions
from app.extensions import db
from app.exceptions import (
    FileServiceError, FileServiceFileNotFoundError as FileNotFoundError, # Use specific subclass
    FileServiceAccessDeniedError as AccessDeniedError,
    FileServiceConflictError as ConflictError, ServiceError, ValidationError
)

class FileService:

    def __init__(self):
        """Initialize without app context. Configuration happens in init_app."""
        self.db = db
        self.base_upload_folder = None
        self.app_logger = None # Store logger instance

    def init_app(self, app):
        """Configure the service with the Flask app instance."""
        self.app_logger = app.logger # Get logger instance
        try:
            base_dir_config = app.config['DATABASE_FILES_DIR']
            self.base_upload_folder = Path(base_dir_config).resolve()

            # Ensure the base folder exists
            if not self.base_upload_folder.exists():
                self.app_logger.info(f"Creating base upload directory: {self.base_upload_folder}")
                self.base_upload_folder.mkdir(parents=True, exist_ok=True)
            elif not self.base_upload_folder.is_dir():
                # Log critical error if path exists but isn't a directory
                self.app_logger.critical(f"Configured DATABASE_FILES_DIR '{self.base_upload_folder}' exists but is not a directory.")
                raise OSError(f"Invalid DATABASE_FILES_DIR configuration: path exists but is not a directory.")

            self.app_logger.info(f"FileService initialized with base folder: {self.base_upload_folder}")

        except KeyError:
             self.app_logger.critical("DATABASE_FILES_DIR not found in Flask app config!")
             raise KeyError("DATABASE_FILES_DIR configuration is missing.")
        except Exception as e:
             self.app_logger.critical(f"Failed to initialize FileService base folder '{base_dir_config}': {e}", exc_info=True)
             raise ServiceError(f"Failed to initialize file storage: {e}") from e


    def _log(self, level, message, exc_info=False):
         """Helper to log messages using the stored logger."""
         if self.app_logger:
             self.app_logger.log(level, message, exc_info=exc_info)
         else:
             # Fallback if logger wasn't initialized (shouldn't happen)
             print(f"LOG ({logging.getLevelName(level)}): {message}")


    def _get_user_root_path(self, user_id: str) -> Path:
        """Gets the resolved absolute root path for a user."""
        if not self.base_upload_folder:
             self._log(logging.CRITICAL,"FileService base_upload_folder not initialized.")
             raise RuntimeError("FileService has not been initialized. Call init_app.")

        # Basic validation of user_id format if desired (e.g., prevent '..')
        safe_user_id = secure_filename(user_id) # secure_filename might be too restrictive, adjust if needed
        if user_id != safe_user_id:
             self._log(logging.ERROR, f"Invalid characters detected in user_id for path creation: {user_id}")
             raise AccessDeniedError("Invalid user identifier for path.") # Prevent path manipulation

        user_root = self.base_upload_folder / safe_user_id
        # Create user directory if it doesn't exist
        if not user_root.exists():
            self._log(logging.INFO, f"Creating user directory on demand: {user_root}")
            try:
                 user_root.mkdir(parents=True, exist_ok=True)
                 # Optionally set permissions if needed: os.chmod(user_root, 0o755)
            except OSError as e:
                 self._log(logging.ERROR, f"Failed to create directory {user_root}: {e}", exc_info=True)
                 raise ServiceError(f"Could not create user storage directory.")

        elif not user_root.is_dir():
             self._log(logging.ERROR, f"User storage path '{user_root}' exists but is not a directory.")
             raise FileNotFoundError(f"User storage path conflict for user {user_id}.")
        return user_root.resolve()


    def _resolve_and_check_path(self, user_id: str, relative_path: str) -> Path:
        """Resolves a relative path against user's root and checks bounds."""
        user_root = self._get_user_root_path(user_id) # Can raise FileNotFoundError/AccessDeniedError

        # Clean the relative path: remove leading slashes, handle empty path
        clean_relative_path = relative_path.lstrip('/') if relative_path else ""

        # Prevent path components like '..' explicitly before resolving
        # This adds a layer of defense beyond resolve()
        path_parts = Path(clean_relative_path).parts
        if '..' in path_parts or '.' in path_parts:
             self._log(logging.WARNING, f"Potentially unsafe path components ('..' or '.') detected in relative path: '{relative_path}' for user {user_id}")
             # Depending on policy, you might allow '.' but definitely block '..'
             raise AccessDeniedError("Invalid path components detected.")

        # Join and resolve
        target_path = (user_root / clean_relative_path).resolve()

        # Security: Final path traversal check after resolution
        # Use os.path.commonpath for robustness (Python 3.5+) or string check
        if os.path.commonpath([str(user_root), str(target_path)]) != str(user_root):
            self._log(logging.WARNING, f"Path traversal attempt blocked: User={user_id}, Path='{relative_path}', Resolved='{target_path}', Root='{user_root}'")
            raise AccessDeniedError("Access outside designated user directory is forbidden.")

        return target_path

    def list_directory(self, user_id: str, relative_path: str = ""):
        """Lists contents of a directory for a user."""
        try:
            target_dir = self._resolve_and_check_path(user_id, relative_path)

            if not target_dir.exists() or not target_dir.is_dir():
                raise FileNotFoundError(f"Directory not found at path: '{relative_path}'")

            items = []
            user_root = self._get_user_root_path(user_id) # Need root again for relative path calc

            for entry in target_dir.iterdir():
                if entry.is_symlink(): continue # Skip symlinks for safety
                try:
                    # Use stat to get size/type, handle potential permission errors here
                    stat_result = entry.stat()
                    is_dir = entry.is_dir() # Re-check using stat result might be safer? S_ISDIR(stat_result.st_mode)
                    rel_path_to_root = str(entry.relative_to(user_root))
                    items.append({
                        "name": rel_path_to_root,
                        "is_directory": is_dir,
                        "size": stat_result.st_size if not is_dir else None,
                        "modified_at": datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc).isoformat() # Example
                    })
                except OSError as stat_error:
                    self._log(logging.ERROR, f"Could not stat file entry '{entry}' in {target_dir}: {stat_error}")
                    # Skip this item or include with an error flag? Skip for now.
                except ValueError as rel_path_error:
                     self._log(logging.ERROR, f"Could not compute relative path for '{entry}' against root '{user_root}': {rel_path_error}")


            # Sort items (directories first, then by name)
            items.sort(key=lambda x: (not x['is_directory'], x['name'].lower()))
            return items
        except (FileNotFoundError, AccessDeniedError):
            raise # Re-raise specific errors
        except OSError as e:
            self._log(logging.ERROR, f"OS error listing directory {target_dir}: {e}", exc_info=True)
            raise ServiceError(f"Error accessing directory contents.")
        except Exception as e:
            self._log(logging.ERROR, f"Unexpected error listing directory '{relative_path}' for user {user_id}: {e}", exc_info=True)
            raise ServiceError("An unexpected error occurred while listing files.")


    def create_directory(self, user_id: str, relative_path: str):
        """Creates a new directory."""
        if not relative_path: # Prevent creating the root itself or empty names
             raise ValidationError("Directory path cannot be empty.")

        try:
             target_path = self._resolve_and_check_path(user_id, relative_path)

             if target_path.exists():
                  raise ConflictError("Directory or file already exists at this path.")

             target_path.mkdir(parents=True, exist_ok=False)
             self._log(logging.INFO, f"Created directory '{target_path}' for user {user_id}")
             return {"message": "Folder created", "path": relative_path}
        except (ConflictError, AccessDeniedError, ValidationError):
             raise
        except OSError as e:
             self._log(logging.ERROR, f"Error creating directory {target_path}: {e}", exc_info=True)
             raise ServiceError(f"Could not create directory: Operating system error.")
        except Exception as e:
             self._log(logging.ERROR, f"Unexpected error creating directory '{relative_path}' for user {user_id}: {e}", exc_info=True)
             raise ServiceError("An unexpected error occurred while creating the directory.")


    def rename_item(self, user_id: str, old_relative_path: str, new_name: str):
        """Renames a file or folder."""
        if not old_relative_path or not new_name or new_name in ('.', '..'):
             raise ValidationError("Invalid old path or new name provided.")
        if "/" in new_name or "\\" in new_name:
             raise ValidationError("New name cannot contain path separators.")

        try:
            target_old = self._resolve_and_check_path(user_id, old_relative_path)

            if not target_old.exists():
                raise FileNotFoundError(f"Item to rename not found at '{old_relative_path}'.")

            # Prevent renaming the root directory
            user_root = self._get_user_root_path(user_id)
            if target_old == user_root:
                 raise AccessDeniedError("Cannot rename the root directory.")

            new_path = target_old.parent / new_name.strip() # New path in same directory

            # Check bounds again just in case parent calculation was weird (unlikely with Pathlib)
            if os.path.commonpath([str(user_root), str(new_path.resolve())]) != str(user_root):
                 self._log(logging.ERROR, f"Rename resulted in path outside root: {new_path}")
                 raise AccessDeniedError("Resulting rename path is invalid.")

            if new_path.exists():
                raise ConflictError(f"An item named '{new_name}' already exists in this location.")

            os.rename(target_old, new_path)
            new_relative_path = str(new_path.relative_to(user_root))
            self._log(logging.INFO, f"Renamed '{target_old}' to '{new_path}' for user {user_id}")
            return {"message": "Renamed successfully", "new_path": new_relative_path}

        except (FileNotFoundError, AccessDeniedError, ConflictError, ValidationError):
            raise
        except OSError as e:
            self._log(logging.ERROR, f"Error renaming {target_old} to {new_path}: {e}", exc_info=True)
            raise ServiceError(f"Could not rename item due to operating system error.")
        except Exception as e:
            self._log(logging.ERROR, f"Unexpected error renaming '{old_relative_path}' to '{new_name}' for user {user_id}: {e}", exc_info=True)
            raise ServiceError("An unexpected error occurred during rename.")


    def delete_item(self, user_id: str, relative_path: str):
        """Deletes a file or folder."""
        if not relative_path:
             raise ValidationError("Cannot delete root directory using empty path.")
        try:
            target_path = self._resolve_and_check_path(user_id, relative_path)

            if not target_path.exists():
                # Idempotent: Success if already gone. Or raise NotFoundError? Let's be idempotent.
                self._log(logging.INFO, f"Item already deleted or never existed: '{target_path}'")
                return {"message": "Item not found or already deleted."}

            # Prevent deleting the root directory itself
            user_root = self._get_user_root_path(user_id)
            if target_path == user_root:
                 raise AccessDeniedError("Cannot delete the root directory.")

            if target_path.is_file():
                target_path.unlink()
                self._log(logging.INFO, f"Deleted file '{target_path}' for user {user_id}")
            elif target_path.is_dir():
                shutil.rmtree(target_path)
                self._log(logging.INFO, f"Deleted directory '{target_path}' and its contents for user {user_id}")
            else:
                # Should not happen if exists() check passed, but handle defensively
                self._log(logging.WARNING, f"Item exists but is not a file or directory: '{target_path}'")
                raise ServiceError("Cannot delete item: Unknown file type.")

            return {"message": "Deleted successfully"}

        except (AccessDeniedError, ValidationError):
             raise
        except OSError as e:
             self._log(logging.ERROR, f"Error deleting {target_path}: {e}", exc_info=True)
             raise ServiceError(f"Could not delete item due to operating system error.")
        except Exception as e:
             self._log(logging.ERROR, f"Unexpected error deleting '{relative_path}' for user {user_id}: {e}", exc_info=True)
             raise ServiceError("An unexpected error occurred during delete.")


    def save_uploaded_file(self, user_id: str, file_storage, relative_dir: str = ""):
        """Saves an uploaded FileStorage object."""
        try:
            target_dir = self._resolve_and_check_path(user_id, relative_dir)

            if not target_dir.exists():
                 # Create the target directory if it doesn't exist
                 self._log(logging.INFO, f"Creating target directory for upload: {target_dir}")
                 target_dir.mkdir(parents=True, exist_ok=True)
            elif not target_dir.is_dir():
                 raise ConflictError("Target upload path exists but is not a directory.")

            if not file_storage or not file_storage.filename:
                raise ValidationError("Invalid file provided for upload.")

            # Sanitize filename
            original_filename = file_storage.filename
            filename = secure_filename(original_filename)
            if not filename: # Handle empty filename after sanitization
                 filename = f"upload_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

            save_path = target_dir / filename
            counter = 1
            name, ext = os.path.splitext(filename)

            # Handle filename collisions efficiently
            while save_path.exists():
                filename = f"{name}_{counter}{ext}"
                save_path = target_dir / filename
                counter += 1
                if counter > 100: # Safety break for unlikely infinite loop
                     self._log(logging.ERROR, f"Could not find unique filename for '{original_filename}' in {target_dir} after 100 attempts.")
                     raise ServiceError("Failed to generate a unique filename for upload.")

            file_storage.save(str(save_path)) # Use string representation for save path
            file_size = save_path.stat().st_size
            user_root = self._get_user_root_path(user_id)
            saved_relative_path = str(save_path.relative_to(user_root))

            # Optional: Save metadata to DB (consider if needed)
            # self.db.save_file_metadata(user_id, saved_relative_path, file_size)
            self._log(logging.INFO, f"Saved uploaded file '{original_filename}' as '{save_path}' ({file_size} bytes) for user {user_id}")

            return {"message": "File uploaded", "filename": filename, "path": saved_relative_path, "size": file_size}

        except (ConflictError, AccessDeniedError, ValidationError):
            raise
        except Exception as e:
            self._log(logging.ERROR, f"Error saving uploaded file '{original_filename}' for user {user_id}: {e}", exc_info=True)
            # Clean up partially saved file if possible? Difficult.
            raise ServiceError(f"Could not save uploaded file.")


    def get_file_for_download(self, user_id: str, relative_path: str):
         """Gets file path and mimetype for sending as attachment."""
         try:
            target_file = self._resolve_and_check_path(user_id, relative_path)

            if not target_file.is_file(): # Check is_file() instead of exists()
                raise FileNotFoundError(f"File not found or is a directory at '{relative_path}'.")

            mime_type, _ = mimetypes.guess_type(target_file.name)
            mime_type = mime_type or "application/octet-stream"

            return target_file, mime_type
         except (FileNotFoundError, AccessDeniedError):
             raise
         except Exception as e:
             self._log(logging.ERROR, f"Unexpected error getting file for download '{relative_path}' user {user_id}: {e}", exc_info=True)
             raise ServiceError("Could not retrieve file for download.")


    def get_file_for_preview(self, user_id: str, relative_path: str):
        """Gets file path and mimetype for inline preview, handling image/docx conversion."""
        try:
            target_file = self._resolve_and_check_path(user_id, relative_path)

            if not target_file.is_file():
                raise FileNotFoundError(f"File not found or is a directory at '{relative_path}'.")

            mime_type, _ = mimetypes.guess_type(target_file.name)
            mime_type = mime_type or "application/octet-stream"
            file_suffix = target_file.suffix.lower()

            # --- Image Handling ---
            if file_suffix in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"]:
                try:
                    img = Image.open(target_file)
                    img.load() # Load image data to catch truncated files

                    # Handle transparency / palette modes for JPEG saving
                    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                         background = Image.new("RGB", img.size, (255, 255, 255))
                         img_rgba = img.convert("RGBA")
                         background.paste(img_rgba, mask=img_rgba.split()[3])
                         img = background
                    elif img.mode != "RGB":
                         img = img.convert("RGB")

                    max_preview_size = (1280, 1280) # Configurable?
                    img.thumbnail(max_preview_size, Image.Resampling.LANCZOS) # Use LANCZOS for better quality

                    buffer = io.BytesIO()
                    img.save(buffer, format="JPEG", quality=85, optimize=True) # Optimize JPEG
                    buffer.seek(0)
                    self._log(logging.DEBUG, f"Generated JPEG preview for '{target_file.name}'")
                    return buffer, "image/jpeg"

                except Exception as e:
                    self._log(logging.ERROR, f"Error processing image preview for {target_file}: {e}", exc_info=True)
                    # Fallback: Send original file, browser might handle it
                    return target_file, mime_type

            # --- DOCX/DOC Handling (Requires LibreOffice) ---
            elif file_suffix in [".docx", ".doc"]:
                # Check if libreoffice is available
                soffice_cmd = shutil.which("libreoffice") or shutil.which("soffice")
                if not soffice_cmd:
                    self._log(logging.WARNING, f"LibreOffice not found, cannot convert {file_suffix} for preview.")
                    raise ServiceError(f"Cannot preview {file_suffix}: Office converter not installed on server.", status_code=501) # 501 Not Implemented

                pdf_path = target_file.with_suffix(".pdf")
                should_convert = True
                if pdf_path.exists():
                    if pdf_path.stat().st_mtime >= target_file.stat().st_mtime:
                        should_convert = False

                if should_convert:
                    # Use subprocess for better control and error capture
                    cmd = [soffice_cmd, '--headless', '--convert-to', 'pdf', '--outdir', str(target_file.parent), str(target_file)]
                    self._log(logging.INFO, f"Converting to PDF: {' '.join(cmd)}")
                    try:
                        # Add timeout?
                        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60) # 60 sec timeout
                        if result.returncode != 0:
                             self._log(logging.ERROR, f"LibreOffice conversion failed (Code {result.returncode}):\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
                             raise ServiceError(f"Office document conversion failed.", status_code=500)
                        if not pdf_path.exists():
                             self._log(logging.ERROR, f"LibreOffice conversion ran (Code 0) but PDF file '{pdf_path}' not found. Output:\n{result.stdout}\n{result.stderr}")
                             raise ServiceError("Office document conversion output missing.", status_code=500)
                        self._log(logging.INFO, f"Successfully converted {target_file.name} to PDF.")
                    except subprocess.TimeoutExpired:
                         self._log(logging.ERROR, f"LibreOffice conversion timed out for {target_file.name}")
                         raise ServiceError("Office document conversion timed out.", status_code=504) # Gateway Timeout
                    except Exception as e:
                         self._log(logging.ERROR, f"Error during DOCX->PDF conversion subprocess: {e}", exc_info=True)
                         raise ServiceError(f"Failed to convert document to PDF: {e}", status_code=500)

                if pdf_path.exists():
                    self._log(logging.DEBUG, f"Serving PDF preview for '{target_file.name}'")
                    return pdf_path, "application/pdf"
                else:
                     # Should be caught above, but defensive check
                     raise ServiceError("Failed to provide PDF preview.", status_code=500)

            # --- Default: Send original file ---
            self._log(logging.DEBUG, f"Serving original file for preview: '{target_file.name}'")
            return target_file, mime_type

        except (FileNotFoundError, AccessDeniedError):
             raise
        except ServiceError as se: # Catch specific ServiceErrors with status codes
             raise se
        except Exception as e:
             self._log(logging.ERROR, f"Unexpected error getting file for preview '{relative_path}' user {user_id}: {e}", exc_info=True)
             raise ServiceError("Could not retrieve file for preview.")


    def search_files(self, user_id: str, query: str):
        """Searches for files within a user's directory."""
        if not query:
             raise ValidationError("Search query cannot be empty.")
        query_lower = query.lower()
        matches = []
        try:
             user_root = self._get_user_root_path(user_id)

             for dirpath, dirnames, filenames in os.walk(user_root):
                 # Optional: Exclude specific directories (e.g., hidden ones)
                 # dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                 # filenames[:] = [f for f in filenames if not f.startswith('.')]

                 current_dir_path = Path(dirpath)
                 for filename in filenames:
                     if query_lower in filename.lower():
                         try:
                             full_path = current_dir_path / filename
                             rel_path = str(full_path.relative_to(user_root))
                             stat_res = full_path.stat()
                             matches.append({
                                 "name": rel_path,
                                 "size": stat_res.st_size,
                                 "is_directory": False,
                                 "modified_at": datetime.fromtimestamp(stat_res.st_mtime, tz=timezone.utc).isoformat()
                             })
                         except OSError as e:
                              self._log(logging.ERROR, f"Could not stat or process search match {full_path}: {e}")
                         except ValueError as e:
                              self._log(logging.ERROR, f"Could not get relative path for search match {full_path}: {e}")

             return matches
        except (FileNotFoundError, AccessDeniedError, ValidationError): # Include user root errors
            raise
        except Exception as e:
             self._log(logging.ERROR, f"Unexpected error during file search for query '{query}' user {user_id}: {e}", exc_info=True)
             raise ServiceError("An error occurred during file search.")


# Instantiate the service object WITHOUT calling __init__ logic needing 'app'
file_service = FileService()