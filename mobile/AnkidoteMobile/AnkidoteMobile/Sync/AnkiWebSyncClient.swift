import Foundation

struct SyncAuth {
    var hkey: String
    var endpoint: URL
}

enum SyncError: LocalizedError {
    case invalidCredentials
    case server(String)
    case http(Int, String)
    case badResponse(String)

    var errorDescription: String? {
        switch self {
        case .invalidCredentials: return "Incorrect AnkiWeb email or password."
        case .server(let m): return m.isEmpty ? "AnkiWeb reported an error." : m
        case .http(let code, let m): return "AnkiWeb error \(code): \(m)"
        case .badResponse(let m): return "Unexpected response from AnkiWeb: \(m)"
        }
    }
}

/// Minimal AnkiWeb sync v11 client: `hostKey` login, `meta` (with 308 shard
/// redirect), and a full collection `download`. Bodies are zstd-framed and
/// carry the `anki-sync` header, exactly as `rslib/src/sync` does.
final class AnkiWebSyncClient: NSObject, URLSessionTaskDelegate {
    static let defaultBase = URL(string: "https://sync.ankiweb.net/")!

    // Any plausible client version string is accepted by the server.
    private let clientShort = "25.02.1,ankidote-ios,iOS"
    private let clientLong = "anki,25.02.1 (ankidote-ios),iOS"
    private let sessionKey = AnkiWebSyncClient.makeSessionKey()

    private lazy var session: URLSession = {
        // A delegate that refuses redirects, so we can read the 308 Location
        // and re-issue against the shard endpoint ourselves.
        URLSession(configuration: .ephemeral, delegate: self, delegateQueue: nil)
    }()

    // MARK: - Public API

    func login(username: String, password: String) async throws -> SyncAuth {
        let body = try JSONSerialization.data(withJSONObject: ["u": username, "p": password])
        let resp = try await request(method: "hostKey", endpoint: Self.defaultBase, hkey: "", jsonBody: body)
        switch resp.status {
        case 200:
            guard let obj = try? JSONSerialization.jsonObject(with: resp.data) as? [String: Any],
                  let key = obj["key"] as? String, !key.isEmpty
            else { throw SyncError.badResponse("missing host key") }
            return SyncAuth(hkey: key, endpoint: Self.defaultBase)
        case 403:
            throw SyncError.invalidCredentials
        default:
            throw SyncError.http(resp.status, String(data: resp.data, encoding: .utf8) ?? "")
        }
    }

    /// Resolve the shard endpoint (following a single 308 redirect) and verify
    /// the server wants us to continue.
    @discardableResult
    func resolveEndpoint(_ auth: SyncAuth) async throws -> URL {
        let metaBody = try JSONSerialization.data(withJSONObject: ["v": 11, "cv": clientLong])
        var endpoint = auth.endpoint
        var resp = try await request(method: "meta", endpoint: endpoint, hkey: auth.hkey, jsonBody: metaBody)
        if resp.status == 308, let loc = resp.location, let url = URL(string: loc) {
            endpoint = url
            resp = try await request(method: "meta", endpoint: endpoint, hkey: auth.hkey, jsonBody: metaBody)
        }
        guard resp.status == 200 else {
            throw SyncError.http(resp.status, String(data: resp.data, encoding: .utf8) ?? "")
        }
        if let meta = try? JSONSerialization.jsonObject(with: resp.data) as? [String: Any] {
            if let cont = meta["cont"] as? Bool, cont == false {
                throw SyncError.server(meta["msg"] as? String ?? "")
            }
        }
        return endpoint
    }

    /// Full-download the collection and write it to `destination` (a
    /// `collection.anki2` SQLite file). Returns the written URL.
    @discardableResult
    func downloadCollection(_ auth: SyncAuth, endpoint: URL, to destination: URL) async throws -> URL {
        let emptyBody = Data("{}".utf8)
        let resp = try await request(method: "download", endpoint: endpoint, hkey: auth.hkey, jsonBody: emptyBody)
        guard resp.status == 200 else {
            throw SyncError.http(resp.status, String(data: resp.data, encoding: .utf8) ?? "")
        }
        // resp.data is the decompressed raw SQLite file.
        try? FileManager.default.removeItem(at: destination)
        try resp.data.write(to: destination, options: .atomic)
        return destination
    }

    // MARK: - Request plumbing

    private struct RawResponse {
        let status: Int
        let location: String?
        let data: Data // decompressed on 200, raw otherwise
    }

    private func request(method: String, endpoint: URL, hkey: String, jsonBody: Data) async throws -> RawResponse {
        guard let url = URL(string: "sync/\(method)", relativeTo: endpoint) else {
            throw SyncError.badResponse("bad endpoint")
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        let header = #"{"v":11,"k":"\#(hkey)","c":"\#(clientShort)","s":"\#(sessionKey)"}"#
        req.setValue(header, forHTTPHeaderField: "anki-sync")
        req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        let compressed = try Zstd.compress(jsonBody)
        req.httpBody = compressed

        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw SyncError.badResponse("no HTTP response")
        }
        let location = http.value(forHTTPHeaderField: "Location")
        if http.statusCode == 200 {
            let expected = http.value(forHTTPHeaderField: "anki-original-size").flatMap { Int($0) }
            let decompressed = data.isEmpty ? Data() : try Zstd.decompress(data, expectedSize: expected)
            return RawResponse(status: 200, location: location, data: decompressed)
        }
        return RawResponse(status: http.statusCode, location: location, data: data)
    }

    // MARK: - URLSessionTaskDelegate (disable redirects)

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil) // deliver the 3xx response instead of following it
    }

    // MARK: - Helpers

    private static func makeSessionKey() -> String {
        let table = Array("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        var n = UInt64.random(in: 1 ... UInt64(UInt32.max))
        var out = ""
        let base = UInt64(table.count)
        while n > 0 {
            out.append(table[Int(n % base)])
            n /= base
        }
        return out.isEmpty ? "a1b2c3" : out
    }
}
