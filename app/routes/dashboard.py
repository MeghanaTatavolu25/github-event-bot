from collections import defaultdict

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse

from app.core.db import actions, events, utcnow
from app.services.engine import process_event
from app.web import current_user, flash, render

router = APIRouter()

STATUSES = ["received", "processing", "done", "failed", "ignored"]


@router.get("/dashboard")
def dashboard(request: Request, repo: str = "", status: str = "", user: dict = Depends(current_user)):
    query = {"user_id": user["_id"]}  # every query is limited to the logged-in user's own data
    if repo:
        query["repo"] = repo
    if status in STATUSES:
        query["status"] = status
    # The payload can be large and the page does not show it, so leave it out.
    latest = list(events.find(query, {"payload": 0}).sort("received_at", -1).limit(50))

    taken = defaultdict(list)
    for action in actions.find({"delivery_id": {"$in": [e["delivery_id"] for e in latest]}}).sort("created_at", 1):
        taken[action["delivery_id"]].append(action)

    # Counts for the clickable status tiles (all of the user's events, not just the latest 50).
    totals = events.aggregate([
        {"$match": {"user_id": user["_id"]}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ])
    return render(
        request,
        "dashboard.html",
        user,
        page="dashboard",
        counts={row["_id"]: row["n"] for row in totals},
        events=latest,
        actions=dict(taken),
        repos=events.distinct("repo", {"user_id": user["_id"]}),
        statuses=STATUSES,
        repo=repo,
        status=status,
    )


@router.post("/events/{delivery_id}/retry")
def retry_now(delivery_id: str, request: Request, background: BackgroundTasks, user: dict = Depends(current_user)):
    # Only your own failed events. Making it due now skips the wait; actions that already
    # succeeded are not repeated because process_event checks the actions collection.
    result = events.update_one(
        {"delivery_id": delivery_id, "user_id": user["_id"], "status": "failed"},
        {"$set": {"next_retry_at": utcnow()}},
    )
    if result.matched_count:
        background.add_task(process_event, delivery_id)
        flash(request, "Retrying now.")
    return RedirectResponse("/dashboard", status_code=303)
