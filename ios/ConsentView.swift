//  ConsentView.swift
//  TestFlight: plain-language consent, shown once, before the first stream.
//
//  The flag is enforced in code, not only in the UI: `MacLink.connect()` and
//  `CapturePacketSender.start()` both refuse while `StreamingConsent.isGranted` is false,
//  so a view that forgets to present this sheet gets a link that will not connect
//  ("not connected · consent needed"), never a silent stream.
//
//  Keep the text short and true. If what the app sends or who receives it changes,
//  change the text *and* bump `StreamingConsent.key` so everyone is asked again.

import SwiftUI

/// The persisted "I agree". Deleting the app clears it (it lives in UserDefaults).
enum StreamingConsent {
  /// Bump the suffix whenever the text below changes materially. v2: the retention
  /// and transcription wording became accurate (v1 promised "test session only",
  /// which was false), so anyone who agreed to v1 is asked again.
  static let key = "streamingConsent.v2"

  static var isGranted: Bool { UserDefaults.standard.bool(forKey: key) }

  static func grant() { UserDefaults.standard.set(true, forKey: key) }

  /// For a debug menu or a pre-flight run: shows the sheet again next time.
  static func revoke() { UserDefaults.standard.removeObject(forKey: key) }
}

struct ConsentView: View {
  /// Called after the flag is stored. Typically dismisses the sheet and calls
  /// `link.connect()`.
  var onAgree: () -> Void
  /// "Not now". Nothing is stored; the link stays refused.
  var onDecline: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      ScrollView {
        VStack(alignment: .leading, spacing: 18) {
          Text("Before you start")
            .font(.title2.bold())
          Text("This is a test build of a student project. While it runs, here is what leaves your phone.")
            .foregroundStyle(.secondary)

          point(
            "camera.fill", "What is sent",
            "Photos from your glasses camera, about one every 1.5 seconds while streaming, with your phone's motion and walking speed. When the glasses ask you a question, the text of your spoken answer.")
          point(
            "mic.fill", "How answers are transcribed",
            "The app listens only for a few seconds after a question. Apple speech recognition turns your answer into text, on your phone when it supports that, otherwise on Apple's servers. Only the text is sent to us, never the audio.")
          point(
            "server.rack", "Who receives it",
            "Our project server. It sends photos and answers to Google (Gemini) and OpenAI to understand them, and reply text to ElevenLabs to voice it.")
          point(
            "clock", "How long it is kept",
            "Some photos are saved as evidence for the feedback you get, and what you said is logged. Both are stored in your private test space and deleted when the team removes your test; the team can delete it on request. Not sold, not used for ads.")

          Text("You can stop at any time by closing the app.")
            .font(.footnote)
            .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
      }

      Button {
        StreamingConsent.grant()
        onAgree()
      } label: {
        Text("I agree")
          .font(.headline)
          .frame(maxWidth: .infinity)
      }
      .buttonStyle(.borderedProminent)
      .controlSize(.large)
      .accessibilityIdentifier("consent_agree_button")

      Button("Not now", action: onDecline)
        .frame(maxWidth: .infinity)
        .accessibilityIdentifier("consent_decline_button")
    }
    .padding(24)
    // No swipe-to-dismiss: leaving must be an explicit choice, and "Not now" is one.
    .interactiveDismissDisabled()
  }

  private func point(_ symbol: String, _ title: String, _ detail: String) -> some View {
    HStack(alignment: .top, spacing: 14) {
      Image(systemName: symbol)
        .font(.title3)
        .frame(width: 28)
        .foregroundStyle(.tint)
        .accessibilityHidden(true)
      VStack(alignment: .leading, spacing: 4) {
        Text(title).font(.headline)
        Text(detail).fixedSize(horizontal: false, vertical: true)
      }
    }
  }
}

extension View {
  /// One-line wiring: present `ConsentView` while `isPresented` is true, dismiss it on
  /// either button, and run `onAgree` after the flag is stored.
  ///
  ///     @State private var askConsent = false
  ///     ...
  ///     .streamingConsentSheet(isPresented: $askConsent) { link.connect() }
  func streamingConsentSheet(
    isPresented: Binding<Bool>, onAgree: @escaping () -> Void
  ) -> some View {
    sheet(isPresented: isPresented) {
      ConsentView(
        onAgree: {
          isPresented.wrappedValue = false
          onAgree()
        },
        onDecline: { isPresented.wrappedValue = false })
    }
  }
}
