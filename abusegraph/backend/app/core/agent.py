"""Evidence-grounded investigation explanations and recommendations."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

from dotenv import load_dotenv

from .policy import HIGH_CONFIDENCE_THRESHOLD
from .risk_engine import CASE_THRESHOLD


REQUIRED_EVIDENCE = (
    "cluster_size",
    "shared_device",
    "shared_ip",
    "shared_address",
    "avg_time_to_refund_hours",
    "ml_score",
    "graph_score",
    "final_score",
    "is_new_customer",
    "prior_refund_count",
    "prior_case_count",
)


def _validate_evidence(evidence: dict[str, Any]) -> None:
    if missing := [key for key in REQUIRED_EVIDENCE if key not in evidence]:
        raise ValueError(f"Missing investigation evidence: {', '.join(missing)}")


def _recommendation(evidence: dict[str, Any]) -> str:
    """Choose a conservative informational recommendation from supplied evidence."""
    if (
        evidence["final_score"] >= HIGH_CONFIDENCE_THRESHOLD
        and evidence["shared_device"]
        and evidence["cluster_size"] >= 4
    ):
        return "restrict"
    only_shared_ip = evidence["shared_ip"] and not evidence["shared_device"] and not evidence["shared_address"]
    only_shared_address = evidence["shared_address"] and not evidence["shared_device"] and not evidence["shared_ip"]
    if only_shared_ip or (only_shared_address and evidence["final_score"] < CASE_THRESHOLD):
        return "allow"
    if evidence["final_score"] >= CASE_THRESHOLD:
        return "manual review"
    return "allow"


def _fallback(evidence: dict[str, Any]) -> dict[str, Any]:
    history = (
        "first-time customer with no prior refund or case history"
        if evidence["is_new_customer"]
        else (
            "repeat pattern with "
            f"{evidence['prior_refund_count']} prior refunds and "
            f"{evidence['prior_case_count']} prior cases"
        )
    )
    signals = []
    if evidence["shared_device"]:
        signals.append("a shared device")
    if evidence["shared_ip"]:
        signals.append("a shared IP")
    if evidence["shared_address"]:
        signals.append("a shared address")
    signal_text = ", ".join(signals) if signals else "no shared device, IP, or address signal"
    caveat = ""
    if evidence["shared_ip"] and not evidence["shared_device"] and not evidence["shared_address"]:
        caveat = " Shared IP alone is not sufficient evidence because hostels, offices, campus Wi-Fi, and mobile NAT can be legitimate."
    elif evidence["shared_address"] and not evidence["shared_device"] and not evidence["shared_ip"]:
        caveat = " Shared address alone is not sufficient evidence because legitimate households can share an address."
    explanation = (
        f"The customer has {history}. The investigation found {signal_text} "
        f"in a cluster of {evidence['cluster_size']} customers. Average time to refund "
        f"is {evidence['avg_time_to_refund_hours']} hours. The individual ML score is "
        f"{evidence['ml_score']}, the graph score is {evidence['graph_score']}, and the "
        f"combined score is {evidence['final_score']}.{caveat}"
    )
    return {"explanation": explanation, "recommendation": _recommendation(evidence), "used_llm": False}


def _numeric_tokens(value: Any) -> set[str]:
    return {
        match.rstrip("0").rstrip(".") if "." in match else match
        for match in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", str(value))
    }


def _llm_has_only_evidence_numbers(text: str, evidence: dict[str, Any]) -> bool:
    allowed = set().union(*(_numeric_tokens(value) for value in evidence.values()))
    return all(token in allowed for token in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", text))


def _call_anthropic(evidence: dict[str, Any], api_key: str) -> dict[str, Any] | None:
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            "Explain this investigation using ONLY the JSON evidence provided. Do not invent "
            "numbers, facts, signals, or history. Mention the customer history as either "
            "first-time customer or repeat pattern. Shared IP or shared address alone must be "
            "explicitly caveated as insufficient evidence because legitimate hostels, offices, "
            "campus Wi-Fi, mobile NAT, or households can share them. End with exactly one line "
            "in this form: Recommendation: <allow|manual review|restrict>.\n\n"
            f"Evidence JSON:\n{json.dumps(evidence, sort_keys=True)}"
        )
        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20240620"),
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(block.text for block in response.content if getattr(block, "text", None)).strip()
        match = re.search(r"Recommendation:\s*(allow|manual review|restrict)\s*$", text, re.IGNORECASE)
        if not match or not _llm_has_only_evidence_numbers(text, evidence):
            return None
        return {
            "explanation": text[: match.start()].strip(),
            "recommendation": match.group(1).lower(),
            "used_llm": True,
        }
    except Exception:
        return None


def investigate(evidence: dict[str, Any]) -> dict[str, Any]:
    """Return an informational explanation and recommendation for evidence."""
    _validate_evidence(evidence)
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    if api_key := os.getenv("ANTHROPIC_API_KEY", "").strip():
        if llm_result := _call_anthropic(evidence, api_key):
            return llm_result
    return _fallback(evidence)