// One @Observable the views read; views never own network code (IOS_SPEC.md).
// This file is the contract between the three builders. Coder A fills in the bodies and
// may add private state; the public surface below is what the Screens are written against.
import Foundation
import Observation
import os
import UIKit

enum LinkState: Equatable {
    case notSet
    case unreachable(String)     // sentence with a fix
    case reachable(String)       // endpoint label with no token, e.g. "glasses.example.com/t/alice"
}

@MainActor
@Observable
final class AppState {
    // Connect
    var glasses: GlassesState = .unavailable
    /// Battery and worn state of the linked glasses (the header); nil while none is linked.
    var glassesDevice: GlassesDeviceState? = nil
    var link: LinkState = .notSet
    /// The Connect/Settings paste box's draft text only — never the applied link. It is
    /// cleared back to "" the moment a paste parses, so a token never sits in a visible
    /// field or round-trips back into one; see `endpointLabel` for what's shown after.
    var serverURL: String = ""          // wss://DOMAIN/t/NAME/ws/glasses?token=...  (hosted) or ws://ip:8010/ws/glasses (LAN)
    /// Redacted, token-free label for the applied server (`ServerURL.endpointLabel`),
    /// e.g. "glasses.example.com/t/alice" or "10.0.0.5:8010". Nil until a pasted link
    /// parses successfully; Connect/Settings show this instead of the paste box then.
    var endpointLabel: String? = nil
    /// The consent gate is the persisted Link gate; there is no second source of truth.
    var consentGiven: Bool {
        get { StreamingConsent.isGranted }
        set {
            if newValue {
                StreamingConsent.grant()
            } else {
                StreamingConsent.revoke()
                glue.disconnect()
                watching = false
                watchingSince = nil
                stopPolling()
            }
        }
    }
    /// The status pill and Settings request Connect; RootView owns the presentation.
    var connectRequested = false
    /// Settings' "Change": Connect opens with the paste field showing, then clears this.
    var linkChangeRequested = false
    /// A link test is in flight (Connect's invite row reads "Checking…").
    var checkingLink = false
    /// DAT registration is running (Connect's glasses row reads "Registering…").
    var registeringGlasses = false
    /// The clipboard probably holds a link; Connect offers "Use the link on your clipboard".
    /// Set without reading the clipboard, so no paste prompt appears until the tap.
    var clipboardOffer = false
    /// Corpus capture (Settings, every build) is opt-in, off by default, persisted across relaunches.
    var recordCorpusEnabled: Bool {
        didSet {
            UserDefaults.standard.set(recordCorpusEnabled, forKey: Self.recordCorpusKey)
            glue.setCorpusRecording(recordCorpusEnabled)
        }
    }
    var demo: Bool = false              // -demo launch arg / BRIAN_DEMO=1: fixtures, mock glasses, "Seeded" chip
    /// Demo only (`-scrollTo summary`, `-scrollTo summary-end`): where Home scrolls once loaded.
    var homeScrollTarget: HomeScrollTarget? = nil
    /// Demo only (`-screen protocol-templates`, `-screen protocol-edit`): what the Protocol
    /// tab opens once loaded. ProtocolView consumes it.
    var protocolLaunch: AppScreen? = nil
    /// Demo only (`-screen session`): Home pushes the newest session's detail once loaded.
    var homeLaunch: AppScreen? = nil
    // Today
    var watching: Bool = false
    var watchingSince: Date? = nil
    var healthspan: Healthspan? = nil
    var episodes: [Episode] = []
    var decisions: [Decision] = []
    var heldBackToday: Int = 0          // decisions that proposed speech and were not spoken
    /// Recent Start → Stop spans, newest first (`GET /api/sessions`).
    var sessions: [WatchSession] = []
    /// `GET /api/recaps` (newest first, no bodies) and the bodies fetched for today's sessions,
    /// by recap id. A recap body never changes once written, so it is fetched once.
    var recapListings: [RecapListing] = []
    var recapBodies: [String: Recap] = [:]
    /// A sessions / recaps fetch is running (the session detail's Refresh).
    var sessionsLoading = false
    /// Home's daily summary (D-004): the last `POST /api/recap` for today, kept in memory only.
    var summary: DailySummary? = nil
    var summaryLoading = false
    /// The last summary fetch failed; the card says so only while nothing is cached.
    var summaryFailed = false
    // Protocol
    var protocolItems: [ProtocolItem] = []
    // Errors: one sentence with a fix, shown on Today under the status pill, never an alert.
    var lastError: String? = nil

    // MARK: Added by Coder A

    /// True while a Today or Protocol fetch is in flight.
    var loading: Bool = false
    /// Debug line for Settings: MacLink status · CapturePacketSender status.
    var linkStatusLine: String { demo ? "connected 10.0.0.5:8010 · sent 412" : glue.statusLine }
    /// Live socket state while capture is running (feeds `connectionStatus`).
    var backendConnected: Bool { demo || glue.backendConnected }

    // MARK: Connection status (N-001)

    /// The one answer to "is it working?" for the status pill and Connect screen.
    /// Observation tracks every input; the minutes and "last frame" parts are time, so a
    /// view that shows them ticks `now` with a `TimelineView`.
    var connectionStatus: ConnectionStatus { connectionStatus(now: Date()) }
    func connectionStatus(now: Date) -> ConnectionStatus {
        ConnectionStatus.derive(connectionInputs(now: now))
    }

    /// Connect's three rows, from the same inputs as the pill.
    func connectRows(now: Date) -> ConnectRows {
        ConnectRows.derive(connectionInputs(now: now), checking: checkingLink, registering: registeringGlasses)
    }

    /// The header above every tab (D-001), from the same inputs as the pill.
    func headerState(now: Date) -> HeaderState {
        let inputs = connectionInputs(now: now)
        return HeaderState.derive(HeaderState.Inputs(
            device: glassesDevice,
            glasses: glasses,
            status: ConnectionStatus.derive(inputs),
            watching: watching,
            canStart: ConnectRows.canStart(inputs)))
    }

    /// Home's tiles and watched line (D-003).
    func homeMetrics(now: Date) -> HomeMetrics {
        HomeMetrics.derive(episodes: episodes, sessions: sessions, watchingSince: watchingSince, now: now,
                           dayStart: dayStart(now: now))
    }

    /// Home's Sessions row and the session detail (D-006).
    func sessionsSummary(now: Date) -> SessionsSummary {
        SessionsSummary.derive(sessions: sessions, recaps: recapListings, bodies: recapBodies,
                               dayStart: dayStart(now: now), now: now)
    }

    /// Local midnight of "today". Demo fixtures are one recorded day (`healthspan.day`), so
    /// demo mode counts that day's sessions as today's; live mode uses the phone's day.
    func dayStart(now: Date) -> Date {
        let calendar = Calendar.current
        if demo, let day = healthspan?.day {
            let parts = day.split(separator: "-").compactMap { Int($0) }
            if parts.count == 3,
               let date = calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2])) {
                return date
            }
        }
        return calendar.startOfDay(for: now)
    }

    /// Home's protocol card and the Protocol tab's rows (D-005).
    func protocolSummary(now: Date) -> ProtocolSummary {
        ProtocolSummary.derive(items: protocolItems, now: now)
    }

    /// Home's daily summary card (D-004).
    var summaryCard: SummaryCard {
        SummaryCard.derive(summary: summary, loading: summaryLoading, failed: summaryFailed,
                           hasEpisodes: !episodes.isEmpty)
    }

    private func connectionInputs(now: Date) -> ConnectionInputs {
        ConnectionInputs(
            glasses: glasses,
            link: link,
            watching: watching,
            watchingSince: watchingSince,
            accessDenied: !demo && glue.accessDenied,
            backendConnected: backendConnected,
            stats: streamStats(now: now),
            now: now)
    }

    /// Live numbers for this watching session; demo mode fakes a steady 1.5 s cadence.
    var streamStats: StreamStats { streamStats(now: Date()) }
    func streamStats(now: Date) -> StreamStats {
        if demo { return Self.demoStats(watching: watching, since: watchingSince, now: now) }
        let sent = watching ? max(0, glue.framesSent - framesAtStart) : 0
        let elapsed = watchingSince.map { now.timeIntervalSince($0) } ?? 0
        return StreamStats(
            framesSent: sent,
            framesPerSecond: elapsed >= CapturePacketSender.defaultInterval ? Double(sent) / elapsed : 0,
            secondsSinceLastFrame: sent > 0 ? glue.lastFrameAt.map { max(0, now.timeIntervalSince($0)) } : nil,
            serverAcknowledged: backendConnected && sent > 0,
            lastSpokenText: glue.lastSpokenText,
            lastSpokenAt: glue.lastSpokenAt)
    }

    /// The whisper demo mode says Bryan last spoke, nine minutes into the session.
    static let demoSpokenLine = "Coffee this late may cost you sleep tonight."

    static func demoStats(watching: Bool, since: Date?, now: Date) -> StreamStats {
        guard watching, let since else {
            return StreamStats(lastSpokenText: demoSpokenLine, lastSpokenAt: now.addingTimeInterval(-9 * 60))
        }
        let interval = CapturePacketSender.defaultInterval
        let elapsed = max(0, now.timeIntervalSince(since))
        return StreamStats(
            framesSent: Int(elapsed / interval),
            framesPerSecond: 1 / interval,
            secondsSinceLastFrame: elapsed.truncatingRemainder(dividingBy: interval),
            serverAcknowledged: true,
            lastSpokenText: demoSpokenLine,
            lastSpokenAt: since.addingTimeInterval(9 * 60))
    }

    // MARK: Web tabs (N-004)

    /// Where Calendar and Analysis load from; changes when the invite link does.
    var webSource: WebSource {
        WebSource.derive(demo: demo, override: webBaseOverride, server: server)
    }

    static let recordCorpusKey = "recordCorpus"
    /// Launch argument / environment switch for demo mode (IOS_SPEC.md "Demo mode").
    static let demoArgument = "-demo"
    static let demoEnvironment = "BRIAN_DEMO"
    /// Demo only: start with the glasses reported off (red status pill).
    static let glassesOffArgument = "-glassesOff"
    /// Demo only: a first launch. No link, glasses not registered, not watching, and a
    /// link on the clipboard.
    static let freshArgument = "-fresh"
    /// Demo only: the server refused the link (red invite row), not watching.
    static let tokenRejectedArgument = "-tokenRejected"
    /// Demo only: `-scrollTo <section>` scrolls Home to that section (`HomeSection`) for screenshots.
    static let scrollToArgument = "-scrollTo"
    /// What the invite row shows in demo mode.
    static let demoLabel = "10.0.0.5:8010"
    static let demoServerURL = "ws://10.0.0.5:8010/ws/glasses"
    static let pollInterval: Duration = .seconds(30)
    /// Session start / end and recap fetches fail quietly: logged here, never shown.
    private static let log = Logger(subsystem: "com.zeroist.app", category: "sessions")

    @ObservationIgnored private let api: APIClient
    @ObservationIgnored private let glue: Link
    @ObservationIgnored private let session: GlassesSessioning
    @ObservationIgnored private var pollTask: Task<Void, Never>?
    /// Observed (not ignored) so `webSource` follows a changed invite link.
    private var server: ServerURL?
    /// Demo only: `-webBase` / BRIAN_WEB_BASE, where the web tabs load from.
    @ObservationIgnored private var webBaseOverride: URL?
    /// Home fetched the summary by itself once; after that only Refresh or a Stop does.
    @ObservationIgnored private var summaryAutoFetched = false
    /// `glue.framesSent` when this watching session started; the sender's count is cumulative.
    @ObservationIgnored private var framesAtStart = 0
    /// Where the applied link's token lives (Keychain in the app; in-memory in tests).
    @ObservationIgnored private let tokenStore: TokenStoring

    /// Reads the launch environment; `BrianApp` calls this.
    convenience init() {
        let info = ProcessInfo.processInfo
        let demo = info.arguments.contains(Self.demoArgument)
            || info.environment[Self.demoEnvironment] == "1"
        self.init(demo: demo)
    }

    init(demo: Bool, defaults: UserDefaults = .standard, tokenStore: TokenStoring = KeychainTokenStore()) {
        self.demo = demo
        self.tokenStore = tokenStore
        self.recordCorpusEnabled = defaults.bool(forKey: Self.recordCorpusKey)
        self.api = APIClient(mode: demo ? .fixtures : .unset)
        self.glue = Link()
        self.session = GlassesFactory.make(demo: demo, defaults: defaults)
        self.glasses = session.state
        session.onStateChange = { [weak self] state in
            self?.glasses = state
        }
        self.glassesDevice = session.deviceState
        session.onDeviceStateChange = { [weak self] device in
            self?.glassesDevice = device
        }
        glue.onAccessDenied = { [weak self] in
            self?.watching = false
            self?.watchingSince = nil
            self?.stopPolling()
            self?.lastError = "This link no longer has access. Replace the link from your invite, then start watching again."
        }

        if demo {
            serverURL = Self.demoServerURL
            glue.configure(serverURL: serverURL)
            glasses = .connected
            link = .reachable(Self.demoLabel)
            watching = true
            watchingSince = Date().addingTimeInterval(-14 * 60)
            // `-glassesOff`: the red pill for screenshots, with everything else still live.
            let arguments = ProcessInfo.processInfo.arguments
            webBaseOverride = WebSource.override(arguments: arguments,
                                                 environment: ProcessInfo.processInfo.environment)
            if arguments.contains(Self.glassesOffArgument) {
                glasses = .unavailable
                glassesDevice = nil
            }
            if arguments.contains(Self.freshArgument) {
                serverURL = ""
                link = .notSet
                glasses = .notRegistered
                glassesDevice = nil
                watching = false
                watchingSince = nil
                clipboardOffer = true
            }
            if let index = arguments.firstIndex(of: Self.scrollToArgument), index + 1 < arguments.count {
                homeScrollTarget = HomeScrollTarget(arguments[index + 1])
            }
            if arguments.contains(Self.tokenRejectedArgument) {
                link = .unreachable(APIError.tokenRejected.sentence)
                watching = false
                watchingSince = nil
            }
        } else {
            // Older builds kept the pasted link, token included, in plain UserDefaults;
            // move it into the Keychain once, then read the token-free form back.
            ServerURLStore.migrateLegacyIfNeeded(defaults: defaults, tokenStore: tokenStore)
            if let parsed = ServerURLStore.load(defaults: defaults, tokenStore: tokenStore) {
                server = parsed
                endpointLabel = parsed.endpointLabel
                api.configure(.live(parsed))
                // Existing seam: MacLink still wants the full link, token included, and
                // keeps managing its own UserDefaults key exactly as it already did — it
                // writes the token there as a side effect of this call, synchronously,
                // so we immediately scrub that back to tokenless (see scrubLegacyKey).
                glue.configure(serverURL: parsed.socketURL.absoluteString)
                ServerURLStore.scrubLegacyKey(tokenlessURLString: parsed.tokenlessURLString, defaults: defaults)
            }
        }
        glue.setCorpusRecording(recordCorpusEnabled)
    }

    // MARK: - Lifecycle (called by RootView)

    /// First appearance: test the stored server and load both tabs.
    func bootstrap() async {
        if demo {
            await refreshToday()
            await refreshProtocol()
            startPolling()
            return
        }
        guard server != nil else { return }
        await testServer()
        if case .reachable = link {
            await refreshToday()
            await refreshProtocol()
        }
    }

    /// Foreground: refresh now, and resume the 30 s poll if watching.
    func didBecomeActive() async {
        if watching { startPolling() }
        guard demo || server != nil else { return }
        await refreshToday()
    }

    /// Background: stop polling. The stream itself keeps running (external-accessory mode).
    func didEnterBackground() {
        stopPolling()
    }

    // MARK: - Connect

    func applyServerURL(_ url: String) async {
        let trimmed = url.trimmingCharacters(in: .whitespacesAndNewlines)
        if demo {
            serverURL = trimmed
            link = .reachable(Self.demoLabel)
            return
        }
        if trimmed.isEmpty {
            ServerURLStore.clear(previous: server, defaults: .standard, tokenStore: tokenStore)
            serverURL = ""
            endpointLabel = nil
            server = nil
            api.configure(.unset)
            glue.configure(serverURL: "")
            ServerURLStore.clearLegacyKey(defaults: .standard)
            link = .notSet
            return
        }
        guard let parsed = ServerURL(trimmed) else {
            link = .unreachable("That link does not look right. Paste the full wss:// link from your invite.")
            return
        }
        // Token to the Keychain, tokenless endpoint to UserDefaults — never the token.
        ServerURLStore.persist(parsed, defaults: .standard, tokenStore: tokenStore)
        serverURL = ""                       // never round-trip the token into the paste box
        endpointLabel = parsed.endpointLabel
        server = parsed
        api.configure(.live(parsed))
        glue.configure(serverURL: trimmed)   // existing seam: MacLink still gets the token
        // MacLink just wrote the token to its own key as a side effect; scrub it back.
        ServerURLStore.scrubLegacyKey(tokenlessURLString: parsed.tokenlessURLString, defaults: .standard)
        await testServer()
    }

    func testServer() async {
        checkingLink = true
        defer { checkingLink = false }
        if demo {
            link = .reachable(Self.demoLabel)
            return
        }
        guard let server else {
            link = .notSet
            return
        }
        do {
            _ = try await api.healthz()
            // /healthz answers without a token; /api/status does not, so it proves the link's token.
            try await api.checkToken()
            link = .reachable(server.label)
            lastError = nil
        } catch APIError.notFound {
            let sentence = "Nothing answers at \(server.label). Check the link from your invite."
            link = .unreachable(sentence)
            lastError = sentence
        } catch {
            let sentence = Self.sentence(for: error)
            link = .unreachable(sentence)
            lastError = sentence
        }
    }

    func registerGlasses() async {
        registeringGlasses = true
        defer { registeringGlasses = false }
        do {
            try await session.register()
            glasses = session.state
        } catch {
            lastError = Self.sentence(for: error)
        }
    }

    func openMetaAI() {
        session.openMetaAI()
    }

    func requestConnect() {
        connectRequested = true
    }

    /// Settings has no link field of its own: "Change" opens Connect's paste field.
    func requestLinkChange() {
        linkChangeRequested = true
        connectRequested = true
    }

    // MARK: - Clipboard (Connect)

    /// Asks the pasteboard whether it probably holds a web link, which iOS answers
    /// without the paste prompt. Only offered while no working link is applied.
    func checkClipboard() async {
        if demo { return }
        if case .reachable = link { clipboardOffer = false; return }
        let patterns = (try? await UIPasteboard.general.detectedPatterns(for: [\.probableWebURL])) ?? []
        clipboardOffer = patterns.contains(\.probableWebURL)
    }

    /// The tap on "Use the link on your clipboard": reads it (iOS may ask once) and
    /// applies it when it is an invite link; otherwise says what to paste instead.
    func useClipboardLink() async {
        clipboardOffer = false
        if demo {
            await applyServerURL(Self.demoServerURL)
            return
        }
        guard let text = UIPasteboard.general.string, ServerURL.isInviteLink(text) else {
            link = .unreachable("The clipboard does not hold an invite link. Copy the full wss:// link from your invite.")
            return
        }
        await applyServerURL(text)
    }

    // MARK: - Watching

    func startWatching() async {
        if !consentGiven, StreamingConsent.isGranted { consentGiven = true }
        guard consentGiven else {
            lastError = "Agree to what is sent before the first stream. Tap Start watching again."
            return
        }
        if demo {
            watching = true
            watchingSince = watchingSince ?? Date()
            startPolling()
            return
        }
        guard server != nil else {
            lastError = APIError.notConfigured.sentence
            return
        }
        framesAtStart = glue.framesSent
        do {
            try await glue.start(session: session)
            watching = true
            watchingSince = Date()
            lastError = nil
            startPolling()
            Task { await self.sessionCall("start") { try await self.api.startSession() } }
            await refreshToday()
        } catch {
            lastError = Self.sentence(for: error)
        }
    }

    func stopWatching() async {
        if !demo {
            glue.stop()
            Task { await self.sessionCall("end") { try await self.api.endSession() } }
        }
        watching = false
        watchingSince = nil
        stopPolling()
        // A session just ended: today's summary is out of date.
        if !episodes.isEmpty { await refreshSummary() }
    }

    // MARK: - Sessions (D-006)

    /// Fire and forget: a failed start or end is logged, never shown. Refetches the list
    /// after, so the Sessions row picks up the new or just-closed session.
    private func sessionCall(_ name: String, _ call: () async throws -> Void) async {
        do {
            try await call()
        } catch {
            Self.log.error("session \(name, privacy: .public) failed: \(String(describing: error), privacy: .public)")
        }
        await refreshSessions()
    }

    /// `GET /api/sessions` and `GET /api/recaps`, then the recap bodies today's sessions
    /// still lack. Failures are logged and keep what was there.
    func refreshSessions() async {
        if !demo && server == nil { return }
        guard !sessionsLoading else { return }
        sessionsLoading = true
        defer { sessionsLoading = false }
        do {
            async let list = api.sessions(limit: 50)
            async let listed = api.recaps(limit: 50)
            (sessions, recapListings) = try await (list, listed)
        } catch is CancellationError {
            return
        } catch {
            Self.log.error("sessions fetch failed: \(String(describing: error), privacy: .public)")
            return
        }
        for row in sessionsSummary(now: Date()).rows {
            guard let id = row.recapID, recapBodies[id] == nil else { continue }
            do {
                recapBodies[id] = try await api.recap(id: id)
            } catch is CancellationError {
                return
            } catch {
                Self.log.error("recap \(id, privacy: .public) failed: \(String(describing: error), privacy: .public)")
            }
        }
    }

    // MARK: - Daily summary (D-004)

    /// Home's appearance: fetch once, as soon as today has an episode. Later fetches are
    /// Refresh and the end of a session.
    func loadSummaryIfNeeded() async {
        guard !summaryAutoFetched, summary == nil, !episodes.isEmpty else { return }
        summaryAutoFetched = true
        await refreshSummary()
    }

    /// `POST /api/recap` for local midnight → now, not spoken. The previous text stays up
    /// while this runs; a failure is kept to the card, never `lastError`.
    func refreshSummary() async {
        if !demo && server == nil { return }
        guard !summaryLoading else { return }
        summaryLoading = true
        defer { summaryLoading = false }
        let now = Date()
        do {
            let recap = try await api.recap(from: Calendar.current.startOfDay(for: now), to: now, speak: false)
            summary = DailySummary(recap: recap, fetchedAt: Date())
            summaryFailed = false
        } catch is CancellationError {
            if summary == nil { summaryAutoFetched = false }     // Home asks again next time
        } catch {
            summaryFailed = true
        }
    }

    // MARK: - Today

    func refreshToday() async {
        if !demo && server == nil { return }
        checkAccess()
        loading = true
        defer { loading = false }
        do {
            async let hs = api.healthspan()
            async let eps = api.episodes()
            async let decs = api.decisions(limit: 50)
            let (h, e, d) = try await (hs, eps, decs)
            healthspan = demo ? h.with(provenance: "seeded") : h
            episodes = e.sorted { $0.startT > $1.startT }
            decisions = Self.todayOnly(d, episodes: e, day: h.day).sorted { $0.t > $1.t }
            heldBackToday = decisions.filter(\.heldBack).count
            lastError = nil
        } catch is CancellationError {
            return
        } catch {
            lastError = Self.sentence(for: error)
        }
        await refreshSessions()
    }

    /// `/api/decisions` is not per day. A decision is today's when its episode is one of
    /// today's (the server's own notion of today), or, with no episode, when its time
    /// falls on `day` locally.
    static func todayOnly(_ decisions: [Decision], episodes: [Episode], day: String?) -> [Decision] {
        let todayEpisodes = Set(episodes.map(\.id))
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        let key = day ?? formatter.string(from: Date())
        return decisions.filter { decision in
            if let episode = decision.episodeId, todayEpisodes.contains(episode) { return true }
            return formatter.string(from: Date(timeIntervalSince1970: decision.t)) == key
        }
    }

    func evidenceThumbnail(for decision: Decision) async -> Data? {
        do {
            return try await api.evidenceThumbnail(decisionID: decision.id)
        } catch {
            return nil     // a missing thumbnail is not an error worth a sentence
        }
    }

    /// Added by Coder A: a protocol item's "Seen" thumbnail (`evidenceRef`).
    func evidenceThumbnail(for item: ProtocolItem) async -> Data? {
        guard let ref = item.evidenceRef else { return nil }
        return try? await api.evidenceImage(ref: ref)
    }

    // MARK: - Protocol

    func refreshProtocol() async {
        if !demo && server == nil { return }
        do {
            protocolItems = try await api.protocolToday()
        } catch is CancellationError {
            return
        } catch {
            lastError = Self.sentence(for: error)
        }
    }

    func markDone(_ item: ProtocolItem) async {
        await protocolEdit { try await self.api.protocolDone(id: item.id) }
    }

    func undo(_ item: ProtocolItem) async {
        await protocolEdit { try await self.api.protocolUndo(id: item.id) }
    }

    func addProtocolItem(name: String, kind: String, windowStart: String, windowEnd: String, days: [Int]) async {
        await protocolEdit {
            try await self.api.addProtocol(name: name, kind: kind, windowStart: windowStart,
                                           windowEnd: windowEnd, days: days)
        }
    }

    func updateProtocolItem(_ item: ProtocolItem, name: String, kind: String, windowStart: String,
                            windowEnd: String, days: [Int]) async {
        await protocolEdit {
            try await self.api.updateProtocol(id: item.id, name: name, kind: kind, windowStart: windowStart,
                                              windowEnd: windowEnd, days: days)
        }
    }

    /// The checkbox: seen or done → Undo; anything else → Mark done.
    func toggleProtocolItem(_ item: ProtocolItem) async {
        let status = item.status.lowercased()
        if status == "seen" || status == "done" { await undo(item) } else { await markDone(item) }
    }

    func deleteProtocolItem(_ item: ProtocolItem) async {
        protocolItems.removeAll { $0.id == item.id }     // the swipe already removed the row
        await protocolEdit { try await self.api.deleteProtocol(id: item.id) }
    }

    private func protocolEdit(_ edit: () async throws -> Void) async {
        do {
            try await edit()
            lastError = nil
        } catch {
            lastError = Self.sentence(for: error)
        }
        await refreshProtocol()
    }

    // MARK: - Debug

    func sayTestLine() async {
        if demo { return }
        glue.sayTestLine()
    }

    // MARK: - Private

    private func startPolling() {
        guard pollTask == nil else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: Self.pollInterval)
                guard !Task.isCancelled, let self, self.watching else { return }
                await self.refreshToday()
            }
        }
    }

    private func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    /// MacLink stops reconnecting once the server refuses the token; say so.
    private func checkAccess() {
        guard !demo, glue.accessDenied else { return }
        let sentence = APIError.tokenRejected.sentence
        link = .unreachable(sentence)
        lastError = sentence
    }

    static func sentence(for error: Error) -> String {
        if let api = error as? APIError { return api.sentence }
        if let described = (error as? LocalizedError)?.errorDescription, !described.isEmpty {
            return described
        }
        let text = error.localizedDescription
        return text.isEmpty ? "Something stopped the request. Try again." : text
    }
}
