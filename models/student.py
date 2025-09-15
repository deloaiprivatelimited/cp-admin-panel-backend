# models/student.py
from datetime import datetime
from mongoengine import (
    Document, StringField, DateField, EmailField, IntField,
    ListField, BooleanField, DateTimeField, FloatField, DictField, ReferenceField,
    NULLIFY
)
from werkzeug.security import generate_password_hash, check_password_hash
class Student(Document):
    # Basic details
    name = StringField(required=True)
    gender = StringField(choices=["Male", "Female", "Other"])
    date_of_birth = DateField()
    email = EmailField(required=True, unique=True)
    phone_number = StringField(max_length=15)

    # College-specific
    usn = StringField()  # e.g., "1RV21CS001"
    enrollment_number = StringField()  # optional if college uses
    branch = StringField()
    year_of_study = IntField(min_value=1, max_value=4)
    semester = IntField(min_value=1, max_value=8)

    # Academic info
    cgpa = FloatField(min_value=0.0, max_value=10.0, default=0.0)
    college = ReferenceField('College', reverse_delete_rule=NULLIFY)

    # Other details
    address = StringField()
    city = StringField()
    state = StringField()
    pincode = StringField(max_length=10)
    guardian_name = StringField()
    guardian_contact = StringField(max_length=15)

    # Authentication
    password_hash = StringField(required=True)
    first_time_login = BooleanField(default=True)

    # Status
    is_active = BooleanField(default=True)
    date_joined = DateTimeField(default=datetime.utcnow)

    meta = {
        "collection": "students",
        "indexes": [
            {"fields": ["email"], "unique": True},
            {"fields": ["usn"], "unique": True, "sparse": True},
            {"fields": ["enrollment_number"], "unique": True, "sparse": True}
        ]
    }

    def __str__(self):
        return f"{self.usn or 'N/A'} - {self.name}"

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)
