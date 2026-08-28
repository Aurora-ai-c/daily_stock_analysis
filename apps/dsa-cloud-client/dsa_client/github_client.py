# -*- coding: utf-8 -*-
"""GitHub REST API 客户端:认证、429/5xx 退避重试、仓库与 Actions 操作。"""

from __future__ import annotations

import base64
import ssl
import time

import requests
try:
    from nacl.public import PublicKey, SealedBox
    HAS_NACL = True
except ImportError:
    HAS_NACL = False

API_BASE = "https://api.github.com"
MAX_RETRIES = 4

# 信任系统证书存储:公司代理常做 TLS 拦截,浏览器/系统信任公司 CA,
# 而 requests+certifi 默认不信任 → SSL 失败。truststore 让请求走系统 CA 存储。
try:
    import truststore
    _SYSTEM_CTX = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except Exception:  # noqa: BLE001
    _SYSTEM_CTX = None


def _default_ca_bundle() -> str:
    """Frozen PyInstaller exe often loses the system CA bundle; fall back to
    certifi's bundled roots so api.github.com TLS works out-of-the-box."""
    try:
        import certifi
        return certifi.where()
    except Exception:
        return True  # requests default verification


class GitHubClient:
    def __init__(self, pat: str, session_factory=None, sleep=time.sleep,
                 proxy: str | None = None, ca_bundle: str | None = None):
        if session_factory is None:
            session_factory = requests.Session
        self._pat = pat
        self._session_factory = session_factory
        self.sleep = sleep
        self._proxy = proxy
        self._ca_bundle = ca_bundle

    def _new_session(self):
        s = self._session_factory()
        s.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {self._pat}",
        })
        if self._proxy:
            s.proxies.update({"http": self._proxy, "https": self._proxy})
        if self._ca_bundle:
            s.verify = self._ca_bundle
        elif _SYSTEM_CTX is not None:
            # 用系统证书存储校验,匹配浏览器行为(公司代理 TLS 拦截也能通过)
            class _SystemTrustAdapter(requests.adapters.HTTPAdapter):
                def init_poolmanager(self, *args, **kwargs):
                    kwargs["ssl_context"] = _SYSTEM_CTX
                    return super().init_poolmanager(*args, **kwargs)
            s.mount("https://", _SystemTrustAdapter())
        else:
            s.verify = _default_ca_bundle()
        return s


    def request(self, method: str, path: str, **kw):
        session = self._new_session()
        attempt = 0
        while True:
            resp = session.request(method, f"{API_BASE}{path}", timeout=30, **kw)
            if resp.status_code != 429 and resp.status_code < 500:
                resp.raise_for_status()
                if not resp.content:
                    return None
                return resp.json()
            attempt += 1
            if attempt > MAX_RETRIES:
                resp.raise_for_status()
            wait = 1.0 * (2 ** (attempt - 1))
            if resp.status_code == 429:
                wait = max(wait, float(resp.headers.get("Retry-After", 1)))
            self.sleep(wait)

    def get_user(self) -> dict:
        return self.request("GET", "/user")

    def get_repo_ok(self, owner: str, repo: str) -> bool:
        try:
            self.request("GET", f"/repos/{owner}/{repo}")
            return True
        except requests.HTTPError:
            return False

    def get_variable(self, owner: str, repo: str, name: str):
        try:
            return self.request("GET", f"/repos/{owner}/{repo}/actions/variables/{name}").get("value")
        except requests.HTTPError:
            return None

    def set_variable(self, owner: str, repo: str, name: str, value: str) -> None:
        self.request("PATCH", f"/repos/{owner}/{repo}/actions/variables/{name}",
                     json={"name": name, "value": value})

    # ---- Secrets (encrypted, libsodium sealed-box) ----
    def _fetch_secret_public_key(self, owner: str, repo: str) -> tuple[str, str]:
        """Fetch the repo's Actions secrets public key (raw base64) and key_id.

        GitHub returns {key_id: str, key: str} where ``key`` is the base64 of a
        raw 32-byte Ed25519 public key.
        """
        data = self.request("GET", f"/repos/{owner}/{repo}/actions/secrets/public-key")
        return data.get("key_id", ""), data.get("key", "")

    def list_secret_names(self, owner: str, repo: str) -> list[str]:
        """Return secret names that exist in the repo (read-only, no values)."""
        data = self.request("GET", f"/repos/{owner}/{repo}/actions/secrets")
        # GitHub wraps the list as {"total_count": N, "secrets": [...]}
        secrets = data.get("secrets", []) if isinstance(data, dict) else []
        return [s.get("name", "") for s in secrets]

    def set_secret(self, owner: str, repo: str, name: str, value: str) -> None:
        """Write a secret value (encrypted via NaCl sealed-box)."""
        if not HAS_NACL:
            raise RuntimeError("pynacl is required to set secrets; pip install pynacl")
        key_id, raw_b64 = self._fetch_secret_public_key(owner, repo)
        public_key = PublicKey(base64.b64decode(raw_b64))
        sealed = SealedBox(public_key).encrypt(value.encode("utf-8"))
        # GitHub expects: {"encrypted_value": "<base64>", "key_id": "<id>"}
        self.request("PUT", f"/repos/{owner}/{repo}/actions/secrets/{name}",
                     json={"encrypted_value": base64.b64encode(sealed).decode("ascii"),
                           "key_id": key_id})

    def get_runs(self, owner: str, repo: str, limit: int = 5) -> list[dict]:
        return self.request("GET", f"/repos/{owner}/{repo}/actions/runs?per_page={limit}").get("workflow_runs", [])

    def dispatch(self, owner: str, repo: str, ref: str = "main", inputs: dict | None = None) -> None:
        self.request("POST", f"/repos/{owner}/{repo}/actions/workflows/00-daily-analysis.yml/dispatches",
                     json={"ref": ref, "inputs": inputs or {}})

    def list_artifacts(self, owner: str, repo: str, per_page: int = 10) -> list[dict]:
        return self.request("GET", f"/repos/{owner}/{repo}/actions/artifacts?per_page={per_page}").get(
            "artifacts", [])

    def download_artifact(self, owner: str, repo: str, artifact_id: int) -> bytes:
        session = self._new_session()
        resp = session.get(f"{API_BASE}/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip", timeout=120)
        resp.raise_for_status()
        return resp.content


def is_running(runs: list[dict]) -> bool:
    return any(r.get("status") in {"queued", "in_progress", "waiting"} for r in runs)