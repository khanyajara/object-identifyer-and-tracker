"""Read-only readiness checks. Never prints keys or database row contents."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from services.supabase_database_service import SupabaseDatabaseService


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    service = SupabaseDatabaseService()
    results = {}
    for name, path in (("accounts", "/rest/v1/roadwatch_accounts?select=username&limit=0"), ("records", "/rest/v1/roadwatch_records?select=record_id&limit=0"), ("buckets", "/storage/v1/bucket")):
        try:
            value = service.request("GET", path)
            results[name] = {"ok": True}
            if name == "buckets":
                results[name]["items"] = [{k: row.get(k) for k in ("name", "public", "file_size_limit", "allowed_mime_types")} for row in value]
        except (ValueError, RuntimeError) as exc:
            results[name] = {"ok": False, "error": str(exc)}
    print(json.dumps(results, indent=2))
    return 0 if all(row["ok"] for row in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
