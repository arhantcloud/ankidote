# Anki Architecture & Flow Diagrams

These diagrams capture the current tech stack and the basic request/data flow
across Anki's four layers (Svelte/TS frontend → PyQt GUI → Python lib → Rust
core), plus the protobuf-based IPC that ties them together.

## 1. Tech stack & layered architecture

```mermaid
graph TD
    subgraph Frontend["Web Frontend — ts/ (Svelte + TypeScript)"]
        SK["SvelteKit pages\nts/routes/*\n(graphs, deck-options, card-info,\nchange-notetype, imports, image-occlusion)"]
        EDR["Editor + Reviewer apps\nts/editor/, ts/reviewer/"]
        GENTS["@generated/backend\nout/ts/lib/generated/backend.ts\n+ post.ts (protobuf over HTTP)"]
    end

    subgraph Qt["PyQt GUI — qt/aqt/ (Python + PyQt6)"]
        MW["AnkiQt main window\nmain.py (state machine)"]
        SCREENS["Screens: deckbrowser.py,\noverview.py, reviewer.py, toolbar.py"]
        DIALOGS["Dialogs: browser/, editor.py,\nclayout.py, models.py, deckoptions.py,\npreferences.py, sync.py, import_export/"]
        WV["AnkiWebView\nwebview.py (embeds web UI)"]
        SRV["MediaServer\nmediasrv.py (Flask + Waitress)"]
    end

    subgraph Pylib["Python Library — pylib/anki/"]
        COL["Collection / col API"]
        BE["RustBackend\n_backend.py + _backend_generated.py"]
        BRIDGE["rsbridge (PyO3)\npylib/rsbridge/lib.rs"]
    end

    subgraph Rust["Core Rust Layer — rslib/src/"]
        BACKEND["Backend shell\nbackend/ + services.rs dispatch"]
        DOMAINS["Domain services:\nscheduler/ (FSRS), card/, notes/,\nnotetype/, decks/, deckconfig/,\nsearch/, stats/, media/,\nimport_export/, sync/, tags/"]
        STORAGE["SQLite storage\nstorage/*"]
        FSRS["fsrs crate (external)\nscheduler/fsrs/"]
    end

    PROTO["proto/anki/*.proto\n(shared contract → codegen for\nRust + Python + TypeScript)"]

    SK --> GENTS
    EDR --> GENTS
    GENTS -->|"POST /_anki/{method}\n(protobuf bytes)"| SRV
    MW --> SCREENS --> WV
    MW --> DIALOGS --> WV
    WV -->|"loads pages / assets"| SRV
    WV -->|"pycmd / bridgeCommand (QWebChannel)"| MW
    SRV -->|"custom handlers / proxy"| BE
    MW --> COL --> BE
    BE --> BRIDGE --> BACKEND
    BACKEND --> DOMAINS --> STORAGE
    DOMAINS --> FSRS
    PROTO -. generates .-> GENTS
    PROTO -. generates .-> BE
    PROTO -. generates .-> BACKEND
```

## 2. Study flow (user journey) & state machine

```mermaid
flowchart LR
    START([App launch\nAnkiApp]) --> PROF[Profile picker\nprofiles.py]
    PROF --> DB[Deck list / home\ndeckbrowser.py]
    DB -->|click deck| OV[Deck overview\noverview.py]
    OV -->|Study / S| REV[Reviewer\nreviewer.py]
    REV -->|answer card| SCHED[(Scheduler\nrslib scheduler/ + FSRS)]
    SCHED -->|next card| REV
    REV -->|queue empty| CONGRATS[Congrats page\nts/routes/congrats]
    CONGRATS --> OV
    DB -->|Decks D| DB
    DB -->|Add A| ADD[Add cards\naddcards.py + editor.py]
    DB -->|Browse B| BROWSE[Browser\nbrowser/browser.py]
    DB -->|Stats T| STATS[Graphs\nts/routes/graphs]
    DB -->|Sync Y| SYNC[Sync\nsync.py + mediasync.py]
    OV -->|gear / options| DOPT[Deck options\nts/routes/deck-options]

    classDef qt fill:#e8f0fe,stroke:#4285f4;
    classDef web fill:#fef7e0,stroke:#f9ab00;
    classDef rust fill:#fce8e6,stroke:#ea4335;
    class DB,OV,REV,ADD,BROWSE,SYNC,PROF qt;
    class CONGRATS,STATS,DOPT web;
    class SCHED rust;
```

## 3. A backend RPC round-trip (e.g. answering a card)

```mermaid
sequenceDiagram
    participant U as User
    participant WV as Web UI (Svelte/TS)
    participant SRV as mediasrv.py (Flask)
    participant PY as RustBackend (_backend.py)
    participant BR as rsbridge (PyO3)
    participant RS as rslib Backend + services
    participant DB as SQLite (storage/)

    U->>WV: Click answer button
    WV->>SRV: POST /_anki/{method} (protobuf)
    SRV->>PY: custom handler or col._backend.{method}_raw
    PY->>BR: Backend.command(service_idx, method_idx, bytes)
    BR->>RS: run_service_method(...)
    RS->>DB: read/write cards, revlog
    RS-->>BR: protobuf response bytes
    BR-->>PY: bytes
    PY-->>SRV: bytes
    SRV-->>WV: 200 application/binary (or 204)
    WV-->>U: Render next card
```
