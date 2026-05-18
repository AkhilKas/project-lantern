"""Load and validate pipeline configuration from params.yaml.

This is the single source of truth for runtime config. Every module that
needs a path, threshold, or list of companies pulls it from here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

# Project root = two levels above this file (src/lantern/config.py -> project root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARAMS_PATH = PROJECT_ROOT / "params.yaml"


class Company(BaseModel):
    ticker: str
    name: str


class FilingSpec(BaseModel):
    form: Literal["10-K", "10-Q"]
    limit: int = Field(ge=1, le=20)


class EdgarConfig(BaseModel):
    user_agent_name: str
    user_agent_email: str
    companies: list[Company]
    filings: list[FilingSpec]
    include_xbrl: bool = True


class PathsConfig(BaseModel):
    raw: Path
    interim: Path
    parsed: Path
    ground_truth: Path

    def absolute(self) -> PathsConfig:
        """Resolve all paths relative to the project root."""
        return PathsConfig(
            raw=PROJECT_ROOT / self.raw,
            interim=PROJECT_ROOT / self.interim,
            parsed=PROJECT_ROOT / self.parsed,
            ground_truth=PROJECT_ROOT / self.ground_truth,
        )


class TextExtractionConfig(BaseModel):
    ocr_char_threshold: int = 50
    ocr_dpi: int = 300
    ocr_lang: str = "eng"


class TablesConfig(BaseModel):
    camelot_flavors: list[Literal["lattice", "stream"]]
    ruling_line_min_count: int = 4


class LanternConfig(BaseModel):
    edgar: EdgarConfig
    paths: PathsConfig
    text_extraction: TextExtractionConfig
    tables: TablesConfig


def load_config(path: Path | str = DEFAULT_PARAMS_PATH) -> LanternConfig:
    """Read params.yaml and return a validated config object."""
    with open(path) as f:
        data = yaml.safe_load(f)
    cfg = LanternConfig.model_validate(data)
    cfg.paths = cfg.paths.absolute()
    return cfg
