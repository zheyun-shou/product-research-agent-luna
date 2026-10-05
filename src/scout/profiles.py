"""Category profiles: common conditions, questions to ask, where to look."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from scout.config import ROOT

PROFILES_DIR = ROOT / "profiles"


class ProfileAttribute(BaseModel):
    name: str
    label: str
    type: Literal["number", "enum", "boolean", "text"]
    kind: Literal["hard", "soft"]
    unit: str | None = None
    values: list[str] | None = None

    @model_validator(mode="after")
    def _enum_values(self) -> "ProfileAttribute":
        if self.type == "enum" and not self.values:
            raise ValueError(f"enum attribute '{self.name}' needs values")
        return self


class ProductKind(BaseModel):
    name: str
    label: str
    search_terms_sv: str


class CategoryProfile(BaseModel):
    category: str
    display_name: str
    attributes: list[ProfileAttribute]
    questions_for_user: list[str]
    complaint_types: list[str] = Field(default_factory=list)
    sources: dict[str, list[str]]
    search_terms_sv: str | None = None
    kinds: list[ProductKind] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> "CategoryProfile":
        names = [a.name for a in self.attributes]
        if len(names) != len(set(names)):
            raise ValueError("attribute names must be unique")
        return self

    def attribute(self, name: str) -> ProfileAttribute | None:
        return next((a for a in self.attributes if a.name == name), None)

    def kind(self, name: str | None) -> ProductKind | None:
        return next((k for k in self.kinds if k.name == name), None)


def load_profile(category: str, profiles_dir: Path = PROFILES_DIR) -> CategoryProfile:
    path = profiles_dir / f"{category}.yaml"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in profiles_dir.glob("*.yaml"))) or "none"
        raise FileNotFoundError(f"No profile for '{category}'. Available: {available}")
    return CategoryProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
