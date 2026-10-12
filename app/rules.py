from app.db import rules, utcnow

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
