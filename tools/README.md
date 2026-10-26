# Markdown to AsciiDoc migration

`md_to_adoc.py` converts an exported ANSTRAT Markdown state into the existing
`analytics` title. It uses the bundle's top-level ToC to decide which topics
belong in the generated API assembly, while retaining unlisted source topics
as reusable generated files. It applies the repository's modular document headers,
AsciiDoc attributes, IDs, cross-references, and title/assembly includes. The
four Markdown topics that already have AAP counterparts are updated in place:

- `assembly-data-dictionary.adoc`
- `proc-controller-configure-analytics.adoc`
- `ref-controller-analytics-gathering.adoc`
- `ref-controller-data-collection-details.adoc`

Review-only `SME Queries` sections are intentionally omitted from generated
customer content. The source Markdown remains the review record.

## Convert a state

From the repository root:

```sh
python3 tools/md_to_adoc.py --source ../markdown
```

For a historical state, point `--source` at a directory exported from the
Markdown repository, for example:

```sh
git -C ../markdown archive fef3cc1 | tar -x -C /tmp/anstrat-baseline
python3 tools/md_to_adoc.py --source /tmp/anstrat-baseline
```

The managed manifest permits later runs to remove only generated files that
disappeared from the source state. It never cleans unrelated AAP documentation.
Commit each converted state separately when preserving review history.
