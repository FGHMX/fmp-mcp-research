from __future__ import annotations

import html
import os
import re
from datetime import date
from html.parser import HTMLParser
from typing import Any, cast

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class SECError(RuntimeError):
    """Raised when SEC EDGAR data cannot be fetched or converted."""


_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINE_RE = re.compile(r"\n{3,}")
_EARNINGS_RELEASE_TERMS = re.compile(
    r"earnings|results|press release|news release|financial results|quarterly results|"
    r"ex-99|exhibit\s*99|item\s*2\.02",
    re.I,
)
_HTML_EXTENSION_RE = re.compile(r"\.(htm|html|txt)$", re.I)


def _clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\xa0", " ")
    value = _SPACE_RE.sub(" ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = _BLANK_LINE_RE.sub("\n\n", value)
    return value.strip()


def _safe_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _accession_no_dashes(accession_number: str) -> str:
    return accession_number.replace("-", "")


class _SECReleaseHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._tag_stack: list[str] = []
        self._text_parts: list[str] = []
        self._blocks: list[dict[str, Any]] = []
        self._current_block_tag: str | None = None
        self._current_block_parts: list[str] = []
        self._tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self._tag_stack.append(tag)
        if tag in {"script", "style", "noscript", "head"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "div"}:
            self._start_block(tag)
        if tag == "br":
            self._append_text("\n")
        elif tag in {"p", "div", "tr", "table"}:
            self._append_text("\n")
        if tag == "table":
            self._current_table = []
        elif tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "head"} and self._skip_depth:
            self._skip_depth -= 1
        elif not self._skip_depth:
            if tag in {"td", "th"} and self._current_cell_parts is not None and self._current_row is not None:
                cell_text = _clean_text(" ".join(self._current_cell_parts))
                self._current_row.append(cell_text)
                self._current_cell_parts = None
            elif tag == "tr" and self._current_row is not None and self._current_table is not None:
                if any(cell for cell in self._current_row):
                    self._current_table.append(self._current_row)
                self._current_row = None
            elif tag == "table" and self._current_table is not None:
                if self._current_table:
                    self._tables.append(self._current_table)
                self._current_table = None
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "div"}:
                self._finish_block(tag)
            if tag in {"p", "div", "li", "tr", "table", "h1", "h2", "h3", "h4"}:
                self._append_text("\n")
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._append_text(data)
        if self._current_block_tag is not None:
            self._current_block_parts.append(data)
        if self._current_cell_parts is not None:
            self._current_cell_parts.append(data)

    def _append_text(self, value: str) -> None:
        if value:
            self._text_parts.append(value)

    def _start_block(self, tag: str) -> None:
        if self._current_block_tag is None:
            self._current_block_tag = tag
            self._current_block_parts = []

    def _finish_block(self, tag: str) -> None:
        if self._current_block_tag != tag:
            return
        text = _clean_text(" ".join(self._current_block_parts))
        if text:
            kind = "heading" if tag.startswith("h") else ("list_item" if tag == "li" else "paragraph")
            self._blocks.append({"type": kind, "tag": tag, "text": text})
        self._current_block_tag = None
        self._current_block_parts = []

    def release_json(self) -> dict[str, Any]:
        full_text = _clean_text("".join(self._text_parts))
        return {
            "text": full_text,
            "blocks": self._dedupe_blocks(self._blocks),
            "tables": self._tables,
        }

    @staticmethod
    def _dedupe_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        previous = ""
        for block in blocks:
            text = block.get("text", "")
            if text and text != previous:
                output.append(block)
                previous = text
        return output


def html_to_llm_json(raw_html: str, *, include_html: bool = False, include_tables: bool = True) -> dict[str, Any]:
    parser = _SECReleaseHTMLParser()
    parser.feed(raw_html)
    parsed = parser.release_json()
    text = parsed["text"]
    blocks = parsed["blocks"]
    raw_tables = parsed["tables"] if include_tables else []
    tables = []
    for index, rows in enumerate(raw_tables, start=1):
        column_count = max((len(row) for row in rows), default=0)
        tables.append(
            {
                "table_index": index,
                "row_count": len(rows),
                "column_count": column_count,
                "rows": rows,
            }
        )
    payload: dict[str, Any] = {
        "format": "sec_earnings_release_llm_json",
        "text": text,
        "text_character_count": len(text),
        "blocks": blocks,
        "block_count": len(blocks),
        "tables_included": include_tables,
        "tables": tables,
        "table_count": len(tables),
    }
    if include_html:
        payload["html"] = raw_html
        payload["html_character_count"] = len(raw_html)
    else:
        payload["html"] = None
        payload["html_character_count"] = len(raw_html)
    return payload


class SECClient:
    def __init__(self) -> None:
        self.data_base_url = os.getenv("SEC_DATA_BASE_URL", "https://data.sec.gov").rstrip("/")
        self.sec_base_url = os.getenv("SEC_BASE_URL", "https://www.sec.gov").rstrip("/")
        self.timeout = float(os.getenv("SEC_TIMEOUT_SECONDS", os.getenv("FMP_TIMEOUT_SECONDS", "30")))
        self.user_agent = os.getenv(
            "SEC_USER_AGENT",
            "fmp-mcp-research/0.3.3 contact@example.com",
        )
        self.max_supplemental_files = int(os.getenv("SEC_MAX_SUPPLEMENTAL_SUBMISSION_FILES", "3"))

    @property
    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError, SECError)),
        reraise=True,
    )
    async def _get(self, url: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, follow_redirects=True) as client:
            response = await client.get(url)
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise SECError(f"SEC temporary error: {response.status_code}")
        if response.status_code >= 400:
            raise SECError(f"SEC request failed: {response.status_code} {response.text[:300]}")
        return response

    async def _get_json(self, url: str) -> Any:
        response = await self._get(url)
        try:
            return response.json()
        except ValueError as exc:
            raise SECError("SEC returned non-JSON response") from exc

    async def _get_text(self, url: str) -> str:
        response = await self._get(url)
        return response.text

    async def cik_for_symbol(self, symbol: str) -> dict[str, Any]:
        symbol_upper = symbol.upper()
        data = await self._get_json(f"{self.sec_base_url}/files/company_tickers.json")
        if not isinstance(data, dict):
            raise SECError("SEC company ticker map returned an unexpected payload")
        for item in data.values():
            if not isinstance(item, dict):
                continue
            if str(item.get("ticker", "")).upper() == symbol_upper:
                cik_int = int(item["cik_str"])
                return {
                    "symbol": symbol_upper,
                    "cik": str(cik_int).zfill(10),
                    "cik_int": cik_int,
                    "company_title": item.get("title"),
                    "ticker_map_source_url": f"{self.sec_base_url}/files/company_tickers.json",
                }
        raise SECError(f"No SEC CIK mapping found for symbol {symbol_upper}")

    async def submissions(self, cik: str) -> dict[str, Any]:
        cik10 = str(cik).zfill(10)
        return cast(dict[str, Any], await self._get_json(f"{self.data_base_url}/submissions/CIK{cik10}.json"))

    async def _supplemental_submissions(self, file_name: str) -> dict[str, Any]:
        return cast(dict[str, Any], await self._get_json(f"{self.data_base_url}/submissions/{file_name}"))

    async def _filing_index(self, cik_int: int, accession_number: str) -> dict[str, Any]:
        accession = _accession_no_dashes(accession_number)
        return cast(dict[str, Any], await self._get_json(f"{self.sec_base_url}/Archives/edgar/data/{cik_int}/{accession}/index.json"))

    async def get_earnings_release_json(
        self,
        *,
        symbol: str,
        fiscal_year: int,
        fiscal_quarter: int,
        filing_date: str,
        include_html: bool = False,
        include_tables: bool = True,
    ) -> dict[str, Any]:
        mapping = await self.cik_for_symbol(symbol)
        cik = mapping["cik"]
        cik_int = int(mapping["cik_int"])
        submissions = await self.submissions(cik)
        filings = self._normalize_filings(submissions.get("filings", {}).get("recent", {}))

        supplemental_files = submissions.get("filings", {}).get("files", [])
        for supplemental in supplemental_files[: self.max_supplemental_files]:
            name = supplemental.get("name") if isinstance(supplemental, dict) else None
            if not name:
                continue
            filings.extend(self._normalize_filings((await self._supplemental_submissions(name)).get("filings", {})))

        candidate = self._select_best_filing(filings, filing_date=filing_date)
        if candidate is None:
            raise SECError(
                f"No likely 8-K/6-K earnings-release filing found for {symbol.upper()} near {filing_date}"
            )

        index_json = await self._filing_index(cik_int, candidate["accessionNumber"])
        document = self._select_best_document(index_json, candidate)
        document_url = self._document_url(cik_int, candidate["accessionNumber"], document["name"])
        raw_html = await self._get_text(document_url)
        release_json = html_to_llm_json(
            raw_html,
            include_html=include_html,
            include_tables=include_tables,
        )

        source_urls = {
            "company_tickers": mapping["ticker_map_source_url"],
            "submissions": f"{self.data_base_url}/submissions/CIK{cik}.json",
            "filing_index": self._filing_index_url(cik_int, candidate["accessionNumber"]),
            "selected_document": document_url,
        }

        warnings = []
        if candidate.get("form") not in {"8-K", "6-K"}:
            warnings.append("selected_filing_is_not_8k_or_6k")
        if not _EARNINGS_RELEASE_TERMS.search(str(candidate) + " " + str(document)):
            warnings.append("earnings_release_terms_not_detected_in_filing_metadata")
        if not release_json.get("text"):
            warnings.append("selected_document_text_is_empty_after_html_conversion")

        return {
            "symbol": symbol.upper(),
            "fiscalYear": fiscal_year,
            "fiscalQuarter": fiscal_quarter,
            "filingDate": filing_date,
            "source_name": "SEC EDGAR",
            "company": {
                "cik": cik,
                "cik_int": cik_int,
                "title": mapping.get("company_title"),
            },
            "selected_filing": {
                "accessionNumber": candidate.get("accessionNumber"),
                "form": candidate.get("form"),
                "filingDate": candidate.get("filingDate"),
                "reportDate": candidate.get("reportDate"),
                "acceptanceDateTime": candidate.get("acceptanceDateTime"),
                "primaryDocument": candidate.get("primaryDocument"),
                "primaryDocDescription": candidate.get("primaryDocDescription"),
                "items": candidate.get("items"),
            },
            "selected_document": document,
            "source_urls": source_urls,
            "release_json": release_json,
            "candidate_filings_reviewed": [self._candidate_summary(row, filing_date) for row in filings[:75]],
            "warnings": warnings,
            "audit_note": (
                "This payload is converted from the selected SEC EDGAR filing document into LLM-friendly JSON. "
                "Review release_json.text and release_json.tables before marking official_release_reviewed=yes."
            ),
        }

    @staticmethod
    def _normalize_filings(recent: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(recent, dict):
            return []
        keys = [key for key, value in recent.items() if isinstance(value, list)]
        length = max((len(recent[key]) for key in keys), default=0)
        rows: list[dict[str, Any]] = []
        for index in range(length):
            row = {}
            for key in keys:
                values = recent.get(key) or []
                row[key] = values[index] if index < len(values) else None
            if row:
                rows.append(row)
        return rows

    @staticmethod
    def _select_best_filing(filings: list[dict[str, Any]], *, filing_date: str) -> dict[str, Any] | None:
        anchor = _safe_date(filing_date)

        def score(row: dict[str, Any]) -> tuple[int, int]:
            form = str(row.get("form") or "").upper()
            filed = _safe_date(row.get("filingDate"))
            metadata = str(row)
            value = 0
            if form in {"8-K", "6-K"}:
                value += 1000
            elif form in {"10-Q", "10-K", "20-F", "40-F"}:
                value += 250
            if re.search(r"\b2\.02\b|results of operations|financial condition", str(row.get("items") or ""), re.I):
                value += 250
            if _EARNINGS_RELEASE_TERMS.search(metadata):
                value += 150
            distance = 9999
            if anchor and filed:
                distance = abs((filed - anchor).days)
                if distance <= 2:
                    value += 350
                elif distance <= 14:
                    value += 250
                elif distance <= 45:
                    value += 150
                elif distance <= 90:
                    value += 50
            return value, -distance

        relevant = [row for row in filings if str(row.get("form") or "").upper() in {"8-K", "6-K", "10-Q", "10-K", "20-F", "40-F"}]
        if not relevant:
            return None
        best = max(relevant, key=score)
        return best if score(best)[0] >= 250 else None

    @staticmethod
    def _select_best_document(index_json: dict[str, Any], filing: dict[str, Any]) -> dict[str, Any]:
        items = index_json.get("directory", {}).get("item", [])
        if isinstance(items, dict):
            items = [items]
        documents = [item for item in items if isinstance(item, dict) and _HTML_EXTENSION_RE.search(str(item.get("name") or ""))]
        primary = str(filing.get("primaryDocument") or "")

        def doc_score(doc: dict[str, Any]) -> tuple[int, str]:
            name = str(doc.get("name") or "")
            description = str(doc.get("description") or "")
            doc_type = str(doc.get("type") or "")
            metadata = f"{name} {description} {doc_type}"
            value = 0
            if name == primary:
                value += 100
            if re.search(r"EX-?99(\.1)?|EXHIBIT\s*99", doc_type, re.I):
                value += 500
            if re.search(r"EX-?99(\.1)?|EXHIBIT\s*99", name + " " + description, re.I):
                value += 400
            if _EARNINGS_RELEASE_TERMS.search(metadata):
                value += 300
            if name.lower().endswith((".htm", ".html")):
                value += 50
            return value, name

        if not documents:
            raise SECError("SEC filing index did not contain an HTML/TXT document")
        return max(documents, key=doc_score)

    @staticmethod
    def _candidate_summary(row: dict[str, Any], filing_date: str) -> dict[str, Any]:
        anchor = _safe_date(filing_date)
        filed = _safe_date(row.get("filingDate"))
        distance_days = abs((filed - anchor).days) if filed and anchor else None
        return {
            "accessionNumber": row.get("accessionNumber"),
            "form": row.get("form"),
            "filingDate": row.get("filingDate"),
            "reportDate": row.get("reportDate"),
            "items": row.get("items"),
            "primaryDocument": row.get("primaryDocument"),
            "primaryDocDescription": row.get("primaryDocDescription"),
            "distance_from_requested_filing_date_days": distance_days,
        }

    @staticmethod
    def _document_url(cik_int: int, accession_number: str, document_name: str) -> str:
        accession = _accession_no_dashes(accession_number)
        return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{document_name}"

    @staticmethod
    def _filing_index_url(cik_int: int, accession_number: str) -> str:
        accession = _accession_no_dashes(accession_number)
        return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/index.json"
