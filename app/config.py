import os
from datetime import timedelta
from dotenv import load_dotenv

# Load environment variables from .env file in the project root
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=dotenv_path)

class Config:
    """Base configuration."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'default-fallback-secret-key')
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'default-fallback-jwt-key')
    DEBUG = False
    TESTING = False

    # JWT Configuration
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=15)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=30)
    JWT_TOKEN_LOCATION = ["cookies"]
    JWT_COOKIE_SECURE = True # IMPORTANT: Set to True in production (HTTPS)
    JWT_COOKIE_CSRF_PROTECT = False # Consider enabling if not using other CSRF methods
    JWT_COOKIE_SAMESITE = "None" # Use "Lax" or "Strict" if frontend is same-site
    JWT_COOKIE_DOMAIN = os.environ.get('JWT_COOKIE_DOMAIN') # Set your domain in production via .env

    # Database / File Storage Paths (relative to project root assumed by default)
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    DATABASE_FILES_DIR = os.path.join(BASE_DIR, os.environ.get('DATABASE_FILES_DIR', 'files'))
    LOG_DIR = os.path.join(BASE_DIR, os.environ.get('LOG_DIR', 'logs'))

    # Audit Log Config
    AUDIT_LOG_FILE = os.path.join(LOG_DIR, 'audit.log')
    AUDIT_LOG_MAX_BYTES = 10_000_000
    AUDIT_LOG_BACKUP_COUNT = 5

    # Database connection (Example - adapt to your Database class needs)
    DB_HOST = os.environ.get('DB_HOST')
    DB_PORT = os.environ.get('DB_PORT')
    DB_USER = os.environ.get('DB_USER')
    DB_PASSWORD = os.environ.get('DB_PASSWORD')
    DB_NAME = os.environ.get('DB_NAME', 'nas_db')


class DevelopmentConfig(Config):
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    JWT_COOKIE_SECURE = False # Allow cookies over HTTP for local dev
    JWT_COOKIE_SAMESITE = "Lax" # Often easier for local dev without HTTPS/domains
    JWT_COOKIE_DOMAIN = None # No domain for localhost usually


class TestingConfig(Config):
    TESTING = True
    JWT_COOKIE_SECURE = False
    # Use a separate test database if applicable
    # DB_NAME = 'test_db'


class ProductionConfig(Config):
    DEBUG = False
    FLASK_ENV = 'production'
    JWT_COOKIE_SECURE = True
    JWT_COOKIE_SAMESITE = "None" # Or "Strict"/"Lax" depending on frontend setup
    # Ensure JWT_COOKIE_DOMAIN is set correctly in .env for production


config_by_name = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}

def get_config_name():
    return os.getenv('FLASK_ENV', 'default')