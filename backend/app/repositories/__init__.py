"""Persistence repositories receive sessions and never commit."""

from app.repositories.message import MessageRepository

__all__ = ["MessageRepository"]
