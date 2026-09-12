"""Shared authentication dependency; honors the application's configured identity adapter."""

from identity.adapters.http_auth import extract_principal as extract_principal
