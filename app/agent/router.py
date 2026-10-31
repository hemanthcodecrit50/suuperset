"""Router node: decide how much work a new or changed posting deserves."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from app.agent.state import Route, StudentMemory

OUT_OF_PROCESS_STATUSES = frozenset(
    {"REJECTED", "WITHDRAWN", "OFFER_REJECTED", "CLOSED_FOR_APPLICATIONS"}
)
IN_PROCESS_STATUSES = frozenset(
    {"APPLIED", "SHORTLISTED", "ASSESSMENT", "INTERVIEW", "OFFERED", "OFFER_ACCEPTED"}
)
MATERIAL_FIELDS = frozenset({"description", "eligibility", "deadline", "skills"})


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    reason: str


def route_change(
    opportunity: dict[str, Any],
    change_type: str,
    changes: dict[str, Any],
    memory: StudentMemory,
    now: dt.datetime | None = None,
) -> RouteDecision:
    """Pick the full path, the short path or drop the item as noise."""
    now = now or dt.datetime.now(dt.UTC)
    status = str(opportunity.get("application_status") or "").upper()

    if status in OUT_OF_PROCESS_STATUSES:
        return RouteDecision(Route.DROP, f"Student is out of this process ({status}).")
    if _is_deadline_passed(opportunity, now) and status not in IN_PROCESS_STATUSES:
        return RouteDecision(Route.DROP, "Deadline has already passed.")

    if change_type == "new":
        if opportunity.get("eligible") is False:
            return RouteDecision(
                Route.SHORT, "Student is not eligible; only fit is explained."
            )
        return RouteDecision(Route.FULL, "New posting.")

    if status in IN_PROCESS_STATUSES or memory.is_company_applied:
        return RouteDecision(
            Route.SHORT, "Update to a process the student is already in."
        )
    if not changes:
        return RouteDecision(Route.DROP, "Change touched no tracked field.")
    if not MATERIAL_FIELDS & set(changes):
        return RouteDecision(Route.SHORT, "Only cosmetic fields changed.")
    return RouteDecision(Route.FULL, "Job details changed.")


def _is_deadline_passed(opportunity: dict[str, Any], now: dt.datetime) -> bool:
    deadline = opportunity.get("deadline")
    if not deadline:
        return False
    parsed = dt.datetime.fromisoformat(str(deadline))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed < now
