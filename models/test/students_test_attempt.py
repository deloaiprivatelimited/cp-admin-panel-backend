from mongoengine import Document, StringField


class StudentTestAttempt(Document):
    """
    Minimal model requested: only stores student_id and test_id.
    """

    student_id = StringField(required=True)  # user id (string)
    test_id = StringField(required=True)     # test / assignment id (string)

    meta = {
        "collection": "student_test_assignments",
        "indexes": [
            ("student_id", "test_id"),
            "student_id",
            "test_id",
        ],
    }

    def to_json(self):
        return {
            "id": str(self.id),
            "student_id": self.student_id,
            "test_id": self.test_id,
        }