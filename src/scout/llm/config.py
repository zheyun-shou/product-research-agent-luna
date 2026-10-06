"""config/models.yaml: providers and the model each role uses."""
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from scout.config import ROOT

DEFAULT_PATH = ROOT / "config" / "models.yaml"


class ProviderConfig(BaseModel):
    base_url: str
    api_key_env: str


class ModelConfig(BaseModel):
    provider: str
    model: str
    rpm: int = Field(gt=0)
    deadline: float | None = Field(None, gt=0)  # seconds for one call in all; None: the default of the role


class RoleConfig(ModelConfig):
    # Used for the rest of the run when the main model is out of quota, busy or unreachable.
    fallback: ModelConfig | None = None


class LLMConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    roles: dict[str, RoleConfig]

    @model_validator(mode="after")
    def _check(self) -> "LLMConfig":
        for needed in ("chat", "worker"):
            if needed not in self.roles:
                raise ValueError(f"models.yaml needs a '{needed}' role")
        for name, role in self.roles.items():
            for used in (role, role.fallback):
                if used is not None and used.provider not in self.providers:
                    raise ValueError(f"role '{name}' uses unknown provider '{used.provider}'")
        return self


def load_llm_config(path: Path = DEFAULT_PATH) -> LLMConfig:
    return LLMConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
