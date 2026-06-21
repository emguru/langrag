from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class SemanticCategory(str, Enum):
    PRIMARY_CONCEPT = "primary_concept"
    DOMAIN_TERM = "domain_term"
    DIRECT_SYNONYM = "direct_synonym"
    ADJACENT_CONCEPT = "adjacent_concept"
    BUSINESS_GOAL = "business_goal"
    USER_INTENT = "user_intent"
    NEGATIVE_CONTEXT = "negative_context"


class WeightedSemanticTerm(BaseModel):
    term: str
    category: SemanticCategory
    weight: float = Field(ge=0.0, le=1.0)
    reason: str = ""

    @field_validator("term")
    @classmethod
    def term_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("term must not be empty")
        return value.strip()


class SWAGProfile(BaseModel):
    primary_concepts: list[str] = Field(default_factory=list)
    domain_terms: list[str] = Field(default_factory=list)
    direct_synonyms: list[str] = Field(default_factory=list)
    adjacent_concepts: list[str] = Field(default_factory=list)
    business_goals: list[str] = Field(default_factory=list)
    user_intents: list[str] = Field(default_factory=list)
    negative_contexts: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    detected_domain: str = "general"
    # Sidecar metadata: title | body | nav | boilerplate — not source content.
    chunk_kind: str = "body"
    weighted_terms: list[WeightedSemanticTerm] = Field(default_factory=list)


class QueryProfile(BaseModel):
    original_query: str
    swag_profile: SWAGProfile
