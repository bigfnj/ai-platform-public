"""FastAPI surface for the recipe-book rail. Run as
``uvicorn --factory recipe_book.api:create_api``."""
from recipe_book.api.app import create_api

__all__ = ["create_api"]
