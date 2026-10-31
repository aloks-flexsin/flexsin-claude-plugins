"""Vercel entry point: every route is rewritten here (see ../vercel.json) and served by `app`.

The Vercel project's root is `auto-runner/central`, so the package directory is this file's parent's parent.
It is loaded as the package `central` by path, which works whatever the directory above it is called.
Configuration comes from the environment; `create_app()` refuses to start on a bad production config.
"""
import importlib.util
import os
import sys

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_central():
    loaded = sys.modules.get("central")
    if loaded is not None and os.path.dirname(os.path.abspath(loaded.__file__)) == PACKAGE_DIR:
        return loaded
    spec = importlib.util.spec_from_file_location(
        "central", os.path.join(PACKAGE_DIR, "__init__.py"), submodule_search_locations=[PACKAGE_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules["central"] = module
    spec.loader.exec_module(module)
    return module


app = _load_central().create_app()
