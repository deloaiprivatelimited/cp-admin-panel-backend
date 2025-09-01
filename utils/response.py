# utils/response.py
from flask import jsonify


def response(success: bool, message: str, data=None):
    return jsonify({
        "success": success,
        "message": message,
        "data": data
    })
