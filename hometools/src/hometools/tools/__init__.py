"""Auto-discovery: importing this package imports every sibling module so
each module's @tool-decorated functions register themselves into
hometools.registry.REGISTRY. To add a new tool, just drop a new module in
this package -- no need to edit app.py or this file.
"""

from __future__ import annotations

import importlib
import pkgutil

for _module_info in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_module_info.name}")
