from pymongo import MongoClient
from app import config

client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=5000)
db = client["github_bot"]
users = db["users"]
repos = db["repos"]
events = db["events"]
actions = db["actions"]


def init_db():
    """Create the unique indexes: the database itself refuses duplicates, so code races can't create them."""
    users.create_index("github_id", unique=True)
    repos.create_index("full_name", unique=True)
    events.create_index("delivery_id", unique=True)
    actions.create_index(
        [("delivery_id", 1), ("rule_id", 1), ("action_index", 1)], unique=True
    )
