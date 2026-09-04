"""Refund risk evaluation wrapper delegating to the full pipeline orchestrator."""

from __future__ import annotations

from typing import Any

from .pipeline import evaluate_refund_risk

__all__ = ["evaluate_refund_risk"]