# Product Requirements Document (PRD)
## ReconcileX: Privacy-First Hybrid Financial Reconciliation Platform

**Document Version:** 1.0.0  
**Status:** Approved  
**Author:** Ahmed Khaled (Ahmed Algendy) <contact@ahmedalgendy.com>  
**Target Delivery:** Q1 2026  

---

## 1. Executive Summary & Problem Statement

### 1.1 The Industry Challenge
Financial reconciliation is the backbone of internal financial controls across every enterprise, SME, and accounting practice. However, modern corporate finance teams face a fundamental trilemma:
1. **Manual Labor Costs:** Accountants spend 30% to 50% of their month-end close cycle matching thousands of vendor invoices, utility receipts, and tax filings against messy bank feeds line by line.
2. **LLM Hallucination & Computational Fragility:** While modern Generative AI models excel at natural language parsing, they are notoriously untrustworthy with pure arithmetic, often hallucinating rounding adjustments, corrupting ledger totals, and producing non-verifiable calculations.
3. **Data Privacy & Regulatory Restrictions:** Financial ledgers, wire notes, and tax IDs (PII/MNPI) cannot legally or ethically be uploaded to third-party public cloud LLM endpoints under GDPR, SOC2, HIPAA, and regional banking compliance laws.

### 1.2 The ReconcileX Solution
**ReconcileX** solves this problem by introducing a **Hybrid Deterministic-Agentic Architecture**:
- **Deterministic Math Engine:** Uses pure Python (`pandas`, `rapidfuzz`, and combinatorial subset-sum algorithms) to guarantee 100% mathematical precision on exact, fuzzy, and bundled 1-to-N transactions with zero AI hallucination.
- **Local-First Extraction Layer:** Processes digital PDFs and bank feeds locally, and leverages state-of-the-art multimodal vision models (e.g., `Qwen2.5-VL` locally via Ollama, or local OCR with Arabic RTL support) without data leaving the user's infrastructure.
- **Agentic Edge-Case Resolver:** Employs a constrained reasoning agent exclusively for semantic ambiguities (e.g., resolving `"STRIPE *ACME CORP"` to `"Acme Cloud Hosting LLC"` or identifying unbilled wire transfer fees).
- **Dual Interface:** Accessible both as a standard **Model Context Protocol (MCP) Server** for IDE-integrated AI agents (Antigravity, Cursor, Claude Desktop) and as an intuitive **Web Dashboard / Standalone CLI** for non-technical finance teams.

---

## 2. Target User Personas

| Persona | Role & Environment | Primary Pain Points | ReconcileX Value Proposition |
| :--- | :--- | :--- | :--- |
| **Sarah (Senior Corporate Accountant)** | Corporate Finance / Mid-market SME; works in Excel, ERPs, and banking portals. | Spends 4 days each month-end resolving mismatched invoice names and split wire payments. | Automated 1-click reconciliation, clear discrepancy highlights, and exportable Excel audit trail. |
| **Tariq (Managing Financial Auditor)** | Audit & Assurance Firm; handles multinational clients with Arabic & English records. | Unclear counterparty notes in Saudi/UAE bank feeds; lack of verifiable audit trails in AI tools. | Deterministic matching with explicit audit log, zero hallucination on math, native Arabic invoice OCR. |
| **Alex (Full-Stack / AI Engineer)** | FinTech Developer; uses Antigravity, Cursor, and Claude Desktop. | Wants AI agents to audit financial datasets directly via MCP without building custom scrapers. | Plug-and-play MCP Server with standard JSON-RPC tools (`run_deterministic_match`, `resolve_ambiguity`). |
| **Elena (Chief Financial Officer - CFO)** | Executive Leadership; oversees enterprise risk, privacy, and compliance. | Risk of confidential payroll, vendor, and revenue data leaking into public cloud AI training sets. | 100% local-first deployment option; complete on-premise operation via Ollama and local parsers. |

---

## 3. Key User Journeys

### Journey 1: The Non-Technical Accountant (Web Dashboard)
1. **Launch:** Accountant starts ReconcileX with a single command or desktop shortcut (`reconcilex dashboard`).
2. **Ingest:** Drags and drops a folder containing 100 PDF/image invoices alongside a 3-month bank statement (`.csv` or `.xlsx`).
3. **Execution:** ReconcileX automatically runs the deterministic matching pipeline, instantly clearing 75–85% of clean entries in seconds.
4. **Review:** The accountant views an interactive dual-table comparison. Green rows indicate verified matches; amber rows indicate suggested semantic matches with reasoning; red rows highlight unreconciled variances.
5. **Resolution:** The accountant reviews agent-suggested resolutions (e.g., bundled invoice payments or $15 wire fee deduction) and clicks "Approve".
6. **Export:** Downloads a fully formatted, color-coded Excel report complete with an immutable Audit Trail tab.

### Journey 2: The Agentic Developer (MCP Server in IDE)
1. **Setup:** Developer adds `reconcilex` to `mcpServers` config in Antigravity or Claude Desktop.
2. **Prompt:** In IDE chat, developer prompts: *"Reconcile Q1 marketing invoices in `./docs/invoices` against `./statement.csv` and show unlinked charges over $500."*
3. **Tool Execution:** The LLM client calls `ingest_sources`, runs `run_deterministic_match`, inspects `get_unmatched_records`, and invokes `resolve_ambiguity` with grounded evidence.
4. **Deliverable:** Developer receives an exact Markdown breakdown and generates the audit dossier directly inside their workspace.

---

## 4. Functional Requirements

### 4.1 Ingestion & Normalization Layer (FR-1)
- **FR-1.1:** Digital PDF Ingestion: Must extract vendor, invoice number, date, subtotal, tax, and total using vector-based PDF parsing (`pdfplumber`) without invoking LLMs.
- **FR-1.2:** Scanned & Image Receipt OCR: Must support PNG, JPG, and scanned PDFs with dual-engine capability:
  - Local mode: Support local OCR engines (`EasyOCR`, `Tesseract`) and local VLM (`Qwen2.5-VL` via Ollama).
  - Cloud fallback: Support vision APIs (Gemini 2.0/1.5 Flash, Claude 3.5 Haiku, OpenAI GPT-4o-mini).
- **FR-1.3:** Multilingual & Arabic Support: Must parse right-to-left (RTL) Arabic tax invoices (e.g., Saudi ZATCA compliant, Egyptian e-invoices) and bilingual Arabic/English receipts.
- **FR-1.4:** Bank Statement Parser: Must ingest CSV, TSV, and Excel (`.xlsx`, `.xls`) files, automatically mapping heterogeneous column headers (`Date`, `Valuta Date`, `Description`, `Debit`, `Credit`, `Amount`, `Balance`, `Reference`).

### 4.2 Deterministic Matching Engine (FR-2)
- **FR-2.1: Pass 1 (Exact Match):** Match records where `Amount_invoice == Amount_bank`, `|Date_invoice - Date_bank| <= tolerance_days` (default: 3 days), and `FuzzyScore(Vendor, Counterparty) >= 80%`.
- **FR-2.2: Pass 2 (Relaxed Fuzzy Match):** Match records with exact amounts, extended date window (up to 7 days), and token set ratio `FuzzyScore >= 70%`.
- **FR-2.3: Pass 3 (Bundled Payments):** Detect 1-to-N batch payments where a single bank transaction equals the exact sum of 2 to 4 invoices from the same vendor within a 15-day window using a bounded subset-sum solver.
- **FR-2.4: Pass 4 (Fee Variance Tolerance):** Detect matches where invoice total and bank transaction differ by a small variance ($\le \$25.00$ or configurable fee threshold), attributing the difference to bank fees, wire charges, or FX spreads.

### 4.3 Agentic Reasoning Intermediary (FR-3)
- **FR-3.1:** Semantic Counterparty Disambiguation: Link bank statement trading names and DBA abbreviations (e.g., `AMZN MKTPLACE`, `STC PAY`, `SQ *COFFEE`) with legal corporate invoice entities.
- **FR-3.2:** Discrepancy Categorization: Classify unmatched records into:
  - *Missing Invoice:* Bank withdrawal with no supporting receipt.
  - *Uncollected Invoice:* Issued invoice with no corresponding bank deposit.
  - *Timing Difference:* Transaction outside the current statement date window.
- **FR-3.3:** Audit Trail Justification: Every agent-resolved record must include an immutable natural-language justification citing specific evidence fields.

### 4.4 Interfaces (FR-4)
- **FR-4.1:** MCP Server: Expose a full JSON-RPC stdio protocol implementing `ingest_sources`, `run_deterministic_match`, `get_unmatched_records`, `resolve_ambiguity`, and `export_reconciliation_report`.
- **FR-4.2:** Standalone CLI: Typer-based command line interface (`reconcilex audit`, `reconcilex dashboard`, `reconcilex mcp`, `reconcilex generate-samples`).
- **FR-4.3:** Human-Crafted Web Dashboard: Lightweight, zero-configuration local web interface (FastAPI + HTML5/CSS3) adhering strictly to human-crafted UI/UX guidelines (light mode default, soft slate theme, high-density financial tables, zero neon slop).

---

## 5. Non-Functional Requirements (NFRs)

- **NFR-1 (Privacy & Air-Gap Compliance):** Default configuration must function 100% offline without external network calls when using local parsing and Ollama.
- **NFR-2 (Performance & Throughput):** Deterministic matching pass must process $\ge 1,000$ transactions in under 2.0 seconds on standard consumer hardware.
- **NFR-3 (Mathematical Precision):** Zero floating-point rounding errors. All monetary computations must utilize 2-decimal fixed precision (`decimal.Decimal` or rounded float representations).
- **NFR-4 (Cross-Platform Portability):** Support Windows 10/11, macOS (Apple Silicon and Intel), and Linux (Ubuntu 20.04+).
- **NFR-5 (Extensibility):** Modular architecture allowing new OCR engines, bank format adapters, or LLM providers to be added via simple abstract base classes.
