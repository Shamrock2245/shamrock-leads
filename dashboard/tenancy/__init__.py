"""Multi-tenant foundation. Default off via SAAS_MULTI_TENANT.

Importing this package must stay free of Motor, FastAPI, and scraper imports
so job runners and scripts can bind a tenant without starting the dashboard.
"""

from dashboard.tenancy.constants import SHAMROCK_TENANT_ID
from dashboard.tenancy.flag import multi_tenant_enabled

__all__ = ["SHAMROCK_TENANT_ID", "multi_tenant_enabled"]
