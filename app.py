import os
from flask import Flask
from mongoengine import connect
from dotenv import load_dotenv
from flask_cors import CORS

# Blueprints
from routes.admin.login import login_bp
from routes.admin.admins import admin_bp
from routes.college.college import college_bp
from routes.questions.mcq import mcq_bp
from routes.course.course import course_bp
from routes.questions.coding import coding_q_bp
from routes.questions.rearrange import rearrange_bp
from routes.course.mcq import course_mcq_bp
from routes.course.rearrange import course_rearrange_bp
from routes.course.coding import course_coding_q_bp
from routes.coding.coding_question import bp as coding_bp

from routes.faculty_admin.test.tests import test_bp
from routes.faculty_admin.college_admin import collegeadmin_bp
from routes.faculty_admin.student_routes import bp
from routes.faculty_admin.test_mail import test_mail
from routes.faculty_admin.test.section import test_bp as section_bp
from routes.faculty_admin.test.questions.mcq import mcq_bp as test_mcq_bp
from routes.faculty_admin.test.questions.coding import coding_bp as test_coding_bp
from routes.faculty_admin.test.questions.rearrange import rearrange_bp as test_rearrange_bp



from routes.faculty_admin.test.college_question.mcq import mcq_bp as test_college_mcq_bp
from routes.faculty_admin.test.college_question.coding import coding_bp as test_college_coding_bp
from routes.faculty_admin.test.college_question.rearrange  import rearrange_bp as test_college_rearrange_bp



from routes.faculty_admin.questions.coding import coding_q_bp as college_coding_q_bp
from routes.faculty_admin.questions.mcq import mcq_bp as college_mcq_bp
from routes.faculty_admin.questions.rearrange import rearrange_bp as college_rearrange_bp
from routes.faculty_admin.questions_test.mcq import mcq_bp as test_edit_mcq

from routes.faculty_admin.v1.questions.coding import coding_bp as generic_coding_bp
from routes.faculty_admin.v1.questions.rearrange import rearrange_bp as generic_rearrange_bp
from routes.faculty_admin.v1.questions.mcq import generic_bp as generic_mcq_bp

from routes.faculty_admin.test.attempt_test import assign_bp as test_assign_bp
# Load environment variables
load_dotenv()

def create_app():
    app = Flask(__name__)

    # Flask config
    CORS(app, resources={r"/*": {"origins": "*"}})
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "fallback-secret")

    # Connect to MongoDB Atlas
    connect(host=os.getenv("MONGO_URI"))

    # Register blueprints
    app.register_blueprint(login_bp, url_prefix="/admin")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(college_bp, url_prefix="/colleges")
    app.register_blueprint(mcq_bp, url_prefix="/mcqs")
    app.register_blueprint(course_bp,url_prefix='/courses')
    app.register_blueprint(coding_q_bp, url_prefix="/coding-questions")
    app.register_blueprint(rearrange_bp, url_prefix="/rearranges")
    app.register_blueprint(course_mcq_bp, url_prefix="/course-mcqs")
    app.register_blueprint(course_rearrange_bp, url_prefix="/course-rearranges")
    app.register_blueprint(course_coding_q_bp, url_prefix="/course-coding-questions")
    app.register_blueprint(coding_bp,url_prefix="/coding/questions")
    app.register_blueprint(test_assign_bp)


    
    app.register_blueprint(collegeadmin_bp)
    app.register_blueprint(bp)
    app.register_blueprint(test_mail)
    app.register_blueprint(test_bp)
    app.register_blueprint(section_bp)
    app.register_blueprint(test_mcq_bp)
    app.register_blueprint(test_rearrange_bp)
    app.register_blueprint(test_coding_bp)

    app.register_blueprint(test_college_mcq_bp)
    app.register_blueprint(test_college_rearrange_bp)
    app.register_blueprint(test_college_coding_bp)

    app.register_blueprint(college_coding_q_bp, url_prefix="/college-coding-questions")
    app.register_blueprint(college_mcq_bp, url_prefix="/college-mcqs")
    app.register_blueprint(college_rearrange_bp, url_prefix="/college-rearranges")
    app.register_blueprint(test_edit_mcq, url_prefix="/test-mcqs")

    app.register_blueprint(generic_mcq_bp)
    app.register_blueprint(generic_rearrange_bp)
    app.register_blueprint(generic_coding_bp)

    @app.route("/")
    def home():
        return {"message": "CP Admin API is running 🚀"}

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
