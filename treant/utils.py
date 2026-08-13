import logging
from typing import Any

logger = logging.getLogger(__name__)


def separate_content(
    content_list: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[tuple[str, int]]]:
    """
    Separate the content after parsing.

    Returns
    -------
    text_content:
        All text blocks joined with double newlines (unchanged from before).
    multimodal_items:
        Non-text items, each annotated with ``_content_list_index`` holding
        their original position in *content_list*.
    text_blocks:
        List of ``(text, original_index)`` pairs for every non-empty text
        block, in document order.  Used by the processor to assign accurate
        document-order positions to text chunks so that
        ``build_parent_child_chunk`` windows across text *and* multimodal
        chunks in true document order.
    """
    text_blocks: list[tuple[str, int]] = []
    multimodal_items: list[dict[str, Any]] = []

    for index, item in enumerate(content_list):
        content_type = item.get("type", "text")

        if content_type == "text":
            text = item.get("text", "")
            if text.strip():
                text_blocks.append((text, index))
        else:
            # Multimodal content (image, table, equation, etc.)
            multimodal_item = dict(item)
            multimodal_item.setdefault("_content_list_index", index)
            multimodal_items.append(multimodal_item)

    text_content = "\n\n".join(text for text, _ in text_blocks)

    logger.info("Content separation complete:")
    logger.info(f"  - Text content length: {len(text_content)} characters")
    logger.info(f"  - Multimodal items count: {len(multimodal_items)}")

    modal_types: dict[str, int] = {}
    for item in multimodal_items:
        modal_type = item.get("type", "unknown")
        modal_types[modal_type] = modal_types.get(modal_type, 0) + 1

    if modal_types:
        logger.info(f"  - Multimodal type distribution: {modal_types}")

    return text_content, multimodal_items, text_blocks
