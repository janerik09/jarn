from datetime import datetime
from typing import Any, Dict, List


def build_snapshot(patches: List[Any], scan_info: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "collected_at": datetime.utcnow().isoformat(),
        "patches": [
            {
                "name": p.name,
                "version": p.version,
                "patch_type": p.patch_type,
                "description": p.description,
                "cve_ids": p.cve_ids or [],
            }
            for p in patches
        ],
        "total_available": len(patches),
        "upgradable_packages": scan_info.get("upgradable_packages", len(patches)),
        "security_packages": scan_info.get("security_packages"),
        "index_refreshed": scan_info.get("index_refreshed"),
    }
