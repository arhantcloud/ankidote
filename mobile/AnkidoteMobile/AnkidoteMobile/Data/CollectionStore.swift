import Foundation
import SQLite3

struct Flashcard: Identifiable {
    let id: Int64
    let front: String
    let back: String
}

enum CollectionError: LocalizedError {
    case openFailed(String)
    var errorDescription: String? {
        switch self {
        case .openFailed(let m): return "Could not open collection: \(m)"
        }
    }
}

/// Reads a downloaded `collection.anki2` (schema 18) directly with SQLite:
/// the synced `ankidote` config blob and each topic deck's cards.
final class CollectionStore {
    private var db: OpaquePointer?
    private let SQLITE_TRANSIENT = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    init(path: String) throws {
        let rc = sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY, nil)
        guard rc == SQLITE_OK else {
            let msg = db.map { String(cString: sqlite3_errmsg($0)) } ?? "rc=\(rc)"
            throw CollectionError.openFailed(msg)
        }
    }

    deinit { if let db { sqlite3_close(db) } }

    // MARK: - Ankidote config

    /// The synced `ankidote` JSON blob. Modern collections keep it in the
    /// `config` table; very old ones inline it in `col.conf`.
    func ankidoteConfig() -> [String: Any]? {
        if let data = queryBlob("SELECT val FROM config WHERE key = 'ankidote'"),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            return obj
        }
        // Legacy fallback: whole config JSON lived in col.conf.
        if let data = queryBlob("SELECT conf FROM col LIMIT 1"),
           let conf = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let node = conf["ankidote"] {
            if let dict = node as? [String: Any] { return dict }
        }
        return nil
    }

    // MARK: - Flashcards

    func deckID(named name: String) -> Int64? {
        // Schema 15+: a `decks` table keyed by full name.
        if tableExists("decks"), let id = queryInt64(
            "SELECT id FROM decks WHERE name = ?1", text: name) {
            return id
        }
        // Legacy: decks live as JSON in col.decks.
        if let data = queryBlob("SELECT decks FROM col LIMIT 1"),
           let decks = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            for (idStr, value) in decks {
                if let d = value as? [String: Any], d["name"] as? String == name {
                    return Int64(idStr)
                }
            }
        }
        return nil
    }

    func flashcards(topic: String, limit: Int = 40) -> [Flashcard] {
        let name = ItemBank.shared.deckName(for: topic)
        guard let did = deckID(named: name) else { return [] }
        var cards: [Flashcard] = []
        let sql = """
        SELECT c.id, n.flds FROM cards c JOIN notes n ON c.nid = n.id
        WHERE c.did = ?1 ORDER BY c.id LIMIT ?2
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_int64(stmt, 1, did)
        sqlite3_bind_int(stmt, 2, Int32(limit))
        while sqlite3_step(stmt) == SQLITE_ROW {
            let cid = sqlite3_column_int64(stmt, 0)
            let flds = columnString(stmt, 1) ?? ""
            let fields = flds.components(separatedBy: "\u{1f}")
            let front = Self.stripHTML(fields.first ?? "")
            let back = fields.count > 1 ? Self.stripHTML(fields[1]) : ""
            cards.append(Flashcard(id: cid, front: front, back: back))
        }
        return cards
    }

    func cardCount(topic: String) -> Int {
        let name = ItemBank.shared.deckName(for: topic)
        guard let did = deckID(named: name) else { return 0 }
        return Int(queryInt64("SELECT count(*) FROM cards WHERE did = ?1", int64: did) ?? 0)
    }

    // MARK: - SQLite helpers

    private func tableExists(_ name: String) -> Bool {
        (queryInt64("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?1", text: name) ?? 0) > 0
    }

    private func columnString(_ stmt: OpaquePointer?, _ idx: Int32) -> String? {
        guard let c = sqlite3_column_text(stmt, idx) else { return nil }
        return String(cString: c)
    }

    private func queryBlob(_ sql: String) -> Data? {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return nil }
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_step(stmt) == SQLITE_ROW else { return nil }
        if let bytes = sqlite3_column_blob(stmt, 0) {
            let count = Int(sqlite3_column_bytes(stmt, 0))
            return Data(bytes: bytes, count: count)
        }
        if let text = sqlite3_column_text(stmt, 0) {
            return Data(String(cString: text).utf8)
        }
        return nil
    }

    private func queryInt64(_ sql: String, text: String? = nil, int64: Int64? = nil) -> Int64? {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return nil }
        defer { sqlite3_finalize(stmt) }
        if let text { sqlite3_bind_text(stmt, 1, text, -1, SQLITE_TRANSIENT) }
        if let int64 { sqlite3_bind_int64(stmt, 1, int64) }
        guard sqlite3_step(stmt) == SQLITE_ROW else { return nil }
        return sqlite3_column_int64(stmt, 0)
    }

    static func stripHTML(_ s: String) -> String {
        var out = s.replacingOccurrences(of: "<[^>]+>", with: " ", options: .regularExpression)
        let entities = ["&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": "\"", "&#39;": "'"]
        for (k, v) in entities { out = out.replacingOccurrences(of: k, with: v) }
        return out.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
