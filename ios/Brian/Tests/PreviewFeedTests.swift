// DEMO_UI_PRD.md "Preview sheet" (D-002): frames are held only while the sheet is open,
// the picture changes at most twice a second, and the line under it.
import Foundation
import Testing
import UIKit
@testable import Brian

struct PreviewFeedTests {
    private let t0 = Date(timeIntervalSince1970: 1_790_000_000)
    private let image = UIGraphicsImageRenderer(size: CGSize(width: 2, height: 2)).image { _ in }

    private func at(_ seconds: TimeInterval) -> Date { t0.addingTimeInterval(seconds) }

    @Test func closedFeedHoldsNothing() {
        var feed = PreviewFeed()
        let shownA = feed.offer(image, at: t0)
        #expect(shownA == false)
        #expect(feed.frame == nil)
        #expect(feed.line(now: t0) == "No frames yet")
    }

    @Test func openFeedKeepsTheLatestFrame() {
        var feed = PreviewFeed()
        feed.open()
        let shownB = feed.offer(image, at: t0)
        #expect(shownB)
        #expect(feed.frame === image)
        #expect(feed.frameAt == t0)
    }

    @Test func closingDropsTheFrameAndRefusesNewOnes() {
        var feed = PreviewFeed()
        feed.open()
        feed.offer(image, at: t0)
        feed.close()
        #expect(feed.frame == nil)
        #expect(feed.frameAt == nil)
        #expect(!feed.isOpen)
        let shown1 = feed.offer(image, at: at(2))
        #expect(shown1 == false)
        #expect(feed.frame == nil)
        #expect(feed.line(now: at(2)) == "No frames yet")
    }

    @Test func reopeningStartsEmpty() {
        var feed = PreviewFeed()
        feed.open()
        feed.offer(image, at: t0)
        feed.close()
        feed.open()
        #expect(feed.frame == nil)
        #expect(feed.line(now: at(1)) == "No frames yet")
    }

    @Test func pictureChangesAtMostTwiceASecond() {
        var feed = PreviewFeed()
        feed.open()
        let second = UIGraphicsImageRenderer(size: CGSize(width: 3, height: 3)).image { _ in }
        let shownC = feed.offer(image, at: t0)
        #expect(shownC)
        let shown2 = feed.offer(second, at: at(0.2))
        #expect(shown2 == false)
        #expect(feed.frame === image)
        let shown3 = feed.offer(second, at: at(0.5))
        #expect(shown3)
        #expect(feed.frame === second)
    }

    @Test func rateCountsEveryArrivalEvenUnshownOnes() {
        var feed = PreviewFeed()
        feed.open()
        for i in 0..<9 { feed.offer(image, at: at(Double(i) * 0.25)) }   // 4 frames/s
        #expect(feed.framesPerSecond(now: at(2)) == 4)
        #expect(feed.line(now: at(2)) == "Live · 4.0 frames/s")
    }

    @Test func demoCadenceReadsPointSeven() {
        var feed = PreviewFeed()
        feed.open()
        feed.offer(image, at: t0)
        feed.offer(image, at: at(PreviewFeed.demoInterval))
        #expect(feed.line(now: at(2)) == "Live · 0.7 frames/s")
    }

    @Test func oneFrameIsLiveWithoutARate() {
        var feed = PreviewFeed()
        feed.open()
        feed.offer(image, at: t0)
        #expect(feed.framesPerSecond(now: at(1)) == nil)
        #expect(feed.line(now: at(1)) == "Live")
    }

    @Test func rateForgetsArrivalsOutsideTheWindow() {
        var feed = PreviewFeed()
        feed.open()
        feed.offer(image, at: t0)
        feed.offer(image, at: at(1))
        feed.offer(image, at: at(12))
        feed.offer(image, at: at(14))
        #expect(feed.framesPerSecond(now: at(14)) == 0.5)
    }

    @Test func staleFeedSaysWhenTheLastFrameCame() {
        var feed = PreviewFeed()
        feed.open()
        feed.offer(image, at: t0)
        feed.offer(image, at: at(1))
        #expect(feed.line(now: at(5)) == "Live · 1.0 frames/s")
        #expect(feed.line(now: at(13)) == "Last frame 12 s ago")
        #expect(feed.line(now: at(181)) == "Last frame 3 min ago")
    }

    @Test func fixtureImageIsInTheBundle() {
        #expect(PreviewFeed.fixtureImage() != nil)
    }

    @MainActor
    @Test func demoStateHoldsAFrameOnlyWhileOpen() async throws {
        let state = AppState(demo: true, defaults: UserDefaults(suiteName: "PreviewFeedTests")!,
                             tokenStore: InMemoryTokenStore())
        #expect(state.previewFrame == nil)
        state.openPreview()
        #expect(state.previewOpen)
        try await Task.sleep(for: .milliseconds(200))
        #expect(state.previewFrame != nil)
        state.closePreview()
        #expect(!state.previewOpen)
        #expect(state.previewFrame == nil)
        try await Task.sleep(for: .seconds(PreviewFeed.demoInterval + 0.3))
        #expect(state.previewFrame == nil)
    }
}
