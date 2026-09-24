// REST client for the backend (docs/API.md, backend/pipeline/api/routes.py).
//
// Two modes. Live: URLSession against `ServerURL.apiBase`, 15 s timeout, the token as
// `X-Access-Token` when the link carries one. Fixtures (-demo / BRIAN_DEMO=1): every
// method decodes Fixtures/*.json from the app bundle instead; protocol edits change an
// in-memory copy so Mark done / Undo / Add / Delete behave on screen. Views never call
// this; AppState does, and turns every thrown `APIError` into one sentence with a fix.
import Foundation

enum APIError: Error, Equatable {
    case notConfigured
    case unreachable(label: String, lan: Bool)
    case timedOut(label: String)
    case tokenRejected
    case notFound
    case server(status: Int)
    case badResponse
    case fixtureMissing(String)

    /// One sentence of cause, one of fix (brian-ios-design, copy rules).
    var sentence: String {
        switch self {
        case .notConfigured:
            return "No invite link set. Paste the link from your invite in Connect."
        case .unreachable(let label, let lan):
            return lan
                ? "Backend \(label) unreachable. Check that the Mac and phone share Wi‑Fi."
                : "Server \(label) unreachable. Check your internet connection, then try again."
        case .timedOut(let label):
            return "Server \(label) did not answer in 15 seconds. Try again in a minute."
        case .tokenRejected:
            return "The server refused this link. Paste the full link from your invite again."
        case .notFound:
            return "The server does not have that item any more. Pull to refresh."
        case .server(let status):
            return "The server returned an error (\(status)). Try again in a minute."
        case .badResponse:
            return "The server sent something this version cannot read. Update the app, then try again."
        case .fixtureMissing(let name):
            return "Sample data \(name) is missing from the build. Rebuild the app."
        }
    }
}

@MainActor
final class APIClient {
    enum Mode: Equatable {
        case unset
        case live(ServerURL)
        case fixtures
    }

    private(set) var mode: Mode
    private let session: URLSession
    private let decoder: JSONDecoder
    /// Where fixture JSON is read from. The app bundle; tests run hosted in the app.
    private let fixtureBundle: Bundle
    /// Fixture mode's protocol list, loaded once, then edited in memory.
    private var fixtureProtocol: [ProtocolItem]?

    static let timeout: TimeInterval = 15
    static let tokenHeader = "X-Access-Token"

    init(mode: Mode = .unset, fixtureBundle: Bundle = .main) {
        self.mode = mode
        self.fixtureBundle = fixtureBundle
        let config = URLSessionConfiguration.ephemeral     // nothing cached to disk
        config.timeoutIntervalForRequest = Self.timeout
        config.timeoutIntervalForResource = Self.timeout
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.urlCache = nil
        config.waitsForConnectivity = false
        self.session = URLSession(configuration: config)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        self.decoder = decoder
    }

    func configure(_ mode: Mode) {
        self.mode = mode
    }

    var isFixtures: Bool { mode == .fixtures }

    // MARK: - Routes

    /// `GET /healthz`: liveness, no token needed.
    func healthz() async throws -> Healthz {
        if isFixtures { return Healthz(ok: true, uptimeS: 4303) }
        return try await get("/healthz")
    }

    /// `GET /api/status`: authenticated, so it doubles as the token check.
    func checkToken() async throws {
        if isFixtures { return }
        let _: Data = try await raw("GET", "/api/status")
    }

    /// `GET /api/healthspan` (today).
    func healthspan() async throws -> Healthspan {
        if isFixtures { return try fixture("today_healthspan") }
        return try await get("/api/healthspan")
    }

    /// `GET /api/episodes` (today, open ones included).
    func episodes() async throws -> [Episode] {
        if isFixtures { return try fixture("today_episodes") }
        return try await get("/api/episodes")
    }

    /// `GET /api/decisions?limit=N`, newest first, silent ones included.
    func decisions(limit: Int = 50) async throws -> [Decision] {
        if isFixtures {
            let all: [Decision] = try fixture("today_decisions")
            return Array(all.prefix(limit))
        }
        return try await get("/api/decisions", query: [URLQueryItem(name: "limit", value: String(limit))])
    }

    /// First saved frame for a decision, as JPEG bytes, or nil when none survived.
    /// `GET /api/evidence/{id}` lists `[{decision_id, frame_ref, t, bytes}]`; then
    /// `GET /api/evidence/{id}/{frame_ref}` is the JPEG. Shown, never written to disk.
    func evidenceThumbnail(decisionID: String) async throws -> Data? {
        if isFixtures { return nil }
        let frames: [EvidenceFrame] = try await get("/api/evidence/\(decisionID)")
        guard let first = frames.first else { return nil }
        return try await evidenceImage(ref: "\(decisionID)/\(first.frameRef)")
    }

    /// `GET /api/evidence/<decision_id>/<frame_ref>`: a protocol item's `evidenceRef`.
    func evidenceImage(ref: String) async throws -> Data? {
        if isFixtures { return nil }
        do {
            return try await raw("GET", "/api/evidence/\(ref)")
        } catch APIError.notFound {
            return nil
        }
    }

    /// `GET /api/protocol/today` → items scheduled today with today's status.
    func protocolToday() async throws -> [ProtocolItem] {
        if isFixtures { return try loadFixtureProtocol() }
        let today: ProtocolToday = try await get("/api/protocol/today")
        return today.items
    }

    /// `POST /api/protocol/{id}/done`.
    func protocolDone(id: String) async throws {
        if isFixtures { return try setFixtureStatus(id: id, status: "done") }
        let _: Data = try await raw("POST", "/api/protocol/\(id)/done")
    }

    /// `POST /api/protocol/{id}/undo` (status becomes `undone`).
    func protocolUndo(id: String) async throws {
        if isFixtures { return try setFixtureStatus(id: id, status: "undone") }
        let _: Data = try await raw("POST", "/api/protocol/\(id)/undo")
    }

    /// `POST /api/protocol` `{name, kind, window_start, window_end, days}`.
    func addProtocol(name: String, kind: String, windowStart: String, windowEnd: String, days: [Int]) async throws {
        if isFixtures {
            var items = try loadFixtureProtocol()
            items.append(ProtocolItem(id: "pi_\(UUID().uuidString.prefix(8).lowercased())", name: name,
                                      kind: kind, windowStart: windowStart, windowEnd: windowEnd,
                                      days: days.sorted(), status: "waiting"))
            fixtureProtocol = items.sorted { $0.windowStart < $1.windowStart }
            return
        }
        let body: [String: Any] = ["name": name, "kind": kind, "window_start": windowStart,
                                   "window_end": windowEnd, "days": Array(Set(days)).sorted()]
        let data = try JSONSerialization.data(withJSONObject: body)
        let _: Data = try await raw("POST", "/api/protocol", body: data)
    }

    /// `DELETE /api/protocol/{id}`.
    func deleteProtocol(id: String) async throws {
        if isFixtures {
            fixtureProtocol = try loadFixtureProtocol().filter { $0.id != id }
            return
        }
        let _: Data = try await raw("DELETE", "/api/protocol/\(id)")
    }

    // MARK: - Fixtures

    func fixture<T: Decodable>(_ name: String) throws -> T {
        guard let url = fixtureBundle.url(forResource: name, withExtension: "json") else {
            throw APIError.fixtureMissing(name)
        }
        let data = try Data(contentsOf: url)
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.badResponse
        }
    }

    private func loadFixtureProtocol() throws -> [ProtocolItem] {
        if let fixtureProtocol { return fixtureProtocol }
        let today: ProtocolToday = try fixture("protocol_today")
        fixtureProtocol = today.items
        return today.items
    }

    private func setFixtureStatus(id: String, status: String) throws {
        fixtureProtocol = try loadFixtureProtocol().map { $0.id == id ? $0.with(status: status) : $0 }
    }

    // MARK: - Transport

    private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        let data: Data = try await raw("GET", path, query: query)
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.badResponse
        }
    }

    private func raw(_ method: String, _ path: String, query: [URLQueryItem] = [],
                     body: Data? = nil) async throws -> Data {
        guard case .live(let server) = mode else { throw APIError.notConfigured }
        guard let url = server.url(path, query: query) else { throw APIError.badResponse }
        var request = URLRequest(url: url, timeoutInterval: Self.timeout)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token = server.token {
            request.setValue(token, forHTTPHeaderField: Self.tokenHeader)
        }
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let error as URLError where error.code == .timedOut {
            throw APIError.timedOut(label: server.label)
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch {
            throw APIError.unreachable(label: server.label, lan: !server.secure)
        }
        guard let http = response as? HTTPURLResponse else { throw APIError.badResponse }
        switch http.statusCode {
        case 200..<300: return data
        case 401, 403: throw APIError.tokenRejected
        case 404, 410: throw APIError.notFound
        default: throw APIError.server(status: http.statusCode)
        }
    }
}
