from __future__ import annotations

import os
from typing import Any

import httpx


class FMPError(RuntimeError):
    """Raised when the FMP API returns an unusable response."""


class FMPClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("FMP_API_KEY")
        if not self.api_key:
            raise FMPError("FMP_API_KEY is required")
        self.base_url = os.getenv("FMP_BASE_URL", "https://financialmodelingprep.com/stable").rstrip("/")
        self.timeout = float(os.getenv("FMP_TIMEOUT_SECONDS", "30"))
        self.max_limit = int(os.getenv("FMP_MAX_LIMIT", "100"))

    async def _get(self, path: str, **params: Any) -> Any:
        clean_params = {k: v for k, v in params.items() if v is not None}
        clean_params["apikey"] = self.api_key
        url = f"{self.base_url}/{path.lstrip('/')}"
        return await self._request(url, clean_params)

    async def _request(self, url: str, params: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, params=params)
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise FMPError(f"FMP temporary error: {response.status_code}")
        if response.status_code >= 400:
            raise FMPError(f"FMP request failed: {response.status_code} {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise FMPError("FMP returned non-JSON response") from exc

    async def profile(self, symbol: str) -> Any:
        return await self._get("profile", symbol=symbol.upper())

    async def transcript_dates(self, symbol: str) -> Any:
        return await self._get("earning-call-transcript-dates", symbol=symbol.upper())

    async def transcript(self, symbol: str, year: int, quarter: int) -> Any:
        return await self._get(
            "earning-call-transcript", symbol=symbol.upper(), year=year, quarter=quarter
        )


    async def income_statement(self, symbol: str, period: str, limit: int) -> Any:
        return await self._get("income-statement", symbol=symbol.upper(), period=period, limit=min(limit, self.max_limit))

    async def balance_sheet(self, symbol: str, period: str, limit: int) -> Any:
        return await self._get("balance-sheet-statement", symbol=symbol.upper(), period=period, limit=min(limit, self.max_limit))

    async def cash_flow(self, symbol: str, period: str, limit: int) -> Any:
        return await self._get("cash-flow-statement", symbol=symbol.upper(), period=period, limit=min(limit, self.max_limit))

    async def key_metrics(self, symbol: str, period: str, limit: int) -> Any:
        return await self._get("key-metrics", symbol=symbol.upper(), period=period, limit=min(limit, self.max_limit))

    async def ratios(self, symbol: str, period: str, limit: int) -> Any:
        return await self._get("ratios", symbol=symbol.upper(), period=period, limit=min(limit, self.max_limit))

    async def financial_growth(self, symbol: str, period: str, limit: int) -> Any:
        return await self._get("financial-growth", symbol=symbol.upper(), period=period, limit=min(limit, self.max_limit))

    async def sec_filings(self, symbol: str, from_date: str, to_date: str | None = None, limit: int = 100) -> Any:
        return await self._get("sec-filings-search/symbol", symbol=symbol.upper(), **{"from": from_date, "to": to_date}, limit=min(limit, self.max_limit))
