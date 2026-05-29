"""Effort rate-card endpoints (GET + PUT JSON).

The card is stored as ``effort-card.json`` next to the .env file the
analyser already manages. GET returns the *effective* card (defaults
merged with any override), the source path (or ``"default"``), and the
list of rule keys recognised by the estimator so the SPA can offer hints.
PUT validates the body and writes it to disk, refusing any path that
would escape the configured config dir.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...effort import DEFAULT_CARD, default_card, dump_card, merge_with_default
from ..deps import AppState, get_state

router = APIRouter()


_CARD_FILENAME = "effort-card.json"


class EffortCardResponse(BaseModel):
    source: str
    card: dict[str, Any]
    available_rule_keys: list[str] = Field(default_factory=list)
    available_phase_keys: list[str] = Field(default_factory=list)
    is_default: bool


class EffortCardUpdate(BaseModel):
    # Accept a *partial* override; it will be merged on top of the
    # shipped default before validation.
    card: dict[str, Any]


class EffortCardSaveResponse(BaseModel):
    saved_to: str
    source: str


def _card_path(state: AppState):
    return state.env_file.parent.resolve() / _CARD_FILENAME


def _load_effective(state: AppState):
    p = _card_path(state)
    if p.is_file():
        # Reuse the same loader so validation/merging is consistent.
        from ...effort import load_card as _load

        card = _load(str(p))
        return card, str(p), False
    return default_card(), "default", True


@router.get("", response_model=EffortCardResponse)
def get_effort_card(state: AppState = Depends(get_state)) -> EffortCardResponse:
    card, source, is_default = _load_effective(state)
    return EffortCardResponse(
        source=source,
        card=card.to_dict(),
        available_rule_keys=sorted(DEFAULT_CARD["rules"].keys()),
        available_phase_keys=sorted(DEFAULT_CARD["phases"].keys()),
        is_default=is_default,
    )


@router.put("", response_model=EffortCardSaveResponse)
def put_effort_card(
    body: EffortCardUpdate,
    state: AppState = Depends(get_state),
) -> EffortCardSaveResponse:
    try:
        merged = merge_with_default(body.card)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"invalid rate card: {exc}") from exc
    target = _card_path(state)
    # Defence-in-depth: refuse to escape the config dir.
    if target.parent != state.env_file.parent.resolve():
        raise HTTPException(status_code=400, detail="invalid effort card destination")
    dump_card(merged, target)
    return EffortCardSaveResponse(saved_to=str(target), source=str(target))


@router.delete("", response_model=EffortCardSaveResponse)
def reset_effort_card(state: AppState = Depends(get_state)) -> EffortCardSaveResponse:
    """Delete any override file and revert to the shipped defaults."""
    target = _card_path(state)
    if target.is_file():
        target.unlink()
    return EffortCardSaveResponse(saved_to=str(target), source="default")
