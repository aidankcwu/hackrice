// The one pasted link a tester gets, split into what each side needs.
//
//   wss://DOMAIN/t/NAME/ws/glasses?token=T  (hosted)
//     socket  wss://DOMAIN/t/NAME/ws/glasses?token=T   (as given)
//     api     https://DOMAIN/t/NAME                     (+ X-Access-Token: T)
//     label   DOMAIN/t/NAME                             (never the token)
//   ws://10.0.0.5:8010/ws/glasses           (a Mac on this Wi-Fi)
//     socket  as given, api http://10.0.0.5:8010, no token, label 10.0.0.5:8010
//
// The socket rules match MacLink's `ServerEndpoint(serverURL:defaultPath:)` exactly, so
// the socket MacLink dials and the API this file derives always point at one server:
// whitespace trimmed; `https`/`http` map to `wss`/`ws`, any other scheme is rejected;
// a host is required; an empty or "/" path becomes /ws/glasses; `?token=` is the
// token when non-empty. A value that does not parse is nil, never a crash.
import Foundation

struct ServerURL: Equatable, Sendable {
    /// The WebSocket MacLink dials, token query included. Never show or log it.
    let socketURL: URL
    /// HTTPS (or HTTP on a LAN) base for the REST API, no trailing slash.
    let apiBase: URL
    /// Sent as `X-Access-Token` on every request. Nil on a LAN link.
    let token: String?
    /// Token-free, for the Connect row and status lines: "DOMAIN/t/NAME" or "IP:PORT".
    let label: String
    let secure: Bool

    /// MacLink's `path` for the real ingest server (`ingest.INGEST_PATH`).
    static let socketPath = "/ws/glasses"

    init?(_ raw: String) {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, var parts = URLComponents(string: trimmed),
              let scheme = parts.scheme?.lowercased()
        else { return nil }
        switch scheme {
        case "wss", "https": parts.scheme = "wss"
        case "ws", "http": parts.scheme = "ws"
        default: return nil
        }
        guard let host = parts.host, !host.isEmpty else { return nil }
        if parts.path.isEmpty || parts.path == "/" { parts.path = Self.socketPath }
        guard let socket = parts.url else { return nil }
        let secure = parts.scheme == "wss"
        let token = parts.queryItems?.first(where: { $0.name == "token" })?.value

        // API base: same host and port, http(s), the socket path minus /ws/glasses.
        var basePath = parts.path
        if basePath.hasSuffix(Self.socketPath) {
            basePath.removeLast(Self.socketPath.count)
        }
        while basePath.hasSuffix("/") { basePath.removeLast() }
        var api = URLComponents()
        api.scheme = secure ? "https" : "http"
        api.host = host
        api.port = parts.port
        api.path = basePath
        guard let apiBase = api.url else { return nil }

        self.socketURL = socket
        self.apiBase = apiBase
        self.token = (token?.isEmpty == false) ? token : nil
        self.secure = secure
        let portSuffix = parts.port.map { ":\($0)" } ?? ""
        self.label = "\(host)\(portSuffix)\(basePath)"
    }

    /// `apiBase` + an absolute path like "/api/decisions", with optional query items.
    func url(_ path: String, query: [URLQueryItem] = []) -> URL? {
        guard var c = URLComponents(url: apiBase, resolvingAgainstBaseURL: false) else { return nil }
        c.path = apiBase.path + path
        if !query.isEmpty { c.queryItems = query }
        return c.url
    }

    /// Same as `label` — the redacted, token-free string Connect/Settings show once a
    /// link is applied (TokenStore.swift's `ServerURLStore`, SettingsView, ConnectView).
    var endpointLabel: String { label }

    /// TokenStore account key for this link: unique per host+path, so two testers'
    /// links never collide (same value as `label`, named for what it's used for here).
    var accountKey: String { label }

    /// `socketURL` with the `?token=` query dropped — safe to keep in UserDefaults.
    /// Re-parses to an equal `ServerURL` (minus `token`) via `ServerURL(_:)`.
    var tokenlessURLString: String {
        guard var comps = URLComponents(url: socketURL, resolvingAgainstBaseURL: false) else {
            return socketURL.absoluteString
        }
        comps.queryItems = nil
        return comps.url?.absoluteString ?? socketURL.absoluteString
    }

    /// Inverse of `tokenlessURLString`: splices `token` back in as the `?token=` query
    /// item, for handing MacLink/`APIClient` the full link via the existing
    /// `Link.configure(serverURL:)` seam after reading the tokenless form back out of
    /// UserDefaults. A nil or empty token returns `tokenlessURLString` unchanged (the
    /// LAN case, which never had one).
    static func reattachingToken(_ token: String?, to tokenlessURLString: String) -> String {
        guard let token, !token.isEmpty,
              var comps = URLComponents(string: tokenlessURLString) else { return tokenlessURLString }
        comps.queryItems = [URLQueryItem(name: "token", value: token)]
        return comps.url?.absoluteString ?? tokenlessURLString
    }
}
