//  CorpusRecorder.swift
//  Drop into the CameraAccess sample target. PERSON_A.md A4.
//
//  Writes the replay corpus: 1 Hz, 512 px longest edge, JPEG q70 — the same encode
//  A12 uses for the live capture packet (SPEC §2.2). That match is the whole point:
//  a corpus encoded differently from production means every sensor threshold tuned
//  against it has to be retuned later.
//
//  Deliberately all-synchronous on the main actor. At 1 Hz a 512 px encode plus a
//  ~40 KB write costs a few ms once a second, which is free next to a 2 fps preview,
//  and it keeps this throwaway tool clear of Sendable/isolation errors. A12 is a
//  different story — that one is in the live path and must encode off the main actor.
//
//  `encode(_:maxEdge:quality:)` is worth keeping after A4 is done; A12 calls it.

import Observation
import SwiftUI
import UIKit

@Observable
@MainActor
final class CorpusRecorder {
  /// Toggled by the button in CameraView.
  var isRecording = false
  /// Frames written this session. Shown in the button so you can see it working.
  var savedCount = 0
  /// Set if a write fails, so a silent permissions problem doesn't look like success.
  var lastError: String?

  /// 1 Hz, matching the T0 sample rate. The DAT stream runs faster and we simply drop
  /// the extra frames (§2.2) — drop, never queue.
  private let interval: TimeInterval = 1.0
  private var lastSave: TimeInterval = 0
  private let dir: URL

  init() {
    let base = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    dir = base.appendingPathComponent("corpus", isDirectory: true)
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
  }

  /// Call with every decoded frame. Throttles internally, so the caller does nothing.
  func offer(_ image: UIImage) {
    guard isRecording else { return }
    let now = Date().timeIntervalSince1970
    guard now - lastSave >= interval else { return }
    lastSave = now

    guard let jpeg = CorpusRecorder.encode(image) else {
      lastError = "encode failed"
      return
    }
    // frame_<unix_millis>.jpg — 13 digits, so lexicographic sort is chronological
    // sort, and there is no timezone to get wrong. The replay adapter parses this.
    let url = dir.appendingPathComponent("frame_\(Int64(now * 1000)).jpg")
    do {
      try jpeg.write(to: url, options: .atomic)
      savedCount += 1
    } catch {
      lastError = error.localizedDescription
    }
  }

  /// Resize to `maxEdge` on the longest side and JPEG-encode. Never upscales.
  nonisolated static func encode(
    _ image: UIImage, maxEdge: CGFloat = 512, quality: CGFloat = 0.7
  ) -> Data? {
    let size = image.size
    guard size.width > 0, size.height > 0 else { return nil }
    let scale = min(1.0, maxEdge / max(size.width, size.height))
    let target = CGSize(
      width: (size.width * scale).rounded(),
      height: (size.height * scale).rounded())

    let format = UIGraphicsImageRendererFormat.default()
    format.scale = 1  // points == pixels, or a 3x device triples the file size
    format.opaque = true  // a JPEG has no alpha channel anyway
    let resized = UIGraphicsImageRenderer(size: target, format: format).image { _ in
      image.draw(in: CGRect(origin: .zero, size: target))
    }
    return resized.jpegData(compressionQuality: quality)
  }
}
