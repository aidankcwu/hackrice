// APP_PRD.md "Web tabs": where Calendar and Analysis load from, decided once from the
// applied invite link (or, in demo mode, a `-webBase` launch argument). Pure, so the four
// cases are unit-tested without a web view.
import Foundation

enum WebSource: Equatable {
    /// Lukas's web app at `base` (no trailing slash). `token` rides on the first load only.
    case page(base: URL, token: String?)
    /// No invite link applied yet: the tab points to Connect.
    case noLink
    /// A LAN `ws://` link: that Mac serves no web app.
    case lan
    /// Demo mode with no web base given: a Seeded placeholder instead of a page.
    case seeded

    /// `-webBase http://127.0.0.1:3100` (demo screenshots against the fixtures server).
    static let argument = "-webBase"
    static let environment = "BRIAN_WEB_BASE"

    /// The demo override from the launch arguments or environment, if it parses as http(s).
    static func override(arguments: [String], environment: [String: String]) -> URL? {
        var raw = environment[Self.environment]
        if let index = arguments.firstIndex(of: argument), index + 1 < arguments.count {
            raw = arguments[index + 1]
        }
        guard var text = raw?.trimmingCharacters(in: .whitespacesAndNewlines) else { return nil }
        while text.hasSuffix("/") { text.removeLast() }
        guard let url = URL(string: text), let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https", url.host != nil else { return nil }
        return url
    }

    static func derive(demo: Bool, override: URL?, server: ServerURL?) -> WebSource {
        if demo {
            guard let override else { return .seeded }
            return .page(base: override, token: nil)
        }
        guard let server else { return .noLink }
        guard let base = server.webBase else { return .lan }
        return .page(base: base, token: server.token)
    }

    /// `<base><path>?token=T&embed=1`. `embed=1` is on every load (the web app hides its
    /// own tab bar and top chrome); the web app strips the token from the address bar.
    static func url(base: URL, path: String, token: String?) -> URL? {
        guard var c = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return nil }
        var basePath = c.path
        while basePath.hasSuffix("/") { basePath.removeLast() }
        c.path = basePath + path
        var query: [URLQueryItem] = []
        if let token, !token.isEmpty { query.append(URLQueryItem(name: "token", value: token)) }
        query.append(URLQueryItem(name: "embed", value: "1"))
        c.queryItems = query
        return c.url
    }
}
