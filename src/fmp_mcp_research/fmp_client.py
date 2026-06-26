from __future__ import annotations

import os
from typing import Any, Literal

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

Period = Literal["annual", "quarter"]


class FMPError(RuntimeError):
    pass


class FMPClient:
    """Small async FMP client with conservative retries and normalized params."""

    def __init__(self) -> None:
        self.api_key = os.getenv("FMP_API_KEY")
        if not self.api_key:
            raise FMPError("Missing FMP_API_KEY environment variable")
        self.base_url = os.getenv("FMP_BASE_URL", "https://financialmodelingprep.com/stable").rstrip("/")
        self.timeout = float(os.getenv("FMP_TIMEOUT_SECONDS", "30"))
        self.max_limit = int(os.getenv("FMP_MAX_LIMIT", "100"))

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError, FMPError)),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def get(self, path: str, **params: Any) -> Any:
        clean_params = {k: v for k, v in params.items() if v is not None}
        clean_params["apikey"] = self.api_key
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=clean_params)
        if resp.status_code == 429:
            raise FMPError("FMP rate limit hit")
        if resp.status_code >= 400:
            raise FMPError(f"FMP HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise FMPError(f"FMP returned non-JSON response for {path}") from exc

    async def profile(self, symbol: str) -> Any:
        return await self.get("profile", symbol=symbol.upper())

    async def transcript_dates(self, symbol: str) -> Any:
        return await self.get("earning-call-transcript-dates", symbol=symbol.upper())

    async def latest_transcripts(self, page: int = 0, limit: int | None = None) -> Any:
        return await self.get("earning-call-transcript-latest", page=page, limit=min(limit or self.max_limit, self.max_limit))

    async def transcript(self, symbol: str, year: int, quarter: int) -> Any:
        return await self.get("earning-call-transcript", symbol=symbol.upper(), year=year, quarter=quarter)

    async def earnings_calendar(self, symbol: str | None = None, from_date: str | None = None, to_date: str | None = None) -> Any:
        return await self.get("earnings-calendar", symbol=symbol.upper() if symbol else None, **{"from": from_date, "to": to_date})

    async def income_statement(self, symbol: str, period: Period = "quarter", limit: int = 8) -> Any:
        return await self.get("income-statement", symbol=symbol.upper(), period=period, limit=min(limit, self.max_limit))

    async def balance_sheet(self, symbol: str, period: Period = "quarter", limit: int = 8) -> Any:
        return await self.get("balance-sheet-statement", symbol=symbol.upper(), period=period, limit=min(limit, self.max_limit))

    async def cash_flow(self, symbol: str, period: Period = "quarter", limit: int = 8) -> Any:
        return await self.get("cash-flow-statement", symbol=symbol.upper(), period=period, limit=min(limit, self.max_limit))

    async def key_metrics(self, symbol: str, period: Period = "quarter", limit: int = 8) -> Any:
        return await self.get("key-metrics", symbol=symbol.upper(), period=period, limit=min(limit, self.max_limit))

    async def ratios(self, symbol: str, period: Period = "quarter", limit: int = 8) -> Any:
        return await self.get("ratios", symbol=symbol.upper(), period=period, limit=min(limit, self.max_limit))

    async def financial_growth(self, symbol: str, period: Period = "quarter", limit: int = 8) -> Any:
        return await self.get("financial-growth", symbol=symbol.upper(), period=period, limit=min(limit, self.max_limit))

    async def sec_filings(self, symbol: str, from_date: str | None = None, to_date: str | None = None, page: int = 0, limit: int = 100) -> Any:
        return await self.get("sec-filings-search/symbol", symbol=symbol.upper(), **{"from": from_date, "to": to_date}, page=page, limit=min(limit, self.max_limit))

    async def eight_k_filings(self, from_date: str, to_date: str, page: int = 0, limit: int = 100) -> Any:
        # FMP stable 8-K endpoint is not symbol-filtered in the public docs; filter client-side in the tool.
        return await self.get("sec-filings-8k", **{"from": from_date, "to": to_date}, page=page, limit=min(limit, self.max_limit))
