// One permission, its live state and one button (Allow, or Open Settings once denied).
// Connect lists these in its collapsed "Permissions" section; SetupView used to host them.
import AVFoundation
import CoreBluetooth
import CoreMotion
import Speech
import SwiftUI
import UIKit
import UserNotifications

struct PermissionRow: View {
    @Environment(AppState.self) private var appState
    enum Kind: CaseIterable {
        case bluetooth, localNetwork, microphone, speech, motion, notifications

        var title: String {
            switch self {
            case .bluetooth: "Bluetooth"
            case .localNetwork: "Local network"
            case .microphone: "Microphone"
            case .speech: "Speech"
            case .motion: "Motion"
            case .notifications: "Notifications"
            }
        }
    }

    let kind: Kind
    @State private var status: PermissionStatus = .notYet
    @State private var bluetoothProbe: BluetoothPermissionProbe?

    var body: some View {
        Group {
            if kind != .localNetwork || !appState.usesHostedServer {
                row
            }
        }
    }

    private var row: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(kind.title)
                Text(status.label).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if status != .allowed {
                Button(status == .denied ? "Open Settings" : "Allow") { request() }
                    .buttonStyle(.glass)
            }
        }
        .frame(minHeight: 44)
        .task { await updateStatus() }
    }

    private func request() {
        if status == .denied {
            if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
            return
        }
        switch kind {
        case .bluetooth:
            bluetoothProbe = BluetoothPermissionProbe()
        case .localNetwork:
            break // iOS presents this prompt when the app first accesses the local network.
        case .microphone:
            AVAudioApplication.requestRecordPermission { _ in Task { await updateStatus() } }
        case .speech:
            SFSpeechRecognizer.requestAuthorization { _ in Task { await updateStatus() } }
        case .motion:
            CMMotionActivityManager().queryActivityStarting(from: Date(), to: Date(), to: .main) { _, _ in
                Task { await updateStatus() }
            }
        case .notifications:
            Task {
                _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
                await updateStatus()
            }
        }
        Task {
            try? await Task.sleep(for: .milliseconds(500))
            await updateStatus()
        }
    }

    @MainActor
    private func updateStatus() async {
        switch kind {
        case .bluetooth:
            status = permission(CBManager.authorization)
        case .localNetwork:
            status = .notYet
        case .microphone:
            switch AVAudioApplication.shared.recordPermission {
            case .granted: status = .allowed
            case .denied: status = .denied
            default: status = .notYet
            }
        case .speech:
            switch SFSpeechRecognizer.authorizationStatus() {
            case .authorized: status = .allowed
            case .denied, .restricted: status = .denied
            default: status = .notYet
            }
        case .motion:
            status = permission(CMMotionActivityManager.authorizationStatus())
        case .notifications:
            let settings = await UNUserNotificationCenter.current().notificationSettings()
            switch settings.authorizationStatus {
            case .authorized, .provisional, .ephemeral: status = .allowed
            case .denied: status = .denied
            default: status = .notYet
            }
        }
    }

    private func permission(_ value: CBManagerAuthorization) -> PermissionStatus {
        switch value {
        case .allowedAlways: .allowed
        case .denied, .restricted: .denied
        default: .notYet
        }
    }

    private func permission(_ value: CMAuthorizationStatus) -> PermissionStatus {
        switch value {
        case .authorized: .allowed
        case .denied, .restricted: .denied
        default: .notYet
        }
    }
}

private enum PermissionStatus {
    case allowed, notYet, denied
    var label: String {
        switch self { case .allowed: "Allowed"; case .notYet: "Not yet"; case .denied: "Denied" }
    }
}

private final class BluetoothPermissionProbe: NSObject, CBCentralManagerDelegate {
    private var manager: CBCentralManager?
    override init() {
        super.init()
        manager = CBCentralManager(delegate: self, queue: .main)
    }
    func centralManagerDidUpdateState(_ central: CBCentralManager) {}
}
