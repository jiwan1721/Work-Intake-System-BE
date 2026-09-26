"""Serializer mixins (adapted from BaseProject core/common/mixins/serializers.py).

Both classes accept optional `fields` and `exclude_fields` kwargs so callers
can request a subset of fields without writing a second serializer class.
"""

from __future__ import annotations

from django.utils.functional import cached_property
from rest_framework.serializers import ModelSerializer, Serializer


class DynamicFieldsSerializerMixin:
    """Mixin for plain Serializer subclasses.

    Pass `fields=[...]` to keep only those fields, or `exclude_fields=[...]`
    to drop specific ones. Both are consumed before reaching the superclass.
    """

    def __init__(self, instance=None, *args, **kwargs):
        fields = kwargs.pop("fields", None)
        exclude_fields = kwargs.pop("exclude_fields", None)

        super().__init__(instance, *args, **kwargs)

        if fields is not None:
            allowed = set(fields)
            for field_name in set(self.fields.keys()) - allowed:
                self.fields.pop(field_name)

        if exclude_fields is not None:
            for field_name in set(exclude_fields):
                self.fields.pop(field_name)


class DynamicFieldsModelSerializer(ModelSerializer):
    """ModelSerializer with dynamic field filtering and create-only field support.

    Pass `fields=[...]` to keep only those fields, or `exclude_fields=[...]`
    to drop specific ones.

    Declare `create_only_fields` on Meta to make those fields read-only on
    updates without having to override `get_extra_kwargs` yourself.
    """

    def __init__(self, instance=None, *args, **kwargs):
        fields = kwargs.pop("fields", None)
        exclude_fields = kwargs.pop("exclude_fields", None)

        super().__init__(instance, *args, **kwargs)

        if fields is not None:
            allowed = set(fields)
            for field_name in set(self.fields.keys()) - allowed:
                self.fields.pop(field_name)

        if exclude_fields is not None:
            for field_name in set(exclude_fields):
                self.fields.pop(field_name)

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        create_only_fields = getattr(self.Meta, "create_only_fields", None)

        if self.instance and create_only_fields:
            for field_name in create_only_fields:
                kwargs = extra_kwargs.get(field_name, {})
                kwargs["read_only"] = True
                extra_kwargs[field_name] = kwargs

        return extra_kwargs

    @cached_property
    def request(self):
        return self.context.get("request")

    @property
    def is_get_method(self):
        return self.request and self.request.method == "GET"
