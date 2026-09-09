# Architecture & Technical Design Document
## ReconcileX: Privacy-First Hybrid Financial Reconciliation Engine

**Author:** Ahmed Khaled (Ahmed Algendy) <contact@ahmedalgendy.com>  
**Version:** 1.0.0  
**Date:** March 2026  

---

## 1. Architectural Philosophy: The Hybrid Deterministic-Agentic Model

Modern generative AI models are exceptional at natural language parsing, fuzzy contextual reasoning, and visual entity extraction. However, they are fundamentally probabilistic and prone to hallucination when performing arithmetic calculations. 

**ReconcileX** solves this via a strict **Separation of Concerns**:
- **Math is 100% Deterministic:** Balancing debits/credits, computing variances, searching for bundled payment combinations, and verifying ledger equations is executed purely by deterministic algorithms (`pandas`, custom `Bounded Subset-Sum`, and `rapidfuzz`). An LLM is never permitted to perform unverified arithmetic.
- **Extraction & Context are Agentic:** Reading noisy scanned receipts, understanding Arabic tax forms, resolving cryptic bank narrations (`"VNDR*9481 AMZN MKTPL"`), and diagnosing wire fee deductions is handled by Vision-Language Models (VLMs) and constrained LLM agents.

```mermaid
graph TD
    A[Invoices: PDF, Images, Scans] --> B[Extraction & Normalization Layer]
    C[Bank Statements: CSV, XLSX] --> B
    
    B -->|Normalized Invoices & Transactions| D[Deterministic Matching Engine]
    
    subgraph "Pure Math (Zero Hallucination)"
        D --> D1[Pass 1: Exact 1-to-1 Match]
        D1 --> D2[Pass 2: Relaxed Fuzzy Match]
        D2 --> D3[Pass 3: Bundled 1-to-N Subset-Sum]
        D3 --> D4[Pass 4: Fee & Variance Tolerance]
    end
    
    D4 -->|70-85% Matched Automatically| E[Reconciliation Ledger]
    D4 -->|15-30% Edge Cases & Ambiguities| F[LLM Agent Intermediary]
    
    subgraph "Reasoning & Context (Local / Cloud)"
        F --> F1[Counterparty Disambiguation]
        F --> F2[Tax & Wire Fee Diagnosis]
        F --> F3[Unmatched Documentation Audit]
    end
    
    F -->|Audited Match Decisions with Reasoning| E
    E --> G[Export Layer: Interactive Web UI, Excel Report, MCP Response]
```

---

## 2. Ingestion & Extraction Architecture

### 2.1 Digital Invoices vs. Scanned Receipts
The ingestion pipeline dynamically inspects document mime-types and structural streams:

1. **Digital PDFs (Native Vector Text):**
   - Parsed directly using `pdfplumber`.
   - Extracts structured text coordinates, tables, and bounding boxes.
   - Zero GPU or API cost; 100% deterministic accuracy on digital invoices.
2. **Scanned Images & Photographed Receipts (Arabic & Multilingual):**
   - **Local-First Tier (Default):**
     - Modern OCR engine (`EasyOCR` / `Surya-OCR`) with native Arabic & English language packs.
     - Local Multimodal VLM via Ollama (`qwen2.5vl:7b` or `minicpm-v`). The image is passed directly to the local model to emit validated JSON schema without data escaping the host machine.
   - **Cloud Fallback Tier (Optional):**
     - Low-latency Vision APIs (Google Gemini 2.0 Flash, Claude 3.5 Haiku, OpenAI GPT-4o-mini).

```mermaid
sequenceDiagram
    autonumber
    participant Doc as Raw Invoice File
    participant Router as Ingestion Router
    participant Plumber as pdfplumber Engine
    participant VLM as Local VLM / OCR
    participant Cloud as Cloud Vision API
    participant Normalizer as Schema Normalizer

    Doc->>Router: Submit File (.pdf, .png, .jpg)
    alt Digital PDF with Vector Text
        Router->>Plumber: Extract text & tables
        Plumber-->>Normalizer: Raw text stream
    else Scanned Document or Image
        alt Local Mode Enabled
            Router->>VLM: Run local OCR / Qwen2.5-VL
            VLM-->>Normalizer: Extracted text & key-values
        else Cloud Fallback
            Router->>Cloud: Call Vision API (Gemini/Claude)
            Cloud-->>Normalizer: Structured JSON
        end
    end
    Normalizer->>Normalizer: Validate Pydantic InvoiceRecord
```

---

## 3. Deterministic Matching Engine: The 4-Pass Algorithm

### Pass 1: Exact Match (High Confidence $\ge 95\%$)
- Criteria: `Amount_inv == Amount_bank`, `|Date_inv - Date_bank| <= 3 days`, `FuzzyScore >= 80`.
- Purpose: Instantly lock in clean, standard transactions.

### Pass 2: Relaxed 1-to-1 Match (Confidence $\ge 85\%$)
- Criteria: `Amount_inv == Amount_bank`, `|Date_inv - Date_bank| <= 7 days`, `TokenSetRatio >= 70`.
- Purpose: Capture transactions with weekend/holiday clearing delays and slight counterparty variations.

### Pass 3: Bundled 1-to-N Matching (Combinatorial Subset-Sum)
- In corporate finance, a buyer often pays 2, 3, or 4 invoices in a single bulk wire transfer.
- Traditional regex or 1-to-1 matching completely fails here.
- ReconcileX runs a **Bounded Subset-Sum Algorithm**:
  For an unmatched bank outflow $T$, group pending invoices by vendor fuzzy similarity. Find any subset $S \subseteq \text{Invoices}$ such that:
  $$\sum_{I \in S} \text{Amount}(I) = \text{Amount}(T) \quad \text{where } 2 \le |S| \le 4$$
- The complexity is strictly bounded by counterparty grouping and date horizons ($\le 15$ days), ensuring microsecond execution times.

### Pass 4: Fee & FX Tolerance Match
- Flags transactions where an international wire transfer fee (e.g., $15–$25) or payment processing fee was deducted at settlement.
- Criteria: `|Amount_bank - Amount_inv| <= 25.00`, with high vendor match and close dates.

---

## 4. MCP Server Architecture (Model Context Protocol)

ReconcileX implements the official Model Context Protocol (MCP) standard over `stdio`, allowing AI agents inside developer tools (Antigravity, Cursor, Claude Desktop) to invoke native reconciliation tools:

```mermaid
graph LR
    subgraph Host Client
        Agent[Antigravity / Claude Agent]
    end

    subgraph "ReconcileX MCP Server (stdio)"
        RPC[JSON-RPC Dispatcher]
        T1[ingest_sources]
        T2[run_deterministic_match]
        T3[get_unmatched_records]
        T4[resolve_ambiguity]
        T5[export_reconciliation_report]
    end

    Agent <-->|stdin / stdout JSON-RPC| RPC
    RPC --> T1
    RPC --> T2
    RPC --> T3
    RPC --> T4
    RPC --> T5
```

---

## 5. Web Dashboard Architecture (Human-Crafted UI/UX)

The Web Dashboard is engineered specifically for non-technical users and corporate accountants:
- **Zero Frontend Build Complexity:** Built using modern vanilla CSS/JS served directly by FastAPI. No Node.js runtime, npm installs, or webpack compilation required on the accountant's machine.
- **Aesthetic Principles:**
  - **Light Mode as First-Class Default:** Calming off-white/zinc tones (`#f8fafc`, `#ffffff`, `#0f172a` text).
  - **Dark Mode Toggle:** Soft charcoal (`#121214`) with crisp slate borders.
  - **High-Density Data Grid:** Monospace tabular numbers (`font-variant-numeric: tabular-nums`) for perfect decimal alignment.
  - **Dual-Pane Reconciliation View:** Invoices on the left, Bank Feed on the right, linked by tactile status chips.
  - **Instant Excel Export:** Generates formatted `.xlsx` files with colored status cells and automated sum formulas.
