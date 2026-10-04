"""
CLI for ingesting PDFs into the RAG database.
Usage:
    python -m rag.ingestion.cli --pdf path/to/file.pdf --class-num 8 --subject mathematics

The LLM provider and embedding model come from backend settings (LLM_PROVIDER etc.).
"""
import argparse
import asyncio
import sys

from rag.ingestion.pipeline import ingest_pdf


def main():
    parser = argparse.ArgumentParser(description="Ingest a Bihar Board PDF into the RAG database")
    parser.add_argument("--pdf", required=True, help="Path to the PDF file")
    parser.add_argument("--class-num", type=int, required=True, help="Class number (5-10)")
    parser.add_argument("--subject", required=True, help="Subject name")
    parser.add_argument("--db-url", default="postgresql+asyncpg://rusty:localdev@localhost:5432/rusty_dev")
    args = parser.parse_args()

    if not 1 <= args.class_num <= 10:
        print(f"Error: class-num must be 1-10, got {args.class_num}")
        sys.exit(1)

    count = asyncio.run(
        ingest_pdf(
            pdf_path=args.pdf,
            class_num=args.class_num,
            subject=args.subject,
            database_url=args.db_url,
        )
    )
    print(f"\nIngested {count} chunks successfully.")


if __name__ == "__main__":
    main()
