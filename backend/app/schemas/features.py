from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FeatureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    layer: Literal[
        "ai_presence",
        "mission_control",
        "universal_workspace",
        "ai_command_center",
        "apps_hub",
    ]
    category: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=320)
    ui_entry_point: str = Field(pattern=r"^/[a-z0-9_/#-]+$")
    backend_capability: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    required_permissions: list[str] = Field(max_length=8)
    dependencies: list[str] = Field(max_length=8)
    status: Literal[
        "implemented",
        "runtime_dependent",
        "external_dependency",
        "planned",
    ]
    test_coverage: list[str] = Field(min_length=1, max_length=8)


class FeatureRegistryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    product: Literal["AI OS"] = "AI OS"
    count: int = Field(ge=140, le=500)
    items: list[FeatureResponse] = Field(min_length=140, max_length=500)
