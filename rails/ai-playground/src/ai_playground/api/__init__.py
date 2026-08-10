"""Re-export the app factory so the Dockerfile can target ``ai_playground.api:create_api``."""
from ai_playground.api.app import create_api

__all__ = ["create_api"]
