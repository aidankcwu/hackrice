import SwiftUI

struct AddItemView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var kind = "dose"
    @State private var windowStart = Calendar.current.date(from: DateComponents(hour: 8)) ?? Date()
    @State private var windowEnd = Calendar.current.date(from: DateComponents(hour: 10)) ?? Date()
    @State private var days = Set(0...6)

    private let kinds = [("dose", "Medication"), ("meal", "Meal"), ("sleep", "Sleep"), ("walk", "Walk")]
    private let weekdayNames = ["M", "T", "W", "T", "F", "S", "S"]

    var body: some View {
        NavigationStack {
            Form {
                Section("Item") {
                    TextField("Name", text: $name)
                    Picker("Kind", selection: $kind) {
                        ForEach(kinds, id: \.0) { value, title in Text(title).tag(value) }
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
            .navigationTitle("Add item")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add item") {
                        Task {
                            await appState.addProtocolItem(
                                name: name.trimmingCharacters(in: .whitespacesAndNewlines), kind: kind,
                                windowStart: time(windowStart), windowEnd: time(windowEnd), days: days.sorted())
                            dismiss()
                        }
                    }
                    .buttonStyle(.glassProminent)
                    .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || days.isEmpty)
                }
            }
        }
    }

    private func time(_ date: Date) -> String {
        let values = Calendar.current.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", values.hour ?? 0, values.minute ?? 0)
    }

    private func dayName(_ day: Int) -> String {
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][day]
    }
}
