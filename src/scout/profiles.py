"""Category profiles: common conditions, questions to ask, where to look."""
import re
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
    site_filter: str | None = None  # the name of the filter group of the shop, when it is not the label
    title_words: list[str] = Field(default_factory=list)  # words next to the value in a search title, any language
    # A dimension of the variants of the shop ("size_name" on amazon.se): each value has its own page and stock
    variant: str | None = None

    @model_validator(mode="after")
    def _enum_values(self) -> "ProfileAttribute":
        if self.type == "enum" and not self.values:
            raise ValueError(f"enum attribute '{self.name}' needs values")
        return self


RESERVED = ("price", "rating", "review_count")  # the system has them for each product


def attribute_errors(attributes: list, existing: tuple[str, ...] = ()) -> list[str]:
    """The problems of attributes that a model wrote (a draft profile, update_profile). existing: the names that
    the profile has already."""
    errors, seen = [], set(existing)
    for a in attributes:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", a.name):
            errors.append(f"attribute {a.name}: use lowercase English letters and underscores")
        if a.name in RESERVED:
            errors.append(f"attribute {a.name}: the system has {a.name} already")
        elif a.name in seen:
            errors.append(f"attribute {a.name} is there two times")
        seen.add(a.name)
        if a.type == "number" and not a.unit:
            errors.append(f"attribute {a.name} is a number, so it needs a unit")
        if a.type == "enum" and not a.values:
            errors.append(f"attribute {a.name} is an enum, so it needs values")
        if a.kind == "hard" and a.type != "number":
            errors.append(f"attribute {a.name} is hard but not a number: only numbers can be conditions, so make it "
                          "soft")
    return errors


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
    edited: bool = False  # changed in a conversation (update_profile); the preset file in Git stays the same

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
    # A preset comes before a draft of the same name. Only a copy that the shopper changed (update_profile marks it
    # edited) comes before the preset.
    draft_path, preset_path = drafts_dir / f"{category}.yaml", profiles_dir / f"{category}.yaml"
    draft = yaml.safe_load(draft_path.read_text(encoding="utf-8")) if draft_path.exists() else None
    if draft and (draft.get("edited") or not preset_path.exists()):
        return CategoryProfile.model_validate(draft)
    if preset_path.exists():
        return CategoryProfile.model_validate(yaml.safe_load(preset_path.read_text(encoding="utf-8")))
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
