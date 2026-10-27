#!/usr/bin/env python3
"""Convert the ANSTRAT Markdown review states into AAP modular AsciiDoc.

The converter deliberately owns only the files recorded in its manifest.  It
can therefore be run repeatedly against an exported Git revision without
deleting unrelated documentation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})(\w*)\s*$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
MD_LINK_RE = re.compile(r"(!?)\[([^]]+)\]\(([^)]+)\)")


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def slugify(value: str) -> str:
    value = re.sub(r"^\d+(?:-\d+)?-", "", value.lower())
    value = re.sub(r"^existing-", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value


def plain_title(value: str) -> str:
    value = re.sub(r"\{#[^}]+\}\s*$", "", value).strip()
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\s+Existing doc:.*$", "", value, flags=re.I)
    return value.strip(" *_")


def find_title(lines: list[str]) -> tuple[str, int]:
    for number, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            return plain_title(match.group(2)), number
    raise ValueError("Markdown document has no heading")


def heading_text(line: str) -> tuple[int, str] | None:
    match = HEADING_RE.match(line)
    if not match:
        return None
    return len(match.group(1)), plain_title(match.group(2))


def remove_review_sections(lines: list[str]) -> list[str]:
    """Drop review-only material that is not publishable customer content."""
    result: list[str] = []
    dropping = False
    for line in lines:
        heading = heading_text(line)
        if heading and heading[0] <= 2:
            dropping = heading[1].strip().lower() in {"sme queries", "review notes"}
        if not dropping:
            if re.match(r"^\s*###\s+Existing doc:", line, re.I):
                continue
            result.append(line)
    return result


def extract_abstract(lines: list[str]) -> tuple[str, list[str]]:
    """Extract the first paragraph under ``Short description``."""
    start = None
    end = len(lines)
    for number, line in enumerate(lines):
        heading = heading_text(line)
        if heading and heading[0] == 2 and heading[1].lower() == "short description":
            start = number
            continue
        if start is not None and number > start:
            heading = heading_text(line)
            if heading and heading[0] <= 2:
                end = number
                break
    if start is None:
        for line in lines:
            if line.strip() and not heading_text(line):
                return line.strip(), lines
        return "", lines

    section = lines[start + 1 : end]
    paragraph: list[str] = []
    for line in section:
        if not line.strip():
            if paragraph:
                break
            continue
        if line.lstrip().startswith(("- ", "* ", "1. ")):
            break
        paragraph.append(line.strip())
    abstract = " ".join(paragraph).strip()
    return abstract, lines[:start] + lines[end:]


def protect_code_spans(text: str) -> tuple[str, dict[str, str]]:
    values: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        key = f"\x00CODE{len(values)}\x00"
        values[key] = match.group(0)
        return key

    return re.sub(r"`[^`\n]+`", replace, text), values


def restore_code_spans(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def unescape_markdown(text: str) -> str:
    return re.sub(r"\\([\\`*_{}\[\]()#+.!|>=\-])", r"\1", text)


def replace_names(text: str, replacements: list[dict]) -> str:
    protected, values = protect_code_spans(text)
    attributes: dict[str, str] = {}

    def protect_attribute(match: re.Match[str]) -> str:
        key = f"\x00ATTRIBUTE{len(attributes)}\x00"
        attributes[key] = match.group(0)
        return key

    protected = re.sub(r"\{[^}\n]+\}", protect_attribute, protected)
    replacement_map = {rule["find"]: rule["replace"] for rule in replacements}
    patterns = sorted(replacement_map, key=len, reverse=True)
    if patterns:
        matcher = re.compile("|".join(re.escape(pattern) for pattern in patterns))
        protected = matcher.sub(lambda match: replacement_map[match.group(0)], protected)
    for key, value in attributes.items():
        protected = protected.replace(key, value)
    return restore_code_spans(protected, values)


def split_table_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells = re.split(r"(?<!\\)\|", line)
    return [cell.strip() for cell in cells]


def convert_table(lines: list[str], start: int) -> tuple[list[str], int]:
    headers = split_table_row(lines[start])
    number = start + 2
    rows: list[list[str]] = []
    while number < len(lines) and lines[number].strip() and lines[number].lstrip().startswith("|"):
        rows.append(split_table_row(lines[number]))
        number += 1
    result = ['[options="header"]', "|===", "|" + " |".join(headers)]
    for row in rows:
        result.append("|" + " |".join(row))
    result.append("|===")
    return result, number


def inline_adoc(text: str, config: dict, aliases: dict[str, str], allow_aliases: bool = True) -> str:
    text = re.sub(r"!\[([^]]*)\]\(([^)]+)\)", r"image::\2[\1]", text)

    def link(match: re.Match[str]) -> str:
        label, url = match.group(2), match.group(3)
        if match.group(1):
            return f"image::{url}[{label}]"
        return f"link:{url}[{label}]"

    text = MD_LINK_RE.sub(link, text)
    bold_values: dict[str, str] = {}

    def protect_bold(match: re.Match[str]) -> str:
        key = f"\x00BOLD{len(bold_values)}\x00"
        bold_values[key] = f"*{match.group(1)}*"
        return key

    text = re.sub(r"\*\*([^*\n]+)\*\*", protect_bold, text)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)\*(?!\*)", r"_\1_", text)
    for key, value in bold_values.items():
        text = text.replace(key, value)
    text = re.sub(r"~~([^~\n]+)~~", r"#\1#", text)
    text = unescape_markdown(text)

    if allow_aliases:
        protected, values = protect_code_spans(text)
        for label, target in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
            protected = re.sub(
                rf"(?<![\w{{]){re.escape(label)}(?![\w}}])",
                f"xref:{target}[{label}]",
                protected,
            )
        text = restore_code_spans(protected, values)
    text = replace_names(text, config["name_replacements"])
    return text


def convert_body(lines: list[str], config: dict, kind: str) -> list[str]:
    result: list[str] = []
    in_fence = False
    fence = ""
    language = ""
    number = 0
    procedure_sections = {
        "prerequisites": ".Prerequisites",
        "context": ".Context",
        "procedure": ".Procedure",
        "verification": ".Verification",
        "result": ".Result",
        "post-requisites": ".Post-requisites",
    }
    while number < len(lines):
        line = lines[number].rstrip()
        fence_match = FENCE_RE.match(line)
        if fence_match:
            if not in_fence:
                in_fence = True
                fence = "----"
                language = fence_match.group(3)
                result.append(f"[source,{language}]" if language else "[source]")
                result.append(fence)
            else:
                result.append(fence)
                in_fence = False
                fence = ""
                language = ""
            number += 1
            continue
        if in_fence:
            result.append(unescape_markdown(line))
            number += 1
            continue

        if (
            line.lstrip().startswith("|")
            and number + 1 < len(lines)
            and TABLE_SEPARATOR_RE.match(lines[number + 1])
        ):
            table, number = convert_table(lines, number)
            result.extend(inline_adoc(row, config, {}, allow_aliases=False) for row in table)
            continue

        heading = heading_text(line)
        if heading:
            level, title = heading
            if kind == "PROCEDURE" and level == 2 and title.lower() in procedure_sections:
                result.append(procedure_sections[title.lower()])
            else:
                result.append("=" * level + " " + inline_adoc(title, config, {}, allow_aliases=False))
            number += 1
            continue

        if re.match(r"^\s*([-*_])(?:\s*\1){2,}\s*$", line):
            number += 1
            continue

        if line.lstrip().startswith("> "):
            result.append("NOTE: " + inline_adoc(line.lstrip()[2:], config, {}))
        elif re.match(r"^\s*\*\*IMPORTANT:\*\*", line, re.I):
            result.append(re.sub(r"^\s*\*IMPORTANT:\*", "IMPORTANT:", inline_adoc(line, config, {}), flags=re.I))
        elif re.match(r"^\s*\*\*NOTE:\*\*", line, re.I):
            result.append(re.sub(r"^\s*\*NOTE:\*", "NOTE:", inline_adoc(line, config, {}), flags=re.I))
        elif re.match(r"^(\s*)[-*]\s+", line):
            line = re.sub(r"^(\s*)[-*]\s+", r"\1* ", line)
            result.append(inline_adoc(line, config, config["aliases"]))
        elif re.match(r"^(\s*)\d+[.)]\s+", line):
            line = re.sub(r"^(\s*)\d+[.)]\s+", r"\1. ", line)
            result.append(inline_adoc(line, config, config["aliases"]))
        else:
            result.append(inline_adoc(line, config, config["aliases"]))
        number += 1
    if in_fence:
        result.append(fence)
    return result


def target_for(slug: str, rule: dict, kind: str) -> tuple[str, str]:
    prefix = {"CONCEPT": "con", "PROCEDURE": "proc", "REFERENCE": "ref", "ASSEMBLY": "assembly"}[kind]
    identifier = rule.get("id", f"{prefix}-{slug}")
    target = rule.get("target", f"downstream/assemblies/analytics/{identifier}.adoc")
    return target, identifier


def discover_documents(source: Path, config: dict) -> list[dict]:
    rules = config["rules"]
    documents: list[dict] = []
    for path in sorted(source.rglob("index.md")):
        relative = path.relative_to(source)
        parts = relative.parts
        if len(parts) == 2 and parts[0].startswith("01-anstrat-1587-docs"):
            continue
        parent_slug = slugify(path.parent.name)
        parent_slug = config.get("source_slug_aliases", {}).get(parent_slug, parent_slug)
        if not parent_slug or parent_slug.startswith("phase-") or parent_slug.startswith("understanding-the-"):
            continue
        rule = rules.get(parent_slug, {})
        kind = rule.get("type", "REFERENCE")
        target, identifier = target_for(parent_slug, rule, kind)
        documents.append({
            "source": path,
            "relative": str(relative),
            "slug": parent_slug,
            "type": kind,
            "target": target,
            "id": identifier,
            "rule": rule,
        })
    order = {slug: number for number, slug in enumerate(config["source_order"])}
    documents.sort(key=lambda item: (order.get(item["slug"], 10000), item["relative"]))
    return documents


def active_document_ids(source: Path, documents: list[dict], config: dict) -> set[str]:
    """Read the review bundle's top-level ToC to decide what the assembly lists."""
    root_files = sorted((source / "01-anstrat-1587-docs").glob("index.md"))
    if not root_files:
        return {document["id"] for document in documents}
    text = root_files[0].read_text(encoding="utf-8")
    labels = [unescape_markdown(label).strip() for label in re.findall(r"\[([^]]+)\]\([^)]*\)", text)]
    aliases = config["aliases"]
    active = {aliases[label] for label in labels if label in aliases}
    return active or {document["id"] for document in documents}


def render_document(document: dict, config: dict) -> str:
    raw_lines = document["source"].read_text(encoding="utf-8").splitlines()
    _, title_line = find_title(raw_lines)
    title = find_title(raw_lines)[0]
    body = remove_review_sections(raw_lines[title_line + 1 :])
    abstract, body = extract_abstract(body)
    if not abstract:
        abstract = "See the following information about " + title.lower() + "."
    title = inline_adoc(title, config, {}, allow_aliases=False)
    abstract = inline_adoc(abstract, config, {}, allow_aliases=False)
    body_adoc = convert_body(body, {**config, "aliases": config["aliases"]}, document["type"])
    header = [f":_mod-docs-content-type: {document['type']}", "", f"[id=\"{document['id']}\"]", "", f"= {title}", "", "[role=\"_abstract\"]", abstract, ""]
    content = header + body_adoc
    while content and not content[-1].strip():
        content.pop()
    return "\n".join(content) + "\n"


def include_path(target: str) -> str:
    path = Path(target)
    if target.startswith("downstream/assemblies/analytics/"):
        return "analytics/" + path.name
    if target.startswith("downstream/modules/platform/"):
        return "platform/" + path.name
    raise ValueError(f"cannot create title include for {target}")


def render_api_assembly(documents: list[dict], config: dict, active_ids: set[str]) -> str:
    lines = [":_mod-docs-content-type: ASSEMBLY", "", '[id="assembly-analytics-api"]', "", "= Analytics API", "", '[role="_abstract"]', "This assembly describes the architecture, configuration, collection behavior, and API used to retrieve Automation Analytics data.", ""]
    for document in documents:
        if document["id"] not in active_ids:
            continue
        if document["rule"].get("omit_from_api_assembly"):
            continue
        lines.append(f"include::{include_path(document['target'])}[leveloffset=+1]")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_master(config: dict, active_ids: set[str]) -> str:
    title = config["title"]
    lines = [":_mod-docs-content-type: ASSEMBLY", ":imagesdir: images", ":numbered:", ":toclevels: 1", "", ":experimental:", "", "include::attributes/attributes.adoc[]", "", f"= {title['book_title']}", "", '[role="_abstract"]', title["abstract"], "", "include::{Boilerplate}[]", ""]
    if title.get("static_includes_before_api", True):
        for include in title["static_includes"]:
            lines.append(f"include::{include}[leveloffset=+1]")
            lines.append("")
    lines.append(f"include::analytics/{Path(title['assembly']).name}[leveloffset=+1]")
    return "\n".join(lines).rstrip() + "\n"


def unlink_managed(repo: Path, manifest_path: Path, keep: set[str], dry_run: bool) -> None:
    if not manifest_path.exists():
        return
    old = load_json(manifest_path).get("files", [])
    for relative in old:
        if relative in keep:
            continue
        path = repo / relative
        if path.is_symlink() or path.is_file():
            if dry_run:
                print(f"remove {relative}")
            else:
                path.unlink()


def write_file(repo: Path, relative: str, content: str, dry_run: bool) -> None:
    path = repo / relative
    if dry_run:
        print(f"write {relative}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def ensure_platform_link(repo: Path, dry_run: bool) -> str:
    relative = "downstream/titles/analytics/platform"
    path = repo / relative
    if not path.exists() and not path.is_symlink():
        if dry_run:
            print(f"link {relative} -> ../../modules/platform")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to("../../modules/platform")
    return relative


def convert(source: Path, repo: Path, config: dict, dry_run: bool) -> int:
    documents = discover_documents(source, config)
    if not documents:
        raise ValueError(f"no Markdown documents found under {source}")
    active_ids = active_document_ids(source, documents, config)
    available_ids = {document["id"] for document in documents}
    active_aliases = {
        label: target
        for label, target in config["aliases"].items()
        if target in available_ids
    }
    active_config = {**config, "aliases": active_aliases}
    generated: dict[str, str] = {}
    for document in documents:
        generated[document["target"]] = render_document(document, active_config)
    assembly_target = config["title"]["assembly"]
    generated[assembly_target] = render_api_assembly(documents, config, active_ids)
    generated[config["title"]["master"]] = render_master(config, active_ids)
    generated[ensure_platform_link(repo, dry_run)] = ""

    manifest_path = repo / config["managed_manifest"]
    keep = set(generated) | set(config.get("preserved_files", []))
    unlink_managed(repo, manifest_path, keep, dry_run)
    for relative, content in generated.items():
        if relative == "downstream/titles/analytics/platform":
            continue
        write_file(repo, relative, content, dry_run)
    if not dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps({"files": sorted(keep)}, indent=2) + "\n", encoding="utf-8")
    print(f"converted {len(documents)} Markdown documents")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Markdown state directory")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("md_to_adoc.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config = load_json(args.config)
    try:
        return convert(args.source.resolve(), args.repo_root.resolve(), config, args.dry_run)
    except (OSError, ValueError, KeyError) as error:
        print(f"md_to_adoc: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
