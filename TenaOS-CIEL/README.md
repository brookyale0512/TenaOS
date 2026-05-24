# TenaOS-CIEL

Raw [CIEL](https://openconceptlab.org/orgs/CIEL) concept store: 58,687
concepts in SQLite with FTS5 full-text indexes. TenaAgent uses this for
exact concept lookup, bundle expansion, and form-builder seeding —
work that does not need a vector model.

For semantic search over the same CIEL concepts, see
`TenaOS-KnowledgeBase-CIEL` (a Qdrant collection populated from the
same source).

## Purpose

| Use case | Why CIEL SQLite, not Qdrant |
| --- | --- |
| Exact lookup by name / synonym | Sub-millisecond, deterministic |
| Bundle expansion | Need authoritative concept IDs |
| Form-builder seed lists | Hard requirement: must include trusted clinical reference sets |

## Layout

```
TenaOS-CIEL/
├── README.md
├── requirements.txt
├── ciel_search/          Python package: models, pipeline, service, validation
└── ciel_search_cli.py    Local CLI for ad-hoc queries
```

## Runtime artifact (host-mounted, gitignored)

The ~1.7 GB `ciel_search.sqlite3` file is **not** committed. There are
two ways to get it.

### Easiest: fetch the prebuilt SQLite from HuggingFace

```bash
bash scripts/fetch-models.sh
# downloads to ./tenaos-bootstrap/ciel/ciel_search.sqlite3
```

The bootstrap script pulls the file from
[`beza4588/tenaos-ciel-search-sqlite`](https://huggingface.co/beza4588/tenaos-ciel-search-sqlite).
Direct download without the script:

```bash
hf download beza4588/tenaos-ciel-search-sqlite \
  ciel_search.sqlite3 --local-dir ./ciel --repo-type model
```

Then set the path in `.env`:

```bash
TENAOS_CIEL_SQLITE_PATH=$(pwd)/ciel/ciel_search.sqlite3
```

### Build from an OpenConceptLab export

If you need a newer CIEL release or want to verify the pipeline, build
the SQLite locally from a fresh OCL export:

```bash
python3 -m ciel_search.pipeline --export /path/to/ciel_export.json --out ./ciel_search.sqlite3
```

## CLI

```bash
python3 ciel_search_cli.py "uncomplicated malaria"
```
