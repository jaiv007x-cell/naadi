"""Re-export — canonical models live in services.pratibimb.auth.models_registry."""
from services.pratibimb.auth.models_registry import TenantIssuerRow, TenantRow

__all__ = ["TenantIssuerRow", "TenantRow"]
