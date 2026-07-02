import Foundation
import SwiftUI

enum AppError: LocalizedError {
    case noDiagnostic
    case demoMissing

    var errorDescription: String? {
        switch self {
        case .noDiagnostic:
            return "No diagnostic found in this account. Complete the diagnostic on your computer, sync to AnkiWeb, then log in here."
        case .demoMissing:
            return "Bundled demo data is missing."
        }
    }
}

/// Top-level app flow: login → sync (download the synced collection) → study
/// loop. Also supports an offline demo using bundled sample data.
@MainActor
final class AppState: ObservableObject {
    enum Phase: Equatable {
        case login
        case syncing(String)
        case loop
        case error(String)
    }

    @Published var phase: Phase = .login
    @Published private(set) var loop: LoopController?

    private var collection: CollectionStore?

    private var collectionURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return dir.appendingPathComponent("collection.anki2")
    }

    // MARK: - Real login + sync

    func loginAndSync(email: String, password: String) async {
        phase = .syncing("Signing in to AnkiWeb…")
        do {
            let client = AnkiWebSyncClient()
            let auth = try await client.login(username: email, password: password)

            phase = .syncing("Locating your collection…")
            let endpoint = try await client.resolveEndpoint(auth)

            phase = .syncing("Downloading your collection…")
            let dir = collectionURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try await client.downloadCollection(auth, endpoint: endpoint, to: collectionURL)

            phase = .syncing("Loading your plan…")
            try openCollectionAndStart(collectionURL)
        } catch {
            phase = .error(error.localizedDescription)
        }
    }

    private func openCollectionAndStart(_ url: URL) throws {
        let store = try CollectionStore(path: url.path)
        guard let cfg = store.ankidoteConfig() else { throw AppError.noDiagnostic }
        let state = AnkidoteState(raw: cfg)
        guard state.hasDiagnostic else { throw AppError.noDiagnostic }
        collection = store
        let controller = LoopController(state: state, collection: store)
        controller.start()
        loop = controller
        phase = .loop
    }

    // MARK: - Offline demo

    func startDemo() {
        guard let url = Bundle.main.url(forResource: "sample_ankidote", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            phase = .error(AppError.demoMissing.localizedDescription)
            return
        }
        let controller = LoopController(state: AnkidoteState(raw: raw), collection: nil)
        controller.start()
        loop = controller
        phase = .loop
    }

    func reset() {
        loop = nil
        collection = nil
        phase = .login
    }
}
