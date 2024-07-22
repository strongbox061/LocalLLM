"""Temporary CLI bridge to Docling for local document processing."""

import argparse
import json
import sys
from pathlib import Path


def process_document(input_path: str, output_dir: str) -> None:
    """Run Docling on a single document and write results to output_dir."""
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    source = Path(input_path)
    result = converter.convert(str(source))

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Write Markdown export
    md_path = out / f"{source.stem}.md"
    md_path.write_text(result.document.export_to_markdown())

    # Write JSON metadata
    meta = {
        "source": str(source),
        "pages": result.input.page_count,
        "title": result.document.metadata.title or "",
    }
    (out / f"{source.stem}.json").write_text(json.dumps(meta, indent=2))

    print(f"Converted {source} -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI wrapper for Docling")
    parser.add_argument("input", help="Path to the document to convert")
    parser.add_argument("-o", "--output", default="./docling_output", help="Output directory")
    args = parser.parse_args()

    if not Path(args.input).is_file():
        print(f"Error: '{args.input}' not found", file=sys.stderr)
        sys.exit(1)

    process_document(args.input, args.output)


if __name__ == "__main__":
    main()
