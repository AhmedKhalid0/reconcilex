"""
Integration tests for ReconcileX FastAPI Endpoints and Web Dashboard.
"""

import pytest
from starlette.testclient import TestClient
from reconcilex.web.app import app, _WEB_SESSION


@pytest.fixture
def client():
    return TestClient(app)


def test_api_status_initial(client):
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert "version" in data


def test_api_load_sample_and_reconcile(client):
    res = client.post("/api/load-sample")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["invoices_count"] >= 6
    assert data["transactions_count"] >= 6
    assert data["summary"]["matched_count"] >= 4

    # Verify results endpoint
    res_results = client.get("/api/results")
    assert res_results.status_code == 200
    r_data = res_results.json()
    assert r_data["report"] is not None
    assert len(r_data["report"]["matched_pairs"]) >= 4

    # Test Excel Export streaming
    res_excel = client.get("/api/export/excel")
    assert res_excel.status_code == 200
    assert "spreadsheetml" in res_excel.headers["content-type"]
    assert len(res_excel.content) > 1000

    # Test Markdown Export streaming
    res_md = client.get("/api/export/markdown")
    assert res_md.status_code == 200
    assert "text/markdown" in res_md.headers["content-type"]
    assert b"ReconcileX Audit & Reconciliation Dossier" in res_md.content


def test_api_document_inspector_endpoint(client):
    # Ensure sample is loaded
    client.post("/api/load-sample")

    # Pick first invoice doc_id
    doc_id = list(_WEB_SESSION["invoices"].keys())[0]
    res = client.get(f"/api/document/{doc_id}")
    assert res.status_code == 200
    doc = res.json()
    assert doc["doc_id"] == doc_id
    assert "vendor_name" in doc
    assert "tax_audit" in doc
    assert "sha256_fingerprint" in doc["tax_audit"]
    assert len(doc["tax_audit"]["sha256_fingerprint"]) == 64
    assert "detected_rate_percent" in doc["tax_audit"]


def test_api_journal_voucher_export(client):
    client.post("/api/load-sample")
    res = client.get("/api/export/journal-voucher")
    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    csv_text = res.text
    assert "Posting Date,Voucher Type,Account Code,Account Name,Debit,Credit" in csv_text
    assert "1010" in csv_text  # Bank account code
    assert ("5210" in csv_text or "9999" in csv_text)


def test_api_analytics_endpoint(client):
    client.post("/api/load-sample")
    res = client.get("/api/analytics")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert "match_type_distribution" in data
    assert "settlement_timeline" in data
    assert len(data["settlement_timeline"]) > 0
    assert "tax_compliance_summary" in data
    assert data["tax_compliance_summary"]["audited"] >= 6


def test_api_manual_batch_match(client):
    client.post("/api/load-sample")
    report = _WEB_SESSION["report"]

    # Pick unmatched invoice and unmatched tx if available, or any IDs
    inv_ids = [i.doc_id for i in report.unmatched_invoices[:1]]
    tx_ids = [t.tx_id for t in report.unmatched_transactions[:1]]

    if not inv_ids:
        inv_ids = [list(_WEB_SESSION["invoices"].keys())[0]]
    if not tx_ids:
        tx_ids = [list(_WEB_SESSION["transactions"].keys())[0]]

    req_body = {
        "invoice_ids": inv_ids,
        "tx_ids": tx_ids,
        "reasoning": "Accountant manually verified discrepancy and settlement"
    }

    res = client.post("/api/manual-batch-match", json=req_body)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["match"]["match_type"] == "MANUAL_OVERRIDE"
    assert "Split-Ledger Manual Match" in data["match"]["audit_reasoning"]


def test_api_reconcile_with_custom_parameters(client):
    client.post("/api/load-sample")
    res = client.post("/api/reconcile?tolerance_days=5&vendor_threshold=65.0&fee_tolerance=30.0&max_bundle=3")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "summary" in data


def test_dashboard_html_view(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "ReconcileX | Financial Reconciliation Platform" in res.text
    assert "Interactive Split-Ledger" in res.text
    assert "Tax & ZATCA Audit" in res.text
    assert "Parameter Studio" in res.text
    assert "chart-method-mix" in res.text
    assert "doc-drawer" in res.text
