# ============================================================
# FMP Public Press Release Scraper for LLM Markdown
# ============================================================
#
# Goal
# ----
# Use FMP Press Releases API to find the correct earnings release,
# then scrape the public URL returned by FMP, such as:
# - PRNewswire
# - GlobeNewswire
# - BusinessWire
# - company IR site
#
# This avoids downloading from SEC.gov.
#
# Outputs:
# - raw FMP JSON
# - raw scraped HTML or text
# - LLM-friendly Markdown
# - manifest JSON
# - debug files
# - ZIP file
#
# Colab install:
#   %pip install -q requests pandas beautifulsoup4 lxml tabulate
#
# Required:
#   import os
#   os.environ["FMP_API_KEY"] = "your_fmp_api_key"
#
# Example:
#   result = extract_selected_period_for_llm_markdown_public_release(
#       ticker="NUTX",
#       fiscal_year=2026,
#       quarter="Q1",
#       auto_download_zip=True,
#   )
#
# ============================================================

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import traceback
import warnings
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")


# ============================================================
# CONFIGURATION
# ============================================================

FMP_API_KEY = os.getenv("FMP_API_KEY", "NXm8LKr3hjBVZLtEezHOL7etMZdbw3g5").strip()

OUTPUT_DIR = Path(os.getenv("EARNINGS_OUTPUT_DIR", os.path.join(tempfile.gettempdir(), "earnings_outputs_fmp_public_release")))
RAW_DIR = OUTPUT_DIR / "raw"
LLM_DIR = OUTPUT_DIR / "llm_markdown"
MANIFEST_DIR = OUTPUT_DIR / "manifest"
DEBUG_DIR = OUTPUT_DIR / "_debug"

for folder in [OUTPUT_DIR, RAW_DIR, LLM_DIR, MANIFEST_DIR, DEBUG_DIR]:
    folder.mkdir(parents=True, exist_ok=True)


# ============================================================
# GENERAL HELPERS
# ============================================================

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def normalize_space(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def clean_text(text: Any) -> str:
    if text is None:
        return ""

    text = str(text)
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"([A-Za-z\)])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", r"\1 \2", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in [None, "", [], {}]:
            return value
    return None


def parse_date(value: Any):
    if not value:
        return None

    value = str(value)[:10]

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def normalize_quarter(quarter: Any) -> str:
    q = str(quarter).upper().strip()

    mapping = {
        "1": "Q1",
        "Q1": "Q1",
        "FIRST": "Q1",
        "2": "Q2",
        "Q2": "Q2",
        "SECOND": "Q2",
        "3": "Q3",
        "Q3": "Q3",
        "THIRD": "Q3",
        "4": "Q4",
        "Q4": "Q4",
        "FOURTH": "Q4",
    }

    if q not in mapping:
        raise ValueError("quarter must be Q1, Q2, Q3, or Q4")

    return mapping[q]


def quarter_to_word(quarter: Any) -> str:
    return {
        "Q1": "first",
        "Q2": "second",
        "Q3": "third",
        "Q4": "fourth",
    }[normalize_quarter(quarter)]


def expected_period_string(fiscal_year: int, quarter: Any) -> str:
    return f"{normalize_quarter(quarter)} FY{fiscal_year}"


def safe_filename_part(value: Any, max_len: int = 90) -> str:
    text = str(value or "")
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text)
    text = text.strip("_").lower()
    text = text[:max_len].strip("_")
    return text or "unknown"


def make_release_id(ticker: str, release_date: Any, title: Any) -> str:
    safe_ticker = safe_filename_part(ticker, 20)
    safe_date = str(release_date or "unknown").replace("-", "")
    safe_title = safe_filename_part(title, 90)
    return f"{safe_ticker}_{safe_date}_{safe_title}"


def yaml_quote(value: Any) -> str:
    text = normalize_space(value)
    text = text.replace('"', '\\"')
    return f'"{text}"'


def estimate_tokenish_size(text: str) -> dict[str, int]:
    text = text or ""

    return {
        "characters": len(text),
        "words": len(re.findall(r"\S+", text)),
        "rough_tokens_estimate": max(1, int(len(text) / 4)),
    }


def split_paragraphs(text: str) -> list[str]:
    paragraphs = []
    buffer = []

    for line in str(text or "").splitlines():
        line = normalize_space(line)

        if not line:
            if buffer:
                paragraphs.append(clean_text(" ".join(buffer)))
                buffer = []
            continue

        buffer.append(line)

    if buffer:
        paragraphs.append(clean_text(" ".join(buffer)))

    return [p for p in paragraphs if p]


def is_html_content(text: str) -> bool:
    lower = str(text or "").lower()
    return "<html" in lower or "<body" in lower or "<table" in lower or "</table" in lower or "<p" in lower


def is_sec_url(url: str) -> bool:
    host = urlparse(str(url or "")).netloc.lower()
    return "sec.gov" in host


def domain_from_url(url: str) -> str:
    return urlparse(str(url or "")).netloc.lower().replace("www.", "")


# ============================================================
# FMP API HELPERS
# ============================================================

def fmp_get_json(url: str, params: dict[str, Any]) -> Any:
    if not FMP_API_KEY:
        raise ValueError(
            "FMP_API_KEY is not set. Run:\n\n"
            "import os\n"
            "os.environ['FMP_API_KEY'] = 'your_fmp_api_key'\n"
            "FMP_API_KEY = os.getenv('FMP_API_KEY', '').strip()\n"
        )

    params = dict(params)
    params["apikey"] = FMP_API_KEY

    response = requests.get(url, params=params, timeout=45)

    if response.status_code != 200:
        raise RuntimeError(f"FMP error {response.status_code}: {response.text[:1000]}")

    try:
        data = response.json()
    except Exception as err:
        raise RuntimeError(f"FMP response was not JSON: {response.text[:1000]}") from err

    if isinstance(data, dict) and data.get("Error Message"):
        raise RuntimeError(f"FMP error: {data}")

    return data


def fmp_search_press_releases(
    ticker: str,
    limit: int = 250,
) -> list[dict[str, Any]]:
    """
    FMP endpoint:
    https://financialmodelingprep.com/stable/news/press-releases?symbols=AAPL
    """

    ticker = ticker.upper()
    url = "https://financialmodelingprep.com/stable/news/press-releases"

    params_variants = [
        {"symbols": ticker, "limit": limit},
        {"symbol": ticker, "limit": limit},
    ]

    errors = []

    for params in params_variants:
        try:
            data = fmp_get_json(url, params)

            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]

            if isinstance(data, dict):
                for key in ["data", "pressReleases", "results"]:
                    if isinstance(data.get(key), list):
                        return [item for item in data[key] if isinstance(item, dict)]

        except Exception as error:
            errors.append(str(error))

    raise RuntimeError("Could not fetch FMP press releases. Errors: " + " | ".join(errors[:3]))


# ============================================================
# PRESS RELEASE FIELD EXTRACTION
# ============================================================

def extract_release_date(item: dict[str, Any]):
    for key in [
        "publishedDate",
        "date",
        "acceptedDate",
        "filingDate",
        "createdAt",
        "updatedAt",
    ]:
        parsed = parse_date(item.get(key))

        if parsed:
            return parsed

    return None


def extract_release_title(item: dict[str, Any]) -> str:
    return clean_text(
        first_non_empty(
            item.get("title"),
            item.get("headline"),
            item.get("subject"),
            "Untitled press release",
        )
    )


def extract_release_url(item: dict[str, Any]) -> str | None:
    return first_non_empty(
        item.get("url"),
        item.get("link"),
        item.get("sourceURL"),
        item.get("sourceUrl"),
        item.get("finalUrl"),
    )


def extract_fmp_release_text(item: dict[str, Any]) -> str:
    body = first_non_empty(
        item.get("text"),
        item.get("content"),
        item.get("body"),
        item.get("summary"),
        item.get("description"),
    )

    return clean_text(body)


def extract_publisher(item: dict[str, Any]) -> str | None:
    return first_non_empty(
        item.get("publisher"),
        item.get("site"),
        item.get("source"),
    )


# ============================================================
# PERIOD MATCHING
# ============================================================

def extract_period_from_text(text: str) -> str | None:
    text = text or ""

    patterns = [
        r"fiscal\s+(?:year\s+)?(\d{4})\s+(first|second|third|fourth)[-\s]quarter",
        r"(first|second|third|fourth)[-\s]quarter\s+(?:(?:of|for)\s+)?(?:fiscal\s+(?:year\s+)?)?(\d{4})",
        r"(first|second|third|fourth)[-\s]quarter\s+(\d{4})\s+results",
        r"\bQ([1-4])\s+FY\s?(\d{4})\b",
        r"\bQ([1-4])\s+fiscal\s+(\d{4})\b",
        r"\bQ([1-4])\s+(\d{4})\b",
    ]

    quarter_map = {
        "first": "Q1",
        "second": "Q2",
        "third": "Q3",
        "fourth": "Q4",
    }

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if not match:
            continue

        g1 = match.group(1)
        g2 = match.group(2)

        if g1.isdigit() and len(g1) == 1:
            return f"Q{g1} FY{g2}"

        if g1.isdigit() and len(g1) == 4:
            fiscal_year = g1
            quarter_word = g2.lower()
        else:
            quarter_word = g1.lower()
            fiscal_year = g2

        quarter = quarter_map.get(quarter_word)

        if quarter:
            return f"{quarter} FY{fiscal_year}"

    return None


def likely_earnings_release_text(text: str) -> bool:
    lower = str(text or "").lower()

    signals = [
        "financial results",
        "earnings",
        "results for the quarter",
        "quarter ended",
        "three months ended",
        "reports first quarter",
        "reports second quarter",
        "reports third quarter",
        "reports fourth quarter",
        "announces first quarter",
        "announces second quarter",
        "announces third quarter",
        "announces fourth quarter",
    ]

    return any(signal in lower for signal in signals)


def release_relevance_score(
    item: dict[str, Any],
    ticker: str,
    fiscal_year: int,
    quarter: Any,
) -> int:
    ticker = ticker.upper()
    q = normalize_quarter(quarter)
    q_num = q.replace("Q", "")
    q_word = quarter_to_word(q)
    requested_period = expected_period_string(fiscal_year, quarter)

    title = extract_release_title(item)
    fmp_text = extract_fmp_release_text(item)
    url = extract_release_url(item) or ""

    combined = f"{title}\n{fmp_text}\n{url}".lower()

    score = 0

    symbol = str(first_non_empty(item.get("symbol"), item.get("ticker"), "")).upper()

    if symbol == ticker:
        score += 20

    if likely_earnings_release_text(combined):
        score += 25

    if str(fiscal_year) in combined:
        score += 15

    if f"q{q_num}" in combined:
        score += 15

    if f"{q_word} quarter" in combined:
        score += 30

    if f"fiscal {fiscal_year}" in combined:
        score += 20

    detected = extract_period_from_text(combined)

    if detected == requested_period:
        score += 60
    elif detected and detected != requested_period:
        score -= 100

    title_lower = title.lower()

    if "results" in title_lower and ("quarter" in title_lower or "financial" in title_lower):
        score += 20

    if "reports" in title_lower or "announces" in title_lower:
        score += 10

    if any(x in title_lower for x in ["class action", "lawsuit", "investor alert", "shareholder alert", "investigation"]):
        score -= 100
        
    if "dividend" in title_lower:
        score -= 50

    if is_sec_url(url):
        score -= 100

    release_date = extract_release_date(item)

    if release_date and release_date.year in {fiscal_year - 1, fiscal_year, fiscal_year + 1}:
        score += 5

    return score


def find_fmp_public_earnings_release(
    ticker: str,
    fiscal_year: int,
    quarter: Any,
    limit: int = 250,
    min_score: int = 55,
) -> dict[str, Any]:
    ticker = ticker.upper()
    requested_period = expected_period_string(fiscal_year, quarter)

    releases = fmp_search_press_releases(ticker=ticker, limit=limit)

    if not releases:
        raise ValueError(f"No FMP press releases found for {ticker}.")

    candidates = []

    for item in releases:
        url = extract_release_url(item)

        if not url:
            continue

        if is_sec_url(url):
            continue

        score = release_relevance_score(item, ticker, fiscal_year, quarter)

        candidates.append({
            "item": item,
            "score": score,
            "title": extract_release_title(item),
            "date": extract_release_date(item),
            "url": url,
            "publisher": extract_publisher(item),
        })

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["date"] or datetime.min.date(),
        ),
        reverse=True,
    )

    if not candidates:
        raise ValueError(
            f"FMP returned press releases for {ticker}, but none had a non-SEC public URL."
        )

    best = candidates[0]

    debug_candidates = [
        {
            "score": c["score"],
            "date": str(c["date"]),
            "title": c["title"],
            "publisher": c["publisher"],
            "url": c["url"],
        }
        for c in candidates[:20]
    ]

    debug_path = DEBUG_DIR / f"{ticker.lower()}_press_release_candidates.json"
    debug_path.write_text(
        json.dumps(debug_candidates, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    if best["score"] < min_score:
        raise ValueError(
            f"Could not confidently find {ticker} {requested_period}. "
            f"Top candidates saved to {debug_path}. "
            f"Best candidate: {json.dumps(debug_candidates[0], ensure_ascii=False, indent=2)}"
        )

    return best["item"]


# ============================================================
# PUBLIC PAGE SCRAPING
# ============================================================

def scrape_public_url(url: str, sleep_seconds: float = 0.2) -> str:
    if not url:
        raise ValueError("URL is empty.")

    if is_sec_url(url):
        raise ValueError(f"Refusing to scrape SEC URL in public_release mode: {url}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    time.sleep(sleep_seconds)

    response = requests.get(url, headers=headers, timeout=45)

    if response.status_code != 200:
        raise RuntimeError(f"Public URL scrape failed {response.status_code} for {url}: {response.text[:500]}")

    if len(response.text or "") < 300:
        raise RuntimeError(f"Public URL returned too little content for {url}: {response.text[:300]}")

    return response.text


def remove_unwanted_html(soup: BeautifulSoup) -> BeautifulSoup:
    soup = BeautifulSoup(str(soup), "lxml")

    for tag in soup(["script", "style", "noscript", "iframe", "svg", "form", "nav", "footer", "header"]):
        tag.decompose()

    for tag in soup.find_all(attrs={"aria-hidden": "true"}):
        tag.decompose()

    return soup


def select_main_press_release_container(html: str, url: str) -> BeautifulSoup:
    soup = BeautifulSoup(html or "", "lxml")
    soup = remove_unwanted_html(soup)

    domain = domain_from_url(url)

    selectors_by_domain = []

    if "prnewswire.com" in domain:
        selectors_by_domain = [
            "section.release-body",
            "div.release-body",
            "article",
            "main",
            "body",
        ]
    elif "globenewswire.com" in domain:
        selectors_by_domain = [
            "div.main-container",
            "article",
            "main",
            "body",
        ]
    elif "businesswire.com" in domain:
        selectors_by_domain = [
            "div.bw-release-story",
            "article",
            "main",
            "body",
        ]
    else:
        selectors_by_domain = [
            "article",
            "main",
            "div.release-body",
            "section.release-body",
            "body",
        ]

    selected = None

    for selector in selectors_by_domain:
        found = soup.select_one(selector)

        if not found:
            continue

        text_len = len(found.get_text(" ", strip=True))

        if text_len > 500:
            selected = found
            break

    if selected is None:
        selected = soup.body or soup

    return BeautifulSoup(str(selected), "lxml")


def html_to_clean_text_for_matching(html: str, url: str) -> str:
    container = select_main_press_release_container(html, url)
    return clean_text(container.get_text("\n"))


# ============================================================
# TABLE CLASSIFICATION AND MARKDOWN
# ============================================================

FINANCIAL_STATEMENT_KEYWORDS = [
    "statement of operations", "statements of operations",
    "statement of income", "statements of income",
    "statement of earnings", "statements of earnings",
    "statement of loss", "statements of loss",
    "balance sheet", "balance sheets",
    "statement of cash flows", "statements of cash flows",
    "consolidated statement", "consolidated statements",
    "condensed consolidated",
]

FINANCIAL_ROW_SIGNALS = [
    "total assets", "current assets", "cash and cash equivalents",
    "accounts receivable", "property and equipment", "inventory",
    "total liabilities", "stockholders' equity", "shareholders' equity",
    "total equity", "net sales", "revenues", "revenue", "operating revenues",
    "gross profit", "operating income", "operating loss", "net income", "net loss",
    "income before provision", "weighted average shares", "earnings per share",
    "net cash provided by operating activities",
    "net cash used in operating activities",
    "cash flows from operating activities",
    "cash flows from investing activities",
    "cash flows from financing activities",
    "capital expenditures",
]

NON_GAAP_KEYWORDS = [
    "non-gaap",
    "reconciliation",
    "adjusted ebitda",
    "adjusted net income",
    "adjusted diluted earnings per share",
    "adjusted earnings per share",
    "adjusted eps",
    "free cash flow",
    "funds from operations",
    "affo",
    "ffo",
    "ebitda",
]

CONTACT_KEYWORDS = [
    "investors",
    "media",
    "investor relations",
    "corporate communications",
]


MISSING_VALUES = {"", "nan", "none", "null", "nat"}
CURRENCY_SYMBOLS = {"$", "€", "£", "¥"}


def cell_to_text(value: Any) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    text = str(value)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"\s+", " ", text).strip()

    if text.lower() in MISSING_VALUES:
        return ""

    return text


def normalize_table_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            " | ".join([cell_to_text(x) for x in col if cell_to_text(x)])
            for col in df.columns
        ]
    else:
        df.columns = [cell_to_text(col) for col in df.columns]

    df.columns = [col if col else f"column_{i + 1}" for i, col in enumerate(df.columns)]
    df = df.dropna(how="all")
    df = df.dropna(axis=1, how="all")

    for col in df.columns:
        df[col] = df[col].map(cell_to_text)

    return df


def remove_consecutive_duplicates(cells: Sequence[str]) -> list[str]:
    out = []

    for cell in cells:
        cell = cell_to_text(cell)

        if not cell:
            continue

        if out and normalize_space(out[-1]).lower() == normalize_space(cell).lower():
            continue

        out.append(cell)

    return out


def merge_currency_cells(cells: Sequence[str]) -> list[str]:
    merged = []
    i = 0

    while i < len(cells):
        cell = cell_to_text(cells[i])

        if cell in CURRENCY_SYMBOLS and i + 1 < len(cells):
            nxt = cell_to_text(cells[i + 1])

            if nxt:
                merged.append(f"{cell}{nxt}")
                i += 2
                continue

        merged.append(cell)
        i += 1

    return [c for c in merged if c]


def compact_row_values(row: Sequence[Any]) -> list[str]:
    cells = [cell_to_text(x) for x in row]
    cells = [c for c in cells if c]
    cells = remove_consecutive_duplicates(cells)
    cells = merge_currency_cells(cells)
    cells = remove_consecutive_duplicates(cells)
    return cells


def markdown_escape_cell(text: Any) -> str:
    return cell_to_text(text).replace("|", "\\|")


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""

    max_cols = max(len(headers), max(len(row) for row in rows))
    headers = headers + [f"Value {i}" for i in range(len(headers), max_cols)]
    normalized_rows = [row + [""] * (max_cols - len(row)) for row in rows]

    lines = []
    lines.append("|" + "|".join(markdown_escape_cell(h) for h in headers[:max_cols]) + "|")
    lines.append("|" + "|".join(["-" if i == 0 else "-:" for i in range(max_cols)]) + "|")

    for row in normalized_rows:
        if not any(cell_to_text(c) for c in row):
            continue

        lines.append("|" + "|".join(markdown_escape_cell(c) for c in row[:max_cols]) + "|")

    return "\n".join(lines).strip()


def previous_context_text(table_tag, max_chars: int = 700) -> str:
    pieces = []

    for sibling in table_tag.find_all_previous(limit=14):
        if sibling.name in ["table", "script", "style"]:
            continue

        text = normalize_space(sibling.get_text(" "))

        if text:
            pieces.append(text)

        if sum(len(p) for p in pieces) >= max_chars:
            break

    return clean_text("\n".join(reversed(pieces)))[-max_chars:]


def infer_table_title(table_tag, table_index: int) -> str:
    context = previous_context_text(table_tag, max_chars=900)
    lines = [normalize_space(line) for line in context.splitlines() if normalize_space(line)]

    heading_keywords = FINANCIAL_STATEMENT_KEYWORDS + NON_GAAP_KEYWORDS + CONTACT_KEYWORDS

    for line in reversed(lines[-12:]):
        lower = line.lower()

        if any(keyword in lower for keyword in heading_keywords):
            return line[:180]

    if lines:
        return lines[-1][:180]

    return f"Table {table_index}"


def classify_table(title: str, df: pd.DataFrame) -> str:
    title_l = normalize_space(title).lower()
    table_l = df.to_string(index=False).lower()
    head_l = df.head(14).to_string(index=False).lower()
    combined = f"{title_l} {head_l}"

    if any(keyword in combined for keyword in NON_GAAP_KEYWORDS):
        return "non_gaap_reconciliation"

    strong_financial_title = any(keyword in title_l for keyword in FINANCIAL_STATEMENT_KEYWORDS)
    row_financial_hits = sum(1 for keyword in FINANCIAL_ROW_SIGNALS if keyword in table_l)

    if strong_financial_title or row_financial_hits >= 3:
        return "financial_statement"

    if any(keyword in combined for keyword in CONTACT_KEYWORDS) or EMAIL_RE.search(table_l):
        return "contact"

    if row_financial_hits >= 1 and not any(keyword in combined for keyword in ["adjusted", "non-gaap", "reconciliation"]):
        return "financial_statement"

    return "other"


def is_year(text: str) -> bool:
    return bool(re.fullmatch(r"20\d{2}|19\d{2}", normalize_space(text)))


def is_period_label(text: str) -> bool:
    lower = normalize_space(text).lower()
    return any(phrase in lower for phrase in [
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
    ])


def is_section_label(cells: Sequence[str]) -> bool:
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


def detect_year_headers(rows: list[list[str]]) -> list[str] | None:
    early = rows[:8]

    for row in early:
        years = [cell for cell in row if is_year(cell)]

        if len(years) >= 2:
            return years

    for row in early:
        date_like = [
            cell
            for cell in row
            if re.search(r"(March|June|September|December)\s+\d{1,2},\s+\d{4}", cell, flags=re.I)
        ]

        if len(date_like) >= 2:
            return date_like

    return None


def row_to_semantic(row: list[str], headers: list[str] | None) -> list[str] | None:
    if not row:
        return None

    if all(is_year(c) for c in row):
        return None

    if len(row) == 1 and is_period_label(row[0]):
        return None

    if not headers:
        return row

    if is_section_label(row):
        return [row[0]] + ["" for _ in headers]

    label = row[0]
    values = row[1:]

    if is_period_label(label) and len(values) == 0:
        return None

    if len(row) <= len(headers) and all(is_year(c) or is_period_label(c) for c in row):
        return None

    if len(values) < len(headers):
        values = values + [""] * (len(headers) - len(values))
    elif len(values) > len(headers):
        values = values[: len(headers) - 1] + [" ".join(values[len(headers) - 1:])]

    return [label] + values


def compact_table_markdown(df: pd.DataFrame, category: str = "other") -> str:
    if df.empty:
        return ""

    compact_rows = [compact_row_values(row) for row in df.itertuples(index=False, name=None)]
    compact_rows = [row for row in compact_rows if row]

    if not compact_rows:
        return ""

    headers = detect_year_headers(compact_rows)

    if headers and category in {"financial_statement", "non_gaap_reconciliation", "other"}:
        semantic_rows = []

        for row in compact_rows:
            converted = row_to_semantic(row, headers)

            if converted:
                semantic_rows.append(converted)

        if semantic_rows:
            return markdown_table(["Item"] + headers, semantic_rows)

    max_cols = max(len(row) for row in compact_rows)
    generic_headers = ["Item"] + [f"Value {i}" for i in range(1, max_cols)]

    return markdown_table(generic_headers, compact_rows)


def table_payload_from_tag(table_tag, table_index: int) -> dict[str, Any] | None:
    try:
        dfs = pd.read_html(str(table_tag), keep_default_na=False)
    except Exception:
        return None

    if not dfs:
        return None

    df = max(dfs, key=lambda x: x.shape[0] * max(x.shape[1], 1))
    df = normalize_table_df(df)

    if df.empty:
        return None

    title = infer_table_title(table_tag, table_index)
    category = classify_table(title, df)
    md = compact_table_markdown(df, category=category)

    if not md:
        return None

    return {
        "type": "table",
        "table_index": table_index,
        "title": title,
        "category": category,
        "shape_original": {
            "rows": int(df.shape[0]),
            "columns": int(df.shape[1]),
        },
        "markdown": md,
    }


# ============================================================
# TEXT TABLE FALLBACK
# ============================================================

def line_looks_table_like(line: str) -> bool:
    if not line or not line.strip():
        return False

    has_numbers = bool(re.search(r"[\$,\(\)%]|\b20\d{2}\b|\b\d+\.\d+\b|\b\d{1,3}(,\d{3})+\b", line))
    has_spacing = bool(re.search(r"\S\s{2,}\S", line))
    has_financial_word = bool(re.search(r"revenue|income|loss|assets|liabilities|cash|shares|ebitda|expenses|operations", line, flags=re.I))

    return has_numbers and (has_spacing or has_financial_word)


def extract_text_table_blocks(text: str) -> list[dict[str, Any]]:
    lines = str(text or "").splitlines()
    sections = []
    text_buffer = []
    table_buffer = []
    in_table = False

    def flush_text():
        nonlocal text_buffer

        block = clean_text("\n".join(text_buffer))
        text_buffer = []

        if block:
            sections.append({
                "type": "text",
                "text": block,
            })

    def flush_table():
        nonlocal table_buffer

        block = "\n".join(table_buffer).strip()
        table_buffer = []

        if block:
            sections.append({
                "type": "table_text",
                "title": "Text-formatted table",
                "text": block,
                "category": "other",
            })

    for line in lines:
        if line_looks_table_like(line):
            if not in_table:
                flush_text()
                in_table = True

            table_buffer.append(line)
        else:
            if in_table:
                flush_table()
                in_table = False

            text_buffer.append(line)

    if in_table:
        flush_table()
    else:
        flush_text()

    return sections


# ============================================================
# ORDERED RELEASE SECTION EXTRACTION
# ============================================================

def clean_structured_text_chunk(text: str) -> str:
    text = clean_text(text)

    lines = [normalize_space(line) for line in text.splitlines()]
    lines = [line for line in lines if line]

    drop_exact = {
        "news provided by",
        "explore",
        "more news releases in similar topics",
        "contact cision",
        "products",
        "about",
        "my services",
    }

    cleaned = []

    for line in lines:
        lower = line.lower().strip()

        if lower in drop_exact:
            continue

        if lower.startswith("view original content"):
            continue

        if lower.startswith("source "):
            continue

        cleaned.append(line)

    return clean_text("\n".join(cleaned))


def extract_ordered_release_sections_from_html(html: str, url: str) -> list[dict[str, Any]]:
    container = select_main_press_release_container(html, url)
    soup = remove_unwanted_html(container)

    body = soup.body or soup

    sections = []
    text_buffer = []
    table_index = 0
    text_index = 0

    def flush_text_buffer():
        nonlocal text_buffer, text_index

        text = clean_structured_text_chunk("\n".join(text_buffer))
        text_buffer = []

        if not text:
            return

        text_index += 1

        sections.append({
            "type": "text",
            "section_index": text_index,
            "text": text,
            "paragraphs": split_paragraphs(text),
        })

    for element in body.descendants:
        name = getattr(element, "name", None)

        if name == "table":
            flush_text_buffer()
            table_index += 1

            payload = table_payload_from_tag(element, table_index)

            if payload:
                sections.append(payload)

            continue

        if name is not None:
            continue

        parent = getattr(element, "parent", None)

        if parent is not None and parent.find_parent("table") is not None:
            continue

        text = normalize_space(str(element))

        if text:
            text_buffer.append(text)

    flush_text_buffer()

    has_tables = any(block.get("type") == "table" for block in sections)

    if not has_tables:
        full_text = clean_text("\n\n".join(block.get("text", "") for block in sections if block.get("type") == "text"))
        fallback_sections = extract_text_table_blocks(full_text)

        if fallback_sections:
            sections = fallback_sections

    deduped = []
    seen_text = set()

    for block in sections:
        if block.get("type") == "text":
            key = normalize_space(block.get("text", "")).lower()

            if not key or key in seen_text:
                continue

            seen_text.add(key)

        deduped.append(block)

    return deduped


def extract_ordered_release_sections_from_text(text: str) -> list[dict[str, Any]]:
    sections = extract_text_table_blocks(text)

    if sections:
        return sections

    text = clean_text(text)

    return [{
        "type": "text",
        "section_index": 1,
        "text": text,
        "paragraphs": split_paragraphs(text),
    }]


def get_narrative_text_from_sections(sections: list[dict[str, Any]]) -> str:
    return clean_text("\n\n".join(block.get("text", "") for block in sections if block.get("type") == "text"))


def extract_headline(text: str) -> str | None:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]

    for line in lines[:80]:
        line_clean = normalize_space(line)
        lower = line_clean.lower()

        if "reports" in lower and ("results" in lower or "earnings" in lower):
            return line_clean

        if "announces" in lower and ("results" in lower or "earnings" in lower):
            return line_clean

        if "financial results" in lower and len(line_clean) < 220:
            return line_clean

    return normalize_space(lines[0]) if lines else None


def table_counts_from_sections(sections: list[dict[str, Any]]) -> dict[str, int]:
    html_counts = Counter(block.get("category", "other") for block in sections if block.get("type") == "table")
    text_table_count = sum(1 for block in sections if block.get("type") == "table_text")

    return {
        "financial": html_counts.get("financial_statement", 0),
        "non_gaap": html_counts.get("non_gaap_reconciliation", 0),
        "contact": html_counts.get("contact", 0),
        "other": html_counts.get("other", 0),
        "text_tables": text_table_count,
        "total": sum(html_counts.values()) + text_table_count,
    }


def heading_for_table(block: dict[str, Any], table_number: int) -> str:
    category = block.get("category") or "other"

    label = {
        "financial_statement": "Financial Table",
        "non_gaap_reconciliation": "Non-GAAP Table",
        "contact": "Contact Table",
        "other": "Table",
    }.get(category, "Table")

    title = clean_text(block.get("title") or "")

    if title and not re.match(r"^table\s+\d+$", title, flags=re.I):
        return f"## {label} {table_number}: {title}"

    return f"## {label} {table_number}"


# ============================================================
# MARKDOWN + MANIFEST
# ============================================================

def build_llm_markdown_release(
    release_id: str,
    metadata: dict[str, Any],
    ordered_sections: list[dict[str, Any]],
    table_counts: dict[str, int],
) -> str:
    headline = metadata.get("headline") or metadata.get("title") or "Earnings Release"

    lines = []
    lines.append("---")

    yaml_fields = {
        "id": release_id,
        "ticker": metadata.get("ticker"),
        "company": metadata.get("company_name"),
        "source": metadata.get("source"),
        "fmp_publisher": metadata.get("publisher"),
        "site": metadata.get("site"),
        "release_date": metadata.get("release_date"),
        "period_requested": metadata.get("requested_period"),
        "period_detected": metadata.get("detected_period"),
        "headline": headline,
        "title": metadata.get("title"),
        "url": metadata.get("url"),
        "tables_financial": table_counts.get("financial"),
        "tables_non_gaap": table_counts.get("non_gaap"),
        "tables_contact": table_counts.get("contact"),
        "tables_other": table_counts.get("other"),
        "tables_text": table_counts.get("text_tables"),
    }

    for key, value in yaml_fields.items():
        if isinstance(value, int):
            lines.append(f"{key}: {value}")
        elif value not in [None, "", [], {}]:
            lines.append(f"{key}: {yaml_quote(value)}")

    lines.append("---")
    lines.append("")
    lines.append(f"# {headline}")
    lines.append("")

    table_number = 0
    previous_text_key = ""

    for block in ordered_sections:
        if block.get("type") == "text":
            text = clean_text(block.get("text") or "")

            if not text:
                continue

            key = normalize_space(text).lower()

            if key == previous_text_key:
                continue

            previous_text_key = key

            lines.append(text)
            lines.append("")

        elif block.get("type") == "table":
            md = clean_text(block.get("markdown") or "")

            if not md:
                continue

            table_number += 1
            lines.append(heading_for_table(block, table_number))
            lines.append("")
            lines.append(md)
            lines.append("")

        elif block.get("type") == "table_text":
            table_text = block.get("text") or ""

            if not table_text:
                continue

            table_number += 1
            title = clean_text(block.get("title") or f"Text Table {table_number}")
            lines.append(f"## Table {table_number}: {title}")
            lines.append("")
            lines.append("```text")
            lines.append(table_text.strip())
            lines.append("```")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def build_manifest(
    release_id: str,
    metadata: dict[str, Any],
    table_counts: dict[str, int],
    llm_markdown_path: str,
    raw_html_path: str,
    raw_fmp_path: str,
    llm_markdown: str,
) -> dict[str, Any]:
    return {
        "id": release_id,
        "ticker": metadata.get("ticker"),
        "company": metadata.get("company_name"),
        "period": metadata.get("requested_period") or metadata.get("detected_period"),
        "release_date": metadata.get("release_date"),
        "headline": metadata.get("headline"),
        "title": metadata.get("title"),
        "source": metadata.get("source"),
        "publisher": metadata.get("publisher"),
        "site": metadata.get("site"),
        "url": metadata.get("url"),
        "table_counts": table_counts,
        "llm_markdown_path": llm_markdown_path,
        "raw_html_path": raw_html_path,
        "raw_fmp_path": raw_fmp_path,
        "size_estimates": {
            "llm_markdown": estimate_tokenish_size(llm_markdown),
        },
        "created_at_utc": datetime.now(UTC).isoformat(),
    }


# ============================================================
# MAIN PROCESSOR
# ============================================================

def process_public_press_release_to_llm_markdown(
    ticker: str,
    release: dict[str, Any],
    fiscal_year: int,
    quarter: Any,
    prefer_scraped_html: bool = True,
) -> dict[str, Any]:
    ticker = ticker.upper()

    title = extract_release_title(release)
    release_date = extract_release_date(release)
    url = extract_release_url(release)
    publisher = extract_publisher(release)
    site = first_non_empty(release.get("site"), domain_from_url(url or ""))

    if not url:
        raise ValueError("FMP release does not contain a URL.")

    if is_sec_url(url):
        raise ValueError(f"Refusing SEC URL in public release mode: {url}")

    scraped_html = ""
    scraped_text = ""
    content_source = None

    if prefer_scraped_html:
        try:
            scraped_html = scrape_public_url(url)
            scraped_text = html_to_clean_text_for_matching(scraped_html, url)
            content_source = "scraped_public_url_html"
        except Exception as error:
            debug_error_path = DEBUG_DIR / f"{ticker.lower()}_scrape_error.txt"
            debug_error_path.write_text(str(error), encoding="utf-8")
            scraped_html = ""
            scraped_text = ""

    fmp_text = extract_fmp_release_text(release)

    if scraped_html:
        raw_content = scraped_html
        ordered_sections = extract_ordered_release_sections_from_html(scraped_html, url)
    elif fmp_text:
        raw_content = fmp_text
        content_source = "fmp_press_release_text"
        ordered_sections = extract_ordered_release_sections_from_text(fmp_text)
        scraped_text = fmp_text
    else:
        raise ValueError("No usable scraped HTML or FMP text content found.")

    requested_period = expected_period_string(fiscal_year, quarter)
    narrative_text = get_narrative_text_from_sections(ordered_sections)
    headline = extract_headline(narrative_text) or title
    detected_period = extract_period_from_text(f"{title}\n{scraped_text}\n{narrative_text}")

    table_counts = table_counts_from_sections(ordered_sections)

    release_id = make_release_id(ticker, release_date, title)

    metadata = {
        "ticker": ticker,
        "company_name": first_non_empty(
            release.get("companyName"),
            release.get("company"),
            release.get("name"),
        ),
        "source": "FMP Press Releases API + Public URL Scrape",
        "content_source": content_source,
        "publisher": publisher,
        "site": site,
        "release_date": str(release_date) if release_date else None,
        "requested_period": requested_period,
        "detected_period": detected_period,
        "headline": headline,
        "title": title,
        "url": url,
        "raw_fmp_keys": sorted(list(release.keys())),
    }

    raw_suffix = "html" if is_html_content(raw_content) else "txt"

    raw_content_path = RAW_DIR / f"{release_id}_raw_public_release.{raw_suffix}"
    raw_fmp_path = RAW_DIR / f"{release_id}_fmp_press_release.json"
    llm_markdown_path = LLM_DIR / f"{release_id}_for_llm.md"
    manifest_path = MANIFEST_DIR / f"{release_id}_manifest.json"

    raw_content_path.write_text(str(raw_content), encoding="utf-8")
    raw_fmp_path.write_text(json.dumps(release, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    llm_markdown = build_llm_markdown_release(
        release_id=release_id,
        metadata=metadata,
        ordered_sections=ordered_sections,
        table_counts=table_counts,
    )

    llm_markdown_path.write_text(llm_markdown, encoding="utf-8")

    manifest = build_manifest(
        release_id=release_id,
        metadata=metadata,
        table_counts=table_counts,
        llm_markdown_path=str(llm_markdown_path),
        raw_html_path=str(raw_content_path),
        raw_fmp_path=str(raw_fmp_path),
        llm_markdown=llm_markdown,
    )

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), default=str),
        encoding="utf-8",
    )

    return {
        "id": release_id,
        "metadata": metadata,
        "table_counts": table_counts,
        "llm_markdown": llm_markdown,
        "llm_markdown_path": str(llm_markdown_path),
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "raw_content_path": str(raw_content_path),
        "raw_fmp_path": str(raw_fmp_path),
        "size_estimates": manifest["size_estimates"],
        "debug": {
            "content_source": content_source,
            "raw_content_length": len(raw_content),
            "raw_content_contains_table_tag": "<table" in raw_content.lower(),
            "ordered_section_count": len(ordered_sections),
            "fmp_keys": sorted(list(release.keys())),
        },
    }


# ============================================================
# ZIP + SAFE RUNNER
# ============================================================

def make_output_zip() -> str:
    return shutil.make_archive(str(OUTPUT_DIR), "zip", str(OUTPUT_DIR))


def try_colab_download(path: str) -> bool:
    try:
        from google.colab import files
        files.download(path)
        return True
    except Exception as error:
        print(f"Could not auto-download file. Path is still available at: {path}")
        print(f"Download error: {error}")
        return False


def write_error_outputs(
    ticker: str,
    fiscal_year: int,
    quarter: Any,
    error: Exception,
) -> dict[str, Any]:
    error_manifest = {
        "status": "error",
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "quarter": quarter,
        "error": str(error),
        "traceback": traceback.format_exc(),
        "output_dir": str(OUTPUT_DIR),
        "debug_dir": str(DEBUG_DIR),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }

    error_manifest_path = MANIFEST_DIR / f"{ticker.lower()}_error_manifest.json"
    error_traceback_path = DEBUG_DIR / f"{ticker.lower()}_error_traceback.txt"

    error_manifest_path.write_text(json.dumps(error_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    error_traceback_path.write_text(traceback.format_exc(), encoding="utf-8")

    zip_path = make_output_zip()

    return {
        "status": "error",
        "error": str(error),
        "error_manifest_path": str(error_manifest_path),
        "error_traceback_path": str(error_traceback_path),
        "zip_path": zip_path,
    }


def extract_selected_period_for_llm_markdown_public_release(
    ticker: str,
    fiscal_year: int,
    quarter: Any,
    limit: int = 250,
    prefer_scraped_html: bool = True,
    auto_download_zip: bool = True,
    print_full_markdown: bool = False,
) -> dict[str, Any]:
    ticker = ticker.upper()
    requested_period = expected_period_string(fiscal_year, quarter)

    print("============================================================")
    print("FMP Public Press Release Scraper")
    print("============================================================")
    print("Ticker:", ticker)
    print("Requested period:", requested_period)
    print("Output dir:", OUTPUT_DIR)
    print("FMP_API_KEY present:", bool(FMP_API_KEY))

    try:
        release = find_fmp_public_earnings_release(
            ticker=ticker,
            fiscal_year=fiscal_year,
            quarter=quarter,
            limit=limit,
        )

        candidates_path = DEBUG_DIR / f"{ticker.lower()}_selected_fmp_press_release.json"
        candidates_path.write_text(json.dumps(release, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        result = process_public_press_release_to_llm_markdown(
            ticker=ticker,
            release=release,
            fiscal_year=fiscal_year,
            quarter=quarter,
            prefer_scraped_html=prefer_scraped_html,
        )

        zip_path = make_output_zip()
        result["zip_path"] = zip_path

        print("")
        print("Public press release processed")
        print("Title:", result["metadata"].get("title"))
        print("Headline:", result["metadata"].get("headline"))
        print("Release date:", result["metadata"].get("release_date"))
        print("Detected period:", result["metadata"].get("detected_period"))
        print("URL:", result["metadata"].get("url"))
        print("Content source:", result["metadata"].get("content_source"))
        print("Raw contains <table>:", result["debug"].get("raw_content_contains_table_tag"))
        print("Raw content length:", result["debug"].get("raw_content_length"))
        print("Table counts:", json.dumps(result["table_counts"], indent=2))
        print("LLM Markdown:", result["llm_markdown_path"])
        print("Manifest:", result["manifest_path"])
        print("Raw content:", result["raw_content_path"])
        print("Raw FMP JSON:", result["raw_fmp_path"])
        print("ZIP:", zip_path)

        if print_full_markdown:
            print("")
            print("Markdown full output:")
            print(result["llm_markdown"])

        if auto_download_zip:
            try_colab_download(zip_path)

        return result

    except Exception as error:
        print("")
        print("ERROR")
        print(str(error))
        print(traceback.format_exc())

        result = write_error_outputs(ticker, fiscal_year, quarter, error)

        print("Error manifest:", result["error_manifest_path"])
        print("Error traceback:", result["error_traceback_path"])
        print("ZIP:", result["zip_path"])

        if auto_download_zip:
            try_colab_download(result["zip_path"])

        return result


def debug_result_files(result: dict[str, Any]) -> None:
    print("")
    print("Debug result files")
    print("------------------")

    for key in [
        "llm_markdown_path",
        "manifest_path",
        "raw_content_path",
        "raw_fmp_path",
        "zip_path",
        "error_manifest_path",
        "error_traceback_path",
    ]:
        path = result.get(key)

        if not path:
            continue

        path_obj = Path(path)
        exists = path_obj.exists()
        size = path_obj.stat().st_size if exists else 0

        print(f"{key}: {path}")
        print(f"  exists: {exists}")
        print(f"  size bytes: {size}")

    raw_path = result.get("raw_content_path")

    if raw_path and Path(raw_path).exists():
        raw = Path(raw_path).read_text(encoding="utf-8", errors="ignore")

        print("")
        print("Raw content checks")
        print("  length:", len(raw))
        print("  contains <table>:", "<table" in raw.lower())
        print("  first 1000 chars:")
        print(raw[:1000])