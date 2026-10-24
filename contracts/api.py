"""HTTP bridge for the Engine entry points, so non-Python callers (Ryan's dashboard) and
other processes can reach the engine.

Routes map one-to-one to `contracts.entrypoints.Engine`, using the `contracts.schemas` models
as JSON bodies. `HttpEngine` is the matching client: it implements `Engine`, so Eric's chat
agents can call the engine in-process or over HTTP without changing code.

Run it on localhost only. Team-only routes (run_round, freeze) trust the `X-Requested-By`
header, which is fine for a local demo but not for a public deployment.

    uv run uvicorn contracts.api:create_stub_app --factory --port 8000
"""

import os

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from contracts import CONTRACTS_VERSION
from contracts.entrypoints import Engine, NotAllowed
from contracts.schemas import Decision, Mandate, PageScan, RoundMetrics, ThreatIntelItem, Transaction

REQUESTED_BY_HEADER = "X-Requested-By"
DASHBOARD_SENDER = "dashboard"  # the dashboard's identity for team-only routes


class CheckPaymentRequest(BaseModel):
    tx: Transaction
    mandate: Mandate | None = None


class FreezeRequest(BaseModel):
    on: bool


class FreezeState(BaseModel):
    frozen: bool


def create_app(engine: Engine) -> FastAPI:
    app = FastAPI(title="Fraud defense engine", version=CONTRACTS_VERSION)

    def team_only(call):
        try:
            return call()
        except NotAllowed as exc:
            raise HTTPException(status_code=403, detail=f"not allowed: {exc}") from exc

    @app.post("/check_payment", response_model=Decision)
    def check_payment(body: CheckPaymentRequest) -> Decision:
        return engine.check_payment(body.tx, body.mandate)

    @app.get("/explain/{tx_id}", response_model=Decision)
    def explain(tx_id: str) -> Decision:
        return engine.explain(tx_id)

    @app.get("/scan_page", response_model=PageScan)
    def scan_page(url: str) -> PageScan:
        return engine.scan_page(url)

    @app.get("/metrics", response_model=RoundMetrics)
    def get_metrics(run_id: str | None = None, round: int | None = None) -> RoundMetrics:
        return engine.get_metrics(run_id=run_id, round=round)

    @app.post("/run_round", response_model=RoundMetrics)
    def run_round(x_requested_by: str = Header(default="")) -> RoundMetrics:
        return team_only(lambda: engine.run_round(requested_by=x_requested_by))

    @app.post("/freeze", response_model=FreezeState)
    def set_freeze(body: FreezeRequest, x_requested_by: str = Header(default="")) -> FreezeState:
        return FreezeState(frozen=team_only(lambda: engine.set_freeze(body.on, requested_by=x_requested_by)))

    @app.get("/threat_intel", response_model=list[ThreatIntelItem])
    def latest_threat_intel(limit: int = 5) -> list[ThreatIntelItem]:
        return engine.latest_threat_intel(limit=limit)

    return app


def team_allowlist() -> set[str]:
    """TEAM_ALLOWLIST_ADDRESSES plus the dashboard's own identity."""
    raw = os.environ.get("TEAM_ALLOWLIST_ADDRESSES", "")
    return {a.strip() for a in raw.split(",") if a.strip()} | {DASHBOARD_SENDER}


def create_stub_app() -> FastAPI:
    """The stub engine behind the real routes, so the dashboard works before the engine exists."""
    from contracts.stubs import StubEngine

    return create_app(StubEngine(allowlist=team_allowlist()))


class HttpEngine:
    """An `Engine` that forwards every call to a running API (see `create_app`)."""

    def __init__(self, base_url: str = "http://localhost:8000", client: httpx.Client | None = None):
        self.client = client or httpx.Client(base_url=base_url, timeout=30)

    def _request(self, method: str, path: str, requested_by: str | None = None, **kwargs) -> httpx.Response:
        headers = {REQUESTED_BY_HEADER: requested_by} if requested_by is not None else None
        response = self.client.request(method, path, headers=headers, **kwargs)
        if response.status_code == 403:
            raise NotAllowed(requested_by)
        response.raise_for_status()
        return response

    def check_payment(self, tx: Transaction, mandate: Mandate | None = None) -> Decision:
        body = CheckPaymentRequest(tx=tx, mandate=mandate).model_dump(mode="json")
        return Decision.model_validate(self._request("POST", "/check_payment", json=body).json())

    def explain(self, tx_id: str) -> Decision:
        return Decision.model_validate(self._request("GET", f"/explain/{tx_id}").json())

    def scan_page(self, url: str) -> PageScan:
        return PageScan.model_validate(self._request("GET", "/scan_page", params={"url": url}).json())

    def get_metrics(self, run_id: str | None = None, round: int | None = None) -> RoundMetrics:
        params = {k: v for k, v in {"run_id": run_id, "round": round}.items() if v is not None}
        return RoundMetrics.model_validate(self._request("GET", "/metrics", params=params).json())

    def run_round(self, requested_by: str) -> RoundMetrics:
        return RoundMetrics.model_validate(self._request("POST", "/run_round", requested_by=requested_by).json())

    def set_freeze(self, on: bool, requested_by: str) -> bool:
        response = self._request("POST", "/freeze", requested_by=requested_by, json={"on": on})
        return FreezeState.model_validate(response.json()).frozen

    def latest_threat_intel(self, limit: int = 5) -> list[ThreatIntelItem]:
        items = self._request("GET", "/threat_intel", params={"limit": limit}).json()
        return [ThreatIntelItem.model_validate(i) for i in items]
