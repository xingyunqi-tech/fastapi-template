# /// script
# requires-python = ">=3.12"
# ///
"""按 OSV 严重级别过滤 pip-audit 的 JSON 结果，存在 high 及以上漏洞时退出码为 1。

用法：audit_severity.py AUDIT_JSON   （未标注严重级别的漏洞按 high 处理）
"""

import json
import sys
import time
import urllib.error
import urllib.request

OSV_URL = "https://api.osv.dev/v1/vulns/"
LEVELS = {"LOW": 1, "MODERATE": 2, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
THRESHOLD = LEVELS["HIGH"]


def fetch(vuln_id: str) -> dict[str, object] | None:
    """OSV 无此 id（404）时返回 None。"""
    for attempt in range(5):
        try:
            return get(vuln_id)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if attempt == 4:
                raise
        except OSError:
            if attempt == 4:
                raise
        time.sleep(2 * (attempt + 1))
    raise AssertionError


def get(vuln_id: str) -> dict[str, object]:
    with urllib.request.urlopen(OSV_URL + vuln_id, timeout=30) as response:  # noqa: S310
        result: dict[str, object] = json.load(response)
    return result


def severity(ids: list[str]) -> str | None:
    for vuln_id in sorted(ids, key=lambda i: not i.startswith("GHSA-")):
        record = fetch(vuln_id)
        if record is None:
            continue
        specific = record.get("database_specific")
        if isinstance(specific, dict) and isinstance(specific.get("severity"), str):
            return specific["severity"].upper()
    return None


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as file:
        report = json.load(file)
    blocking = 0
    for dependency in report["dependencies"]:
        for vuln in dependency.get("vulns", []):
            ids = [vuln["id"], *vuln.get("aliases", [])]
            level = severity(ids)
            blocked = LEVELS.get(level or "", THRESHOLD) >= THRESHOLD
            blocking += blocked
            fixes = ", ".join(vuln.get("fix_versions", [])) or "-"
            print(
                f"{'FAIL' if blocked else 'warn'} {dependency['name']}=={dependency['version']} "
                f"{' / '.join(ids)} severity={level or 'UNKNOWN'} fix={fixes}"
            )
    print(f"high 及以上漏洞：{blocking}")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
