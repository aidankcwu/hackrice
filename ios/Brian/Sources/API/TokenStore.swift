// Where the bearer token from a pasted server link lives once it's been applied.
//
// UserDefaults keeps only the tokenless endpoint (ServerURL.tokenlessURLString); the
// token itself lives in the Keychain, one item per link (account = ServerURL.accountKey,
// i.e. host+path), so two testers' links on the same device never collide or leak into
// each other. MacLink is unaffected: it still gets the full wss://…?token=… string
// through its existing `configure(serverURL:)` seam and keeps managing its own
// UserDefaults key exactly as it already does (see MacLink.swift's own TODO on that).
import Foundation
import os
import Security

/// A place to keep one bearer token per server link, keyed by `ServerURL.accountKey`.
protocol TokenStoring {
    func token(for key: String) -> String?
    /// `nil` or empty deletes the stored token for `key`.
    @discardableResult func setToken(_ token: String?, for key: String) -> Bool
}

/// Keychain-backed store: `kSecClassGenericPassword`, service "com.zeroist.app.token",
/// one item per `key`. Works on the simulator without any extra entitlement — a
/// generic-password item just needs the app's own (implicit) keychain access group.
struct KeychainTokenStore: TokenStoring {
    static let service = "com.zeroist.app.token"
    private static let log = Logger(subsystem: "com.zeroist.app", category: "keychain")

    func token(for key: String) -> String? {
        var query = baseQuery(key)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult func setToken(_ token: String?, for key: String) -> Bool {
        let query = baseQuery(key)
        guard let token, !token.isEmpty else {
            let status = SecItemDelete(query as CFDictionary)
            if status != errSecSuccess && status != errSecItemNotFound {
                Self.log.error("Token delete failed with status \(status, privacy: .public)")
                return false
            }
            return true
        }
        let data = Data(token.utf8)
        let update = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if update == errSecSuccess { return true }
        guard update == errSecItemNotFound else {
            Self.log.error("Token update failed with status \(update, privacy: .public)")
            return false
        }
        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        let add = SecItemAdd(attributes as CFDictionary, nil)
        guard add == errSecSuccess else {
            Self.log.error("Token add failed with status \(add, privacy: .public)")
            return false
        }
        return true
    }

    private func baseQuery(_ key: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: key,
        ]
    }
}

/// In-memory double for tests: same protocol, no Keychain entitlement or simulator
/// keychain state involved.
final class InMemoryTokenStore: TokenStoring {
    private var storage: [String: String] = [:]
    func token(for key: String) -> String? { storage[key] }
    @discardableResult func setToken(_ token: String?, for key: String) -> Bool {
        if let token, !token.isEmpty {
            storage[key] = token
        } else {
            storage.removeValue(forKey: key)
        }
        return true
    }
}

/// Orchestrates ServerURL + TokenStoring + UserDefaults so AppState's persistence
/// concern is a few call sites, not hand-rolled logic, and so it's testable without
/// constructing the rest of AppState (glasses session, Link, …).
enum ServerURLStore {
    /// Pre-fix builds stored the pasted link, token included, under this single key —
    /// the same one MacLink itself reads/writes (`MacLink.serverURLKey`). We read it
    /// once, to migrate. MacLink also *writes* it itself, synchronously and only from
    /// inside `configure(serverURL:)` (its own cold-launch bootstrap, not ours to
    /// touch) — see `scrubLegacyKey`/`clearLegacyKey` below for how we live with that
    /// without editing MacLink.swift.
    static let legacyKey = "serverURL"
    /// Where the tokenless endpoint lives from now on. Distinct from `legacyKey` on
    /// purpose: MacLink and AppState no longer share one slot, so AppState can keep it
    /// token-free without racing MacLink's own writes to `legacyKey`.
    static let endpointKey = "serverEndpoint"

    /// Runs once per fresh `endpointKey`: if an old combined value is sitting in
    /// `legacyKey`, move its token to the Keychain and seed `endpointKey` with the
    /// tokenless form. Idempotent — a second call is a no-op once `endpointKey` exists.
    static func migrateLegacyIfNeeded(defaults: UserDefaults, tokenStore: TokenStoring) {
        guard (defaults.string(forKey: endpointKey) ?? "").isEmpty else { return }
        let legacy = (defaults.string(forKey: legacyKey) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !legacy.isEmpty, let parsed = ServerURL(legacy) else { return }
        if let token = parsed.token {
            guard tokenStore.setToken(token, for: parsed.accountKey) else { return }
        }
        defaults.set(parsed.tokenlessURLString, forKey: endpointKey)
    }

    /// Reconstructs the full `ServerURL` (endpoint from UserDefaults, token from the
    /// Keychain) for `APIClient` / `Link.configure(serverURL:)`, or nil when unset.
    static func load(defaults: UserDefaults, tokenStore: TokenStoring) -> ServerURL? {
        let stored = (defaults.string(forKey: endpointKey) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !stored.isEmpty, let tokenless = ServerURL(stored) else { return nil }
        let token = tokenStore.token(for: tokenless.accountKey)
        let full = ServerURL.reattachingToken(token, to: stored)
        return ServerURL(full)
    }

    /// Splits a freshly-parsed link: token to the Keychain, tokenless endpoint to
    /// UserDefaults. Never writes the token anywhere in `defaults`.
    @discardableResult
    static func persist(_ parsed: ServerURL, defaults: UserDefaults, tokenStore: TokenStoring) -> Bool {
        guard tokenStore.setToken(parsed.token, for: parsed.accountKey) else { return false }
        defaults.set(parsed.tokenlessURLString, forKey: endpointKey)
        return true
    }

    /// MacLink's own `configure(serverURL:)` is a plain synchronous function: by the
    /// time it returns, it has already copied the token into its in-memory
    /// `accessToken` (and dialed with it) *and* written the full link to `legacyKey`
    /// as a side effect. Nothing about MacLink's wire behavior depends on what's on
    /// disk after that — only on what it already read into memory — and AppState
    /// re-feeds it the full URL (endpoint from `endpointKey`, token from the Keychain)
    /// through this same `configure(serverURL:)` seam on every launch. So the instant
    /// that call returns, we overwrite `legacyKey` back to the tokenless form: call
    /// this right after every `glue.configure(serverURL:)` that passed a real link.
    static func scrubLegacyKey(tokenlessURLString: String, defaults: UserDefaults) {
        defaults.set(tokenlessURLString, forKey: legacyKey)
    }

    /// Same idea as `scrubLegacyKey`, for the empty-paste / "Change" path: MacLink's
    /// `configure(serverURL: "")` already removes `legacyKey` itself, but we drop it
    /// too so a `nil`-vs-"maybe stale" read never depends on call order.
    static func clearLegacyKey(defaults: UserDefaults) {
        defaults.removeObject(forKey: legacyKey)
    }

    /// Clears the applied link (empty paste): drops its Keychain entry and the
    /// UserDefaults endpoint.
    static func clear(previous: ServerURL?, defaults: UserDefaults, tokenStore: TokenStoring) {
        if let previous {
            tokenStore.setToken(nil, for: previous.accountKey)
        }
        defaults.removeObject(forKey: endpointKey)
    }
}
