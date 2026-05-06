"""Shared CORS middleware wiring for Niuu HTTP services."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from niuu.config import CorsConfig


def apply_cors_middleware(app: FastAPI, cors: CorsConfig) -> None:
    """Attach CORSMiddleware when CORS origins are configured."""
    if not cors.allowed_origins:
        return

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors.allowed_origins,
        allow_credentials=cors.allow_credentials,
        allow_methods=cors.allow_methods,
        allow_headers=cors.allow_headers,
    )
