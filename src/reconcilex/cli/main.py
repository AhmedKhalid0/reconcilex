"""
Standalone Command Line Interface for ReconcileX.
Empowers accountants and automated workflows without writing code.
"""

from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from reconcilex import __version__
from reconcilex.config import settings
from reconcilex.core.agent.reconciler_agent import ReconcilerAgent
from reconcilex.core.extraction.ocr_engine import OCREngine
from reconcilex.core.extraction.pdf_parser import PDFInvoiceParser
from reconcilex.core.extraction.statement_parser import BankStatementParser
from reconcilex.core.matching.matcher import DeterministicMatcher
from reconcilex.core.reporting.excel_exporter import ExcelReportExporter
from reconcilex.core.reporting.markdown_exporter import MarkdownReportExporter

app = typer.Typer(
    name="reconcilex",
    help="ReconcileX: Hybrid Privacy-First Financial Reconciliation Platform.",
    add_completion=False,
)
console = Console()


@app.command("audit")
def audit(
    invoices_dir: Path = typer.Option(
        ..., "--invoices", "-i", help="Directory containing invoice PDFs or images."
    ),
    statement_file: Path = typer.Option(
        ..., "--statement", "-s", help="Path to bank statement CSV or Excel file."
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output path for reconciliation Excel report."
    ),
    tolerance_days: int = typer.Option(
        3, "--tolerance", "-t", help="Date matching tolerance window in days."
    ),
    ai_resolve: bool = typer.Option(
        True, "--ai-resolve/--no-ai", help="Run agentic LLM reasoning on remaining discrepancies."
    ),
):
    """Execute end-to-end reconciliation between invoices and bank statement."""
    console.print(
        Panel.fit(
            f"[bold blue]ReconcileX Audit Engine[/bold blue] v{__version__}\n"
            f"[dim]Invoices:[/dim] {invoices_dir} | [dim]Statement:[/dim] {statement_file}",
            border_style="blue",
        )
    )

    if not invoices_dir.exists() or not invoices_dir.is_dir():
        console.print(f"[bold red]Error:[/bold red] Invoices directory '{invoices_dir}' not found.")
        raise typer.Exit(code=1)

    if not statement_file.exists() or not statement_file.is_file():
        console.print(f"[bold red]Error:[/bold red] Bank statement file '{statement_file}' not found.")
        raise typer.Exit(code=1)

    # 1. Ingest Invoices
    with console.status("[cyan]Ingesting invoice documents (PDFs & Scanned Images)...[/cyan]"):
        invoices = []
        for file_p in invoices_dir.iterdir():
            if file_p.suffix.lower() == ".pdf":
                try:
                    invoices.append(PDFInvoiceParser.extract(file_p))
                except Exception as e:
                    console.print(f"[yellow]Warning:[/yellow] Could not parse {file_p.name}: {e}")
            elif file_p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                try:
                    invoices.append(OCREngine.extract_image_invoice(file_p))
                except Exception as e:
                    console.print(f"[yellow]Warning:[/yellow] Could not parse {file_p.name}: {e}")

    # 2. Ingest Bank Statement
    with console.status("[cyan]Parsing bank statement feeds...[/cyan]"):
        try:
            transactions = BankStatementParser.parse(statement_file)
        except Exception as e:
            console.print(f"[bold red]Statement Parse Error:[/bold red] {e}")
            raise typer.Exit(code=1)

    console.print(f" Loaded [bold green]{len(invoices)}[/bold green] invoices and [bold green]{len(transactions)}[/bold green] bank transactions.")

    # 3. Deterministic Match Pass
    with console.status("[cyan]Running 4-pass deterministic reconciliation engine...[/cyan]"):
        matcher = DeterministicMatcher(date_tolerance=tolerance_days)
        report = matcher.reconcile(invoices, transactions)

    # 4. Agentic Disambiguation Pass (Optional)
    if ai_resolve and (report.unmatched_invoices or report.unmatched_transactions):
        with console.status("[magenta]Engaging AI Auditor for semantic alias and fee disambiguation...[/magenta]"):
            agent = ReconcilerAgent()
            report = agent.resolve_edge_cases(report)

    # 5. Display Summary Table
    s = report.summary
    table = Table(title="Reconciliation Executive Summary", border_style="dim")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Match Rate", f"[bold green]{s.match_rate_percentage:.1f}%[/bold green]")
    table.add_row("Matched Pairs", str(s.matched_count))
    table.add_row("Unmatched Invoices", f"[yellow]{s.unmatched_invoices_count}[/yellow]")
    table.add_row("Unmatched Bank Feeds", f"[red]{s.unmatched_transactions_count}[/red]")
    table.add_row("Total Invoiced Value", f"${s.total_invoiced_amount:,.2f}")
    table.add_row("Total Cleared Value", f"${s.total_bank_amount:,.2f}")
    table.add_row("Net Variance", f"${s.net_variance:,.2f}")

    console.print(table)

    # 6. Export Results
    out_path = output or Path(f"./data/exports/reconciliation_{statement_file.stem}.xlsx")
    ExcelReportExporter.export(report, out_path)
    console.print(f"\n[bold green]Success:[/bold green] Audit report generated at: [underline]{out_path.resolve()}[/underline]")


@app.command("dashboard")
def dashboard(
    host: str = typer.Option(settings.host, "--host", "-h", help="Host interface to bind to."),
    port: int = typer.Option(settings.port, "--port", "-p", help="Port number for web server."),
):
    """Launch the accountant-friendly Web Dashboard."""
    import uvicorn
    console.print(
        Panel.fit(
            f"[bold green]ReconcileX Web Dashboard[/bold green]\n"
            f"Access URL: [bold underline]http://{host}:{port}[/bold underline]\n"
            f"[dim]Press CTRL+C to stop.[/dim]",
            border_style="green",
        )
    )
    uvicorn.run("reconcilex.web.app:app", host=host, port=port, reload=False)


@app.command("mcp")
def mcp():
    """Start the ReconcileX Model Context Protocol (MCP) server over stdio."""
    from reconcilex.mcp.server import run_server
    run_server()


@app.command("generate-samples")
def generate_samples(
    output_dir: Path = typer.Option(
        Path("./sample_data"), "--output", "-o", help="Target folder for test dataset."
    )
):
    """Generate realistic synthetic bilingual (English & Arabic) invoices and bank statements."""
    from reconcilex.utils.sample_generator import SampleDataGenerator
    console.print(f"[cyan]Generating synthetic dataset in {output_dir}...[/cyan]")
    invoices_dir, stmt_file = SampleDataGenerator.generate_all(output_dir)
    console.print(f"[bold green]Success![/bold green] Generated samples:")
    console.print(f" - Invoices: [underline]{invoices_dir}[/underline]")
    console.print(f" - Statement: [underline]{stmt_file}[/underline]")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(f"[bold blue]ReconcileX[/bold blue] v{__version__} - Hybrid Financial Reconciliation Platform")
        console.print("Run [bold cyan]reconcilex --help[/bold cyan] for available commands.")


if __name__ == "__main__":
    app()
