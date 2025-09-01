
import os
from flask import Flask
from mongoengine import connect
from dotenv import load_dotenv
# from routes.login import login_bp
from routes.admin.login import login_bp
from routes.admin.admins import admin_bp
from routes.college.college import college_bp
# Load environment variables from .env
load_dotenv()
from flask_cors import CORS


def create_app():
    app = Flask(__name__)

    # Flask config from .env
    CORS(app, resources={r"/*": {"origins": "*"}})

    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "fallback-secret")
    
    # Connect to MongoDB
    connect(
        db=os.getenv("MONGO_DB", "cp-admin"),
        host=os.getenv("MONGO_HOST", "localhost"),
        port=int(os.getenv("MONGO_PORT", 27017))
    )

    # Register blueprints
    app.register_blueprint(login_bp, url_prefix="/admin")
    app.register_blueprint(admin_bp,url_prefix="/admin")
    app.register_blueprint(college_bp,url_prefix="/colleges")

    @app.route("/")
    def home():
        return {"message": "CP Admin API is running 🚀"}

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
