// One @Observable the views read; views never own network code (IOS_SPEC.md).
// This file is the contract between the three builders. Coder A fills in the bodies and
// may add private state; the public surface below is what the Screens are written against.
import Foundation
import Observation
import os
import AVFoundation
import Speech
import UIKit
import UserNotifications

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
                cancelRecovery()
                startingWatch = false
                watching = false
                watchingSince = nil
                UIApplication.shared.isIdleTimerDisabled = false
                stopPolling()
                if !demo { Task { await self.sessionCall("end") { try await self.api.endSession() } } }
            }
        }
    }
    /// The status pill and Settings request Connect; RootView owns the presentation.
    var connectRequested = false
    /// Settings' "Redo questions" asks for the questionnaire again; RootView presents it.
    var onboardingRequested = false
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
    /// A Start tap is already opening the transport and DAT camera session.
    var startingWatch: Bool = false
    var watchingSince: Date? = nil
    /// DEFECT 1: the glasses left `.connected` mid-watch and `WatchRecovery` is waiting
    /// out the grace period, has tried its one restart, or is about to give up. Feeds
    /// `ConnectionInputs.recovering` (Connect's Stream row, the header pill).
    var recovering: Bool = false
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
    // Preview (D-002)
    /// The Glasses view sheet's latest frame, held only while the sheet is open.
    private(set) var preview = PreviewFeed()
    var previewFrame: UIImage? { preview.frame }
    var previewOpen: Bool { preview.isOpen }
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
    /// Hosted links do not need the inert local-network permission row.
    var usesHostedServer: Bool { server?.secure == true }

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
        ConnectRows.derive(connectionInputs(now: now), checking: checkingLink,
                           registering: registeringGlasses, starting: startingWatch)
    }

    /// The header above every tab (D-001), from the same inputs as the pill.
    func headerState(now: Date) -> HeaderState {
        let inputs = connectionInputs(now: now)
        return HeaderState.derive(HeaderState.Inputs(
            device: glassesDevice,
            glasses: glasses,
            status: ConnectionStatus.derive(inputs),
            watching: watching,
            canStart: ConnectRows.canStart(inputs),
            starting: startingWatch))
    }

    /// Home's tiles and watched line (D-003).
    func homeMetrics(now: Date) -> HomeMetrics {
        HomeMetrics.derive(episodes: episodes, sessions: sessions, watchingSince: watchingSince, now: now,
                           dayStart: dayStart(now: now))
    }

    /// Home's "Today's stats" sheet (D-007).
    func todayStats(now: Date) -> [TodayStats.Cell] {
        TodayStats.derive(episodes: episodes, now: now)
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
            starting: startingWatch,
            accessDenied: !demo && glue.accessDenied,
            recovering: recovering,
            backendConnected: backendConnected,
            stats: streamStats(now: now),
            now: now)
    }

    /// Live numbers for this watching session; demo mode fakes a steady 1.5 s cadence.
    var streamStats: StreamStats { streamStats(now: Date()) }
    func streamStats(now: Date) -> StreamStats {
        if demo {
            var stats = Self.demoStats(watching: watching, since: watchingSince, now: now)
            if demoEmpty { (stats.lastSpokenText, stats.lastSpokenAt) = (nil, nil) }
            return stats
        }
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
    /// Demo only: a reachable link, glasses registered, nothing watched, no episodes,
    /// sessions, protocol or summary (DEMO_UI_PRD.md "Empty state").
    static let emptyArgument = "-empty"
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
    /// Demo `-empty`: nothing has been spoken yet either.
    @ObservationIgnored private var demoEmpty = false
    /// Demo only: offers `Fixtures/preview.jpg` to the open Preview sheet.
    @ObservationIgnored private var demoPreviewTask: Task<Void, Never>?
    /// `glue.framesSent` when this watching session started; the sender's count is cumulative.
    @ObservationIgnored private var framesAtStart = 0
    /// Prevents startup's normal registered state from looking like a mid-watch stop.
    @ObservationIgnored private var connectedDuringWatch = false
    /// DEFECT 1 recovery: the task polling `WatchRecovery` while the glasses are down mid-watch.
    @ObservationIgnored private var recoveryTask: Task<Void, Never>?
    /// Active seconds counted before the current segment (see `recoverySegmentStart`):
    /// together they give `WatchRecovery` a clock that freezes while backgrounded.
    @ObservationIgnored private var recoveryActiveSeconds: TimeInterval = 0
    /// Start of the current active segment; nil while backgrounded or not recovering.
    @ObservationIgnored private var recoverySegmentStart: Date?
    /// The one automatic `session.startStream()` retry for the current drop already ran.
    @ObservationIgnored private var recoveryRestarted = false
    /// The restart's own thrown sentence, shown instead of the generic one if recovery
    /// gives up right after a failed restart.
    @ObservationIgnored private var recoveryFailureSentence: String?
    /// Foreground/background, from `didBecomeActive` / `didEnterBackground`.
    @ObservationIgnored private var appActive = true
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
            guard let self else { return }
            self.glasses = state
            if state == .connected {
                if self.watching || self.startingWatch { self.connectedDuringWatch = true }
                self.cancelRecovery()
            } else if self.watching, self.connectedDuringWatch {
                self.beginRecoveryIfNeeded()
            }
        }
        self.glassesDevice = session.deviceState
        session.onDeviceStateChange = { [weak self] device in
            self?.glassesDevice = device
        }
        session.onRegistrationFailure = { [weak self] sentence in
            guard let self else { return }
            self.registeringGlasses = false     // Register's own await may still be pending
            self.lastError = sentence
        }
        glue.onAccessDenied = { [weak self] in
            guard let self else { return }
            Task {
                await self.endWatching(stopGlue: false, refreshSummary: false)
                self.lastError = "This link no longer has access. Replace the link from your invite, then start watching again."
            }
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
            if arguments.contains(Self.emptyArgument) { startEmpty() }
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

    /// Demo `-empty`: the fixtures become a day with nothing in it, and nothing is watching.
    func startEmpty() {
        guard demo else { return }
        demoEmpty = true
        api.emptyFixtures = true
        watching = false
        watchingSince = nil
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
            await syncPersonaIfNeeded()
        }
    }

    func completeOnboarding(_ answers: PersonaAnswers) async {
        // A demo run (`-screen onboarding`) must not queue its answers for a real server.
        guard !demo else { return }
        OnboardingStore(defaults: .standard).save(answers)
        await syncPersonaIfNeeded()
    }

    func syncPersonaIfNeeded() async {
        let store = OnboardingStore(defaults: .standard)
        guard !demo, server != nil, case .reachable = link, store.needsSync,
              let persona = store.persona else { return }
        do {
            try await api.putPersona(text: persona)
            store.markSynced()
        } catch {
            Self.log.error("Persona sync failed: \(String(describing: error), privacy: .public)")
        }
    }

    /// Foreground: refresh now, resume the 30 s poll if watching, and resume DEFECT 1's
    /// recovery clock (frozen while backgrounded, so time spent locked never counts
    /// against the grace period or the total budget).
    func didBecomeActive() async {
        appActive = true
        if recovering, recoverySegmentStart == nil { recoverySegmentStart = Date() }
        if watching { startPolling() }
        guard demo || server != nil else { return }
        if !demo, !checkingLink {
            if case .reachable = link {} else { await testServer() }
        }
        if !demo {
            guard case .reachable = link else { return }
        }
        await refreshToday()
        await syncPersonaIfNeeded()          // retry a persona PUT that failed earlier
    }

    /// Background: stop polling. The stream itself keeps running (external-accessory mode).
    /// Freeze DEFECT 1's recovery clock too: it must never restart or end the watch while
    /// the app cannot see or drive DAT.
    func didEnterBackground() {
        appActive = false
        if let start = recoverySegmentStart {
            recoveryActiveSeconds += Date().timeIntervalSince(start)
            recoverySegmentStart = nil
        }
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
        guard ServerURLStore.persist(parsed, defaults: .standard, tokenStore: tokenStore) else {
            lastError = "Zeroist could not save this invite securely. Restart the phone, then paste the link again."
            return
        }
        serverURL = ""                       // never round-trip the token into the paste box
        endpointLabel = parsed.endpointLabel
        server = parsed
        api.configure(.live(parsed))
        glue.configure(serverURL: parsed.socketURL.absoluteString) // canonical link, token included
        // MacLink just wrote the token to its own key as a side effect; scrub it back.
        ServerURLStore.scrubLegacyKey(tokenlessURLString: parsed.tokenlessURLString, defaults: .standard)
        await testServer()
        await syncPersonaIfNeeded()
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
        guard !startingWatch else { return }
        startingWatch = true
        defer { startingWatch = false }
        if !consentGiven, StreamingConsent.isGranted { consentGiven = true }
        guard consentGiven else {
            lastError = "Agree to what is sent before the first stream. Tap Start watching again."
            return
        }
        if demo {
            watching = true
            watchingSince = watchingSince ?? Date()
            UIApplication.shared.isIdleTimerDisabled = true
            startPolling()
            return
        }
        guard server != nil else {
            lastError = APIError.notConfigured.sentence
            return
        }
        await requestWatchingPermissionsIfNeeded()
        framesAtStart = glue.framesSent
        connectedDuringWatch = false
        do {
            try await glue.start(session: session)
            watching = true
            // The camera start has settled. End the in-flight window here, not at the end of
            // this method: recovery waits on it, and refreshToday below can take many seconds.
            startingWatch = false
            watchingSince = Date()
            connectedDuringWatch = session.state == .connected
            // startStream returns once the camera is asked to start, not once frames flow.
            // If they never arrive, no state change would ever trigger recovery: arm it now.
            // The first `.connected` cancels it, normally well inside the grace period.
            if !connectedDuringWatch { beginRecoveryIfNeeded() }
            UIApplication.shared.isIdleTimerDisabled = true
            lastError = nil
            startPolling()
            Task { await self.sessionCall("start") { try await self.api.startSession() } }
            await refreshToday()
        } catch {
            UIApplication.shared.isIdleTimerDisabled = false
            connectedDuringWatch = false
            lastError = Self.sentence(for: error)
        }
    }

    func stopWatching() async {
        await endWatching(stopGlue: true, refreshSummary: true)
    }

    // MARK: - Preview (D-002)

    /// The Glasses view sheet appeared: frames start reaching `preview`. The session hands
    /// them over only while `onPreviewFrame` is set, i.e. from here until `closePreview`.
    func openPreview() {
        guard !preview.isOpen else { return }
        preview.open()
        session.onPreviewFrame = { [weak self] image, at in
            self?.preview.offer(image, at: at)
        }
        guard demo else { return }
        demoPreviewTask = Task { [weak self] in
            let image = PreviewFeed.fixtureImage()
            // Stamped on a steady clock: sleep overshoots a little, which would read 0.6.
            let start = Date()
            var tick = 0.0
            while !Task.isCancelled {
                guard let self else { return }
                if let image, self.watching {
                    self.preview.offer(image, at: start.addingTimeInterval(tick * PreviewFeed.demoInterval))
                }
                tick += 1
                let next = start.addingTimeInterval(tick * PreviewFeed.demoInterval)
                try? await Task.sleep(for: .seconds(max(0, next.timeIntervalSinceNow)))
            }
        }
    }

    /// The sheet went away: stop taking frames and drop the one on screen.
    func closePreview() {
        session.onPreviewFrame = nil
        demoPreviewTask?.cancel()
        demoPreviewTask = nil
        preview.close()
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
        do {
            try await api.speak(text: "This is Bryan. If you can hear me, the glasses are working.")
            lastError = nil
        } catch {
            lastError = "Bryan could not play the test line. Check your connection, then try Test voice again."
        }
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

    // MARK: - DEFECT 1: recover from the glasses leaving `.connected` mid-watch

    /// A lock-button press, a glance at another app, a brief DAT flicker, or a touchpad
    /// pause all leave `.connected` for a moment. None of them should end the watch: keep
    /// the transport up and let `WatchRecovery` decide whether to wait, restart the
    /// camera stream once, or give up. Never runs two recoveries at once.
    private func beginRecoveryIfNeeded() {
        guard recoveryTask == nil else { return }
        recoveryRestarted = false
        recoveryFailureSentence = nil
        recoveryActiveSeconds = 0
        recoverySegmentStart = appActive ? Date() : nil
        recovering = true
        recoveryTask = Task { [weak self] in
            await self?.runRecovery()
        }
    }

    /// Any return to `.connected`, or the person's own Stop, cancels a pending recovery.
    private func cancelRecovery() {
        recoveryTask?.cancel()
        recoveryTask = nil
        recovering = false
        recoverySegmentStart = nil
        recoveryActiveSeconds = 0
        recoveryRestarted = false
        recoveryFailureSentence = nil
    }

    /// Active seconds since the drop: frozen (via `recoverySegmentStart`) while backgrounded,
    /// so a long spell locked never itself burns through `WatchRecovery`'s budget.
    private var recoveryElapsedSeconds: TimeInterval {
        recoveryActiveSeconds + (recoverySegmentStart.map { Date().timeIntervalSince($0) } ?? 0)
    }

    /// Polls `WatchRecovery.action` at a steady tick until the glasses come back
    /// (`cancelRecovery` stops this task from the outside) or it gives up.
    private func runRecovery() async {
        while !Task.isCancelled {
            guard watching, glasses != .connected else { return }
            switch WatchRecovery.action(watching: watching, glasses: glasses, appActive: appActive,
                                        secondsSinceDropped: recoveryElapsedSeconds,
                                        restartAttempted: recoveryRestarted) {
            case .wait:
                break
            case .restart:
                // Never race a Start already in flight; try again once it settles.
                if !startingWatch {
                    recoveryRestarted = true
                    do {
                        try await session.startStream()
                    } catch {
                        if !Task.isCancelled { recoveryFailureSentence = Self.sentence(for: error) }
                    }
                    // Stop (and maybe a new Start) happened while the restart waited: this
                    // recovery is stale and must not touch the newer watch's state.
                    if Task.isCancelled { return }
                }
            case .end:
                await endRecoveryAndStopWatch()
                return
            }
            try? await Task.sleep(for: .milliseconds(500))
        }
    }

    /// Recovery gave up: end the watch with the most specific sentence available -- the
    /// restart's own error when there was one, otherwise the generic "stopped sending".
    private func endRecoveryAndStopWatch() async {
        let sentence = recoveryFailureSentence
            ?? "The glasses stopped sending video. Put them on, unfold them, and tap Start watching."
        cancelRecovery()
        await endWatching(stopGlue: true, refreshSummary: false)
        lastError = sentence
    }

    private func endWatching(stopGlue: Bool, refreshSummary: Bool) async {
        cancelRecovery()
        let wasWatching = watching
        if !demo, stopGlue { glue.stop() }
        watching = false
        watchingSince = nil
        connectedDuringWatch = false
        UIApplication.shared.isIdleTimerDisabled = false
        stopPolling()
        if !demo, wasWatching {
            Task { await self.sessionCall("end") { try await self.api.endSession() } }
        }
        if refreshSummary, !episodes.isEmpty { await self.refreshSummary() }
    }

    /// These prompts are useful to the question flow, but a denial must not stop video.
    private func requestWatchingPermissionsIfNeeded() async {
        if AVAudioApplication.shared.recordPermission == .undetermined {
            await withCheckedContinuation { continuation in
                AVAudioApplication.requestRecordPermission { _ in continuation.resume() }
            }
        }
        if SFSpeechRecognizer.authorizationStatus() == .notDetermined {
            await withCheckedContinuation { continuation in
                SFSpeechRecognizer.requestAuthorization { _ in continuation.resume() }
            }
        }
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        if settings.authorizationStatus == .notDetermined {
            _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
        }
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
