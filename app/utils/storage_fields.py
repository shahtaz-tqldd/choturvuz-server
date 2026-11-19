from rest_framework import serializers


class StoredImageField(serializers.ImageField):
    """Accept an uploaded image while representing the stored Cloudinary URL."""

    def to_representation(self, value):
        if isinstance(value, str):
            return value
        return super().to_representation(value)


# Backwards-compatible alias.
R2ImageField = StoredImageField
