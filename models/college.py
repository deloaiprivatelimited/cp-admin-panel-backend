# models/college.py
from mongoengine import (
    Document, EmbeddedDocument,
    StringField, BooleanField, ReferenceField,
    EmbeddedDocumentField, ListField, EmailField
)

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

    def to_json(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "email": self.email,
            "designation": self.designation,
            "status": self.status,
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

    def to_json(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "college_id": self.college_id,
            "address": self.address.to_json() if self.address else None,
            "notes": self.notes,
            "status": self.status,
            "contacts": [c.to_json() for c in self.contacts],
            "admins": [admin.to_json() for admin in self.admins]
        }
