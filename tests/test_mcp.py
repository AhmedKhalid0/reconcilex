"""
Integration tests for ReconcileX MCP Server tools.
"""

import json
from pathlib import Path
import pytest
from reconcilex.mcp.server import create_mcp_server
from reconcilex.utils.sample_generator import SampleDataGenerator


@pytest.fixture(scope="module")
def sample_dataset():
    base_dir = Path("./data/test_tmp/mcp_tests")
    base_dir.mkdir(parents=True, exist_ok=True)
    inv_dir, stmt_file = SampleDataGenerator.generate_all(base_dir)
    return inv_dir, stmt_file


@pytest.mark.asyncio
async def test_mcp_full_pipeline(sample_dataset):
    inv_dir, stmt_file = sample_dataset
    server = create_mcp_server()

    # 1. Test ingest_sources
    raw_ingest = await server.call_tool(
        "ingest_sources",
        arguments={"paths": [str(inv_dir), str(stmt_file)]}
    )
    # Extract text from CallToolResult
    ingest_text = raw_ingest.content[0].text if hasattr(raw_ingest, 'content') else str(raw_ingest)
    ingest_res = json.loads(ingest_text)
    assert ingest_res["status"] == "success"
    assert ingest_res["ingested_invoices_count"] >= 5
    assert ingest_res["ingested_transactions_count"] >= 5

    # 2. Test run_deterministic_match
    raw_match = await server.call_tool(
        "run_deterministic_match",
        arguments={"tolerance_days": 3, "fee_tolerance": 25.0}
    )
    match_text = raw_match.content[0].text if hasattr(raw_match, 'content') else str(raw_match)
    match_res = json.loads(match_text)
    assert match_res["status"] == "success"
    assert match_res["matched_count"] >= 3
    assert match_res["match_rate_percentage"] > 40.0

    # 3. Test get_unmatched_records
    raw_unmatched = await server.call_tool("get_unmatched_records", arguments={})
    unmatched_text = raw_unmatched.content[0].text if hasattr(raw_unmatched, 'content') else str(raw_unmatched)
    unmatched_res = json.loads(unmatched_text)
    assert "unmatched_invoices" in unmatched_res
    assert "unmatched_transactions" in unmatched_res

    # 4. Test resolve_ambiguity
    raw_resolve = await server.call_tool(
        "resolve_ambiguity",
        arguments={
            "invoice_id": "INV-2026-007",
            "tx_id": "TXN-882107",
            "reasoning": "Accountant verified cash advance settlement against unbilled project ledger."
        }
    )
    resolve_text = raw_resolve.content[0].text if hasattr(raw_resolve, 'content') else str(raw_resolve)
    resolve_res = json.loads(resolve_text)
    assert resolve_res["status"] == "success"

    # 5. Test export_reconciliation_report (Markdown)
    raw_export = await server.call_tool(
        "export_reconciliation_report",
        arguments={"format": "markdown"}
    )
    export_text = raw_export.content[0].text if hasattr(raw_export, 'content') else str(raw_export)
    export_res = json.loads(export_text)
    assert export_res["status"] == "success"
    assert "reconciliation_report" in export_res["file_path"]
    assert Path(export_res["file_path"]).exists()
