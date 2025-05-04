from flask_cors import CORS
from flask_jwt_extended import JWTManager
from .database import Database # Import your Database class

cors = CORS(supports_credentials=True)
jwt = JWTManager()
db = Database() # Initialize your database instance proxy