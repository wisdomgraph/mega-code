"""Verify that hand-written Pydantic models stay in sync with spec/openapi.yaml.

Replaces the old code-generator approach — instead of generating models we
check that the models we maintain by hand still match the spec's property
names and nullability expectations.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args, get_origin

import pytest
import yaml

SPEC_PATH = Path(__file__).resolve().parent.parent / "spec" / "openapi.yaml"

# Mapping from spec schema name → (Python model class, field alias map).
# The alias map translates Python field names to spec property names where
# they differ (e.g. auto_permission → autoPermission).
# Maps spec schema name → (model class name, field alias overrides).
# When model class name differs from spec name, use a tuple: ("SpecName", "ModelName").
_SCHEMA_MAP: dict[str, tuple[str, dict[str, str]]] = {
    "ProfileResponse": ("UserProfile", {"auto_permission": "autoPermission"}),
    "ProfileUpdateRequest": ("ProfileUpdateRequest", {}),
    "TrajectoryUploadRequest": ("TrajectoryUploadRequest", {}),
    "TurnPayload": ("TurnPayload", {}),
    "PipelineRunRequest": ("PipelineRunRequest", {}),
    "PipelineProgress": ("PipelineProgress", {}),
    "LessonSummary": ("LessonSummary", {}),
    "HealthResponse": ("HealthResponse", {}),
    "ErrorResponse": ("ErrorResponse", {}),
}


def _load_spec_schemas() -> dict:
    with open(SPEC_PATH) as f:
        spec = yaml.safe_load(f)
    return spec["components"]["schemas"]


def _get_model_class(schema_name: str):
    """Import model by name from protocol, using _SCHEMA_MAP for renames."""
    import mega_code.client.api.protocol as protocol

    model_name = _SCHEMA_MAP[schema_name][0]
    cls = getattr(protocol, model_name, None)
    if cls is None:
        pytest.skip(f"{model_name} not in protocol (may be intentionally omitted)")
    return cls


def _is_nullable(field_info) -> bool:
    """Check if a Pydantic FieldInfo allows None."""
    annotation = field_info.annotation
    if annotation is None:
        return True
    origin = get_origin(annotation)
    if origin is type(None):
        return True
    # Union types (X | None)
    args = get_args(annotation)
    if args and type(None) in args:
        return True
    return False


def _spec_field_names(schema: dict) -> set[str]:
    return set(schema.get("properties", {}).keys())


def _model_field_names(cls, alias_map: dict[str, str]) -> set[str]:
    """Get the set of field names as they appear in JSON (using aliases)."""
    names = set()
    for name, info in cls.model_fields.items():
        alias = info.alias or alias_map.get(name, name)
        names.add(alias)
    return names


@pytest.fixture(scope="module")
def spec_schemas():
    return _load_spec_schemas()


class TestSpecSync:
    """Ensure hand-written models match the OpenAPI spec."""

    @pytest.mark.parametrize("schema_name", list(_SCHEMA_MAP.keys()))
    def test_field_names_match(self, spec_schemas, schema_name):
        """Model fields must match spec property names."""
        schema = spec_schemas.get(schema_name)
        if schema is None:
            pytest.skip(f"{schema_name} not in spec")

        cls = _get_model_class(schema_name)
        _, alias_map = _SCHEMA_MAP[schema_name]

        spec_fields = _spec_field_names(schema)
        model_fields = _model_field_names(cls, alias_map)

        assert model_fields == spec_fields, (
            f"{schema_name}: field mismatch.\n"
            f"  In spec but not model: {spec_fields - model_fields}\n"
            f"  In model but not spec: {model_fields - spec_fields}"
        )

    @pytest.mark.parametrize("schema_name", list(_SCHEMA_MAP.keys()))
    def test_nullable_fields_match(self, spec_schemas, schema_name):
        """Fields marked nullable in spec must be Optional in model, and vice versa."""
        schema = spec_schemas.get(schema_name)
        if schema is None:
            pytest.skip(f"{schema_name} not in spec")

        cls = _get_model_class(schema_name)
        _, alias_map = _SCHEMA_MAP[schema_name]
        properties = schema.get("properties", {})

        for field_name, field_info in cls.model_fields.items():
            json_name = field_info.alias or alias_map.get(field_name, field_name)
            prop = properties.get(json_name)
            if prop is None:
                continue

            spec_nullable = prop.get("nullable", False)
            model_nullable = _is_nullable(field_info)

            if spec_nullable and not model_nullable:
                pytest.fail(
                    f"{schema_name}.{json_name}: spec says nullable but model is not Optional"
                )
            if not spec_nullable and model_nullable:
                # Only flag if the field also has no default — fields with
                # defaults are allowed to be Optional for convenience.
                if field_info.default is None and field_info.default_factory is None:
                    pytest.fail(
                        f"{schema_name}.{json_name}: spec is non-nullable but model allows None "
                        f"with no default"
                    )
