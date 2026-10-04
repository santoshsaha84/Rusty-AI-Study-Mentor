"""
Ingestion pipeline: PDF → pages → semantic chunks → metadata → embeddings → DB.
"""
import asyncio
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from rag.ingestion.pdf_extractor import extract_pages
from rag.ingestion.chunker import chunk_pages
from rag.ingestion.metadata_tagger import tag_chunk
from rag.retrieval.llm_client import embed_batch
from rag.ingestion.language_detect import detect_language


def _embed_model_name() -> str:
    from app.core.config import get_settings

    settings = get_settings()
    return settings.ollama_embed_model if settings.llm_provider == "ollama" else settings.vertex_embed_model


def _contextual_header(
    class_num: int,
    subject: str,
    chapter: str | None,
    section_type: str,
    page_num: int | None,
) -> str:
    """Prepend contextual header to each chunk for better embedding quality."""
    parts = [f"Class {class_num}", subject.replace("_", " ").title()]
    if chapter:
        parts.append(chapter.title())
    if section_type and section_type != "explanation":
        parts.append(section_type.replace("_", " ").title())
    if page_num:
        parts.append(f"Page {page_num}")
    return " | ".join(parts)


async def ingest_pdf(
    pdf_path: str,
    class_num: int,
    subject: str,
    database_url: str,
    batch_size: int = 10,
    source_pdf_name: str | None = None,
) -> int:
    pdf_name = source_pdf_name or Path(pdf_path).name
    print(f"[1/5] Extracting text from {pdf_name} (block-level with table detection)...")
    pages = extract_pages(pdf_path)
    print(f"       Extracted {len(pages)} pages")

    print("[2/5] Semantic chunking (section boundary detection)...")
    chunks = chunk_pages(pages)
    print(f"       Created {len(chunks)} chunks")

    print("[3/5] Tagging metadata + contextual headers...")
    sample_text = " ".join(c["text"][:200] for c in chunks[:5])
    pdf_language = detect_language(sample_text)
    print(f"       Detected language: {pdf_language}")
    tagged = []
    for c in chunks:
        meta = tag_chunk(
            text=c["text"],
            class_num=class_num,
            subject=subject,
            chapter=c["chapter"],
            source_pdf=pdf_name,
            page_num=c.get("page_num"),
            language=pdf_language,
        )
        header = _contextual_header(
            class_num, subject, c["chapter"], meta.section_type, c.get("page_num")
        )
        meta.text_content = f"{header}\n\n{meta.text_content}"
        tagged.append(meta)

    print(f"[4/5] Embedding {len(tagged)} chunks with {_embed_model_name()} (multilingual)...")
    all_texts = [t.text_content for t in tagged]
    all_embeddings = []
    for i in range(0, len(all_texts), batch_size):
        batch = all_texts[i:i + batch_size]
        embs = await embed_batch(batch)
        all_embeddings.extend(embs)
        print(f"       Embedded {min(i + batch_size, len(all_texts))}/{len(all_texts)}")

    print("[5/5] Inserting into database...")
    engine = create_async_engine(database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        await session.execute(
            text("DELETE FROM chunks WHERE source_pdf = :pdf"),
            {"pdf": pdf_name},
        )

        for meta, embedding in zip(tagged, all_embeddings):
            chunk_id = uuid.uuid4()
            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
            await session.execute(
                text("""
                    INSERT INTO chunks (
                        chunk_id, class_num, subject, chapter, section_type,
                        math_type, contains_formula, latex_equations,
                        diagram_image_path, text_content, embedding, source_pdf, page_num, language
                    ) VALUES (
                        :chunk_id, :class_num, :subject, :chapter, :section_type,
                        :math_type, :contains_formula, :latex_equations,
                        :diagram_image_path, :text_content, :embedding, :source_pdf, :page_num, :language
                    )
                """),
                {
                    "chunk_id": str(chunk_id),
                    "class_num": meta.class_num,
                    "subject": meta.subject,
                    "chapter": meta.chapter,
                    "section_type": meta.section_type,
                    "math_type": meta.math_type,
                    "contains_formula": meta.contains_formula,
                    "latex_equations": meta.latex_equations or None,
                    "diagram_image_path": meta.diagram_image_path,
                    "text_content": meta.text_content,
                    "embedding": embedding_str,
                    "source_pdf": meta.source_pdf,
                    "page_num": meta.page_num,
                    "language": meta.language,
                },
            )
        await session.commit()

    await engine.dispose()
    print(f"Done! Ingested {len(tagged)} chunks from {pdf_name}")
    return len(tagged)
