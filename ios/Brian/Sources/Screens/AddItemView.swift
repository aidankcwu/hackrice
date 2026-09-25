import SwiftUI

/// Add item, or edit one (`editing`): name, kind, window, days. Saved through
/// `POST /api/protocol` or `PUT /api/protocol/{id}`.
struct AddItemView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    let editing: ProtocolItem?
    @State private var name: String
    @State private var kind: String
    @State private var windowStart: Date
    @State private var windowEnd: Date
    @State private var days: Set<Int>

    private let weekdayNames = ["M", "T", "W", "T", "F", "S", "S"]

    init(editing: ProtocolItem? = nil) {
        self.editing = editing
        _name = State(initialValue: editing?.name ?? "")
        let known = ProtocolTemplates.kinds.contains { $0.value == editing?.kind }
        _kind = State(initialValue: known ? editing?.kind ?? "dose" : "dose")
        _windowStart = State(initialValue: Self.date(editing?.windowStart, fallbackHour: 8))
        _windowEnd = State(initialValue: Self.date(editing?.windowEnd, fallbackHour: 10))
        _days = State(initialValue: Set(editing?.days ?? Array(0...6)))
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Item") {
                    TextField("Name", text: $name)
                    Picker("Kind", selection: $kind) {
                        ForEach(ProtocolTemplates.kinds, id: \.value) { value, title in Text(title).tag(value) }
                    }
                }
                Section("Window") {
                    DatePicker("Start", selection: $windowStart, displayedComponents: .hourAndMinute)
                    DatePicker("End", selection: $windowEnd, displayedComponents: .hourAndMinute)
                }
                Section("Days") {
                    HStack(spacing: 6) {
                        ForEach(0..<7, id: \.self) { day in
                            Button {
                                if days.contains(day) { days.remove(day) } else { days.insert(day) }
                            } label: {
                                Text(weekdayNames[day])
                                    .font(BrianType.chip)
                                    .foregroundStyle(Brian.ink)
                                    .frame(maxWidth: .infinity, minHeight: 44)
                                    .background(days.contains(day) ? Brian.surface2 : Color.clear, in: Capsule())
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel(dayName(day))
                            .accessibilityValue(days.contains(day) ? "Selected" : "Not selected")
                        }
                    }
                }
            }
            .navigationTitle(editing == nil ? "Add item" : "Edit item")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(editing == nil ? "Add item" : "Save") { save() }
                        .buttonStyle(.glassProminent)
                        .foregroundStyle(Brian.page)
                        .disabled(!canSave)
                }
            }
        }
        .tint(Brian.ink)
    }

    private var trimmedName: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// The backend refuses an empty name, no days, or a window that does not move forward.
    private var canSave: Bool {
        !trimmedName.isEmpty && !days.isEmpty && time(windowStart) < time(windowEnd)
    }

    private func save() {
        Task {
            if let editing {
                await appState.updateProtocolItem(editing, name: trimmedName, kind: kind,
                                                  windowStart: time(windowStart), windowEnd: time(windowEnd),
                                                  days: days.sorted())
            } else {
                await appState.addProtocolItem(name: trimmedName, kind: kind, windowStart: time(windowStart),
                                               windowEnd: time(windowEnd), days: days.sorted())
            }
            dismiss()
        }
    }

    private func time(_ date: Date) -> String {
        let values = Calendar.current.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", values.hour ?? 0, values.minute ?? 0)
    }

    /// "19:30" → today at 7:30 PM, for the pickers.
    private static func date(_ hhmm: String?, fallbackHour: Int) -> Date {
        let minutes = hhmm.flatMap(ProtocolSummary.minutes) ?? fallbackHour * 60
        return Calendar.current.date(from: DateComponents(hour: minutes / 60, minute: minutes % 60)) ?? Date()
    }

    private func dayName(_ day: Int) -> String {
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][day]
    }
}
