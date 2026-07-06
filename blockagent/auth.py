"""Credential + token helpers.

Uses azure-identity so the same code works with a Managed Identity in Azure and
with an app registration (client secret) or developer sign-in locally.
"""
from __future__ import annotations

from typing import Optional

from azure.identity import ClientSecretCredential, DefaultAzureCredential

from .config import Config

# Data-plane audience for Azure AI Foundry / Cognitive Services accounts.
FOUNDRY_SCOPE = "https://ai.azure.com/.default"
# Control-plane (ARM) audience for tag operations.
ARM_SCOPE = "https://management.azure.com/.default"


def get_credential(config: Config):
    """Return a TokenCredential.

    Prefers an explicit app registration when tenant/client/secret are supplied
    (useful for local dev); otherwise falls back to DefaultAzureCredential which
    resolves to the Function App's Managed Identity in Azure.
    """
    if config.tenant_id and config.client_id and config.client_secret:
        return ClientSecretCredential(
            tenant_id=config.tenant_id,
            client_id=config.client_id,
            client_secret=config.client_secret,
        )
    return DefaultAzureCredential()


def get_token(config: Config, scope: str, credential: Optional[object] = None) -> str:
    """Acquire a bearer token for the given scope."""
    cred = credential or get_credential(config)
    return cred.get_token(scope).token
