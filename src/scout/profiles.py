"""Category profiles: common conditions, questions to ask, where to look."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from scout.config import ROOT

PROFILES_DIR = ROOT / "profiles"
DRAFTS_DIR = ROOT / "data" / "profiles"  # categories that the chat model drafted; not in Git


class ProfileAttribute(BaseModel):
    name: str
    label: str
    type: Literal["number", "enum", "boolean", "text"]
    kind: Literal["hard", "soft"]
    unit: str | None = None
    values: list[str] | None = None
    # [min, max] that a real product of the category can have. Sellers sometimes give "10H millimeter" for a fridge;
    # the raw-text check cannot catch that, because the page really says it.
    plausible: list[float] | None = None

    @model_validator(mode="after")
    def _enum_values(self) -> "ProfileAttribute":
        if self.type == "enum" and not self.values:
            raise ValueError(f"enum attribute '{self.name}' needs values")
        if self.plausible is not None and (len(self.plausible) != 2 or self.plausible[0] >= self.plausible[1]):
            raise ValueError(f"plausible of '{self.name}' must be [min, max] with min below max")
        return self

    def is_plausible(self, value) -> bool:
        if self.plausible is None or not isinstance(value, (int, float)):
            return True
        return self.plausible[0] <= value <= self.plausible[1]


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
    draft: bool = False  # drafted by the chat model, not checked by a person

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


def load_profile(category: str, profiles_dir: Path = PROFILES_DIR, drafts_dir: Path = DRAFTS_DIR) -> CategoryProfile:
    for folder in (profiles_dir, drafts_dir):
        path = folder / f"{category}.yaml"
        if path.exists():
            return CategoryProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    available = ", ".join(profile_names(profiles_dir, drafts_dir)) or "none"
    raise FileNotFoundError(f"No profile for '{category}'. Available: {available}")


def profile_names(profiles_dir: Path = PROFILES_DIR, drafts_dir: Path = DRAFTS_DIR) -> list[str]:
    return sorted({p.stem for folder in (profiles_dir, drafts_dir) for p in folder.glob("*.yaml")})


def save_draft(profile: CategoryProfile, drafts_dir: Path = DRAFTS_DIR) -> Path:
    drafts_dir.mkdir(parents=True, exist_ok=True)
    path = drafts_dir / f"{profile.category}.yaml"
    path.write_text(yaml.safe_dump(profile.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return path
