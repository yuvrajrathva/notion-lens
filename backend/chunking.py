DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 100


def chunk_text(
    text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP
) -> list[str]:
    """Fixed-size naive chunking: slides a fixed-width character window over the
    text with a fixed overlap. No sentence/token awareness."""
    text = text.strip()
    if not text:
        return []
    if chunk_size <= overlap:
        raise ValueError("chunk_size must be greater than overlap")

    chunks = []
    start = 0
    text_len = len(text)
    stride = chunk_size - overlap

    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == text_len:
            break
        start += stride

    return chunks


def _split_into_sections(blocks: list[dict]) -> list[dict]:
    """Groups blocks into heading-bounded sections. A heading of any level
    starts a new section and closes any open sections at the same or a
    deeper level (a new h2 ends the previous h2 and any h3 under it, but not
    an ancestor h1). Content before the first heading becomes its own
    leading section with heading_path=[] rather than being dropped."""
    sections = []
    stack: list[tuple[int, str]] = []
    current = None

    for block in blocks:
        level = block.get("heading_level")
        if level:
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, block["text"]))
            if current is not None:
                sections.append(current)
            current = {"heading_path": [text for _, text in stack], "blocks": [block]}
        else:
            if current is None:
                current = {"heading_path": [], "blocks": []}
            current["blocks"].append(block)

    if current is not None:
        sections.append(current)

    return sections


def _render_section(blocks: list[dict]) -> str:
    """Renders a section's blocks back to readable text, preserving structure
    that the old flat-string extraction lost: code fences with language,
    real numbered-list numbers, checkbox state, and markdown tables."""
    lines = []
    i = 0
    n = len(blocks)

    while i < n:
        block = blocks[i]
        block_type = block["type"]

        if block_type == "table":
            i += 1
            rows = []
            while i < n and blocks[i]["type"] == "table_row":
                rows.append(blocks[i])
                i += 1
            if rows:
                lines.append("| " + " | ".join(rows[0]["cells"]) + " |")
                if block.get("has_column_header"):
                    lines.append("| " + " | ".join(["---"] * len(rows[0]["cells"])) + " |")
                for row in rows[1:]:
                    lines.append("| " + " | ".join(row["cells"]) + " |")
            continue

        if block_type == "table_row":
            # Orphaned row with no preceding `table` block in this section;
            # render defensively rather than dropping it.
            if block["cells"]:
                lines.append("| " + " | ".join(block["cells"]) + " |")
        elif block_type in ("heading_1", "heading_2", "heading_3"):
            lines.append(f"{'#' * block['heading_level']} {block['text']}")
        elif block_type == "bulleted_list_item":
            lines.append(f"- {block['text']}")
        elif block_type == "numbered_list_item":
            lines.append(f"{block['list_index']}. {block['text']}")
        elif block_type == "to_do":
            mark = "x" if block.get("checked") else " "
            lines.append(f"- [{mark}] {block['text']}")
        elif block_type == "quote":
            lines.append(f"> {block['text']}")
        elif block_type == "code":
            language = block.get("language") or ""
            lines.append(f"```{language}\n{block['text']}\n```")
        elif block["text"]:
            lines.append(block["text"])

        i += 1

    return "\n".join(lines)


def _build_breadcrumb(page_title: str | None, heading_path: list[str]) -> str:
    title = page_title or "(untitled)"
    if not heading_path:
        return f"Page: {title}"
    return f"Page: {title} > Section: {' > '.join(heading_path)}"


def chunk_blocks(
    blocks: list[dict],
    page_title: str | None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[dict]:
    """Structure-aware, contextual chunking: groups blocks into heading-bounded
    sections, renders each back to text, and only falls back to the raw
    sliding window (`chunk_text`) within a single oversized section - so a
    sub-split can never cross into a neighboring section. Each resulting
    chunk is prefixed with a "Page: X > Section: Y" breadcrumb before
    embedding, and carries heading/block metadata for storage.

    Returns a list of {"content": str, "metadata": dict}, in document order.
    """
    results = []

    for section in _split_into_sections(blocks):
        rendered = _render_section(section["blocks"])
        if not rendered.strip():
            continue

        breadcrumb = _build_breadcrumb(page_title, section["heading_path"])
        block_types = sorted({b["type"] for b in section["blocks"]})
        block_ids = [b["id"] for b in section["blocks"]]

        for piece in chunk_text(rendered, chunk_size=chunk_size, overlap=overlap):
            results.append(
                {
                    "content": f"{breadcrumb}\n\n{piece}",
                    "metadata": {
                        "breadcrumb": breadcrumb,
                        "heading_path": section["heading_path"],
                        "block_types": block_types,
                        "block_ids": block_ids,
                    },
                }
            )

    return results
