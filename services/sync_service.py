import requests


class SyncService:
    def __init__(self, base_url="", api_key=""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def sync(self, record):
        if not self.base_url:
            raise RuntimeError("Configure a sync URL in Settings first.")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = requests.post(
            f"{self.base_url}/videos/sync",
            json=record,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json() if response.content else {"status": "synced"}
