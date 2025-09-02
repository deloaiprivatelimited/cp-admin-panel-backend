# utils/admin_helper.py

def get_current_admin_id():
    """
    Returns the ID of the currently authenticated admin from the request context.
    Assumes `token_required` decorator has already set `request.admin`.
    """
    from flask import request

    admin_payload = getattr(request, "admin", None)
    if not admin_payload:
        return None

    return admin_payload.get("id")  # should match the field in your JWT payload
