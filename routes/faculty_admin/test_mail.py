import os
import smtplib
from flask import Blueprint, jsonify
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

test_mail = Blueprint("test_mail", __name__)

@test_mail.route("/test-mail/<to_email>", methods=["GET"])
def test_send_mail(to_email):
    """
    Test sending a mail using Gmail App Password.
    Example:
        http://localhost:5000/test-mail/yourtest@gmail.com
    """
    try:
        msg = EmailMessage()
        msg["From"] = os.getenv("SMTP_USER")
        msg["To"] = to_email
        msg["Subject"] = "SMTP Test ✅"
        msg.set_content("Hello! This is a working SMTP test message from your Flask app.")

        with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT"))) as server:
            server.starttls()
            server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
            server.send_message(msg)

        return jsonify({"success": True, "message": f"Test mail sent to {to_email}"}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
