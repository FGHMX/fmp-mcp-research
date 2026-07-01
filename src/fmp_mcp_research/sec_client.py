from __future__ import annotations

import html
import os
import re
from datetime import date
from html.parser import HTMLParser
from typing import Any, cast

import httpx


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

_EXHIBIT_99_TERMS = re.compile(
    r"ex[-_ ]?99|exhibit\s*99",
    re.I,
)
_EARNINGS_DOCUMENT_POSITIVE_TERMS = re.compile(
    r"financial results|quarterly results|annual results|full year|total revenue|net income|"
    r"diluted share|diluted eps|revenue comparison|cost of materials|gross margin|"
    r"consolidated balance sheets|consolidated statements|statements of income|"
    r"statements of operations|statements of cash flows|cash flows|guidance|conference call",
    re.I,
)
_NON_EARNINGS_EXHIBIT_TERMS = re.compile(
    r"credit agreement|loan agreement|revolving credit|cusip|lenders|administrative agent|"
    r"syndication agent|joint lead arranger|bookrunner|article\s+i\.?\s+definitions|"
    r"borrower|guarantor|indenture|lease agreement|employment agreement|separation agreement|"
    r"purchase agreement|merger agreement|bylaws|certificate of amendment|execution version",
    re.I,
)
_CAPITAL_RETURN_ONLY_TERMS = re.compile(
    r"share repurchase authorization|repurchase program|quarterly cash dividend|"
    r"10b5-1|rule 10b-18",
    re.I,
)

_EARNINGS_DOCUMENT_POSITIVE_TERMS = re.compile(
    r"financial results|quarterly results|full year|total revenue|net income|diluted share|"
    r"revenue comparison|cost of materials|consolidated balance sheets|consolidated statements|"
    r"statements of income|statements of cash flows|cash flows|guidance",
    re.I,
)
_CAPITAL_RETURN_ONLY_TERMS = re.compile(
    r"share repurchase authorization|repurchase program|quarterly cash dividend|"
    r"10b5-1|rule 10b-18",
    re.I,
)


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
        self._sections: list[dict[str, Any]] = []
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
                    self._sections.append({"type": "table", "rows": self._current_table})
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
            block = {"type": kind, "tag": tag, "text": text}
            self._blocks.append(block)
            self._sections.append(block)
        self._current_block_tag = None
        self._current_block_parts = []

    def release_json(self) -> dict[str, Any]:
        full_text = _clean_text("".join(self._text_parts))
        return {
            "text": full_text,
            "blocks": self._dedupe_blocks(self._blocks),
            "tables": self._tables,
            "sections": self._dedupe_sections(self._sections),
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

    @staticmethod
    def _dedupe_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        previous_text = ""
        for section in sections:
            if section.get("type") == "table":
                output.append(section)
                previous_text = ""
                continue
            text = str(section.get("text") or "")
            if text and text != previous_text:
                output.append(section)
                previous_text = text
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
    return payload


def _yaml_quote(value: Any) -> str:
    clean = _clean_text(str(value or "")).replace('\"', '\\\"')
    return f'"{clean}"'


def _markdown_escape_cell(value: Any) -> str:
    return _clean_text(str(value or "")).replace("|", "\\|")


def _cell_to_text(value: Any) -> str:
    text = _clean_text(str(value or ""))
    if text.lower() in {"nan", "none", "null", "nat"}:
        return ""
    return text


def _remove_consecutive_duplicates(cells: list[str]) -> list[str]:
    output: list[str] = []
    for cell in cells:
        clean = _cell_to_text(cell)
        if not clean:
            continue
        if output and output[-1].lower() == clean.lower():
            continue
        output.append(clean)
    return output


def _merge_currency_cells(cells: list[str]) -> list[str]:
    output: list[str] = []
    i = 0
    while i < len(cells):
        cell = _cell_to_text(cells[i])
        if cell in {"$", "€", "£", "¥"} and i + 1 < len(cells):
            nxt = _cell_to_text(cells[i + 1])
            if nxt:
                output.append(f"{cell}{nxt}")
                i += 2
                continue
        output.append(cell)
        i += 1
    return [item for item in output if item]


def _compact_row_values(row: list[str]) -> list[str]:
    cells = [_cell_to_text(cell) for cell in row]
    cells = [cell for cell in cells if cell]
    cells = _remove_consecutive_duplicates(cells)
    cells = _merge_currency_cells(cells)
    return _remove_consecutive_duplicates(cells)


def _is_year(text: str) -> bool:
    return bool(re.fullmatch(r"20\d{2}|19\d{2}", _cell_to_text(text)))


def _is_period_label(text: str) -> bool:
    lower = _cell_to_text(text).lower()
    return any(
        phrase in lower
        for phrase in (
            "three months ended",
            "six months ended",
            "nine months ended",
            "twelve months ended",
            "year ended",
            "years ended",
            "quarter ended",
            "months ended",
            "march 31",
            "june 30",
            "september 30",
            "december 31",
            "january 31",
            "april 30",
            "july 31",
            "october 31",
        )
    )


def _is_section_label(cells: list[str]) -> bool:
    if not cells:
        return False
    if len(cells) == 1 and cells[0].endswith(":"):
        return True
    first = cells[0].lower().strip()
    return first in {
        "assets",
        "current assets:",
        "current assets",
        "liabilities and stockholders' equity",
        "liabilities and shareholders' equity",
        "current liabilities:",
        "current liabilities",
        "stockholders' equity:",
        "shareholders' equity:",
        "cash flows from operating activities:",
        "cash flows from investing activities:",
        "cash flows from financing activities:",
        "net income per share:",
        "weighted average shares outstanding:",
    }


def _detect_period_headers(rows: list[list[str]]) -> list[str] | None:
    early = rows[:8]
    for row in early:
        years = [cell for cell in row if _is_year(cell)]
        if len(years) >= 2:
            return years
    date_re = re.compile(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}",
        re.I,
    )
    for row in early:
        dates = [cell for cell in row if date_re.search(cell)]
        if len(dates) >= 2:
            return dates
    return None


def _row_to_semantic(row: list[str], headers: list[str] | None) -> list[str] | None:
    if not row:
        return None
    if all(_is_year(cell) for cell in row):
        return None
    if len(row) == 1 and _is_period_label(row[0]):
        return None
    if not headers:
        return row
    if _is_section_label(row):
        return [row[0]] + [""] * len(headers)
    label = row[0]
    values = row[1:]
    if _is_period_label(label) and not values:
        return None
    if len(row) <= len(headers) and all(_is_year(cell) or _is_period_label(cell) for cell in row):
        return None
    if len(values) < len(headers):
        values = values + [""] * (len(headers) - len(values))
    elif len(values) > len(headers):
        values = values[: len(headers) - 1] + [" ".join(values[len(headers) - 1 :])]
    return [label] + values


def _markdown_table_from_rows(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    max_cols = max(len(headers), max((len(row) for row in rows), default=0))
    headers = headers + [f"Value {i}" for i in range(len(headers), max_cols)]
    normalized_rows = [row + [""] * (max_cols - len(row)) for row in rows]
    lines = ["|" + "|".join(_markdown_escape_cell(cell) for cell in headers[:max_cols]) + "|"]
    lines.append("|" + "|".join("-" if i == 0 else "-:" for i in range(max_cols)) + "|")
    for row in normalized_rows:
        if any(_cell_to_text(cell) for cell in row):
            lines.append("|" + "|".join(_markdown_escape_cell(cell) for cell in row[:max_cols]) + "|")
    return "\n".join(lines).strip()


def _compact_table_markdown(rows: list[list[str]]) -> str:
    compact_rows = [_compact_row_values(row) for row in rows]
    compact_rows = [row for row in compact_rows if row]
    if not compact_rows:
        return ""
    headers = _detect_period_headers(compact_rows)
    if headers:
        semantic_rows = []
        for row in compact_rows:
            converted = _row_to_semantic(row, headers)
            if converted:
                semantic_rows.append(converted)
        if semantic_rows:
            return _markdown_table_from_rows(["Item"] + headers, semantic_rows)
    max_cols = max(len(row) for row in compact_rows)
    generic_headers = ["Item"] + [f"Value {i}" for i in range(1, max_cols)]
    return _markdown_table_from_rows(generic_headers, compact_rows)


FINANCIAL_STATEMENT_KEYWORDS = (
    "statement of operations", "statements of operations", "statement of income",
    "statements of income", "statement of earnings", "balance sheet", "balance sheets",
    "statement of cash flows", "statements of cash flows", "consolidated statement",
    "consolidated statements", "condensed consolidated",
)
NON_GAAP_KEYWORDS = (
    "non-gaap", "reconciliation", "adjusted ebitda", "adjusted net income",
    "adjusted diluted earnings per share", "adjusted eps", "free cash flow", "affo", "ffo", "ebitda",
)
CONTACT_KEYWORDS = ("investors", "media", "investor relations", "corporate communications")


def _table_heading(title: str, table_number: int, rows: list[list[str]]) -> str:
    blob = (title + " " + " ".join(" ".join(row) for row in rows[:8])).lower()
    if any(keyword in blob for keyword in NON_GAAP_KEYWORDS):
        label = "Non-GAAP Table"
    elif any(keyword in blob for keyword in FINANCIAL_STATEMENT_KEYWORDS):
        label = "Financial Table"
    elif any(keyword in blob for keyword in CONTACT_KEYWORDS):
        label = "Contact Table"
    else:
        label = "Table"
    clean_title = _clean_text(title)[:180]
    if clean_title and not re.fullmatch(r"table\s+\d+", clean_title, flags=re.I):
        return f"## {label} {table_number}: {clean_title}"
    return f"## {label} {table_number}"


def _headline_from_sections(sections: list[dict[str, Any]]) -> str:
    for section in sections[:20]:
        if section.get("type") == "table":
            continue
        text = _clean_text(str(section.get("text", "")))
        lower = text.lower()
        if text and len(text) <= 220 and (
            ("reports" in lower and ("results" in lower or "earnings" in lower))
            or ("announces" in lower and ("results" in lower or "earnings" in lower))
        ):
            return text
    for section in sections[:20]:
        if section.get("type") != "table":
            text = _clean_text(str(section.get("text", "")))
            if text and len(text) <= 220:
                return text
    return "SEC Earnings Release"


def _period_label(fiscal_year: int, fiscal_quarter: int) -> str:
    return f"Q{int(fiscal_quarter)} FY{int(fiscal_year)}"


def html_to_llm_markdown(raw_html: str, *, metadata: dict[str, Any]) -> str:
    parser = _SECReleaseHTMLParser()
    parser.feed(raw_html)
    parsed = parser.release_json()
    sections = parsed.get("sections") or []
    tables = parsed.get("tables") or []
    title = _headline_from_sections(sections)

    frontmatter = {
        "format": "sec_earnings_release_llm_markdown",
        "symbol": metadata.get("symbol"),
        "fiscal_year": metadata.get("fiscalYear"),
        "fiscal_quarter": metadata.get("fiscalQuarter"),
        "requested_period": metadata.get("requestedPeriod"),
        "filing_date": metadata.get("filingDate"),
        "source_name": "SEC EDGAR",
        "company_title": metadata.get("company_title"),
        "cik": metadata.get("cik"),
        "accession_number": metadata.get("accessionNumber"),
        "selected_document": metadata.get("selected_document_name"),
        "selected_document_url": metadata.get("selected_document_url"),
        "table_count": len(tables),
    }

    lines: list[str] = ["---"]
    for key, value in frontmatter.items():
        if value not in [None, "", [], {}]:
            lines.append(f"{key}: {_yaml_quote(value)}")
    lines.extend(["---", "", f"# {title}", ""])

    previous_text = ""
    recent_text_title = ""
    table_number = 0
    for section in sections:
        if section.get("type") == "table":
            rows = section.get("rows") or []
            table_md = _compact_table_markdown(rows)
            if table_md:
                table_number += 1
                lines.extend([_table_heading(recent_text_title, table_number, rows), "", table_md, ""])
            continue
        block_text = _clean_text(str(section.get("text", "")))
        if not block_text or block_text == previous_text:
            continue
        previous_text = block_text
        if len(block_text) <= 220:
            recent_text_title = block_text
        if section.get("type") == "heading":
            if block_text != title:
                lines.extend([f"## {block_text}", ""])
            continue
        if section.get("type") == "list_item":
            lines.append(f"- {block_text}")
        else:
            lines.extend([block_text, ""])

    markdown = "\n".join(lines)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip() + "\n"
    return markdown




class SECClient:
    def __init__(self) -> None:
        self.data_base_url = os.getenv("SEC_DATA_BASE_URL", "https://data.sec.gov").rstrip("/")
        self.sec_base_url = os.getenv("SEC_BASE_URL", "https://www.sec.gov").rstrip("/")
        self.timeout = float(os.getenv("SEC_TIMEOUT_SECONDS", os.getenv("FMP_TIMEOUT_SECONDS", "30")))
        self.user_agent = os.getenv(
            "SEC_USER_AGENT",
            "fmp-mcp-research/0.3.4 contact@example.com",
        )
        self.max_supplemental_files = int(os.getenv("SEC_MAX_SUPPLEMENTAL_SUBMISSION_FILES", "3"))
        self.max_earnings_release_scan = int(os.getenv("SEC_EARNINGS_RELEASE_SCAN_LIMIT", "40"))

    @property
    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
        }

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

    async def get_earnings_release(
        self,
        *,
        symbol: str,
        fiscal_year: int,
        fiscal_quarter: int,
    ) -> str:
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

        ranked_filings = self._rank_candidate_filings(
            filings,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
        )[: self.max_earnings_release_scan]
        if not ranked_filings:
            raise SECError(f"No likely 8-K/6-K earnings-release filings found for {symbol.upper()}")

        best: tuple[int, int, dict[str, Any], dict[str, Any], str, str] | None = None
        failures: list[str] = []
        for candidate in ranked_filings:
            try:
                index_json = await self._filing_index(cik_int, candidate["accessionNumber"])
                document, document_url, raw_html = await self._select_best_document_with_content(
                    index_json=index_json,
                    filing=candidate,
                    cik_int=cik_int,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                )
                parsed = html_to_llm_json(raw_html, include_html=False, include_tables=True)
                parsed_text = str(parsed.get("text") or "")
                tables = parsed.get("tables")
                table_count = len(tables) if isinstance(tables, list) else 0
                content_score = self._document_content_rank(
                    text=parsed_text,
                    table_count=table_count,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                )
                filing_score = self._filing_metadata_rank(
                    candidate,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                )
                score = filing_score + content_score
                item = (score, len(parsed_text), candidate, document, document_url, raw_html)
                if best is None or item[:2] > best[:2]:
                    best = item
            except Exception as exc:  # pragma: no cover - network payloads vary by filing
                failures.append(str(exc)[:160])
                continue

        if best is None:
            detail = "; ".join(failures[:3])
            raise SECError(f"Could not read a likely earnings-release exhibit for {symbol.upper()}. {detail}")

        score, _, candidate, document, document_url, raw_html = best
        requested_period = _period_label(fiscal_year, fiscal_quarter)
        markdown = html_to_llm_markdown(
            raw_html,
            metadata={
                "symbol": symbol.upper(),
                "fiscalYear": fiscal_year,
                "fiscalQuarter": fiscal_quarter,
                "requestedPeriod": requested_period,
                "filingDate": candidate.get("filingDate"),
                "company_title": mapping.get("company_title"),
                "cik": cik,
                "accessionNumber": candidate.get("accessionNumber"),
                "selected_document_name": document.get("name") or document.get("href"),
                "selected_document_url": document_url,
            },
        )

        warnings = []
        if candidate.get("form") not in {"8-K", "6-K"}:
            warnings.append("filing_form_is_not_8k_or_6k")
        if not _EARNINGS_RELEASE_TERMS.search(str(candidate) + " " + str(document)):
            warnings.append("earnings_release_terms_not_detected_in_filing_metadata")
        if not self._period_text_rank(str(html_to_llm_json(raw_html).get("text") or ""), fiscal_year, fiscal_quarter):
            warnings.append("requested_fiscal_period_not_strongly_detected_in_release_text")
        if not markdown.strip():
            warnings.append("selected_document_markdown_is_empty_after_html_conversion")
        if score < 1500:
            warnings.append("low_confidence_filing_selection")

        if warnings:
            warning_lines = "\n".join(f"- {warning}" for warning in warnings)
            markdown = markdown.rstrip() + "\n\n## Conversion Warnings\n\n" + warning_lines + "\n"

        return markdown

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
    def _filing_metadata_rank(
        row: dict[str, Any],
        *,
        fiscal_year: int | None = None,
        fiscal_quarter: int | None = None,
        filing_date: str | None = None,
    ) -> int:
        form = str(row.get("form") or "").upper()
        filed = _safe_date(row.get("filingDate"))
        report_date = _safe_date(row.get("reportDate"))
        metadata = str(row)
        value = 0
        if form in {"8-K", "6-K"}:
            value += 1000
        elif form in {"10-Q", "10-K", "20-F", "40-F"}:
            value += 250
        if re.search(r"\b2\.02\b|results of operations|financial condition", str(row.get("items") or ""), re.I):
            value += 350
        if _EARNINGS_RELEASE_TERMS.search(metadata):
            value += 175
        if fiscal_year and str(int(fiscal_year)) in metadata:
            value += 40
        if fiscal_quarter and re.search(rf"\bq{int(fiscal_quarter)}\b|quarter", metadata, re.I):
            value += 30
        anchor = _safe_date(filing_date) if filing_date else None
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
        if fiscal_year and report_date:
            # Report dates close to common quarter-end months are a mild signal only;
            # many issuers use non-calendar fiscal years.
            if str(int(fiscal_year)) in str(row.get("reportDate") or ""):
                value += 50
        return value

    @classmethod
    def _rank_candidate_filings(
        cls,
        filings: list[dict[str, Any]],
        *,
        fiscal_year: int | None = None,
        fiscal_quarter: int | None = None,
        filing_date: str | None = None,
    ) -> list[dict[str, Any]]:
        relevant = [
            row
            for row in filings
            if str(row.get("form") or "").upper() in {"8-K", "6-K", "10-Q", "10-K", "20-F", "40-F"}
        ]
        return sorted(
            relevant,
            key=lambda row: (
                cls._filing_metadata_rank(
                    row,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    filing_date=filing_date,
                ),
                str(row.get("filingDate") or ""),
            ),
            reverse=True,
        )

    @classmethod
    def _select_best_filing(
        cls,
        filings: list[dict[str, Any]],
        *,
        filing_date: str | None = None,
        fiscal_year: int | None = None,
        fiscal_quarter: int | None = None,
    ) -> dict[str, Any] | None:
        ranked = cls._rank_candidate_filings(
            filings,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            filing_date=filing_date,
        )
        if not ranked:
            return None
        best = ranked[0]
        score = cls._filing_metadata_rank(
            best,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            filing_date=filing_date,
        )
        return best if score >= 250 else None

    async def _select_best_document_with_content(
        self,
        *,
        index_json: dict[str, Any],
        filing: dict[str, Any],
        cik_int: int,
        fiscal_year: int | None = None,
        fiscal_quarter: int | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        """Select the earnings-release exhibit, not unrelated contracts in the same 8-K."""
        documents = self._html_documents(index_json)
        if not documents:
            raise SECError("SEC filing index did not contain an HTML/TXT document")

        primary = str(filing.get("primaryDocument") or "")
        accession_number = str(filing.get("accessionNumber") or "")
        if not accession_number:
            raise SECError("SEC filing candidate did not include an accession number")

        ex99_documents = [
            document
            for document in documents
            if self._is_exhibit_99_document(document)
            and not self._is_obvious_non_earnings_document(document)
        ]

        # If the filing has Exhibit 99 documents, do not allow EX-10 credit agreements,
        # contracts, bylaws, indentures, or other legal exhibits to win just because
        # they are larger or contain financial-looking words.
        ranked_candidates = ex99_documents or [
            document
            for document in documents
            if not self._is_obvious_non_earnings_document(document)
        ] or documents

        metadata_ranked = sorted(
            ranked_candidates,
            key=lambda doc: self._document_metadata_rank(doc, primary),
            reverse=True,
        )[:12]

        ranked: list[tuple[int, int, str, dict[str, Any], str, str]] = []

        for document in metadata_ranked:
            name = str(document.get("name") or "")
            if not name:
                continue

            document_url = self._document_url(cik_int, accession_number, name)
            raw_html = await self._get_text(document_url)

            parsed = html_to_llm_json(
                raw_html,
                include_html=False,
                include_tables=True,
            )

            parsed_text = str(parsed.get("text") or "")
            tables = parsed.get("tables")
            table_count = len(tables) if isinstance(tables, list) else 0

            metadata_rank = self._document_metadata_rank(document, primary)[0]
            content_rank = self._document_content_rank(
                text=parsed_text,
                table_count=table_count,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
            )

            ranked.append(
                (
                    metadata_rank + content_rank,
                    len(parsed_text),
                    name,
                    document,
                    document_url,
                    raw_html,
                )
            )

        if not ranked:
            raise SECError("SEC filing index documents could not be ranked")

        ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        _, _, _, document, document_url, raw_html = ranked[0]

        return document, document_url, raw_html

    @staticmethod
    def _html_documents(index_json: dict[str, Any]) -> list[dict[str, Any]]:
        items = index_json.get("directory", {}).get("item", [])
        if isinstance(items, dict):
            items = [items]

        return [
            item
            for item in items
            if isinstance(item, dict)
            and _HTML_EXTENSION_RE.search(str(item.get("name") or ""))
        ]

    @staticmethod
    def _document_metadata_text(doc: dict[str, Any]) -> str:
        return " ".join(
            str(doc.get(key) or "")
            for key in ("name", "description", "type")
        )

    @staticmethod
    def _is_exhibit_99_document(doc: dict[str, Any]) -> bool:
        return bool(_EXHIBIT_99_TERMS.search(SECClient._document_metadata_text(doc)))

    @staticmethod
    def _is_obvious_non_earnings_document(doc: dict[str, Any]) -> bool:
        metadata = SECClient._document_metadata_text(doc)
        return bool(_NON_EARNINGS_EXHIBIT_TERMS.search(metadata))

    @staticmethod
    def _document_metadata_rank(doc: dict[str, Any], primary: str) -> tuple[int, str]:
        name = str(doc.get("name") or "")
        description = str(doc.get("description") or "")
        doc_type = str(doc.get("type") or "")
        metadata = f"{name} {description} {doc_type}"

        value = 0

        if SECClient._is_obvious_non_earnings_document(doc):
            value -= 5000

        if SECClient._is_exhibit_99_document(doc):
            value += 3000

        # In most SEC earnings 8-Ks, EX-99.1 is the earnings press release.
        # EX-99.2+ is often a supplemental release, slides, or capital-return notice.
        if re.search(
            r"(?:^|[_\-.])ex[-_]?99[_\-.]?1(?:\D|$)|exhibit\s*99\.1",
            metadata,
            re.I,
        ):
            value += 700
        elif re.search(
            r"(?:^|[_\-.])ex[-_]?99[_\-.]?[2-9](?:\D|$)|exhibit\s*99\.[2-9]",
            metadata,
            re.I,
        ):
            value -= 150

        if name == primary:
            value += 100

        if _EARNINGS_RELEASE_TERMS.search(metadata):
            value += 300

        if name.lower().endswith((".htm", ".html")):
            value += 50

        return value, name

    @staticmethod
    def _period_text_rank(text: str, fiscal_year: int | None, fiscal_quarter: int | None) -> int:
        if not fiscal_year or not fiscal_quarter:
            return 0
        year = int(fiscal_year)
        quarter = int(fiscal_quarter)
        q_word = {1: "first", 2: "second", 3: "third", 4: "fourth"}.get(quarter, "")
        q_short = f"q{quarter}"
        lower = text.lower()
        value = 0
        strong_patterns = [
            rf"{q_word}\s+quarter\s+(?:fiscal\s+)?{year}",
            rf"fiscal\s+{year}\s+{q_word}\s+quarter",
            rf"{q_short}\s+(?:fy|fiscal\s+)?{year}",
            rf"{q_short}\s+{year}",
        ]
        if any(re.search(pattern, lower, re.I) for pattern in strong_patterns):
            value += 2500
        if str(year) in lower:
            value += 200
        if q_word and f"{q_word} quarter" in lower:
            value += 500
        if q_short in lower:
            value += 250
        # Penalize obvious wrong quarters when the requested quarter is not mentioned.
        other_words = {1: "first", 2: "second", 3: "third", 4: "fourth"}
        for other_q, other_word in other_words.items():
            if other_q != quarter and f"{other_word} quarter" in lower and f"{q_word} quarter" not in lower:
                value -= 600
        return value

    @staticmethod
    def _document_content_rank(
        *,
        text: str,
        table_count: int,
        fiscal_year: int | None = None,
        fiscal_quarter: int | None = None,
    ) -> int:
        value = 0

        if _NON_EARNINGS_EXHIBIT_TERMS.search(text[:20000]):
            value -= 5000

        positive_matches = _EARNINGS_DOCUMENT_POSITIVE_TERMS.findall(text)
        value += min(len(positive_matches), 12) * 180
        value += SECClient._period_text_rank(text[:40000], fiscal_year, fiscal_quarter)

        if table_count >= 3:
            value += 500
        elif table_count:
            value += table_count * 50

        if len(text) >= 10000:
            value += 200
        elif len(text) >= 5000:
            value += 80

        if (
            _CAPITAL_RETURN_ONLY_TERMS.search(text[:20000])
            and len(positive_matches) < 3
            and table_count < 3
        ):
            value -= 1200

        return value

    @staticmethod
    def _select_best_document(index_json: dict[str, Any], filing: dict[str, Any]) -> dict[str, Any]:
        documents = SECClient._html_documents(index_json)
        if not documents:
            raise SECError("SEC filing index did not contain an HTML/TXT document")

        primary = str(filing.get("primaryDocument") or "")
        preferred = [
            doc
            for doc in documents
            if SECClient._is_exhibit_99_document(doc)
            and not SECClient._is_obvious_non_earnings_document(doc)
        ]

        candidates = preferred or [
            doc
            for doc in documents
            if not SECClient._is_obvious_non_earnings_document(doc)
        ] or documents

        return max(
            candidates,
            key=lambda doc: SECClient._document_metadata_rank(doc, primary),
        )

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
