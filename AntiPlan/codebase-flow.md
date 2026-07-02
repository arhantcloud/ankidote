# Anki Codebase Flow & Reference

A practical map of where each tab, feature, endpoint, and data flow lives.
Anki is built in four cooperating layers that communicate through protobuf:

| Layer          | Language            | Location      | Role                                      |
| -------------- | ------------------- | ------------- | ----------------------------------------- |
| Web frontend   | Svelte / TypeScript | `ts/`         | Rich UI pages embedded in webviews        |
| Desktop GUI    | Python / PyQt6      | `qt/aqt/`     | Native windows, dialogs, app lifecycle    |
| Python library | Python              | `pylib/anki/` | Wraps the Rust core, exposes `Collection` |
| Core engine    | Rust                | `rslib/src/`  | Scheduling, storage, sync, business logic |
| IPC contract   | Protobuf            | `proto/anki/` | Shared schema → generates Rust/Py/TS APIs |

---

## 1. The big picture: how a request flows

1. **User clicks something** in a webview (Svelte page, editor, reviewer) or a Qt widget.
2. **Web UI** either:
   - calls a generated function from `@generated/backend` → `POST /_anki/{method}` (protobuf), or
   - sends a `pycmd()` / `bridgeCommand()` over QWebChannel to Python (for Qt-side actions).
3. **`mediasrv.py`** (Flask + Waitress, localhost) receives the POST and either runs a
   custom Python handler or proxies to `col._backend.{method}_raw`.
4. **`_backend.py`** (`RustBackend`) calls into `rsbridge` (PyO3) with a numeric
   `(service_idx, method_idx)` + serialized protobuf.
5. **`rslib`** decodes the protobuf, runs the matching domain service against the
   SQLite collection, and returns protobuf bytes back up the chain.

The protobuf files in `proto/anki/` are the single source of truth — build scripts
generate Rust traits, Python snake_case methods (`out/pylib/anki/_backend_generated.py`),
and TypeScript functions (`out/ts/lib/generated/backend.ts`) from them.

---

## 2. Desktop GUI — tabs, screens & navigation

**Main window:** `AnkiQt` in `qt/aqt/main.py`. It is a state machine
(`moveToState`) that swaps the central webview between screens. The window is a
vertical stack: top toolbar → main webview → bottom toolbar.

**App entry / dialog registry:** `qt/aqt/__init__.py` (`AnkiApp`, `DialogManager`).

### Top toolbar (primary navigation)

Defined in `qt/aqt/toolbar.py` (`TopWebView` + `Toolbar`); rendered via
`data/web/js/toolbar.ts`.

| Tab / button | Shortcut | Handler                       | Destination              |
| ------------ | -------- | ----------------------------- | ------------------------ |
| Decks        | `D`      | `moveToState("deckBrowser")`  | `deckbrowser.py`         |
| Add          | `A`      | `mw.onAddCard()`              | `addcards.py`            |
| Browse       | `B`      | `mw.onBrowse()`               | `browser/browser.py`     |
| Stats        | `T`      | `mw.onStats()`                | `stats.py` → graphs page |
| Sync         | `Y`      | `mw.on_sync_button_clicked()` | `sync.py`                |

### Core states (the central webview)

| State                        | Controller                       | Notes                                           |
| ---------------------------- | -------------------------------- | ----------------------------------------------- |
| `startup` / `profileManager` | `main.py` + `profiles.py`        | Profile picker before a collection opens        |
| `deckBrowser`                | `deckbrowser.py` (`DeckBrowser`) | Home deck list; web assets `js/deckbrowser.ts`  |
| `overview`                   | `overview.py` (`Overview`)       | Per-deck overview + study button                |
| `review`                     | `reviewer.py` (`Reviewer`)       | Card review; bottom bar `js/reviewer-bottom.ts` |
| `resetRequired`              | `main.py`                        | Shown when a background op invalidates the view |

`moveToState()` runs `_{old}Cleanup`, fires `gui_hooks.state_will_change` /
`state_did_change`, then `_{state}State`. Study flow:
`deckBrowser → overview → review → (congrats) → overview`.

### Feature dialogs (managed by `aqt.dialogs`)

| Feature                  | File                               | UI form                  |
| ------------------------ | ---------------------------------- | ------------------------ |
| Add cards                | `qt/aqt/addcards.py`               | `forms/addcards.ui`      |
| Browser                  | `qt/aqt/browser/browser.py`        | `forms/browser.ui`       |
| Edit current (in review) | `qt/aqt/editcurrent.py`            | `forms/editcurrent.ui`   |
| Filtered deck config     | `qt/aqt/filtered_deck.py`          | `forms/filtered_deck.ui` |
| Stats / graphs           | `qt/aqt/stats.py` (`NewDeckStats`) | SvelteKit `graphs`       |
| Preferences              | `qt/aqt/preferences.py`            | `forms/preferences.ui`   |
| Add-ons manager          | `qt/aqt/addons.py`                 | `forms/addons.ui`        |
| About                    | `qt/aqt/about.py`                  | —                        |
| Media sync log           | `qt/aqt/mediasync.py`              | `forms/synclog.ui`       |

### Browser internals (`qt/aqt/browser/`)

| Part                  | File                                                 |
| --------------------- | ---------------------------------------------------- |
| Window                | `browser.py`                                         |
| Card/note table       | `table/` (`table.py`, `model.py`, `state.py`)        |
| Sidebar tree + search | `sidebar/` (`tree.py`, `searchbar.py`, `toolbar.py`) |
| Find & replace        | `find_and_replace.py` (`forms/findreplace.ui`)       |
| Find duplicates       | `find_duplicates.py` (`forms/finddupes.ui`)          |
| Preview               | `previewer.py` (`forms/preview.ui`)                  |
| Card info panel       | `card_info.py` → SvelteKit `card-info`               |

### Editor, note types, card layout

| Feature                   | File                                                          |
| ------------------------- | ------------------------------------------------------------- |
| Rich-text editor (shared) | `qt/aqt/editor.py` (`Editor`, `EditorWebView`) → `ts/editor/` |
| Note types manager        | `qt/aqt/models.py` (`forms/models.ui`)                        |
| Card templates / layout   | `qt/aqt/clayout.py` (`forms/template.ui`)                     |
| Field editor              | `qt/aqt/fields.py` (`forms/fields.ui`)                        |
| Change note type          | `qt/aqt/changenotetype.py` → SvelteKit `change-notetype`      |

### Deck options, study, import/export, sync

| Feature                 | File                                                       |
| ----------------------- | ---------------------------------------------------------- |
| Deck options (modern)   | `qt/aqt/deckoptions.py` → SvelteKit `deck-options/{id}`    |
| Deck options (legacy)   | `qt/aqt/deckconf.py` (`forms/dconf.ui`)                    |
| Custom study            | `qt/aqt/customstudy.py` (`forms/customstudy.ui`)           |
| Empty cards cleanup     | `qt/aqt/emptycards.py`                                     |
| Collection sync         | `qt/aqt/sync.py`                                           |
| Media sync worker       | `qt/aqt/mediasync.py`                                      |
| Import (modern)         | `qt/aqt/import_export/importing.py`, `import_dialog.py`    |
| Export (modern)         | `qt/aqt/import_export/exporting.py` (`forms/exporting.ui`) |
| Progress / blocking ops | `qt/aqt/progress.py`                                       |
| DB check / media check  | `qt/aqt/dbcheck.py`, `qt/aqt/mediacheck.py`                |

### Web embedding (`qt/aqt/webview.py`)

`AnkiWebView` (subclass of `QWebEngineView`) renders web UI two ways:

- **Inline HTML (`stdHtml`)** — Python builds HTML, posts it to `mediasrv`
  (`set_page_html`), webview loads `/_anki/legacyPageData?id=...`. Used by deck
  browser, overview, reviewer, toolbar, editor, card-layout preview.
- **Full SvelteKit pages (`load_sveltekit_page` / `load_ts_page`)** — navigates
  to `http://127.0.0.1:{port}/{route}` (or Vite HMR `:5173` in dev). Used by deck
  options, graphs, imports, etc.

JS↔Python bridge: an injected QWebChannel script exposes `pycmd()`/`bridgeCommand()`;
`set_bridge_command(handler, context)` wires clicks to Python.

---

## 3. Web frontend — SvelteKit pages (`ts/routes/`)

Built with the SvelteKit static adapter → `out/sveltekit/` → copied to
`qt/_aqt/data/web/sveltekit/`. SSR/prerender disabled (`ts/routes/+layout.ts`).
The server rewrites top-level paths (e.g. `/graphs`) to the SvelteKit build via
`is_sveltekit_page()` in `mediasrv.py`.

| Page                | Route                               | Functionality                                                                    | Opened from                      |
| ------------------- | ----------------------------------- | -------------------------------------------------------------------------------- | -------------------------------- |
| graphs              | `/graphs`                           | Statistics dashboard: reviews, intervals, FSRS metrics, calendar, true retention | `stats.py`                       |
| congrats            | `/congrats`                         | "Finished for now" screen + buried/custom-study links                            | `overview.py`                    |
| deck-options        | `/deck-options/{deckId}`            | Full deck config: presets, FSRS, limits, burying, order                          | `deckoptions.py`                 |
| change-notetype     | `/change-notetype/{fromId}/{toId?}` | Map fields/templates when converting note type                                   | `changenotetype.py`              |
| card-info           | `/card-info/{cardId}`               | Card stats, review log, FSRS forgetting curve                                    | `browser/card_info.py`           |
| card-info (compare) | `/card-info/{cardId}/{previousId}`  | Current vs previous card stats                                                   | `browser/card_info.py`           |
| import-csv          | `/import-csv/{path}`                | CSV wizard: delimiter/encoding, field mapping                                    | `import_export/import_dialog.py` |
| import-anki-package | `/import-anki-package/{path}`       | `.apkg` import with scheduling/notetype merge                                    | `import_export/import_dialog.py` |
| import-page         | `/import-page/{path}`               | Shared import shell + log viewer (JSON imports)                                  | `import_export/import_dialog.py` |
| image-occlusion     | `/image-occlusion/{pathOrNoteId}`   | Standalone image-occlusion mask/note editor                                      | editor / browser                 |

**Other TS apps (not SvelteKit routes):** `ts/editor/` (note editor `NoteEditor.svelte`),
`ts/reviewer/` (answer buttons, scheduling-state mutation, IO review),
`ts/editable/` (shared field components), `ts/html-filter/`, `ts/mathjax/`.
Legacy Qt webview scripts live in `qt/aqt/data/web/js/` (`deckbrowser.ts`,
`toolbar.ts`, `reviewer-bottom.ts`, `webview.ts`).

**Backend transport:** `ts/lib/generated/post.ts` → `postProto()` sends
`POST /_anki/{method}` with protobuf bytes (`Content-Type: application/binary`).
Auth header is auto-injected by `AuthInterceptor` in `webview.py` for trusted webviews.

---

## 4. HTTP endpoints — `qt/aqt/mediasrv.py`

Flask app on `127.0.0.1:{ANKI_API_PORT}` served by Waitress. Single catch-all
route `/<path:pathin>` (GET/POST) plus `/favicon.ico`. Requests are classified by
`_extract_request`.

### GET (assets & pages)

| Pattern                                                      | Serves                                                     |
| ------------------------------------------------------------ | ---------------------------------------------------------- |
| `/_anki/{path}`                                              | Built JS/CSS/HTML from `qt/_aqt/data/web/{path}`           |
| `/_anki/legacyPageData?id={id}`                              | In-memory HTML for legacy `setHtml()` pages                |
| `/_anki/sveltekit/**`, `/_app/**`                            | SvelteKit build (SPA fallback to `index.html`)             |
| top-level SvelteKit routes (`/graphs`, `/deck-options/1`, …) | Rewritten to SvelteKit build                               |
| `/_addons/{addonId}/{subpath}`                               | Add-on web exports                                         |
| `/{anything else}`                                           | Collection media folder (strict CSP for untrusted content) |

### POST `/_anki/{camelCaseMethod}` (protobuf RPC)

All POSTs require `Content-Type: application/binary`. Two categories:

**Custom Python handlers** (`post_handler_list`):

| Endpoint                                                                  | Functionality                                                   |
| ------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `congratsInfo`                                                            | Congrats screen data; redirects to overview if study not done   |
| `getDeckConfigsForUpdate` / `updateDeckConfigs`                           | Load / save deck options (save runs on main thread w/ progress) |
| `getSchedulingStatesWithContext` / `setSchedulingStates`                  | Reviewer next-card states / apply custom scheduling             |
| `changeNotetype`                                                          | Persist change-notetype mapping                                 |
| `importDone`                                                              | Unblock import dialog after import                              |
| `importCsv` / `importAnkiPackage` / `importJsonFile` / `importJsonString` | Run respective imports                                          |
| `searchInBrowser`                                                         | Open browser with a search node                                 |
| `deckOptionsRequireClose` / `deckOptionsReady`                            | Deck options dialog lifecycle signals                           |
| `saveCustomColours`                                                       | Persist color picker palette                                    |

**Direct Rust proxies** (`exposed_backend_list` → `col._backend.{method}_raw`):

| Area             | Endpoints                                                                                                                                                              |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Collection       | `latestProgress`, `getCustomColours`                                                                                                                                   |
| Decks            | `getDeckNames`                                                                                                                                                         |
| I18n             | `i18nResources`                                                                                                                                                        |
| Import/export    | `getCsvMetadata`, `getImportAnkiPackagePresets`                                                                                                                        |
| Notes            | `getFieldNames`, `getNote`                                                                                                                                             |
| Notetypes        | `getNotetypeNames`, `getChangeNotetypeInfo`                                                                                                                            |
| Stats            | `cardStats`, `getReviewLogs`, `graphs`, `getGraphPreferences`, `setGraphPreferences`                                                                                   |
| Tags             | `completeTag`                                                                                                                                                          |
| Image occlusion  | `getImageForOcclusion`, `addImageOcclusionNote`, `getImageOcclusionNote`, `updateImageOcclusionNote`, `getImageOcclusionFields`                                        |
| Scheduler / FSRS | `computeFsrsParams`, `computeOptimalRetention`, `setWantsAbort`, `evaluateParamsLegacy`, `getOptimalRetentionParameters`, `simulateFsrsReview`, `simulateFsrsWorkload` |
| Deck config      | `getIgnoredBeforeCount`, `getRetentionWorkload`                                                                                                                        |

**Security:** full API access needs `Authorization: Bearer {token}` (injected for
trusted webviews) or `ANKI_API_HOST=0.0.0.0`. Legacy pages may only call
`getSchedulingStatesWithContext`, `setSchedulingStates`, `i18nResources`, `congratsInfo`.

> Note: `@generated/backend` wraps _all_ proto RPCs, but only the handlers above
> are reachable over HTTP. Other RPCs are Python-only via `col._backend`.

---

## 5. Python library — `pylib/anki/`

| Component         | Location                               | Role                                                                 |
| ----------------- | -------------------------------------- | -------------------------------------------------------------------- |
| `Collection`      | `pylib/anki/collection.py`             | Main API object the GUI uses                                         |
| `RustBackend`     | `pylib/anki/_backend.py`               | Subclasses generated backend; `_run_command` → rsbridge              |
| Generated methods | `out/pylib/anki/_backend_generated.py` | One snake_case method per RPC                                        |
| rsbridge (PyO3)   | `pylib/rsbridge/lib.rs`                | Exposes `open_backend()` + `Backend.command(service, method, bytes)` |

Flow: `col.{op}()` → generated method → `_run_command(service_idx, method_idx, proto)`
→ `_rsbridge.Backend.command(...)` → Rust `run_service_method`.

---

## 6. Core Rust layer — `rslib/src/`

Module root: `rslib/src/lib.rs`. Each domain has a `service.rs` implementing the
generated protobuf trait.

| Domain                   | Location                                                               | Service impl                       |
| ------------------------ | ---------------------------------------------------------------------- | ---------------------------------- |
| Collection lifecycle     | `collection/`                                                          | `collection/service.rs`            |
| Scheduler (incl. FSRS)   | `scheduler/` (`answering/`, `queue/`, `states/`, `filtered/`, `fsrs/`) | `scheduler/service/mod.rs`         |
| Cards                    | `card/`                                                                | `card/service.rs`                  |
| Notes                    | `notes/`                                                               | `notes/service.rs`                 |
| Notetypes                | `notetype/`                                                            | `notetype/service.rs`              |
| Decks                    | `decks/`                                                               | `decks/service.rs`                 |
| Deck config              | `deckconfig/`                                                          | `deckconfig/service.rs`            |
| Tags                     | `tags/`                                                                | `tags/service.rs`                  |
| Search / browser         | `search/`                                                              | `search/service/mod.rs`            |
| Stats / graphs           | `stats/`                                                               | `stats/service.rs`                 |
| Media                    | `media/`                                                               | `media/service.rs`                 |
| Import / export          | `import_export/`                                                       | `import_export/service.rs`         |
| Sync                     | `sync/`                                                                | `backend/sync.rs`                  |
| Card rendering           | `card_rendering/`                                                      | `card_rendering/service.rs`        |
| Image occlusion          | `image_occlusion/`                                                     | `image_occlusion/service.rs`       |
| Config / prefs           | `config/`                                                              | `backend/config.rs`                |
| Storage (SQLite)         | `storage/` (`card/`, `note/`, `deck/`, `revlog/`, …)                   | per-entity SQL                     |
| Backend shell + dispatch | `backend/`, `services.rs`                                              | holds open collection, routes RPCs |

### FSRS / scheduling engine

| Layer                     | Path                                                                                                           |
| ------------------------- | -------------------------------------------------------------------------------------------------------------- |
| External FSRS crate       | workspace dep `fsrs` (root `Cargo.toml`) — core algorithm                                                      |
| Anki FSRS integration     | `rslib/src/scheduler/fsrs/` (`memory_state.rs`, `params.rs`, `retention.rs`, `rescheduler.rs`, `simulator.rs`) |
| Scheduler orchestration   | `rslib/src/scheduler/` (`answering/`, `states/`, `queue/`, `new.rs`, `reviews.rs`, `timing.rs`)                |
| Review history input      | `rslib/src/revlog/`                                                                                            |
| FSRS deck settings schema | `rslib/src/deckconfig/`                                                                                        |

Legacy SM-2 scheduling lives alongside FSRS; version tracked via `config::SchedulerVersion`.

---

## 7. Protobuf contract — `proto/anki/`

Each domain file defines two services: `XxxService` (collection-scoped, runs on an
open DB) and `BackendXxxService` (backend-only extras). Shared primitives are in
`generic.proto`. Build pipeline:

```
proto/*.proto
  → descriptors.bin (build/configure)
  → rslib/proto/build.rs
       ├─ Rust prost types          (rslib/proto/rust.rs)
       ├─ Python snake_case methods  (rslib/proto/python.rs → _backend_generated.py)
       └─ TypeScript functions       (rslib/proto/typescript.rs → backend.ts)
  → rslib/build.rs → OUT_DIR/backend.rs (run_service_method dispatch)
```

| Domain                                 | File                                                                |
| -------------------------------------- | ------------------------------------------------------------------- |
| Shared types                           | `generic.proto`                                                     |
| Backend core / errors                  | `backend.proto`                                                     |
| Collection lifecycle                   | `collection.proto`                                                  |
| Scheduler / study / FSRS               | `scheduler.proto`                                                   |
| Cards                                  | `cards.proto`                                                       |
| Notes                                  | `notes.proto`                                                       |
| Notetypes                              | `notetypes.proto`                                                   |
| Decks                                  | `decks.proto`                                                       |
| Deck config                            | `deck_config.proto`                                                 |
| Tags                                   | `tags.proto`                                                        |
| Search / browser                       | `search.proto`                                                      |
| Stats / graphs                         | `stats.proto`                                                       |
| Media                                  | `media.proto`                                                       |
| Import / export                        | `import_export.proto`                                               |
| Sync                                   | `sync.proto`                                                        |
| Config / prefs                         | `config.proto`                                                      |
| Card rendering (HTML/TTS/LaTeX)        | `card_rendering.proto`                                              |
| Image occlusion                        | `image_occlusion.proto`                                             |
| I18n                                   | `i18n.proto`                                                        |
| Links / help                           | `links.proto`                                                       |
| Frontend bridge (web↔Qt)               | `frontend.proto`                                                    |
| AnkiWeb / AnkiHub / GitHub / AnkiDroid | `ankiweb.proto`, `ankihub.proto`, `github.proto`, `ankidroid.proto` |

---

## 8. Quick "where do I look?" index

| If you're working on…      | Start here                                                                                                                       |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| The deck list home screen  | `qt/aqt/deckbrowser.py` + `data/web/js/deckbrowser.ts`                                                                           |
| Reviewing / answer buttons | `qt/aqt/reviewer.py` + `ts/reviewer/` + `rslib/src/scheduler/`                                                                   |
| Deck options UI            | `ts/routes/deck-options/` + `qt/aqt/deckoptions.py` + `mediasrv` (`getDeckConfigsForUpdate`)                                     |
| Statistics / graphs        | `ts/routes/graphs/` + `qt/aqt/stats.py` + `rslib/src/stats/`                                                                     |
| The note editor            | `ts/editor/` + `qt/aqt/editor.py`                                                                                                |
| Card templates / layout    | `qt/aqt/clayout.py`                                                                                                              |
| Browser table / sidebar    | `qt/aqt/browser/` + `rslib/src/search/`                                                                                          |
| Importing files            | `ts/routes/import-*` + `qt/aqt/import_export/` + `rslib/src/import_export/`                                                      |
| Sync                       | `qt/aqt/sync.py`, `qt/aqt/mediasync.py` + `rslib/src/sync/`                                                                      |
| FSRS algorithm             | `rslib/src/scheduler/fsrs/` + external `fsrs` crate                                                                              |
| Adding a backend RPC       | edit `proto/anki/*.proto`, run a full build, implement in `rslib/src/{domain}/service.rs`, expose in `mediasrv.py` if web-facing |
| HTTP endpoints             | `qt/aqt/mediasrv.py`                                                                                                             |
