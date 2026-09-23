//  PhoneSensors.swift
//  docs/PERSON_A.md A13. Drop into the CameraAccess target.
//
//  Two sensors, no arithmetic. SPEC §11.2 is explicit: "the phone computes nothing
//  from them" — `accel_rms` is derived on the Mac (`sources/glasses.py: AccelWindow`),
//  and anything this file smoothed, filtered, or averaged would have to be un-averaged
//  there. Read, store, hand over. That is the entire contract.
//
//  ## The two ways to get this silently wrong
//
//  1. **Raw accelerometer, never `deviceMotion.userAcceleration`.** The Mac computes
//     `|a| - 1g` and assumes gravity is *included*. `userAcceleration` has it removed,
//     so every magnitude collapses toward 0, and `accel_rms` reads a near-constant ~1.0
//     for both a sprint and a nap. `AccelWindow._check_units` logs a warning if it
//     smells this, but the fix is here: `CMAccelerometerData.acceleration`, nothing else.
//  2. **CoreLocation reports -1 for "speed unknown".** Sent raw that reads on the Mac
//     as travelling backwards at 1 m/s. `nil` (→ JSON null) is the honest answer.
//
//  ## Why a burst and not one sample
//
//  One sample per packet cannot distinguish "moving" from "tilted", and forces the
//  Mac's window to span 8 real seconds just to collect 8 points — so a step or a head
//  turn shows up seconds late. At 20 Hz, 20 samples cover the last second for ~500
//  bytes against a ~53 KB packet, and `AccelWindow` switches itself to a 2.5 s window
//  the first time it sees one. Effectively free; take it.

import CoreLocation
import CoreMotion
import Foundation

/// `@unchecked Sendable`: every property read across threads is behind `lock`.
/// CoreMotion delivers on its own operation queue; CoreLocation delivers on the thread
/// this object was created on, which is also where `start()`/`stop()` are called, so
/// `running` alone needs no lock.
final class PhoneSensors: NSObject, @unchecked Sendable {

  /// One raw accelerometer vector in g, gravity included (`CMAcceleration`).
  struct Accel: Sendable {
    let x: Double
    let y: Double
    let z: Double
  }

  /// 20 Hz × 20 samples = the last ~1.0 s, which is what `wire.capture_packet`
  /// documents `accel_burst` to be.
  static let sampleHz: Double = 20
  static let ringCapacity = 20

  private let motion = CMMotionManager()
  private let locator = CLLocationManager()
  private let queue: OperationQueue
  private let lock = NSLock()

  private var _latest: Accel?
  private var _ring: [Accel] = []
  private var _speed: Double?
  private var running = false

  /// The newest sample, or nil before the first one lands / if there is no
  /// accelerometer (the Simulator). Becomes `"accel": null` on the wire.
  var latest: Accel? {
    lock.lock()
    defer { lock.unlock() }
    return _latest
  }

  /// The last ~1 s as `[[x, y, z]]`, oldest first — the shape `ingest._as_burst` parses.
  ///
  /// Non-destructive on purpose. Draining would make the burst mean "everything since
  /// the last packet", which at a 1.5 s interval is 30 samples of which the Mac keeps
  /// the newest anyway; a fixed trailing window is both smaller and better defined.
  /// Nothing accumulates either way — the ring is capped, so this is still "drop,
  /// never queue".
  var burst: [[Double]] {
    lock.lock()
    defer { lock.unlock() }
    return _ring.map { [$0.x, $0.y, $0.z] }
  }

  /// Ground speed in m/s, or nil when unknown. Becomes `"gps_speed": null`.
  var speed: Double? {
    lock.lock()
    defer { lock.unlock() }
    return _speed
  }

  /// Create on the main thread: `CLLocationManager` delivers its delegate callbacks on
  /// the run loop of the thread it was initialised on, and a background thread has none.
  override init() {
    queue = OperationQueue()
    queue.name = "phone.sensors.accel"
    queue.maxConcurrentOperationCount = 1
    queue.qualityOfService = .userInitiated
    super.init()
    locator.delegate = self
    locator.desiredAccuracy = kCLLocationAccuracyBest
  }

  // MARK: - Lifecycle

  /// Needs `NSMotionUsageDescription` and `NSLocationWhenInUseUsageDescription` in
  /// Info.plist. Without the location string the authorization prompt never appears
  /// and `speed` stays nil forever, with no error anywhere.
  func start() {
    guard !running else { return }
    running = true

    if motion.isAccelerometerAvailable {
      motion.accelerometerUpdateInterval = 1.0 / Self.sampleHz
      motion.startAccelerometerUpdates(to: queue) { [weak self] data, _ in
        // RAW: gravity included. See the header — this is the one line that matters.
        guard let self, let a = data?.acceleration else { return }
        self.append(Accel(x: a.x, y: a.y, z: a.z))
      }
    }

    locator.requestWhenInUseAuthorization()
    locator.startUpdatingLocation()
  }

  func stop() {
    running = false
    motion.stopAccelerometerUpdates()
    locator.stopUpdatingLocation()
    lock.lock()
    _ring.removeAll(keepingCapacity: true)
    lock.unlock()
  }

  private func append(_ sample: Accel) {
    lock.lock()
    _latest = sample
    _ring.append(sample)
    if _ring.count > Self.ringCapacity {
      _ring.removeFirst(_ring.count - Self.ringCapacity)
    }
    lock.unlock()
  }
}

// MARK: - CoreLocation

extension PhoneSensors: CLLocationManagerDelegate {

  func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
    guard let location = locations.last else { return }
    // -1 means "unknown" for both fields; a negative accuracy invalidates the speed
    // even when the speed itself looks plausible.
    let usable = location.speed >= 0 && location.speedAccuracy >= 0
    lock.lock()
    _speed = usable ? location.speed : nil
    lock.unlock()
  }

  func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
    // Denied, or simply no fix indoors. Neither is fatal: the Mac reads a null
    // `gps_speed` as "no reading" and every other field still rides.
    lock.lock()
    _speed = nil
    lock.unlock()
  }

  /// Updates only start flowing once the user answers the prompt, so re-arm here.
  func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
    switch manager.authorizationStatus {
    case .authorizedWhenInUse, .authorizedAlways:
      if running { manager.startUpdatingLocation() }
    default:
      lock.lock()
      _speed = nil
      lock.unlock()
    }
  }
}
