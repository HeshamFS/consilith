"""Bounded platform inputs; this module imports no scientific implementation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_CIF_BYTES = 1024 * 1024
MAX_SITES = 256
EXAMPLE_NAMES = ("SrTiO3", "BaTiO3", "CaTiO3", "Bi2Se3")
TOOL = "crystal.analyze"
CASE = "crystal-polyhedra"


class AnalysisConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    method: Literal["shannon", "crystalnn", "cutoff"] = "shannon"
    max_bond: float | None = Field(default=None, gt=0, le=6)
    min_bond: float = Field(default=0, ge=0, lt=6)
    radius_tolerance: float = Field(default=0.15, ge=0, le=0.5)
    include_element_shells: bool = False

    @model_validator(mode="after")
    def check_cutoff(self):
        if self.method == "cutoff":
            if self.max_bond is None or self.max_bond <= self.min_bond:
                raise ValueError("Cutoff analysis needs max_bond greater than min_bond (angstrom)")
        elif self.max_bond is not None:
            raise ValueError("Set method to cutoff before supplying max_bond")
        return self


def configuration_from_lane(lane: dict) -> AnalysisConfiguration:
    values = {}
    for key in ("method", "max_bond", "min_bond", "radius_tolerance"):
        if key in lane:
            value = lane[key]
            values[key] = value.get("v") if isinstance(value, dict) else value
    if "include_element_shells" in lane:
        choice = lane["include_element_shells"]
        if choice not in ("yes", "no"):
            raise ValueError("Element shells must be yes or no")
        values["include_element_shells"] = choice == "yes"
    return AnalysisConfiguration.model_validate(values)
