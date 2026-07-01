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
_TEXT_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
)
_BINARY_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".pdf",
    ".zip",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
)
_BINARY_SIGNATURES = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",
    b"GIF89a",
    b"%PDF",
    b"PK\x03\x04",  # ZIP / Office containers
)
_DEFAULT_MAX_RELEASE_CHARS = 200_000

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


def _looks_binary(data: bytes) -> bool:
    if not data:
        return False
    if any(data.startswith(signature) for signature in _BINARY_SIGNATURES):
        return True

    sample = data[:4096]
    if b"\x00" in sample:
        return True

    control_chars = sum(1 for byte in sample if byte < 32 and byte not in (9, 10, 13))
    return control_chars / max(len(sample), 1) > 0.05


def _looks_like_binary_garbage(text: str) -> bool:
    if not text:
        return False

    sample = text[:10000]
    printable = sum(ch.isprintable() or ch in "\n\r\t" for ch in sample)
    printable_ratio = printable / max(len(sample), 1)
    repeated_noise = sample.count("BBB@") > 20 or sample.count("HHHH") > 20 or sample.count("****") > 100
    text_markers = ("<html", "<table", "revenue", "net income", "cash flow", "balance sheet")
    has_text_marker = any(marker in sample.lower() for marker in text_markers)

    return (printable_ratio < 0.85 or repeated_noise) and not has_text_marker


def _is_text_content_type(content_type: str) -> bool:
    lowered = content_type.lower().split(";", 1)[0].strip()
    return not lowered or lowered in _TEXT_CONTENT_TYPES or lowered.endswith("+xml")


def _is_binary_document_name(name: str) -> bool:
    return name.lower().endswith(_BINARY_EXTENSIONS)


def _truncate_release_markdown(markdown: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(markdown) <= max_chars:
        return markdown, False
    return markdown[:max_chars].rstrip() + "\n", True


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
    return payload


def _yaml_quote(value: Any) -> str:
    clean = _clean_text(str(value or "")).replace('\"', '\\\"')
    return f'"{clean}"'


def _markdown_escape_cell(value: Any) -> str:
    return _clean_text(str(value or "")).replace("|", "\\|")


def _markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    max_cols = max((len(row) for row in rows), default=0)
    if max_cols == 0:
        return ""
    normalized = [row + [""] * (max_cols - len(row)) for row in rows]
    header = normalized[0]
    body = normalized[1:]
    # SEC tables often do not have clean header rows. Keep the first row as the header
    # to preserve source order and avoid inventing labels.
    lines = ["|" + "|".join(_markdown_escape_cell(cell) for cell in header) + "|"]
    lines.append("|" + "|".join("---" for _ in range(max_cols)) + "|")
    for row in body:
        if any(_clean_text(cell) for cell in row):
            lines.append("|" + "|".join(_markdown_escape_cell(cell) for cell in row) + "|")
    return "\n".join(lines)


def html_to_llm_markdown(raw_html: str, *, metadata: dict[str, Any]) -> str:
    parser = _SECReleaseHTMLParser()
    parser.feed(raw_html)
    parsed = parser.release_json()
    blocks = parsed["blocks"]
    tables = parsed["tables"]

    title = "SEC Earnings Release"
    for block in blocks[:12]:
        block_text = _clean_text(str(block.get("text", "")))
        if block_text and len(block_text) <= 180:
            title = block_text
            break

    frontmatter = {
        "format": "sec_earnings_release_llm_markdown",
        "symbol": metadata.get("symbol"),
        "fiscal_year": metadata.get("fiscalYear"),
        "fiscal_quarter": metadata.get("fiscalQuarter"),
        "filing_date": metadata.get("filingDate"),
        "source_name": "SEC EDGAR",
        "company_title": metadata.get("company_title"),
        "cik": metadata.get("cik"),
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
    for block in blocks:
        block_text = _clean_text(str(block.get("text", "")))
        if not block_text or block_text == previous_text:
            continue
        previous_text = block_text
        if block.get("type") == "heading" and block_text != title:
            lines.extend([f"## {block_text}", ""])
        elif block.get("type") == "list_item":
            lines.append(f"- {block_text}")
        else:
            lines.extend([block_text, ""])

    for index, rows in enumerate(tables, start=1):
        table_md = _markdown_table(rows)
        if table_md:
            lines.extend([f"## Table {index}", "", table_md, ""])

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
        self.max_release_chars = int(os.getenv("SEC_MAX_RELEASE_TEXT_CHARS", str(_DEFAULT_MAX_RELEASE_CHARS)))

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
        if _is_binary_document_name(url):
            raise SECError(f"Skipping binary SEC file by extension: {url}")

        response = await self._get(url)
        content_type = response.headers.get("content-type", "")
        raw = response.content

        if not _is_text_content_type(content_type):
            raise SECError(f"Skipping non-text SEC file: {content_type} {url}")
        if _looks_binary(raw):
            raise SECError(f"Skipping binary SEC file by content: {url}")

        encoding = response.encoding or "utf-8"
        text = raw.decode(encoding, errors="replace")
        if _looks_like_binary_garbage(text):
            raise SECError(f"Skipping SEC file that decoded as binary garbage: {url}")
        return text

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
        filing_date: str,
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

        candidate = self._select_best_filing(filings, filing_date=filing_date)
        if candidate is None:
            raise SECError(
                f"No likely 8-K/6-K earnings-release filing found for {symbol.upper()} near {filing_date}"
            )

        index_json = await self._filing_index(cik_int, candidate["accessionNumber"])
        document, _document_url, raw_html = await self._select_best_document_with_content(
            index_json=index_json,
            filing=candidate,
            cik_int=cik_int,
        )
        markdown = html_to_llm_markdown(
            raw_html,
            metadata={
                "symbol": symbol.upper(),
                "fiscalYear": fiscal_year,
                "fiscalQuarter": fiscal_quarter,
                "filingDate": filing_date,
                "company_title": mapping.get("company_title"),
                "cik": cik,
                "selected_document_name": document.get("name") or document.get("href"),
                "selected_document_url": _document_url,
            },
        )

        warnings = []
        if candidate.get("form") not in {"8-K", "6-K"}:
            warnings.append("filing_form_is_not_8k_or_6k")
        if not _EARNINGS_RELEASE_TERMS.search(str(candidate) + " " + str(document)):
            warnings.append("earnings_release_terms_not_detected_in_filing_metadata")
        if not markdown.strip():
            warnings.append("selected_document_markdown_is_empty_after_html_conversion")

        markdown, was_truncated = _truncate_release_markdown(markdown, self.max_release_chars)
        if was_truncated:
            warnings.append("selected_document_markdown_truncated_server_side")

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
    def _select_best_filing(filings: list[dict[str, Any]], *, filing_date: str) -> dict[str, Any] | None:
        anchor = _safe_date(filing_date)

        def rank(row: dict[str, Any]) -> tuple[int, int]:
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
        best = max(relevant, key=rank)
        return best if rank(best)[0] >= 250 else None

    async def _select_best_document_with_content(
        self,
        *,
        index_json: dict[str, Any],
        filing: dict[str, Any],
        cik_int: int,
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
    def _document_content_rank(*, text: str, table_count: int) -> int:
        value = 0

        if _NON_EARNINGS_EXHIBIT_TERMS.search(text[:20000]):
            value -= 5000

        positive_matches = _EARNINGS_DOCUMENT_POSITIVE_TERMS.findall(text)
        value += min(len(positive_matches), 12) * 180

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
