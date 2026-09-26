// ServerURL must agree with MacLink's ServerEndpoint on the socket, and derive the API
// base, token and label the Setup row shows.
import Foundation
import Testing
@testable import Brian

struct ServerURLTests {
    @Test func hostedLinkSplitsIntoSocketAPITokenLabel() throws {
        let s = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=abc123"))
        #expect(s.socketURL.absoluteString == "wss://glasses.example.com/t/alice/ws/glasses?token=abc123")
        #expect(s.apiBase.absoluteString == "https://glasses.example.com/t/alice")
        #expect(s.token == "abc123")
        #expect(s.label == "glasses.example.com/t/alice")
        #expect(s.secure)
    }

    @Test func labelNeverCarriesTheToken() throws {
        let s = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=secret-token"))
        #expect(!s.label.contains("secret"))
        #expect(!s.label.contains("token"))
    }

    @Test func lanLinkHasNoToken() throws {
        let s = try #require(ServerURL("ws://10.0.0.5:8010/ws/glasses"))
        #expect(s.socketURL.absoluteString == "ws://10.0.0.5:8010/ws/glasses")
        #expect(s.apiBase.absoluteString == "http://10.0.0.5:8010")
        #expect(s.token == nil)
        #expect(s.label == "10.0.0.5:8010")
        #expect(!s.secure)
    }

    @Test func httpsIsMappedToWssLikeMacLink() throws {
        let s = try #require(ServerURL("https://glasses.example.com/t/bob/ws/glasses?token=T"))
        #expect(s.socketURL.absoluteString == "wss://glasses.example.com/t/bob/ws/glasses?token=T")
        #expect(s.apiBase.absoluteString == "https://glasses.example.com/t/bob")
        #expect(s.token == "T")
    }

    @Test func httpIsMappedToWs() throws {
        let s = try #require(ServerURL("http://192.168.1.20:8010/ws/glasses"))
        #expect(s.socketURL.absoluteString == "ws://192.168.1.20:8010/ws/glasses")
        #expect(s.apiBase.absoluteString == "http://192.168.1.20:8010")
    }

    @Test func emptyOrSlashPathGetsTheSocketPath() throws {
        let bare = try #require(ServerURL("ws://10.0.0.5:8010"))
        #expect(bare.socketURL.absoluteString == "ws://10.0.0.5:8010/ws/glasses")
        #expect(bare.apiBase.absoluteString == "http://10.0.0.5:8010")
        let slash = try #require(ServerURL("wss://glasses.example.com/?token=T"))
        #expect(slash.socketURL.absoluteString == "wss://glasses.example.com/ws/glasses?token=T")
        #expect(slash.apiBase.absoluteString == "https://glasses.example.com")
        #expect(slash.label == "glasses.example.com")
    }

    @Test func whitespaceAndSchemeCaseAreForgiven() throws {
        let s = try #require(ServerURL("  WSS://glasses.example.com/t/alice/ws/glasses?token=T \n"))
        #expect(s.socketURL.scheme == "wss")
        #expect(s.apiBase.absoluteString == "https://glasses.example.com/t/alice")
    }

    @Test func firstURLIsExtractedFromPastedWords() throws {
        let s = try #require(ServerURL("Cory, open this link: https://glasses.example.com/t/cory/app?token=T please"))
        #expect(s.socketURL.absoluteString == "wss://glasses.example.com/t/cory/ws/glasses?token=T")
        #expect(s.apiBase.absoluteString == "https://glasses.example.com/t/cory")
    }

    @Test(arguments: ["app", "app/today", "dashboard", "dashboard/session/123"])
    func hostedWebPathsBecomeTheGlassesSocket(_ suffix: String) throws {
        let s = try #require(ServerURL("https://glasses.example.com/t/cory/\(suffix)?token=secret"))
        #expect(s.socketURL.absoluteString == "wss://glasses.example.com/t/cory/ws/glasses?token=secret")
        #expect(s.token == "secret")
    }

    @Test func emptyTokenIsNoToken() throws {
        let s = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token="))
        #expect(s.token == nil)
    }

    @Test func explicitPortIsKeptInAPIAndLabel() throws {
        let s = try #require(ServerURL("wss://glasses.example.com:8443/t/alice/ws/glasses?token=T"))
        #expect(s.apiBase.absoluteString == "https://glasses.example.com:8443/t/alice")
        #expect(s.label == "glasses.example.com:8443/t/alice")
    }

    @Test(arguments: ["", "   ", "glasses.example.com/t/alice", "ftp://glasses.example.com/ws/glasses",
                      "wss://", "not a url at all", "file:///ws/glasses"])
    func garbageIsRejected(_ raw: String) {
        #expect(ServerURL(raw) == nil)
    }

    @Test func routeURLsKeepTheTenantPrefix() throws {
        let s = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=T"))
        #expect(s.url("/healthz")?.absoluteString == "https://glasses.example.com/t/alice/healthz")
        #expect(s.url("/api/decisions", query: [URLQueryItem(name: "limit", value: "50")])?.absoluteString
                == "https://glasses.example.com/t/alice/api/decisions?limit=50")
        #expect(s.url("/api/evidence/d_1/f_2")?.absoluteString
                == "https://glasses.example.com/t/alice/api/evidence/d_1/f_2")
    }

    // MARK: - Token never lands in a plain preference (adversarial review finding)

    @Test func tokenlessURLStringDropsTheQueryButNothingElse() throws {
        let s = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=secret-token"))
        #expect(!s.tokenlessURLString.contains("secret-token"))
        #expect(!s.tokenlessURLString.contains("token"))
        #expect(s.tokenlessURLString == "wss://glasses.example.com/t/alice/ws/glasses")
        // Re-parsing it back agrees with the original on everything but the token.
        let reparsed = try #require(ServerURL(s.tokenlessURLString))
        #expect(reparsed.apiBase == s.apiBase)
        #expect(reparsed.label == s.label)
        #expect(reparsed.token == nil)
    }

    @Test func endpointLabelIsLabelAndNeverCarriesTheToken() throws {
        let s = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=secret-token"))
        #expect(s.endpointLabel == s.label)
        #expect(!s.endpointLabel.contains("secret"))
        let lan = try #require(ServerURL("ws://10.0.0.5:8010/ws/glasses"))
        #expect(lan.endpointLabel == "10.0.0.5:8010")
    }

    @Test func accountKeyDistinguishesTwoTestersOnTheSameHost() throws {
        let alice = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=aaa"))
        let bob = try #require(ServerURL("wss://glasses.example.com/t/bob/ws/glasses?token=bbb"))
        #expect(alice.accountKey != bob.accountKey)
        #expect(alice.accountKey == "glasses.example.com/t/alice")
    }

    @Test func reattachingTokenReconstructsTheOriginalLink() throws {
        let original = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=secret-token"))
        let rebuilt = ServerURL.reattachingToken("secret-token", to: original.tokenlessURLString)
        let s = try #require(ServerURL(rebuilt))
        #expect(s.socketURL == original.socketURL)
        #expect(s.token == "secret-token")
    }

    @Test func reattachingNilOrEmptyTokenLeavesTheURLUnchanged() throws {
        let lan = try #require(ServerURL("ws://10.0.0.5:8010/ws/glasses"))
        #expect(ServerURL.reattachingToken(nil, to: lan.tokenlessURLString) == lan.tokenlessURLString)
        #expect(ServerURL.reattachingToken("", to: lan.tokenlessURLString) == lan.tokenlessURLString)
    }

    @Test func tokenRoundTripsThroughTokenStoreAndStaysOutOfTheTokenlessForm() throws {
        let s = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=secret-token"))
        let store = InMemoryTokenStore()
        #expect(store.token(for: s.accountKey) == nil)
        store.setToken(s.token, for: s.accountKey)
        #expect(store.token(for: s.accountKey) == "secret-token")
        #expect(!s.tokenlessURLString.contains("secret-token"))
        // Clearing (empty/nil) deletes the entry, same as ServerURLStore.clear.
        store.setToken(nil, for: s.accountKey)
        #expect(store.token(for: s.accountKey) == nil)
    }

    @Test func serverURLStorePersistsTokenlessAndKeepsTokenOutOfDefaults() throws {
        let (defaults, suiteName) = try scratchDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let tokenStore = InMemoryTokenStore()
        let s = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=secret-token"))

        ServerURLStore.persist(s, defaults: defaults, tokenStore: tokenStore)

        let stored = try #require(defaults.string(forKey: ServerURLStore.endpointKey))
        #expect(!stored.contains("secret-token"))
        #expect(tokenStore.token(for: s.accountKey) == "secret-token")

        let reloaded = try #require(ServerURLStore.load(defaults: defaults, tokenStore: tokenStore))
        #expect(reloaded.token == "secret-token")
        #expect(reloaded.endpointLabel == s.endpointLabel)
    }

    @Test func serverURLStoreClearRemovesBothTheEndpointAndTheToken() throws {
        let (defaults, suiteName) = try scratchDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let tokenStore = InMemoryTokenStore()
        let s = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=secret-token"))
        ServerURLStore.persist(s, defaults: defaults, tokenStore: tokenStore)

        ServerURLStore.clear(previous: s, defaults: defaults, tokenStore: tokenStore)

        #expect(defaults.string(forKey: ServerURLStore.endpointKey) == nil)
        #expect(tokenStore.token(for: s.accountKey) == nil)
        #expect(ServerURLStore.load(defaults: defaults, tokenStore: tokenStore) == nil)
    }

    @Test func migrationMovesAnOldCombinedLinkIntoTheKeychainAndLeavesTheEndpointTokenless() throws {
        let (defaults, suiteName) = try scratchDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let tokenStore = InMemoryTokenStore()
        // What a pre-fix build left behind: one key, token and all.
        defaults.set("wss://glasses.example.com/t/alice/ws/glasses?token=secret-token",
                     forKey: ServerURLStore.legacyKey)

        ServerURLStore.migrateLegacyIfNeeded(defaults: defaults, tokenStore: tokenStore)

        let stored = try #require(defaults.string(forKey: ServerURLStore.endpointKey))
        #expect(!stored.contains("secret-token"))
        #expect(tokenStore.token(for: "glasses.example.com/t/alice") == "secret-token")

        let loaded = try #require(ServerURLStore.load(defaults: defaults, tokenStore: tokenStore))
        #expect(loaded.token == "secret-token")
        #expect(!loaded.endpointLabel.contains("secret-token"))

        // Idempotent: running it again with the new key already set is a no-op.
        defaults.set("garbage, not a url", forKey: ServerURLStore.legacyKey)
        ServerURLStore.migrateLegacyIfNeeded(defaults: defaults, tokenStore: tokenStore)
        #expect(defaults.string(forKey: ServerURLStore.endpointKey) == stored)
    }

    // MacLink.configure(serverURL:) persists the full (token-included) link to
    // `legacyKey` itself, as a side effect we can't edit away (MacLink is off-limits).
    // AppState calls scrubLegacyKey/clearLegacyKey immediately after every such call,
    // so that key never sits on disk with a token for longer than that one call.
    @Test func scrubLegacyKeyOverwritesWhateverMacLinkJustWroteThere() throws {
        let (defaults, suiteName) = try scratchDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let s = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=secret-token"))
        // Simulate MacLink.configure(serverURL:) having just written the full link.
        defaults.set(s.socketURL.absoluteString, forKey: ServerURLStore.legacyKey)

        ServerURLStore.scrubLegacyKey(tokenlessURLString: s.tokenlessURLString, defaults: defaults)

        let stored = try #require(defaults.string(forKey: ServerURLStore.legacyKey))
        #expect(!stored.contains("secret-token"))
        #expect(stored == s.tokenlessURLString)
    }

    @Test func clearLegacyKeyRemovesIt() throws {
        let (defaults, suiteName) = try scratchDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        defaults.set("wss://glasses.example.com/t/alice/ws/glasses?token=secret-token",
                     forKey: ServerURLStore.legacyKey)
        ServerURLStore.clearLegacyKey(defaults: defaults)
        #expect(defaults.string(forKey: ServerURLStore.legacyKey) == nil)
    }

    private func scratchDefaults() throws -> (UserDefaults, String) {
        let name = "ServerURLTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: name))
        return (defaults, name)
    }
}
