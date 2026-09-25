// DEMO_UI_PRD.md "Preview sheet" (D-002). The one place a camera frame is held on the
// phone, and only while the Glasses view sheet is open: closed, it drops the frame and
// refuses new ones (CLAUDE.md invariant 4). Nothing here touches the disk.
import Foundation
import UIKit

struct PreviewFeed {
    /// The picture changes at most twice a second, whatever the camera sends.
    static let minInterval: TimeInterval = 0.5
    /// Frames per second counts the arrivals in this window.
    static let rateWindow: TimeInterval = 10
    /// After this long without a frame the line stops saying "Live".
    static let staleAfter: TimeInterval = 5

    private(set) var isOpen = false
    /// The frame on screen; nil while closed and before the first arrival.
    private(set) var frame: UIImage?
    private(set) var frameAt: Date?
    /// Every arrival in the last `rateWindow`, shown or not, for the frame rate.
    private var arrivals: [Date] = []

    mutating func open() {
        isOpen = true
    }

    /// Drops the frame with the sheet.
    mutating func close() {
        isOpen = false
        frame = nil
        frameAt = nil
        arrivals = []
    }

    /// A frame from the glasses. Ignored while closed; kept when the last shown frame is at
    /// least `minInterval` old. Returns whether the picture changed.
    @discardableResult
    mutating func offer(_ image: UIImage, at date: Date) -> Bool {
        guard isOpen else { return false }
        arrivals.append(date)
        arrivals.removeAll { date.timeIntervalSince($0) > Self.rateWindow }
        if let frameAt, date.timeIntervalSince(frameAt) < Self.minInterval { return false }
        frame = image
        frameAt = date
        return true
    }

    /// Arrivals per second over the window ending at `now`; nil until two frames arrived.
    func framesPerSecond(now: Date) -> Double? {
        let recent = arrivals.filter { now.timeIntervalSince($0) <= Self.rateWindow }
        guard recent.count >= 2, let first = recent.first, let last = recent.last else { return nil }
        let span = last.timeIntervalSince(first)
        guard span > 0 else { return nil }
        return Double(recent.count - 1) / span
    }

    /// The line under the picture: "Live · 0.7 frames/s", "Live", "Last frame 12 s ago",
    /// or "No frames yet".
    func line(now: Date) -> String {
        guard let last = arrivals.last ?? frameAt else { return "No frames yet" }
        let age = now.timeIntervalSince(last)
        if age > Self.staleAfter {
            let seconds = Int(age.rounded())
            return seconds < 60 ? "Last frame \(seconds) s ago" : "Last frame \(seconds / 60) min ago"
        }
        guard let rate = framesPerSecond(now: now) else { return "Live" }
        return "Live · " + String(format: "%.1f", locale: Locale(identifier: "en_US_POSIX"), rate) + " frames/s"
    }

    /// Demo and mock glasses: `Fixtures/preview.jpg`, a desk by a window, read from the bundle.
    static func fixtureImage(bundle: Bundle = .main) -> UIImage? {
        guard let url = bundle.url(forResource: "preview", withExtension: "jpg") else { return nil }
        return UIImage(contentsOfFile: url.path)
    }

    /// Demo frames arrive on the watching cadence, 1.5 s apart ("Live · 0.7 frames/s").
    static let demoInterval: TimeInterval = 1.5
}
