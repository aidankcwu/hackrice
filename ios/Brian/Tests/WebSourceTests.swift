// APP_PRD.md "Web tabs": the web base comes from the invite link; a LAN link has none.
import Foundation
import Testing
@testable import Brian

struct WebSourceTests {
    @Test func hostedLinkGivesTheAppUnderTheSamePrefix() throws {
        let s = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=abc123"))
        #expect(s.webBase?.absoluteString == "https://glasses.example.com/t/alice/app")
    }

    @Test func hostedLinkKeepsAPort() throws {
        let s = try #require(ServerURL("wss://glasses.example.com:8443/t/bob/ws/glasses?token=T"))
        #expect(s.webBase?.absoluteString == "https://glasses.example.com:8443/t/bob/app")
    }

    @Test func hostedLinkWithoutPrefix() throws {
        let s = try #require(ServerURL("wss://glasses.example.com/?token=T"))
        #expect(s.webBase?.absoluteString == "https://glasses.example.com/app")
    }

    @Test func lanLinkHasNoWebApp() throws {
        let s = try #require(ServerURL("ws://10.0.0.5:8010/ws/glasses"))
        #expect(s.webBase == nil)
        #expect(WebSource.derive(demo: false, override: nil, server: s) == .lan)
    }

    @Test func hostedLinkDerivesAPageWithItsToken() throws {
        let s = try #require(ServerURL("wss://glasses.example.com/t/alice/ws/glasses?token=abc123"))
        let base = try #require(URL(string: "https://glasses.example.com/t/alice/app"))
        #expect(WebSource.derive(demo: false, override: nil, server: s) == .page(base: base, token: "abc123"))
    }

    @Test func noLinkPointsToConnect() {
        #expect(WebSource.derive(demo: false, override: nil, server: nil) == .noLink)
    }

    @Test func demoUsesTheOverrideElseSeeded() throws {
        let lan = try #require(ServerURL("ws://10.0.0.5:8010/ws/glasses"))
        let base = try #require(URL(string: "http://127.0.0.1:3100"))
        #expect(WebSource.derive(demo: true, override: base, server: lan) == .page(base: base, token: nil))
        #expect(WebSource.derive(demo: true, override: nil, server: lan) == .seeded)
    }

    @Test func liveModeIgnoresTheOverride() throws {
        let base = try #require(URL(string: "http://127.0.0.1:3100"))
        #expect(WebSource.derive(demo: false, override: base, server: nil) == .noLink)
    }

    @Test func overrideFromArgumentBeatsEnvironment() {
        let env = [WebSource.environment: "http://10.0.0.9:3000"]
        #expect(WebSource.override(arguments: ["-webBase", "http://127.0.0.1:3100/"], environment: env)?
            .absoluteString == "http://127.0.0.1:3100")
        #expect(WebSource.override(arguments: [], environment: env)?.absoluteString == "http://10.0.0.9:3000")
        #expect(WebSource.override(arguments: ["-webBase", "ws://x"], environment: [:]) == nil)
        #expect(WebSource.override(arguments: ["-webBase"], environment: [:]) == nil)
    }

    @Test func firstLoadCarriesTokenAndEmbed() throws {
        let base = try #require(URL(string: "https://glasses.example.com/t/alice/app"))
        let url = WebSource.url(base: base, path: "/calendar", token: "abc123")
        #expect(url?.absoluteString == "https://glasses.example.com/t/alice/app/calendar?token=abc123&embed=1")
    }

    @Test func noTokenStillEmbeds() throws {
        let base = try #require(URL(string: "http://127.0.0.1:3100"))
        #expect(WebSource.url(base: base, path: "/analysis", token: nil)?.absoluteString
            == "http://127.0.0.1:3100/analysis?embed=1")
    }

    @Test func tokenIsPercentEncoded() throws {
        let base = try #require(URL(string: "https://glasses.example.com/t/alice/app"))
        let url = try #require(WebSource.url(base: base, path: "/calendar", token: "a b&c"))
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems
        #expect(items?.first(where: { $0.name == "token" })?.value == "a b&c")
    }
}
