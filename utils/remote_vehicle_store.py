import json
import re
import os
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

from utils.pretty_print import display_info, display_error, display_warning

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    httpx = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def get_cache_root(cache_dir: str = "assets_cache") -> Path:
    p = PROJECT_ROOT / cache_dir
    p.mkdir(parents=True, exist_ok=True)
    return p

def sanitize_raw_json(text: str) -> str:
    # Fix CRLF -> LF, trailing comma before } or ]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text

def _manifest_paths(cache_dir: str = "assets_cache") -> Tuple[Path, Path, Path, Path]:
    root = get_cache_root(cache_dir)
    return (root / "Assets.json", root / "Assets.etag", root / "manifest_cache.json", root / "Server.json")

def load_manifest_cache(cache_dir: str = "assets_cache") -> Dict[str, str]:
    _, _, cache_path, _ = _manifest_paths(cache_dir)
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_manifest_cache(data: Dict[str, str], cache_dir: str = "assets_cache"):
    _, _, cache_path, _ = _manifest_paths(cache_dir)
    cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def get_server_list(cache_dir: str = "assets_cache") -> List[Dict[str, str]]:
    # Try cache first
    _, _, _, server_path = _manifest_paths(cache_dir)
    if server_path.exists():
        try:
            return json.loads(server_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def get_asset_status(code: str, cache_dir: str = "assets_cache") -> Dict[str, Any]:
    code = (code or "us").lower()
    root = get_cache_root(cache_dir)
    manifest_path, etag_path, cache_path, server_path = _manifest_paths(cache_dir)
    code_dir = root / code
    cached_files = list(code_dir.glob("*.mscv")) if code_dir.exists() else []
    count = len(cached_files)
    # Check Vehicle.mscv presence
    has_vehicle = (code_dir / "Vehicle.mscv").exists()
    manifest_cache = load_manifest_cache(cache_dir)
    # Count expected for code from manifest cache
    expected = len([k for k in manifest_cache.keys() if f"/Vehicle/{code}/" in k]) or None
    last_sync = None
    try:
        if cache_path.exists():
            last_sync = int(cache_path.stat().st_mtime)
    except Exception:
        last_sync = None
    etag = None
    try:
        if etag_path.exists():
            etag = etag_path.read_text(encoding="utf-8").strip()[:16]
    except Exception:
        etag = None
    return {
        "code": code,
        "cached_files": count,
        "has_vehicle": has_vehicle,
        "expected": expected,
        "cache_dir": str(root),
        "code_dir": str(code_dir),
        "manifest_etag": etag,
        "manifest_cached": manifest_path.exists(),
        "manifest_cache_entries": len(manifest_cache),
        "last_sync": last_sync,
        "server_json_cached": server_path.exists(),
    }

def check_remote_changes(manifest_url: str, cache_dir: str = "assets_cache") -> Dict[str, Any]:
    if not HAS_HTTPX:
        return {"error": "httpx not installed", "needs_update": None, "changed": None}
    manifest_path, etag_path, _, _ = _manifest_paths(cache_dir)
    headers = {}
    if etag_path.exists():
        try:
            etag = etag_path.read_text(encoding="utf-8").strip()
            if etag:
                headers["If-None-Match"] = etag
        except Exception:
            pass
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            r = client.get(manifest_url, headers=headers)
            if r.status_code == 304:
                return {"needs_update": False, "status": 304, "changed": 0, "message": "No changes on remote (304 Not Modified)"}
            r.raise_for_status()
            new_etag = r.headers.get("ETag", "")
            new_data = r.json()
            # Compare md5 diff for concerned code? Caller filters.
            # Here just return counts and etag
            return {"needs_update": True, "status": r.status_code, "count": len(new_data) if isinstance(new_data, list) else 0, "etag": new_etag[:24] if new_etag else None, "message": "Remote manifest changed"}
    except Exception as e:
        return {"error": str(e), "needs_update": None}

def fetch_manifest(manifest_url: str, server_manifest_url: str, cache_dir: str = "assets_cache") -> Tuple[Optional[list], Optional[list]]:
    if not HAS_HTTPX:
        display_error("httpx not installed — cannot fetch manifest. pip install httpx")
        return None, None
    root = get_cache_root(cache_dir)
    manifest_path, etag_path, _, server_path = _manifest_paths(cache_dir)
    headers = {}
    if etag_path.exists():
        try:
            etag = etag_path.read_text(encoding="utf-8").strip()
            if etag:
                headers["If-None-Match"] = etag
        except Exception:
            pass
    manifest_data = None
    server_data = None
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            # Manifest
            r = client.get(manifest_url, headers=headers)
            if r.status_code == 304:
                display_info("Manifest unchanged (304)")
                try:
                    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    manifest_data = None
            else:
                r.raise_for_status()
                manifest_data = r.json()
                # Save atomically
                tmp = root / "Assets.json.tmp"
                tmp.write_text(json.dumps(manifest_data), encoding="utf-8")
                tmp.replace(manifest_path)
                etag = r.headers.get("ETag", "")
                if etag:
                    etag_path.write_text(etag, encoding="utf-8")
                display_info(f"Fetched manifest: {len(manifest_data) if isinstance(manifest_data, list) else 0} entries")
            # Server.json
            try:
                rs = client.get(server_manifest_url, timeout=10)
                rs.raise_for_status()
                server_data = rs.json()
                tmp2 = root / "Server.json.tmp"
                tmp2.write_text(json.dumps(server_data, indent=2), encoding="utf-8")
                tmp2.replace(server_path)
            except Exception as e:
                display_warning(f"Server.json fetch failed: {e}")
                # keep cached
                try:
                    server_data = json.loads(server_path.read_text(encoding="utf-8"))
                except Exception:
                    server_data = None
    except Exception as e:
        display_error(f"Manifest fetch failed: {e}")
        # Try cache fallback
        try:
            if manifest_path.exists():
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest_data = None
    return manifest_data, server_data

def sync_code(code: str, cache_dir: str = "assets_cache", manifest_url: str = None, server_manifest_url: str = None, concurrency: int = 12) -> Dict[str, Any]:
    code = (code or "us").lower()
    from data.config_settings import get_manifest_url as _gmu, get_server_manifest_url as _gsmu
    if manifest_url is None:
        manifest_url = _gmu()
    if server_manifest_url is None:
        server_manifest_url = _gsmu()

    # Ensure httpx
    if not HAS_HTTPX:
        return {"error": "httpx not installed — pip install httpx", "code": code, "fetched": 0}

    root = get_cache_root(cache_dir)
    code_dir = root / code
    code_dir.mkdir(parents=True, exist_ok=True)

    manifest_data, _ = fetch_manifest(manifest_url, server_manifest_url, cache_dir)
    if manifest_data is None:
        # Try cached manifest
        manifest_path, _, _, _ = _manifest_paths(cache_dir)
        if manifest_path.exists():
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as e:
                return {"error": f"no manifest and cache unreadable: {e}", "code": code, "fetched": 0}
        else:
            return {"error": "no manifest available offline", "code": code, "fetched": 0}

    # Filter for code
    want = [e for e in manifest_data if isinstance(e, dict) and f"/Vehicle/{code}/" in e.get("url", "")]
    if not want:
        # Check if code has zero assets (es/jp/ro/ru)
        return {"code": code, "fetched": 0, "total": 0, "checked": len(manifest_data), "message": f"No Vehicle assets for code '{code}' on remote (ES/JP/RO/RU have zero) — using bundled us fallback", "cached_files": len(list(code_dir.glob("*.mscv")))}

    manifest_cache = load_manifest_cache(cache_dir)
    to_fetch = []
    for e in want:
        url = e.get("url")
        md5 = e.get("md5")
        fname = Path(url).name
        fpath = code_dir / fname
        if manifest_cache.get(url) != md5 or not fpath.exists():
            to_fetch.append(e)

    if not to_fetch:
        # Still ensure manifest_cache has entries for want (if first sync)
        changed = False
        for e in want:
            if e.get("url") not in manifest_cache:
                manifest_cache[e["url"]] = e.get("md5")
                changed = True
        if changed:
            save_manifest_cache(manifest_cache, cache_dir)
        return {"code": code, "fetched": 0, "total": len(want), "checked": len(want), "message": "Already up-to-date (md5 match, no download needed)", "cached_files": len(list(code_dir.glob("*.mscv")))}

    # Fetch with concurrency via ThreadPool (httpx.Client is thread-safe for separate clients per thread)
    fetched = 0
    errors = []
    # Use ThreadPool for concurrency (httpx sync client per thread)
    import concurrent.futures
    def _fetch_one(e):
        url = e["url"]
        fname = Path(url).name
        fpath = code_dir / fname
        try:
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                r = client.get(url, timeout=15)
                r.raise_for_status()
                raw = r.text
                clean = sanitize_raw_json(raw)
                json.loads(clean)
                tmp = code_dir / f"{fname}.tmp"
                tmp.write_text(clean, encoding="utf-8")
                tmp.replace(fpath)
                return (url, e.get("md5"), None)
        except Exception as ex:
            return (url, None, str(ex))

    try:
        # Limit concurrency
        max_workers = max(1, min(concurrency, len(to_fetch)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_one, e): e for e in to_fetch}
            for fut in concurrent.futures.as_completed(futures):
                url, md5, err = fut.result()
                if err:
                    errors.append(f"{Path(url).name}: {err}")
                    display_warning(f"Failed {Path(url).name}: {err}")
                else:
                    manifest_cache[url] = md5
                    fetched += 1
                    if fetched % 20 == 0:
                        display_info(f"Sync {code}: {fetched}/{len(to_fetch)} fetched")
    except Exception as e:
        return {"error": str(e), "code": code, "fetched": fetched, "total": len(want), "errors": errors}

    save_manifest_cache(manifest_cache, cache_dir)
    # Ensure etag saved already via fetch_manifest
    return {"code": code, "fetched": fetched, "total": len(want), "checked": len(want), "cached_files": len(list(code_dir.glob("*.mscv"))), "errors": errors, "message": f"Synced {fetched}/{len(want)} files (smart diff, md5 check)"}
