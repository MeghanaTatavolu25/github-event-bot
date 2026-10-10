from pymongo import MongoClient
from app import config

client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=5000)
db = client["github_bot"] 