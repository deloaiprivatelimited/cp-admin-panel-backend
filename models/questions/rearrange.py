# models/rearrange.py

import uuid
from mongoengine import (
    Document, EmbeddedDocument,
    StringField, ListField, EmbeddedDocumentField,
    IntField, BooleanField, FloatField, DictField
)


class Item(EmbeddedDocument):
    """Single item/segment for a rearrange question"""
    item_id = StringField(required=True, default=lambda: str(uuid.uuid4()))
    value = StringField(required=True)  # text/html of the item


class RearrangeConfig(Document):
    """Stores Config data auto-created from Rearrange questions"""
    difficulty_levels = ListField(StringField())
    topics = ListField(StringField())
    subtopics = ListField(StringField())
    tags = ListField(StringField())

    meta = {"collection": "rearrange_configs"}


class Rearrange(Document):
    """Model for a 'rearrange these items in correct order' question"""
    title = StringField(required=True)
    prompt = StringField(required=True)  # main question/prompt shown to the student

    items = ListField(EmbeddedDocumentField(Item), required=True)
    # correct_order is a list of item_ids in the intended correct sequence
    correct_order = ListField(StringField(), required=True)

    is_drag_and_drop = BooleanField(default=True)  # optional UX hint

    marks = FloatField(required=True, min_value=0)
    negative_marks = FloatField(required=True, min_value=0)

    difficulty_level = StringField(choices=["Easy", "Medium", "Hard"], required=True)

    explanation = StringField()
    tags = ListField(StringField())
    time_limit = IntField()  # in seconds

    topic = StringField(required=True)
    subtopic = StringField()
    created_by = DictField(required=True, default=lambda: {"id": "system", "name": "System"})

    meta = {"collection": "rearranges"}

    def clean(self):
        """Validation before saving"""
        # Ensure items are present
        if not self.items or len(self.items) == 0:
            raise ValueError("At least one item is required")

        item_ids = [it.item_id for it in self.items]

        # correct_order must contain exactly the same ids as items (same length, no unknowns, no duplicates)
        if len(self.correct_order) != len(item_ids):
            raise ValueError("correct_order must contain the same number of ids as items")

        if set(self.correct_order) != set(item_ids):
            raise ValueError("correct_order must be a permutation of item ids from `items`")

        if len(self.correct_order) != len(set(self.correct_order)):
            raise ValueError("correct_order contains duplicate item ids")

    def save(self, *args, **kwargs):
        """Override save to auto-update/create RearrangeConfig"""
        self.clean()  # run validation

        # Save Rearrange first
        result = super(Rearrange, self).save(*args, **kwargs)

        # Update RearrangeConfig
        config = RearrangeConfig.objects.first()
        if not config:
            config = RearrangeConfig()

        if self.difficulty_level and self.difficulty_level not in config.difficulty_levels:
            config.difficulty_levels.append(self.difficulty_level)

        if self.topic and self.topic not in config.topics:
            config.topics.append(self.topic)

        if self.subtopic and self.subtopic not in config.subtopics:
            config.subtopics.append(self.subtopic)

        for tag in self.tags or []:
            if tag not in config.tags:
                config.tags.append(tag)

        config.save()

        return result

    def to_json(self):
        """Convert Rearrange document to dict/JSON"""
        return {
            "id": str(self.id),
            "title": self.title,
            "prompt": self.prompt,
            "items": [{"item_id": i.item_id, "value": i.value} for i in self.items],
            "correct_order": self.correct_order,
            "is_drag_and_drop": self.is_drag_and_drop,
            "marks": self.marks,
            "negative_marks": self.negative_marks,
            "difficulty_level": self.difficulty_level,
            "explanation": self.explanation,
            "tags": self.tags,
            "time_limit": self.time_limit,
            "topic": self.topic,
            "subtopic": self.subtopic,
            "created_by": self.created_by,
        }
