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
| Bundle expansion (SMART DAK) | Need authoritative concept IDs |
| Form-builder seed lists | Hard requirement: must include the WHO DAK reference set |

## Layout

```
TenaOS-CIEL/
├── README.md
├── requirements.txt
├── ciel_search/          Python package: models, pipeline, service, validation
└── ciel_search_cli.py    Local CLI for ad-hoc queries
```

## Runtime artifact (host-mounted, gitignored)

The 1.7 GB `ciel_search.sqlite3` file is **not** committed. Point
TenaAgent at it via:

```bash
export TENAOS_CIEL_ROOT=/var/www/TenaOS/TenaOS-CIEL
export TENAOS_CIEL_SQLITE=$TENAOS_CIEL_ROOT/ciel_search.sqlite3
```

## Build the SQLite from an OCL export

```bash
python3 -m ciel_search.pipeline --export /path/to/ciel_export.json --out ./ciel_search.sqlite3
```

## CLI

```bash
python3 ciel_search_cli.py "uncomplicated malaria"
```
