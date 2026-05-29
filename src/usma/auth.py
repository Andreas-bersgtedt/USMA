"""Authentication helpers built on azure-identity."""
from __future__ import annotations

from functools import lru_cache

from azure.identity import ClientSecretCredential

from .config import AzureConfig


@lru_cache(maxsize=1)
def _credential_for(tenant_id: str, client_id: str, client_secret: str) -> ClientSecretCredential:
    return ClientSecretCredential(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)


def get_credential(azure: AzureConfig) -> ClientSecretCredential:
    """Return a cached ClientSecretCredential for the configured service principal."""
    return _credential_for(azure.tenant_id, azure.client_id, azure.client_secret)


def get_sql_access_token(azure: AzureConfig) -> str:
    """Acquire an AAD access token for Azure SQL / Synapse SQL endpoints."""
    cred = get_credential(azure)
    token = cred.get_token("https://database.windows.net/.default")
    return token.token
