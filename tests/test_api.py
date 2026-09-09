"""
Integration tests for ReconcileX FastAPI Endpoints and Web Dashboard.
"""

import pytest
from starlette.testclient import TestClient
from reconcilex.web.app import app


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


def test_dashboard_html_view(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "ReconcileX | Financial Reconciliation Platform" in res.text
    assert "Run Deterministic Reconciliation Engine" in res.text
