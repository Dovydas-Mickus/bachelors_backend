import os
import logging
from logging.handlers import RotatingFileHandler # Import for logging setup
from flask import Flask, jsonify, request, make_response
from datetime import datetime, timezone, timedelta
import werkzeug.exceptions # Import explicitly for error handler type hint

# Import configurations and extensions
from .config import config_by_name, get_config_name
from .extensions import cors, jwt, db
from .utils.audit import setup_audit_logging

# --- IMPORT BLUEPRINTS ---
from .routes.auth import auth_bp
from .routes.teams import teams_bp
from .routes.files import files_bp
from .routes.admin import admin_bp
from .routes.shared import shared_bp

# --- IMPORT SERVICE INSTANCES ---
# Import instances that need init_app called
from .services.file_service import file_service
# Import others if they were changed to need init_app
# from .services.auth_service import auth_service
# from .services.team_service import team_service

# Import JWT functions needed globally
from flask_jwt_extended import (
    create_access_token, get_jwt_identity, get_jwt,
    set_access_cookies, unset_jwt_cookies
)
# Import custom exceptions for global error handler
from .exceptions import ServiceError


def create_app(config_name=None):
    """Application Factory Function"""
    if config_name is None:
        config_name = get_config_name() # Use helper from config.py

    app = Flask(__name__)
    try:
        app.config.from_object(config_by_name[config_name])
        print(f"INFO: Loading configuration '{config_name}'") # Simple startup log
        print(f"INFO: Debug mode: {app.config['DEBUG']}")
        print(f"INFO: Database files dir: {app.config['DATABASE_FILES_DIR']}")
        print(f"INFO: Log dir: {app.config['LOG_DIR']}")
    except KeyError:
        print(f"ERROR: Invalid configuration name: {config_name}. Using 'default'.")
        config_name = 'default'
        app.config.from_object(config_by_name[config_name])


    # --- Initialize Extensions ---
    cors.init_app(app)
    jwt.init_app(app)
    try:
        db.init_app(app) # Initialize your Database class with app config
    except Exception as db_init_error:
         app.logger.critical(f"CRITICAL: Database initialization failed: {db_init_error}", exc_info=True)
         # Optional: exit or prevent app from starting fully if DB is essential
         # raise RuntimeError("Database connection failed, cannot start application.") from db_init_error


    # --- Initialize Services that need the app context ---
    try:
        file_service.init_app(app) # Initialize FileService here
        # auth_service.init_app(app) # If needed
        # team_service.init_app(app) # If needed
        # share_service.init_app(app) # If needed
        # user_service.init_app(app) # If needed
    except Exception as service_init_error:
         app.logger.critical(f"CRITICAL: Service initialization failed: {service_init_error}", exc_info=True)
         # Optional: exit or prevent app start
         # raise RuntimeError("Core service failed to initialize, cannot start application.") from service_init_error


    # --- Setup Logging ---
    log_dir = app.config.get('LOG_DIR')
    log_file = os.path.join(log_dir, 'app.log')

    # Ensure log directory exists before setting up handlers
    if log_dir and not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir)
        except OSError as e:
            app.logger.error(f"Failed to create application log directory {log_dir}: {e}")
            log_file = None # Prevent handler setup if dir fails

    # Basic logging config (adjust level and format as needed)
    log_level = logging.DEBUG if app.debug else logging.INFO
    log_format = logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]')

    # Remove default Flask handler to avoid duplicate logs if setting up own handler
    # from flask.logging import default_handler
    # app.logger.removeHandler(default_handler)

    app.logger.setLevel(log_level)

    # Console Handler (useful for development/containers)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(log_format)
    stream_handler.setLevel(log_level) # Match app level
    if not app.logger.handlers: # Add only if no handlers exist yet
         app.logger.addHandler(stream_handler)

    # File Handler (optional, good for production)
    if log_file and not app.debug: # Add file handler only if path valid and not in debug
        try:
            file_handler = RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5, encoding='utf-8')
            file_handler.setFormatter(log_format)
            file_handler.setLevel(logging.INFO) # Log INFO and above to file
            app.logger.addHandler(file_handler)
        except Exception as e:
            app.logger.error(f"Failed to configure file log handler for {log_file}: {e}", exc_info=True)

    app.logger.info(f'Application startup with config: {config_name}')

    # Setup Audit Logging (after standard logging)
    setup_audit_logging(app)


    # --- Register Blueprints ---
    app.register_blueprint(auth_bp)
    app.register_blueprint(teams_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(shared_bp)
    app.logger.info("Blueprints registered.")


    # --- Global Request Hooks ---
    @app.before_request
    def log_request_info():
        app.logger.debug(f"Request Start: {request.method} {request.url} from {request.remote_addr}")
        # Limit cookie logging in production
        if app.debug:
             app.logger.debug(f"Request Cookies: {request.cookies}")

    @app.after_request
    def refresh_expiring_jwts(response):
        """Refresh access token if nearing expiry."""
        try:
            # Check only if it's a successful JSON response and requires JWT
            # Use request.endpoint to see if the route is likely protected
            # This check is imperfect, depends on your endpoint naming
            # A better way might be a custom decorator on protected routes
            is_json_success = response.content_type and 'application/json' in response.content_type and 200 <= response.status_code < 300
            requires_auth = request.endpoint and not request.endpoint.endswith('static') # Basic check

            if is_json_success and requires_auth:
                jwt_data = get_jwt() # Can raise NoAuthorizationError if no token
                identity = get_jwt_identity()
                if identity: # Proceed only if we have an identity
                    exp_timestamp = jwt_data["exp"]
                    now = datetime.now(timezone.utc)
                    # Refresh if token expires within next X minutes (e.g., half the lifetime)
                    expires_delta = app.config.get("JWT_ACCESS_TOKEN_EXPIRES", timedelta(minutes=15))
                    refresh_threshold = expires_delta / 2
                    if not isinstance(refresh_threshold, timedelta): # Ensure it's timedelta
                         refresh_threshold = timedelta(minutes=10)

                    target_timestamp = (now + refresh_threshold).timestamp()

                    if target_timestamp > exp_timestamp:
                        app.logger.info(f"Refreshing JWT access token for user: {identity}")
                        access_token = create_access_token(identity=identity)
                        set_access_cookies(response, access_token)
            return response
        except (RuntimeError, KeyError, AttributeError): # Include NoAuthorizationError? Verify JWTEx handling
             # Handles cases like no JWT, expired JWT, non-JWT routes, or response lacking attributes
            return response
        except Exception as e:
             # Log unexpected errors during refresh but don't break the original response
            app.logger.error(f"Unexpected error during JWT refresh hook: {e}", exc_info=True)
            return response


    # --- Error Handling ---
    @app.errorhandler(werkzeug.exceptions.HTTPException)
    def handle_http_exception(e: werkzeug.exceptions.HTTPException):
        """Return JSON instead of HTML for HTTP errors."""
        # Audit decorator should handle logging the event itself
        app.logger.warning(f"HTTP Exception Handler: {e.code} {e.name} - {e.description} for {request.path}")
        response = e.get_response()
        # Ensure JSON response
        response.data = jsonify({
            "code": e.code,
            "name": e.name,
            "error": e.description,
        }).get_data(as_text=True)
        response.content_type = "application/json"
        return response

    @app.errorhandler(ServiceError)
    def handle_service_error(e: ServiceError):
        """Handle custom service layer errors."""
        # Audit decorator *might* have caught this if it happened *during* the view execution
        # If it happened before (e.g., in a helper called by the route before view logic), log here?
        # Consider if audit logging is needed here in addition to the decorator
        status_code = getattr(e, 'status_code', 500)
        app.logger.error(f"Service Error Handler ({status_code}): {e}", exc_info=app.debug) # Log traceback in debug
        return jsonify({"error": str(e), "code": status_code, "name": type(e).__name__}), status_code


    @app.errorhandler(Exception)
    def handle_generic_exception(e: Exception):
        """Handle non-HTTP, non-ServiceError exceptions."""
        # Audit decorator should handle logging if error occurs within the wrapped view
        app.logger.critical(f"Unhandled Exception Handler: {e}", exc_info=True) # Log full traceback
        error_message = "An internal server error occurred."
        # Avoid exposing internal details in production
        # if app.debug: error_message = f"{type(e).__name__}: {str(e)}" # Optionally show more in debug
        return jsonify({"error": error_message, "code": 500, "name": "Internal Server Error"}), 500


    # --- Simple Root Route ---
    @app.route('/')
    def home():
        return jsonify({"message": "API is running", "status": "OK"}), 200

    app.logger.info("Application creation complete.")
    return app