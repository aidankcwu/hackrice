// APP_PRD.md "Web tabs": Calendar and Analysis are Lukas's web app in a WKWebView, loaded
// with `?token=T&embed=1` so it drops its own tab bar and top chrome. Where it loads from
// is AppState.webSource; this file only shows it. States: loading, unreachable (Try
// again), rejected link, no invite link and LAN link (both point to Connect), Seeded.
import SwiftUI
import WebKit

struct WebScreen: View {
    @Environment(AppState.self) private var appState
    let title: String
    /// "/calendar" or "/analysis", appended to the web base.
    let path: String

    @State private var load = WebLoad()

    var body: some View {
        content
            // A changed invite link is a fresh start, even after a failed or rejected load.
            .onChange(of: appState.webSource) { load.retry() }
    }

    @ViewBuilder
    private var content: some View {
        switch appState.webSource {
        case .page(let base, let token):
            if let url = WebSource.url(base: base, path: path, token: token) {
                page(url)
            } else {
                noLink
            }
        case .noLink:
            noLink
        case .lan:
            message("\(title) needs the hosted link from your invite. A link to a Mac on this Wi‑Fi has no \(title).",
                    tone: Brian.text, button: "Open Connect") { appState.requestConnect() }
        case .seeded:
            seeded
        }
    }

    private var noLink: some View {
        message("Paste the link from your invite in Connect to see \(title).",
                tone: Brian.text, button: "Open Connect") { appState.requestConnect() }
    }

    @ViewBuilder
    private func page(_ url: URL) -> some View {
        switch load.phase {
        case .failed:
            message("\(title) did not load. Check that the phone is online, then try again.",
                    tone: Brian.cost, button: "Try again") { load.retry() }
        case .rejected:
            message(APIError.tokenRejected.sentence,
                    tone: Brian.cost, button: "Open Connect") { appState.requestConnect() }
        case .loading, .loaded:
            WebPageView(url: url, load: load)
                .ignoresSafeArea(edges: .bottom)
                .background(Brian.page)
                .overlay {
                    if load.phase == .loading {
                        ProgressView()
                            .controlSize(.large)
                            .accessibilityLabel("Loading \(title)")
                    }
                }
                // The web app shows its own title under the bar; no second one here.
                .toolbarTitleDisplayMode(.inline)
        }
    }

    /// A seeded session has no web app running; say so instead of a blank page.
    private var seeded: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Chip(text: "Seeded")
                Text("\(title) loads from the server in your invite link. Seeded mode has none.")
                    .font(BrianType.body)
                    .foregroundStyle(Brian.muted)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, 24)
        }
        .background(Brian.page)
        .navigationTitle(title)
    }

    /// One sentence of cause or instruction, one button of fix (skill, copy rules).
    private func message(_ text: String, tone: Color, button: String,
                         action: @escaping () -> Void) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.section) {
                Text(text)
                    .font(BrianType.body)
                    .foregroundStyle(tone)
                    .fixedSize(horizontal: false, vertical: true)
                Button(button, action: action)
                    .buttonStyle(.glassProminent)
                    .controlSize(.large)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, 24)
        }
        .background(Brian.page)
        .navigationTitle(title)
    }
}

/// What the web view last reported. View state only: the page, not the system.
@MainActor
@Observable
final class WebLoad {
    enum Phase: Equatable { case loading, loaded, failed, rejected }

    var phase: Phase = .loading
    /// Bumped by Try again; WebPageView reloads when it changes.
    private(set) var attempt = 0

    func retry() {
        phase = .loading
        attempt += 1
    }
}

/// The WKWebView. Loads `url` once, again on Try again or when the link changes; in-app
/// navigation after that belongs to the web app (it keeps `embed=1` itself).
struct WebPageView: UIViewRepresentable {
    let url: URL
    let load: WebLoad

    func makeCoordinator() -> Coordinator { Coordinator(load: load) }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.isOpaque = false
        webView.backgroundColor = UIColor(Brian.page)
        webView.scrollView.backgroundColor = UIColor(Brian.page)
        webView.allowsBackForwardNavigationGestures = true
        context.coordinator.load(url, attempt: load.attempt, in: webView)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        let coordinator = context.coordinator
        if coordinator.requested != url || coordinator.attempt != load.attempt {
            coordinator.load(url, attempt: load.attempt, in: webView)
        }
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate {
        let load: WebLoad
        private(set) var requested: URL?
        private(set) var attempt = 0

        init(load: WebLoad) { self.load = load }

        func load(_ url: URL, attempt: Int, in webView: WKWebView) {
            requested = url
            self.attempt = attempt
            if load.phase != .loading { load.phase = .loading }
            // Embed without the query too: the web app reads this cookie from
            // document.cookie, so it must not be HttpOnly (APP_WEB_NOTES.md "Embed").
            let cookie = url.host.flatMap { host in
                HTTPCookie(properties: [.domain: host, .path: "/", .name: "zeroist_embed", .value: "1",
                                        .expires: Date().addingTimeInterval(30 * 24 * 3600)])
            }
            guard let cookie else {
                webView.load(URLRequest(url: url))
                return
            }
            webView.configuration.websiteDataStore.httpCookieStore.setCookie(cookie) {
                webView.load(URLRequest(url: url))
            }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            load.phase = .loaded
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                     withError error: Error) {
            fail(error)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            fail(error)
        }

        /// A cancelled load (a newer one replaced it) is not a failure.
        private func fail(_ error: Error) {
            let code = (error as NSError).code
            if code == NSURLErrorCancelled || code == 102 /* WebKitErrorFrameLoadInterruptedByPolicyChange */ { return }
            if load.phase == .loaded { return }     // a later sub-navigation; the page is still there
            load.phase = .failed
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                     decisionHandler: @escaping @MainActor (WKNavigationResponsePolicy) -> Void) {
            if navigationResponse.isForMainFrame, let http = navigationResponse.response as? HTTPURLResponse {
                switch http.statusCode {
                case 401, 403:
                    load.phase = .rejected
                    decisionHandler(.cancel)
                    return
                case 500...:
                    load.phase = .failed
                    decisionHandler(.cancel)
                    return
                default: break
                }
            }
            decisionHandler(.allow)
        }

        /// Links off the web app's host open in Safari, never inside the tab.
        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
            if navigationAction.navigationType == .linkActivated,
               let target = navigationAction.request.url,
               target.host != requested?.host,
               target.scheme == "http" || target.scheme == "https" {
                UIApplication.shared.open(target)
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow)
        }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            webView.reload()
        }
    }
}
