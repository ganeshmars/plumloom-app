import json
import re
from typing import Dict, Any
from app.core.logging_config import logger



def escape_markdown(text):
    """Escapes Markdown special characters in a string."""
    if not text:
        return ""
    # Escape characters: \ ` * _ { } [ ] ( ) # + - . ! >
    # Adjusted to be more comprehensive for general text.
    escape_chars = r"([\\`*_{}\[\]()#+.!-])"
    return re.sub(escape_chars, r"\\\1", text)


def render_node_to_markdown(node, list_stack=None):
    """
    Recursively renders a Prosemirror node to Markdown.
    list_stack is used to keep track of list type (bullet/ordered) and depth for correct prefixing.
    """
    if list_stack is None:
        list_stack = []

    node_type = node.get("type")
    content_md = ""  # This will store rendered content of children nodes
    attrs = node.get("attrs", {})

    # 1. Render children's content first (if any)
    if "content" in node and node["content"]:
        child_content_parts = []
        if node_type == "orderedList":
            new_list_level = len(list_stack)
            list_stack.append({"type": "ordered", "counter": 1, "level": new_list_level})
            for child_node in node["content"]:
                child_content_parts.append(render_node_to_markdown(child_node, list_stack))
            list_stack.pop()
        elif node_type == "bulletList":
            new_list_level = len(list_stack)
            list_stack.append({"type": "bullet", "level": new_list_level})
            for child_node in node["content"]:
                child_content_parts.append(render_node_to_markdown(child_node, list_stack))
            list_stack.pop()
        else:
            for child_node in node["content"]:
                child_content_parts.append(render_node_to_markdown(child_node, list_stack))
        content_md = "".join(child_content_parts)

    # 2. Handle current node type
    if node_type == "doc":
        return content_md.strip()

    elif node_type == "paragraph":
        stripped_content = content_md.strip()
        if not stripped_content:  # Empty paragraph
            return "\n"  # Will be condensed later if multiple, or provide minimal spacing
        return stripped_content + "\n\n"

    elif node_type == "text":
        text_content = node.get("text", "")  # Raw text
        current_val = escape_markdown(text_content)  # Default: escaped text

        if "marks" in node:
            for mark_spec in reversed(node["marks"]):  # Apply from inside out
                mark_type = mark_spec.get("type")
                mark_attrs = mark_spec.get("attrs", {})

                if mark_type == "code":
                    # For 'code' mark, use the original unescaped text_content
                    current_val = f"`{text_content}`"
                elif mark_type == "bold":
                    current_val = f"**{current_val}**"
                elif mark_type == "italic":
                    current_val = f"*{current_val}*"
                elif mark_type == "strike":
                    current_val = f"~~{current_val}~~"
                elif mark_type == "link":
                    href = escape_markdown(mark_attrs.get("href", ""))  # Escape URL too
                    title_val = mark_attrs.get("title")
                    title_str = f' "{escape_markdown(title_val)}"' if title_val else ""
                    current_val = f"[{current_val}]({href}{title_str})"
                # Ignored marks (like textStyle) will let current_val pass through
        return current_val

    elif node_type == "heading":
        level = attrs.get("level", 1)
        return f"{'#' * level} {content_md.strip()}\n\n"

    elif node_type == "blockquote":
        if not content_md.strip(): return ">\n\n"
        quoted_lines = [f"> {line}" for line in content_md.strip().split('\n')]
        return "\n".join(quoted_lines) + "\n\n"

    elif node_type == "callout":  # From previous iteration, seems fine
        icon_type = attrs.get("iconType")
        prefix_text = ""
        if icon_type == "info":
            prefix_text = "ℹ️ "
        elif icon_type == "warning":
            prefix_text = "⚠️ "

        if not content_md.strip():
            return f"> {prefix_text}\n\n"

        lines_of_content = content_md.strip().split('\n')
        output_lines = []
        first_content_line = True
        for line in lines_of_content:
            if first_content_line:
                output_lines.append(f"> {prefix_text}{line}")
                first_content_line = False
            else:
                output_lines.append(f"> {line}")
        return "\n".join(output_lines) + "\n\n"

    elif node_type == "hardBreak":
        return "  \n"

    elif node_type == "horizontalRule":
        return "---\n\n"

    elif node_type == "bulletList" or node_type == "orderedList":
        # content_md already contains formatted list items
        if not content_md.strip(): return "\n"  # An empty list might still occupy a line
        return content_md  # List items manage their own newlines

    elif node_type == "listItem":
        if not list_stack: return content_md.strip() + "\n"  # Fallback
        current_list_ctx = list_stack[-1]
        indent = "  " * current_list_ctx["level"]
        prefix = ""
        if current_list_ctx["type"] == "bullet":
            prefix = f"{indent}* "
        elif current_list_ctx["type"] == "ordered":
            prefix = f"{indent}{current_list_ctx['counter']}. "
            current_list_ctx["counter"] += 1

        stripped_content = content_md.strip()
        if not stripped_content: return prefix.rstrip() + "\n"  # Empty item: `* `

        lines = stripped_content.split('\n')
        formatted_item_lines = [f"{prefix}{lines[0]}"]

        # Continuation indent logic (simplified)
        continuation_indent_chars = "  "  # Default 2 spaces for bullet list continuation
        if current_list_ctx["type"] == "ordered":
            continuation_indent_chars = "   "  # Default 3 spaces for ordered list (e.g., after "1. ")
            if current_list_ctx['counter'] - 1 >= 10: continuation_indent_chars = "    "  # After "10. "

        continuation_indent = indent + continuation_indent_chars

        for line in lines[1:]:
            formatted_item_lines.append(f"{continuation_indent}{line}")
        return "\n".join(formatted_item_lines) + "\n"

    elif node_type == "codeBlock":
        language = attrs.get("language", "")
        raw_code = ""
        if "content" in node:
            for child_text_node in node.get("content", []):
                if child_text_node.get("type") == "text":
                    raw_code += child_text_node.get("text", "")
        return f"```{language}\n{raw_code.strip()}\n```\n\n"

    elif node_type == "image":
        src = attrs.get("src", "")
        alt = escape_markdown(attrs.get("alt", ""))
        title = escape_markdown(attrs.get("title", ""))
        if title:
            return f'![{alt}]({escape_markdown(src)} "{title}")\n\n'
        else:
            return f"![{alt}]({escape_markdown(src)})\n\n"

    # ****** ADDED CUSTOM_TABLE HANDLER ******
    elif node_type == "custom-table":
        md_parts_for_this_node = []

        # 1. Try to render table from attrs.data
        table_attrs_data = attrs.get("data")
        rendered_table_from_attrs = ""
        if table_attrs_data and isinstance(table_attrs_data, dict):
            columns = table_attrs_data.get("columns")
            rows_data = table_attrs_data.get("rows")

            if columns and rows_data and isinstance(columns, list) and isinstance(rows_data, list) and len(columns) > 0:
                table_lines = []
                # Header row
                header_names = [escape_markdown(col.get("name", " ")) for col in columns]  # Use space if name is empty
                table_lines.append("| " + " | ".join(header_names) + " |")
                # Separator row
                table_lines.append("| " + " | ".join(["---"] * len(columns)) + " |")

                # Data rows
                for row_item in rows_data:
                    cells_in_row_data = row_item.get("cells")
                    if not cells_in_row_data or not isinstance(cells_in_row_data, dict):
                        # Add an empty row if cell data is missing, to maintain table structure
                        table_lines.append("| " + " | ".join([" "] * len(columns)) + " |")
                        continue

                    row_values = []
                    for col in columns:
                        col_id = col.get("id")
                        cell_info = cells_in_row_data.get(col_id)
                        cell_value_str = " "  # Default to empty space for a cell

                        if cell_info and isinstance(cell_info, dict):
                            cell_type = cell_info.get("type")
                            val = cell_info.get("value")
                            if cell_type == "text":
                                cell_value_str = escape_markdown(str(val) if val is not None else "")
                            elif cell_type == "checkbox":
                                if val is True:
                                    cell_value_str = "[x]"
                                elif val is False:
                                    cell_value_str = "[ ]"
                                else:
                                    cell_value_str = "[ ]"  # null or other as unchecked
                            else:  # Fallback for unknown cell types
                                cell_value_str = escape_markdown(str(val) if val is not None else "")

                        # Ensure internal pipes in cell content are escaped for Markdown table
                        row_values.append(cell_value_str.replace("|", r"\|"))
                    table_lines.append("| " + " | ".join(row_values) + " |")

                if table_lines:
                    rendered_table_from_attrs = "\n".join(table_lines)

        if rendered_table_from_attrs:
            md_parts_for_this_node.append(rendered_table_from_attrs)

        # 2. Render the direct "content" of the custom-table node (which is in content_md)
        # content_md already includes its own block spacing (e.g. \n\n from paragraphs)
        stripped_content_md = content_md.strip()
        if stripped_content_md:
            md_parts_for_this_node.append(stripped_content_md)

        if not md_parts_for_this_node:
            return "\n\n"  # Empty custom-table node, but still provide block spacing

        # Join table (if any) and direct content (if any) with a blank line, then add final block spacing
        return "\n\n".join(md_parts_for_this_node) + "\n\n"


    # --- Fallback for other unknown nodes ---
    elif "content" in node and content_md.strip():  # If it's a container with content
        return content_md.strip() + "\n\n"  # Treat as a block, ensure spacing
    elif "text" in node:  # An unknown node that seems to be primarily text-based
        return escape_markdown(node.get("text", "")) + "\n\n"  # Treat as a paragraph

    return ""  # Ignore completely unknown leaf nodes or nodes with no renderable parts


def tiptap_json_to_markdown(tiptap_json_input):
    try:
        if isinstance(tiptap_json_input, str):
            doc = json.loads(tiptap_json_input)
        elif isinstance(tiptap_json_input, dict):
            doc = tiptap_json_input
        else:
            raise ValueError("Input must be a JSON string or a Python dictionary.")

        if doc.get("type") != "doc":
            raise ValueError("JSON does not seem to be a Tiptap document.")

        markdown_output = render_node_to_markdown(doc)

        # Post-processing: Clean up excessive newlines
        markdown_output = re.sub(r'\n{3,}', '\n\n', markdown_output)
        return markdown_output.strip()

    except json.JSONDecodeError:
        return "Error: Invalid JSON input."
    except ValueError as ve:
        return f"Error: {ve}"
    except Exception as e:
        import traceback
        # print(f"An unexpected error occurred: {e}\n{traceback.format_exc()}") # For debugging
        return f"An unexpected error occurred: {e}"



def extract_text_from_json(content: Dict[str, Any]) -> str:
    """Extract plain text from JSON content."""
    text_parts = [] # Use a list for efficiency

    def process_node(node):
        if isinstance(node, dict):
            node_type = node.get("type")
            # Handle text nodes
            if node_type == "text" and "text" in node:
                text_parts.append(node["text"])

            # Handle image nodes (add alt/title text if available)
            elif node_type == "image" and "attrs" in node:
                attrs = node.get("attrs", {})
                if "alt" in attrs and attrs["alt"]:
                    text_parts.append(attrs["alt"])
                elif "title" in attrs and attrs["title"]: # Use title if alt is empty/missing
                    text_parts.append(attrs["title"])
                # Optionally add a placeholder like "[Image]" if no text is available

            # Recursively process content if it exists and is a list
            node_content = node.get("content")
            if isinstance(node_content, list):
                for child in node_content:
                    process_node(child)
        elif isinstance(node, list): # Handle cases where content is directly a list
                for child_node in node:
                    process_node(child_node)


    # Start processing from the root content structure
    # TipTap often has a root 'doc' type with 'content' list
    if isinstance(content, dict) and content.get("type") == "doc" and "content" in content:
            process_node(content)
    elif isinstance(content, list): # Handle if the root is directly a list of nodes
            process_node(content)
    else:
            logger.warning("Unexpected TipTap root structure. Trying basic node processing.")
            process_node(content) # Attempt processing anyway

    # Join parts with spaces, handling potential multiple spaces
    full_text = " ".join(text_parts).strip()
    # Optional: Clean up multiple spaces resulting from joins
    full_text = " ".join(full_text.split())
    return full_text


def extract_text_from_json_v2(content: Dict[str, Any]) -> str:
        """Extract plain text from JSON content."""
        if not content or not isinstance(content, dict):
            logger.error(f"Invalid content type: {type(content)}")
            return ""
            
        text_parts = []
        
        def process_node(node):
            if not isinstance(node, dict):
                return
            
            node_type = node.get("type")
            
            # Handle root doc type
            if node_type == "doc":
                if "content" in node and isinstance(node["content"], list):
                    for child in node["content"]:
                        process_node(child)
                return
            
            # Handle text nodes
            if node_type == "text":
                if "text" in node:
                    text = node["text"]
                    text_parts.append(text)
                    # Add space after text if not punctuation
                    if text and not text[-1] in ",.!?:;":
                        text_parts.append(" ")
                return
            
            # Handle block elements - add spacing
            if node_type in ["paragraph", "heading"]:
                if "content" in node and isinstance(node["content"], list):
                    for child in node["content"]:
                        process_node(child)
                    text_parts.append("\n\n")
                return
                
            # Handle list items
            if node_type == "listItem":
                text_parts.append("• ")
                if "content" in node and isinstance(node["content"], list):
                    for child in node["content"]:
                        process_node(child)
                    text_parts.append("\n")
                return
                
            # Handle code blocks
            if node_type == "codeBlock":
                if "content" in node and isinstance(node["content"], list):
                    text_parts.append("\n```\n")
                    for child in node["content"]:
                        process_node(child)
                    text_parts.append("\n```\n")
                return
                
            # Handle tables
            if node_type == "tableCell":
                if "content" in node and isinstance(node["content"], list):
                    for child in node["content"]:
                        process_node(child)
                    text_parts.append(" | ")
                return
                
            if node_type == "tableRow":
                if "content" in node and isinstance(node["content"], list):
                    for child in node["content"]:
                        process_node(child)
                    text_parts.append("\n")
                return
            
            # Handle images
            if node_type == "image" and "attrs" in node:
                attrs = node["attrs"]
                if "alt" in attrs:
                    text_parts.append(f"[Image: {attrs['alt']}]")
                text_parts.append("\n")
                return
            
            # Handle blockquotes
            if node_type == "blockquote":
                text_parts.append("> ")
                if "content" in node and isinstance(node["content"], list):
                    for child in node["content"]:
                        process_node(child)
                    text_parts.append("\n")
                return
            
            # Recursively process any other content
            if "content" in node and isinstance(node["content"], list):
                for child in node["content"]:
                    process_node(child)
        
        # Process the root node
        process_node(content)
        
        # Join all parts and clean up
        text = "".join(text_parts)
        # Clean up multiple newlines and spaces
        text = "\n".join(line.strip() for line in text.split("\n"))
        text = " ".join(text.split())
        
        logger.info(f"Extracted text length: {len(text)}")
        return text