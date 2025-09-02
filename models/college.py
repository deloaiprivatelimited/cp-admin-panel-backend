# models/college.py
from mongoengine import (
    Document, EmbeddedDocument,
    StringField, BooleanField, ReferenceField,
    EmbeddedDocumentField, ListField, EmailField,IntField,DateTimeField
)


from datetime import datetime


# Embedded Address document
class Address(EmbeddedDocument):
    line1 = StringField(required=True)
    line2 = StringField()
    city = StringField(required=True)
    state = StringField()
    country = StringField()
    zip_code = StringField()

    def to_json(self):
        return {
            "line1": self.line1,
            "line2": self.line2,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "zip_code": self.zip_code
        }

# Embedded Contact document
class Contact(EmbeddedDocument):
    name = StringField(required=True)
    phone = StringField(required=True)
    email = EmailField(required=True)
    designation = StringField()
    status = StringField(default="active")

    def to_json(self):
        return {
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "designation": self.designation,
            "status": self.status
        }

# College Admin document
class CollegeAdmin(Document):
    name = StringField(required=True)
    email = EmailField(required=True, unique=True)
    password = StringField(required=True)
    designation = StringField()
    status = StringField(default="active")
    is_first_login = BooleanField(default=True)
    designation = StringField()
    phone=StringField()


    def to_json(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "email": self.email,
            "designation": self.designation,
            "status": self.status,
            "phone": self.phone,
            "is_first_login": self.is_first_login
        }

# College document
class College(Document):
    name = StringField(required=True)
    college_id = StringField(required=True, unique=True)
    address = EmbeddedDocumentField(Address)
    notes = StringField()
    status = StringField(default="active")
    contacts = ListField(EmbeddedDocumentField(Contact))
    admins = ListField(ReferenceField(CollegeAdmin))
    token_logs = ListField(ReferenceField('TokenLog'))  # <- Added this
    token = ReferenceField('TokenConfig')  # <- Added this
    



    def to_json(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "college_id": self.college_id,
            "address": self.address.to_json() if self.address else None,
            "notes": self.notes,
            "status": self.status,
            "contacts": [c.to_json() for c in self.contacts],
            "admins": [admin.to_json() for admin in self.admins],
            "token_logs": [log.to_json() for log in self.token_logs]  # <- Added

        }

from models.admin import Admin  # Import Admin model for ReferenceField
# Embedded document for token status
class TokenStatus(EmbeddedDocument):
    count = IntField(default=0)
    status = StringField(default="active")  

class TokenLog(Document):
    assigned_date = DateTimeField(default=datetime.utcnow)
    number_of_tokens = EmbeddedDocumentField(TokenStatus, default=TokenStatus)
    assigned_by = ReferenceField(Admin, required=True)
    consumed_tokens = EmbeddedDocumentField(TokenStatus, default=TokenStatus)
    pending_initiation = EmbeddedDocumentField(TokenStatus, default=TokenStatus)
    unused_tokens = EmbeddedDocumentField(TokenStatus, default=TokenStatus)  # <- New field
    notes = StringField()

    def to_json(self):
        return {
            "id": str(self.id),
            "assigned_date": self.assigned_date.isoformat(),
            "number_of_tokens": {
                "count": self.number_of_tokens.count,
                "status": self.number_of_tokens.status
            },
            "assigned_by": {
                "id": str(self.assigned_by.id),
                "name": self.assigned_by.name,
                "email": self.assigned_by.email
            } if self.assigned_by else None,
            "consumed_tokens": {
                "count": self.consumed_tokens.count,
                "status": self.consumed_tokens.status
            },
            "pending_initiation": {
                "count": self.pending_initiation.count,
                "status": self.pending_initiation.status
            },
            "unused_tokens": {  # <- Added to JSON
                "count": self.unused_tokens.count,
                "status": self.unused_tokens.status
            },
            "notes": self.notes
        }

# Token Configuration per College
class TokenConfig(Document):
    college = ReferenceField(College, required=True, unique=True)  # One config per college
    total_tokens = EmbeddedDocumentField(TokenStatus, default=TokenStatus)
    consumed_tokens = EmbeddedDocumentField(TokenStatus, default=TokenStatus)
    pending_tokens = EmbeddedDocumentField(TokenStatus, default=TokenStatus)
    unused_tokens = EmbeddedDocumentField(TokenStatus, default=TokenStatus)  # <- New field

    def to_json(self):
        return {
            "id": str(self.id),
            "college": {
                "id": str(self.college.id),
                "name": self.college.name
            } if self.college else None,
            "total_tokens": {
                "count": self.total_tokens.count,
                "status": self.total_tokens.status
            },
            "consumed_tokens": {
                "count": self.consumed_tokens.count,
                "status": self.consumed_tokens.status
            },
            "pending_tokens": {
                "count": self.pending_tokens.count,
                "status": self.pending_tokens.status
            },
            "unused_tokens": {  # <- Added to JSON
                "count": self.unused_tokens.count,
                "status": self.unused_tokens.status
            }
        }
