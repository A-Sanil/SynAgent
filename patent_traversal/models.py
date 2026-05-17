"""
Patent data models and utilities.

Schemas and helpers for patent data processing.
"""

from typing import Optional, List
from pydantic import BaseModel, Field


class ReactionConditions(BaseModel):
    """Reaction conditions extracted from patent."""
    temperature_celsius: Optional[float] = Field(None, description="Reaction temperature in Celsius")
    pressure_bar: Optional[float] = Field(None, description="Reaction pressure in bar")
    solvent: Optional[str] = Field(None, description="Solvent used")
    catalyst: Optional[str] = Field(None, description="Catalyst or reagent")
    reaction_time_hours: Optional[float] = Field(None, description="Reaction duration in hours")
    stirring_rate_rpm: Optional[int] = Field(None, description="Stirring rate in RPM")
    atmosphere: Optional[str] = Field(None, description="Reaction atmosphere (air, N2, Ar, etc.)")
    scale_grams: Optional[float] = Field(None, description="Reaction scale in grams")


class PatentReaction(BaseModel):
    """Chemical reaction from a patent."""
    patent_id: str
    reaction_smarts: Optional[str] = Field(None, description="Reaction SMARTS")
    reactants_smiles: List[str] = Field(default_factory=list, description="Reactant SMILES")
    product_smiles: Optional[str] = Field(None, description="Product SMILES")
    yield_percent: Optional[float] = Field(None, description="Reaction yield %")
    selectivity_percent: Optional[float] = Field(None, description="Selectivity %")
    enantioselectivity_percent: Optional[float] = Field(None, description="Enantioselectivity %")
    regioselectivity_percent: Optional[float] = Field(None, description="Regioselectivity %")
    conditions: Optional[ReactionConditions] = Field(None, description="Reaction conditions")
    mechanism: Optional[str] = Field(None, description="Proposed mechanism")
    notes: Optional[str] = Field(None, description="Additional notes")


class Patent(BaseModel):
    """Patent record with synthesis data."""
    patent_id: str = Field(..., description="Patent identifier")
    title: str
    abstract: str
    publication_date: Optional[str] = Field(None, description="Publication date (YYYY-MM-DD)")
    filing_date: Optional[str] = Field(None, description="Filing date (YYYY-MM-DD)")
    inventors: List[str] = Field(default_factory=list)
    assignee: Optional[str]
    country: Optional[str] = Field(default="US")
    reactions: List[PatentReaction] = Field(default_factory=list, description="Extracted reactions")
    url: Optional[str] = Field(None, description="Link to patent")
    source: Optional[str] = Field(None, description="Database source (Google Patents, USPTO, etc.)")
