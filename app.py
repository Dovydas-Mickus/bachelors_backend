import os
from app import create_app
from app.config import get_config_name

# Determine the configuration environment
config_name = get_config_name() # Uses FLASK_ENV or defaults to 'development'
app = create_app(config_name)

if __name__ == "__main__":
    # Use Flask's built-in server for development
    # Host 0.0.0.0 makes it accessible on the network
    # Debug mode is controlled by FLASK_DEBUG in .env via config
    # Use port 5000 or configure via environment variable
    port = int(os.environ.get("PORT", 5000))
    print(f"--- Starting Flask Development Server on http://0.0.0.0:{port}/ ---")
    print(f"--- Running with configuration: {config_name} ---")
    app.run(host="0.0.0.0", port=port) # Debug is set by app.config

    # Reminder for production:
    # Use a proper WSGI server like Gunicorn or uWSGI
    # Example: gunicorn --workers 4 --bind 0.0.0.0:5000 "run:create_app('production')"