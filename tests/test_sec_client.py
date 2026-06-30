from fmp_mcp_research.sec_client import SECClient, html_to_llm_json


def test_html_to_llm_json_extracts_text_blocks_and_tables():
    html = """
    <html><body>
      <h1>Company Reports First Quarter Results</h1>
      <p>Revenue increased year over year.</p>
      <table><tr><th>Metric</th><th>Q1</th></tr><tr><td>Revenue</td><td>$10</td></tr></table>
    </body></html>
    """
    payload = html_to_llm_json(html, include_html=False, include_tables=True)
    assert "Company Reports First Quarter Results" in payload["text"]
    assert "html" not in payload
    assert payload["table_count"] == 1
    assert payload["tables"][0]["rows"] == [["Metric", "Q1"], ["Revenue", "$10"]]
    assert payload["blocks"][0]["type"] == "heading"


def test_select_best_document_prefers_exhibit_99_release():
    index_json = {
        "directory": {
            "item": [
                {"name": "company-8k.htm", "type": "8-K", "description": "FORM 8-K"},
                {"name": "ex99-1.htm", "type": "EX-99.1", "description": "Earnings press release"},
            ]
        }
    }
    filing = {"primaryDocument": "company-8k.htm"}
    selected = SECClient._select_best_document(index_json, filing)
    assert selected["name"] == "ex99-1.htm"


def test_select_best_filing_uses_requested_filing_date_and_item_202():
    filings = [
        {"form": "8-K", "filingDate": "2026-02-15", "items": "Item 8.01", "accessionNumber": "1"},
        {"form": "8-K", "filingDate": "2026-01-02", "items": "Item 2.02 Results of Operations", "accessionNumber": "2"},
        {"form": "10-Q", "filingDate": "2026-01-03", "items": "", "accessionNumber": "3"},
    ]
    selected = SECClient._select_best_filing(filings, filing_date="2026-01-01")
    assert selected["accessionNumber"] == "2"
