# Software Requirements Specification (SRS)
## ReconcileX: Hybrid Financial Reconciliation Platform
### IEEE Standard 830-1998 Compliant Specification

**Document Identifier:** RECONCILEX-SRS-2026-V1  
**Author:** Ahmed Khaled (Ahmed Algendy) <contact@ahmedalgendy.com>  
**Status:** Approved  
**Version:** 1.0.0  

---

## 1. Introduction

### 1.1 Purpose
This Software Requirements Specification (SRS) establishes the formal functional, performance, architectural, and data interface requirements for **ReconcileX**, an enterprise-grade, privacy-first, hybrid financial reconciliation engine.

### 1.2 Scope of the Software
ReconcileX provides automated, auditable matching between heterogeneous accounts payable/receivable source documents (digital invoices, scanned bilingual receipts, credit memos) and bank account statements. It operates across three deployment modalities:
1. An IDE-integrated **Model Context Protocol (MCP)** server communicating via standard JSON-RPC over `stdio`.
2. A headless **Command-Line Interface (CLI)** for automated scripting and terminal users.
3. A local **Web Dashboard** tailored for corporate accountants and auditors.

### 1.3 Definitions, Acronyms, and Abbreviations
- **MCP:** Model Context Protocol (Anthropic open standard for AI agent tool integration).
- **VLM:** Vision-Language Model (e.g., Qwen2.5-VL, MiniCPM-V, Gemini Vision).
- **OCR:** Optical Character Recognition.
- **RTL:** Right-to-Left (referring to Arabic language text direction).
- **ZATCA:** Zakat, Tax and Customs Authority (Saudi Arabian electronic invoicing standard).
- **Audit Trail:** An unalterable chronological record providing documentary evidence of the sequence of activities that have affected at any time a specific financial operation.

---

## 2. System Architecture & Context

```text
+-------------------------------------------------------------------------------+
|                               RECONCILEX SYSTEM                               |
|                                                                               |
|  +-------------------------+     +-------------------+    +----------------+  |
|  |  Ingestion & Extraction | --> |   Deterministic   | -> | LLM Agent Edge |  |
|  |  - pdfplumber           |     |  Matching Engine  |    |  Case Resolver |  |
|  |  - Statement Auto-Map   |     |  - Exact (P1)     |    |  - Ollama      |  |
|  |  - Arabic/Multilingual  |     |  - Fuzzy (P2)     |    |  - Cloud APIs  |  |
|  |    OCR & VLM Adapters   |     |  - Bundled (P3)   |    |    (Fallback)  |  |
|  |                         |     |  - Fee Tol. (P4)  |    |                |  |
|  +-------------------------+     +-------------------+    +----------------+  |
|                                                                    |          |
|                                                                    v          |
|                                                           +----------------+  |
|                                                           |  Audit Trail & |  |
|                                                           |  Report Exporter| |
|                                                           +----------------+  |
+-------------------------------------------------------------------------------+
```

---

## 3. Detailed Data Dictionary & Schemas

### 3.1 Invoice Record Entity (`InvoiceRecord`)
| Field Name | Type | Mandatory | Description | Example |
| :--- | :--- | :--- | :--- | :--- |
| `doc_id` | `String` | Yes | Unique normalized invoice identifier | `"INV-2026-081"` |
| `source_path` | `String` | Yes | Absolute or relative file path to original document | `"./docs/INV-2026-081.pdf"` |
| `vendor_name` | `String` | Yes | Name of vendor/supplier extracted from header | `"Acme Cloud Hosting LLC"` |
| `vendor_tax_id` | `String (Optional)` | No | Tax ID, VAT number, or CR registration | `"300123456700003"` |
| `invoice_date` | `ISO Date (YYYY-MM-DD)` | Yes | Transaction or issuance date | `"2026-02-15"` |
| `due_date` | `ISO Date (Optional)` | No | Due date if specified on invoice | `"2026-03-15"` |
| `subtotal` | `Decimal(12,2)` | No | Net amount before taxes and discounts | `1261.53` |
| `tax_amount` | `Decimal(12,2)` | No | Tax / VAT component | `188.47` |
| `total_amount` | `Decimal(12,2)` | Yes | Total invoice liability | `1450.00` |
| `currency` | `String(3)` | Yes | ISO 4217 Currency code | `"USD"`, `"SAR"`, `"EUR"` |
| `extraction_method` | `Enum` | Yes | `"digital_pdf"`, `"local_ocr"`, `"vlm_vision"` | `"digital_pdf"` |

### 3.2 Bank Transaction Entity (`BankTransaction`)
| Field Name | Type | Mandatory | Description | Example |
| :--- | :--- | :--- | :--- | :--- |
| `tx_id` | `String` | Yes | Unique row hash or bank assigned transaction ID | `"TXN-88421"` |
| `tx_date` | `ISO Date (YYYY-MM-DD)` | Yes | Value date of cleared funds | `"2026-02-16"` |
| `counterparty` | `String` | Yes | Raw memo, narration, or party name from bank | `"STRIPE *ACME HOSTING"` |
| `amount` | `Decimal(12,2)` | Yes | Cleared monetary value (positive float) | `1450.00` |
| `direction` | `Enum` | Yes | `"DEBIT"` (outflow/expense) or `"CREDIT"` (inflow/revenue) | `"DEBIT"` |
| `balance` | `Decimal(12,2) (Optional)`| No | Account balance after transaction | `42190.50` |
| `reference` | `String (Optional)` | No | Bank reference, check number, or wire code | `"WIRE-REF-99210"` |

### 3.3 Match Record Entity (`MatchResult`)
| Field Name | Type | Description |
| :--- | :--- | :--- |
| `match_id` | `String` | UUID4 identifier of the reconciliation match |
| `match_status` | `Enum` | `"MATCHED"`, `"AMBIGUOUS"`, `"UNMATCHED_INVOICE"`, `"UNMATCHED_BANK"` |
| `match_type` | `Enum` | `"EXACT_1TO1"`, `"FUZZY_1TO1"`, `"BUNDLED_1TON"`, `"FEE_ADJUSTED"`, `"AGENT_RESOLVED"` |
| `invoice_ids` | `List[String]` | Array of associated invoice identifiers |
| `tx_id` | `String (Optional)` | Associated bank transaction ID |
| `confidence_score` | `Float (0.0 - 1.0)` | Quantitative certainty score of the match |
| `variance_amount` | `Decimal(12,2)` | Mathematical discrepancy (`Amount_bank - Sum(Amount_invoices)`) |
| `audit_reasoning` | `String` | Deterministic derivation or LLM justification |
| `timestamp` | `ISO Timestamp` | UTC timestamp of resolution |

---

## 4. Mathematical Models & Matching Algorithms

### 4.1 Pass 1: Deterministic Exact Match
A candidate pair $(I, T)$ where $I \in \text{Invoices}$ and $T \in \text{Transactions}$ is classified as `EXACT_1TO1` if and only if:
$$\text{Amount}(I) = \text{Amount}(T)$$
$$|\text{Date}(I) - \text{Date}(T)| \le \delta_{\text{exact}} \quad (\text{Default } \delta_{\text{exact}} = 3 \text{ days})$$
$$\text{RapidFuzzTokenSetRatio}(\text{Vendor}(I), \text{Counterparty}(T)) \ge 80.0$$

### 4.2 Pass 2: Relaxed Fuzzy Match
For unlinked records after Pass 1:
$$\text{Amount}(I) = \text{Amount}(T)$$
$$|\text{Date}(I) - \text{Date}(T)| \le \delta_{\text{fuzzy}} \quad (\text{Default } \delta_{\text{fuzzy}} = 7 \text{ days})$$
$$\text{RapidFuzzPartialRatio}(\text{Vendor}(I), \text{Counterparty}(T)) \ge 70.0$$

### 4.3 Pass 3: Bounded Subset-Sum Bundled Matching
To resolve bundled payments where a single bank transaction $T$ settles multiple open invoices $I_1, I_2, \dots, I_k$ from the same counterparty within an active date window:
$$\sum_{j=1}^{k} \text{Amount}(I_j) = \text{Amount}(T)$$
Where $2 \le k \le K_{\max}$ (default $K_{\max} = 4$), and for all $j$:
$$|\text{Date}(I_j) - \text{Date}(T)| \le 15 \text{ days}$$
$$\text{RapidFuzzTokenSetRatio}(\text{Vendor}(I_j), \text{Counterparty}(T)) \ge 65.0$$

### 4.4 Pass 4: Fee & FX Variance Tolerance
To accommodate bank wire transfer fees and payment gateway processing deductions:
$$0 < |\text{Amount}(T) - \text{Amount}(I)| \le \Delta_{\text{fee}} \quad (\text{Default } \Delta_{\text{fee}} = \$25.00)$$
$$|\text{Date}(I) - \text{Date}(T)| \le 5 \text{ days}$$
$$\text{RapidFuzzTokenSetRatio}(\text{Vendor}(I), \text{Counterparty}(T)) \ge 80.0$$
The discrepancy is explicitly flagged as `variance_amount` with type `FEE_ADJUSTED`.

---

## 5. Interface Specifications

### 5.1 Model Context Protocol (MCP) Interface
The server communicates over `stdio` and implements 5 tools:
1. `ingest_sources(paths: list[str]) -> dict`: Scans directory paths for invoices and bank statements, returning normalized ingestion summaries.
2. `run_deterministic_match(tolerance_days: int = 3, fee_tolerance: float = 25.0) -> dict`: Executes deterministic Passes 1–4, returning match counts and summary metrics.
3. `get_unmatched_records() -> dict`: Returns lists of remaining unmatched invoices and unmatched bank transactions with metadata.
4. `resolve_ambiguity(invoice_id: str, tx_id: str, reasoning: str, fee_amount: float = 0.0) -> dict`: Enters an audited agent resolution linking the items.
5. `export_reconciliation_report(format: str = "excel") -> str`: Produces a complete reconciliation artifact (Excel `.xlsx` or Markdown `.md`).

### 5.2 REST API Interface (FastAPI Web Server)
- `GET /api/status`: System health, active LLM provider, and OCR engine status.
- `POST /api/upload`: Multi-part file upload for invoices (PDF/images) and statements (CSV/XLSX).
- `POST /api/reconcile`: Trigger multi-pass reconciliation with custom parameters.
- `GET /api/results`: Retrieve categorized reconciliation results (Matched, Ambiguous, Unmatched).
- `POST /api/resolve`: Accept or provide custom resolution for ambiguous candidates.
- `GET /api/export`: Stream generated Excel workbook with full conditional formatting and audit tabs.

---

## 6. Security, Privacy, and Auditability

- **Zero Remote Leakage in Local Mode:** When configured with Ollama or local OCR, zero payload bytes are transmitted over external networks.
- **Audit Immutability:** Every match record retains:
  - Source document paths and extracted text hashes.
  - Applied rule identifier (`P1_EXACT`, `P2_FUZZY`, `P3_BUNDLED`, `P4_FEE`, `AGENT_RESOLVED`).
  - Original timestamp and reasoning narrative.
- **Input Sanitization:** Filenames and paths are sanitized against path traversal vulnerabilities (`Path.resolve()` check within workspace sandbox).
