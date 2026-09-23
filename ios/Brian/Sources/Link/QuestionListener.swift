//  QuestionListener.swift
//  A short, explicit microphone window for ask/answer. The window is deliberately
//  owned here so no reconnect, interruption, or recognition exit can strand it open.

import AVFoundation
import Foundation
import Observation
import Speech

@Observable
@MainActor
final class QuestionListener {
  struct Question: Equatable {
    let id: String
    let listenS: TimeInterval
    let answerKind: String
    let text: String
  }

  enum State: Equatable {
    case idle
    case awaitingPlayback(Question)
    case listening(Question)
    case sending
  }

  private(set) var state: State = .idle
  private(set) var lastTranscript = ""
  private(set) var lastError: String?

  var statusLine: String {
    switch state {
    case .idle: return lastError.map { "answer idle · \($0)" } ?? "answer idle"
    case .awaitingPlayback: return "answer waiting for question"
    case .listening: return lastTranscript.isEmpty ? "answer listening…" : "heard: \(lastTranscript)"
    case .sending: return "answer sending…"
    }
  }

  @ObservationIgnored private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
  @ObservationIgnored private var engine: AVAudioEngine?
  @ObservationIgnored private var request: SFSpeechAudioBufferRecognitionRequest?
  @ObservationIgnored private var recognitionTask: SFSpeechRecognitionTask?
  @ObservationIgnored private var deadlineTask: Task<Void, Never>?
  @ObservationIgnored private var pauseTask: Task<Void, Never>?
  @ObservationIgnored private var askTask: Task<Void, Never>?
  @ObservationIgnored private var lifecycleTask: Task<Void, Never>?
  @ObservationIgnored private var activeID: String?
  @ObservationIgnored private var activeQuestion: Question?
  @ObservationIgnored private var activeAnswerSend: ((String) -> Void)?
  @ObservationIgnored private var answered = Set<String>()
  @ObservationIgnored private var observers: [NSObjectProtocol] = []
  @ObservationIgnored private let send: (String) -> Void

  init(send: @escaping (String) -> Void) {
    self.send = send
    let center = NotificationCenter.default
    // Only the *start* of an interruption (a phone call, Siri) ends the window; the
    // `.ended` half arrives after we have already restored playback.
    observers.append(center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] note in
      let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
      guard raw == AVAudioSession.InterruptionType.began.rawValue else { return }
      Task { @MainActor in self?.cancel(reason: "audio interrupted") }
    })
    // Opening the mic *is* a route change: `.playAndRecord` flips the glasses from
    // A2DP to the hands-free profile, and restoring playback flips them back. Cancelling
    // on every route change would therefore close the window the instant it opened.
    // Only a device going away (glasses off, Bluetooth dropped) ends the window.
    observers.append(center.addObserver(forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main) { [weak self] note in
      let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
      guard raw == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue else { return }
      Task { @MainActor in self?.cancel(reason: "audio device went away") }
    })
  }

  deinit {
    for observer in observers { NotificationCenter.default.removeObserver(observer) }
  }

  func receive(
    _ obj: [String: Any], afterPlayback: @escaping () async -> Bool,
    send answer: @escaping (String) -> Void
  ) {
    guard let id = obj["question_id"] as? String,
      let listenS = obj["listen_s"] as? Double,
      let kind = obj["answer_kind"] as? String,
      let text = obj["text"] as? String,
      ["yes_no", "count", "free"].contains(kind), listenS > 0
    else { return }

    let question = Question(id: id, listenS: listenS, answerKind: kind, text: text)
    guard !answered.contains(id), activeID == nil else { return }
    activeID = id
    activeQuestion = question
    activeAnswerSend = answer
    state = .awaitingPlayback(question)
    lastTranscript = ""
    lastError = nil
    lifecycleTask = Task { @MainActor [weak self] in
      try? await Task.sleep(for: .seconds(listenS + 15))
      guard !Task.isCancelled, let self, self.activeID == id else { return }
      self.finish(
        question, text: "", heard: false, error: "timed out before listening", send: answer)
    }
    askTask = Task { @MainActor [weak self] in
      let playbackFinished = await afterPlayback()
      guard !Task.isCancelled, self?.activeID == id else { return }
      guard playbackFinished else {
        self?.finish(question, text: "", heard: false, error: "playback timed out", send: answer)
        return
      }
      let authorized = await QuestionListener.authorized()
      guard !Task.isCancelled, let self, self.activeID == id else { return }
      guard authorized else {
        self.finish(question, text: "", heard: false, error: "microphone or speech denied", send: answer)
        return
      }
      self.startListening(question, send: answer)
    }
  }

  func receive(_ obj: [String: Any], afterPlayback: @escaping () async -> Bool) {
    receive(obj, afterPlayback: afterPlayback, send: send)
  }

  func cancel() { cancel(reason: "cancelled") }
  func stop() { cancel(reason: "stopped") }

  private func cancel(reason: String) {
    guard let question = activeQuestion else {
      // Nothing open: leave the audio session alone. Re-applying the playback category
      // here would itself post a route change while the DAT stream is live.
      state = .idle
      return
    }
    askTask?.cancel()
    finish(question, text: "", heard: false, error: reason)
  }

  private nonisolated static func authorized() async -> Bool {
    let mic: Bool
    switch AVAudioApplication.shared.recordPermission {
    case .granted: mic = true
    case .denied: mic = false
    case .undetermined: mic = await AVAudioApplication.requestRecordPermission()
    @unknown default: mic = false
    }
    guard mic else { return false }

    let speech: SFSpeechRecognizerAuthorizationStatus
    if SFSpeechRecognizer.authorizationStatus() == .notDetermined {
      speech = await withCheckedContinuation { continuation in
        SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
      }
    } else {
      speech = SFSpeechRecognizer.authorizationStatus()
    }
    return speech == .authorized
  }

  private func startListening(_ question: Question, send: @escaping (String) -> Void) {
    cleanupAndRestore(cancelLifecycle: false)
    let audioEngine = AVAudioEngine()
    let speechRequest = SFSpeechAudioBufferRecognitionRequest()
    speechRequest.shouldReportPartialResults = true
    if recognizer?.supportsOnDeviceRecognition == true {
      speechRequest.requiresOnDeviceRecognition = true
    }

    do {
      let session = AVAudioSession.sharedInstance()
      // `allowBluetoothHFP` is the iOS 17 spelling of the contract's deprecated
      // `.allowBluetooth` option; it is what admits the glasses microphone route.
      try session.setCategory(.playAndRecord, mode: .spokenAudio, options: [.allowBluetoothHFP, .allowBluetoothA2DP])
      try session.setActive(true)
      let input = audioEngine.inputNode
      let format = input.outputFormat(forBus: 0)
      input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, when in
        speechRequest.append(buffer)
      }
      audioEngine.prepare()
      try audioEngine.start()
      print("listening inputs: \(session.currentRoute.inputs.map { "\($0.portName) [\($0.portType.rawValue)]" })")
    } catch {
      finish(question, text: "", heard: false, error: "microphone failed: \(error.localizedDescription)", send: send)
      return
    }

    engine = audioEngine
    request = speechRequest
    state = .listening(question)
    recognitionTask = recognizer?.recognitionTask(with: speechRequest) { [weak self] result, error in
      Task { @MainActor in self?.recognitionUpdate(question, result: result, error: error, send: send) }
    }
    deadlineTask = Task { @MainActor [weak self] in
      try? await Task.sleep(for: .seconds(question.listenS))
      guard !Task.isCancelled else { return }
      self?.finish(question, text: self?.lastTranscript ?? "", heard: !(self?.lastTranscript.isEmpty ?? true), error: nil, send: send)
    }
  }

  private func recognitionUpdate(_ question: Question, result: SFSpeechRecognitionResult?, error: Error?, send: @escaping (String) -> Void) {
    guard activeID == question.id else { return }
    if let result {
      lastTranscript = result.bestTranscription.formattedString
      if result.isFinal {
        finish(question, text: lastTranscript, heard: !lastTranscript.isEmpty, error: nil, send: send)
        return
      }
      if !lastTranscript.isEmpty {
        pauseTask?.cancel()
        pauseTask = Task { @MainActor [weak self] in
          try? await Task.sleep(for: .seconds(1.2))
          guard !Task.isCancelled, let self else { return }
          self.finish(question, text: self.lastTranscript, heard: !self.lastTranscript.isEmpty, error: nil, send: send)
        }
      }
    }
    if let error { finish(question, text: lastTranscript, heard: !lastTranscript.isEmpty, error: error.localizedDescription, send: send) }
  }

  private func finish(
    _ question: Question, text: String, heard: Bool, error: String?,
    send answer: ((String) -> Void)? = nil
  ) {
    guard activeID == question.id, !answered.contains(question.id) else { return }
    answered.insert(question.id)
    state = .sending
    lastError = error
    cleanupAndRestore()
    let body: [String: Any] = [
      "v": 1, "type": "answer", "question_id": question.id,
      "text": text, "heard": heard, "t": Date().timeIntervalSince1970,
    ]
    if let data = try? JSONSerialization.data(withJSONObject: body),
      let json = String(data: data, encoding: .utf8) { (answer ?? activeAnswerSend ?? send)(json) }
    activeID = nil
    activeQuestion = nil
    activeAnswerSend = nil
    askTask = nil
    state = .idle
  }

  private func cleanupAndRestore(cancelLifecycle: Bool = true) {
    if cancelLifecycle { lifecycleTask?.cancel(); lifecycleTask = nil }
    deadlineTask?.cancel(); deadlineTask = nil
    pauseTask?.cancel(); pauseTask = nil
    recognitionTask?.cancel(); recognitionTask = nil
    request?.endAudio(); request = nil
    if let engine {
      engine.inputNode.removeTap(onBus: 0)
      engine.stop()
    }
    engine = nil
    do {
      let session = AVAudioSession.sharedInstance()
      try session.setCategory(.playback, mode: .spokenAudio, options: [.allowBluetoothA2DP])
      try session.setActive(true)
    } catch { print("restore playback session (non-fatal): \(error)") }
  }
}
