from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.core.db import repos, rules, utcnow
from app.web import current_user, flash, render

router = APIRouter()

EVENTS = ["issues", "pull_request", "push", "any"]
FIELDS = ["title", "body", "author", "label"]
OPS = ["contains", "equals"]
ACTION_TYPES = ["add_label", "post_comment", "slack"]

EXAMPLE_RULE = {
    "name": "Example: label and announce bugs",
    "event": "issues",
    "enabled": True,
    "conditions": [{"field": "title", "op": "contains", "value": "bug"}],
    "actions": [
        {"type": "add_label", "value": "bug"},
        {"type": "slack", "value": "New bug reported"},
    ],
}


def add_example_rule(user_id, repo: str):
    """Give a new user one working rule so the bot does something right away."""
    rules.insert_one({**EXAMPLE_RULE, "user_id": user_id, "repo": repo, "created_at": utcnow()})


def build_rule(connected: list, name, repo, event, cond_field, cond_op, cond_value, act_type, act_value) -> dict:
    """Check the form input (a trust boundary) and return the rule. Raises ValueError with a message for the user."""
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("Give the rule a name (up to 100 characters).")
    if repo != "*" and repo not in connected:
        raise ValueError("Pick one of your connected repos, or all repos.")
    if event not in EVENTS:
        raise ValueError("Pick an event.")

    conditions = []
    for field, op, value in zip(cond_field, cond_op, cond_value):
        value = value.strip()
        if not value:
            continue  # an empty row means "no condition"
        if field not in FIELDS or op not in OPS or len(value) > 200:
            raise ValueError("A condition is not valid.")
        conditions.append({"field": field, "op": op, "value": value})

    chosen = []
    for kind, value in zip(act_type, act_value):
        value = value.strip()
        if not kind:
            continue  # an empty row means "no action"
        if kind not in ACTION_TYPES or len(value) > 500:
            raise ValueError("An action is not valid.")
        if kind != "slack" and not value:
            raise ValueError("Labels and comments need a value (the label name or the comment text).")
        if event == "push" and kind != "slack":
            raise ValueError("Push events have no issue to label or comment on, so use Slack only.")
        chosen.append({"type": kind, "value": value})
    if not chosen:
        raise ValueError("Add at least one action.")

    return {"name": name, "repo": repo, "event": event, "enabled": True, "conditions": conditions, "actions": chosen}


def rule_object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except InvalidId:
        raise HTTPException(404, "No such rule")


@router.get("/rules")
def rules_page(request: Request, user: dict = Depends(current_user)):
    return render(
        request,
        "rules.html",
        user,
        page="rules",
        rules=list(rules.find({"user_id": user["_id"]}).sort("created_at", 1)),
        repos=[r["full_name"] for r in repos.find({"user_id": user["_id"]})],
        events=EVENTS,
        fields=FIELDS,
        ops=OPS,
        action_types=ACTION_TYPES,
    )


@router.post("/rules")
def create_rule(
    request: Request,
    name: str = Form(""),
    repo: str = Form(""),
    event: str = Form(""),
    cond_field: list[str] = Form([]),
    cond_op: list[str] = Form([]),
    cond_value: list[str] = Form([]),
    act_type: list[str] = Form([]),
    act_value: list[str] = Form([]),
    user: dict = Depends(current_user),
):
    connected = [r["full_name"] for r in repos.find({"user_id": user["_id"]})]
    try:
        rule = build_rule(connected, name, repo, event, cond_field, cond_op, cond_value, act_type, act_value)
    except ValueError as e:
        flash(request, str(e))
    else:
        rules.insert_one({**rule, "user_id": user["_id"], "created_at": utcnow()})
        flash(request, f"Added rule: {rule['name']}")
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/enabled")
def set_enabled(rule_id: str, enabled: str = Form(...), user: dict = Depends(current_user)):
    # Filtering by user_id means you can only change your own rules.
    rules.update_one({"_id": rule_object_id(rule_id), "user_id": user["_id"]}, {"$set": {"enabled": enabled == "true"}})
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/delete")
def delete_rule(rule_id: str, request: Request, user: dict = Depends(current_user)):
    if rules.delete_one({"_id": rule_object_id(rule_id), "user_id": user["_id"]}).deleted_count:
        flash(request, "Rule deleted.")
    return RedirectResponse("/rules", status_code=303)
