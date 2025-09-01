# models/admin.py
from mongoengine import Document, StringField, EmailField, DateTimeField, BooleanField, DictField
from datetime import datetime


class Admin(Document):
    """
    Admin model for user management in the system.
    """
    name = StringField(required=True, max_length=100)
    email = EmailField(required=True, unique=True)
    password = StringField(required=True)  # Store hashed password, not plain text
    permissions = DictField(default={})    # Example: {"can_add_user": True, "can_delete_task": False}
    is_active = BooleanField(default=True)

    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    meta = {
        "collection": "admins",
        "indexes": ["email"],
        "ordering": ["-created_at"]
    }

    def save(self, *args, **kwargs):
        """Override save to update timestamp."""
        if not self.created_at:
            self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        return super(Admin, self).save(*args, **kwargs)

    def to_json(self):
        """Return a JSON-serializable dictionary."""
        return {
            "id": str(self.id),
            "name": self.name,
            "email": self.email,
            "permissions": self.permissions,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
