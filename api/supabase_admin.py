"""Server-side calls to Supabase Auth and Storage with the project's secret key."""
import requests

from config import SUPABASE_SECRET_KEY, SUPABASE_URL

RESUME_BUCKET = "resumes"
TIMEOUT = 30


class SupabaseAdmin:
    def __init__(self, url: str = SUPABASE_URL, secret_key: str = SUPABASE_SECRET_KEY):
        self.url = url.rstrip("/")
        self.headers = {"apikey": secret_key, "Authorization": f"Bearer {secret_key}"}

    def upload_resume(self, path: str, data: bytes, content_type: str) -> None:
        r = requests.post(f"{self.url}/storage/v1/object/{RESUME_BUCKET}/{path}", data=data,
                          headers={**self.headers, "Content-Type": content_type, "x-upsert": "false"},
                          timeout=TIMEOUT)
        r.raise_for_status()

    def delete_files(self, paths: list[str]) -> None:
        if not paths:
            return
        r = requests.delete(f"{self.url}/storage/v1/object/{RESUME_BUCKET}", json={"prefixes": paths},
                            headers=self.headers, timeout=TIMEOUT)
        r.raise_for_status()

    def list_user_files(self, user_id: str) -> list[str]:
        r = requests.post(f"{self.url}/storage/v1/object/list/{RESUME_BUCKET}",
                          json={"prefix": user_id, "limit": 1000}, headers=self.headers, timeout=TIMEOUT)
        r.raise_for_status()
        return [f"{user_id}/{item['name']}" for item in r.json() if item.get("name")]

    def delete_user(self, user_id: str) -> None:
        r = requests.delete(f"{self.url}/auth/v1/admin/users/{user_id}", headers=self.headers,
                            timeout=TIMEOUT)
        if r.status_code != 404:
            r.raise_for_status()
