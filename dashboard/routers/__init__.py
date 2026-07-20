"""
FastAPI routers package initialization.

Implements auto-discovery and auto-registration of APIRouter instances.
"""
from __future__ import annotations

import pkgutil
import importlib
import logging
from fastapi import FastAPI, APIRouter

logger = logging.getLogger(__name__)

# Modules that failed to import during the last init_routers() pass.
# Exposed so /api/status /health checks can surface silently-missing API
# surface area (e.g. a dependency absent from the deployed image).
FAILED_ROUTER_MODULES: dict[str, str] = {}


def init_routers(app: FastAPI):
    """Dynamic directory-walking router registration using Python reflection.

    Scans the routers/ package and automatically attaches all APIRouter instances.
    Tracks registered router IDs to avoid duplicate registration.

    Import failures are logged CRITICAL and recorded in FAILED_ROUTER_MODULES —
    a failed module silently removes its whole endpoint group from the API, so
    the gap must be loud and observable.
    """
    logger.info("☘️  Starting router auto-discovery...")
    registered_routers = set()
    count = 0
    FAILED_ROUTER_MODULES.clear()

    for _, module_name, _ in pkgutil.iter_modules(__path__):
        # Skip shared helpers and background modules that don't expose APIs
        if module_name in ["helpers", "bb_firebase_sync", "agent_brain", "bb_private_api"]:
            continue

        try:
            mod = importlib.import_module(f".{module_name}", package=__package__)

            # Iterate over all attributes in the imported module
            for attr_name in dir(mod):
                attr_val = getattr(mod, attr_name)
                if isinstance(attr_val, APIRouter):
                    router_id = id(attr_val)
                    if router_id not in registered_routers:
                        app.include_router(attr_val)
                        registered_routers.add(router_id)
                        count += 1
                        logger.debug("Registered APIRouter '%s' from module '%s'", attr_name, module_name)
        except Exception as e:
            FAILED_ROUTER_MODULES[module_name] = f"{type(e).__name__}: {e}"
            logger.critical(
                "🚨 Router module '%s' FAILED to load — its endpoints are MISSING "
                "from the API: %s", module_name, e,
            )

    if FAILED_ROUTER_MODULES:
        logger.critical(
            "🚨 Router auto-discovery finished with %d FAILED module(s): %s "
            "(registered %d router instances)",
            len(FAILED_ROUTER_MODULES), sorted(FAILED_ROUTER_MODULES), count,
        )
    else:
        logger.info("☘️  Router auto-discovery complete — registered %d router instances", count)
