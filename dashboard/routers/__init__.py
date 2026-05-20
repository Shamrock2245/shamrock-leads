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


def init_routers(app: FastAPI):
    """Dynamic directory-walking router registration using Python reflection.

    Scans the routers/ package and automatically attaches all APIRouter instances.
    Tracks registered router IDs to avoid duplicate registration.
    """
    logger.info("☘️  Starting router auto-discovery...")
    registered_routers = set()
    count = 0

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
            logger.error("Failed to auto-load router from module '%s': %s", module_name, e)

    logger.info("☘️  Router auto-discovery complete — registered %d router instances", count)
