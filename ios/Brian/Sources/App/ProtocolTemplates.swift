// DEMO_UI_PRD.md "Protocol tab · Templates": the six groups of the web library (Doses,
// Meals, Movement, Light, Sleep, Screens), copied from phone/src/content/protocols.ts.
// The backend knows four kinds, so Light and Movement become `walk`, Sleep and Screens
// `winddown`. The backend needs start < end, so windows that cross midnight end at it.
import Foundation

struct ProtocolTemplate: Equatable, Identifiable {
    let name: String
    let kind: String
    let windowStart: String
    let windowEnd: String
    var days: [Int] = Array(0...6)

    var id: String { "\(name)|\(windowStart)" }

    /// nil every day; otherwise "Mondays", "Mondays, Thursdays".
    var daysText: String? {
        guard Set(days) != Set(0...6) else { return nil }
        let names = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"]
        return days.sorted().filter { (0...6).contains($0) }.map { names[$0] }.joined(separator: ", ")
    }
}

struct ProtocolTemplateGroup: Equatable, Identifiable {
    let title: String
    let symbol: String
    let templates: [ProtocolTemplate]

    var id: String { title }
}

enum ProtocolTemplates {
    /// The backend's four kinds (PROTOCOL_KINDS), with the words Add item shows.
    static let kinds: [(value: String, title: String)] = [
        ("dose", "Dose"), ("meal", "Meal"), ("walk", "Walk"), ("winddown", "Wind‑down"),
    ]

    static let groups: [ProtocolTemplateGroup] = [
        ProtocolTemplateGroup(title: "Doses", symbol: "pills.fill", templates: [
            ProtocolTemplate(name: "Morning dose", kind: "dose", windowStart: "07:00", windowEnd: "10:00"),
            ProtocolTemplate(name: "Evening dose", kind: "dose", windowStart: "19:00", windowEnd: "22:00"),
            ProtocolTemplate(name: "Essentials", kind: "dose", windowStart: "05:35", windowEnd: "06:00"),
            ProtocolTemplate(name: "Omega-3", kind: "dose", windowStart: "05:35", windowEnd: "06:00"),
            ProtocolTemplate(name: "Weekly pen", kind: "dose", windowStart: "07:00", windowEnd: "10:00", days: [0]),
        ]),
        ProtocolTemplateGroup(title: "Meals", symbol: "fork.knife", templates: [
            ProtocolTemplate(name: "First meal", kind: "meal", windowStart: "05:35", windowEnd: "06:00"),
            ProtocolTemplate(name: "Lunch window", kind: "meal", windowStart: "11:30", windowEnd: "14:00"),
            ProtocolTemplate(name: "Protein-first meals", kind: "meal", windowStart: "08:00", windowEnd: "19:00"),
            ProtocolTemplate(name: "Last meal", kind: "meal", windowStart: "18:00", windowEnd: "19:00"),
        ]),
        ProtocolTemplateGroup(title: "Movement", symbol: "figure.walk", templates: [
            ProtocolTemplate(name: "Vigorous exercise", kind: "walk", windowStart: "06:30", windowEnd: "18:30"),
            ProtocolTemplate(name: "Movement daily", kind: "walk", windowStart: "07:00", windowEnd: "20:00"),
            ProtocolTemplate(name: "Outdoor walk", kind: "walk", windowStart: "19:30", windowEnd: "20:00"),
        ]),
        ProtocolTemplateGroup(title: "Light", symbol: "sun.max.fill", templates: [
            ProtocolTemplate(name: "Up by 6:30", kind: "walk", windowStart: "06:00", windowEnd: "06:30"),
            ProtocolTemplate(name: "Daylight walk", kind: "walk", windowStart: "07:00", windowEnd: "10:00"),
            ProtocolTemplate(name: "Red light", kind: "walk", windowStart: "08:00", windowEnd: "08:10"),
        ]),
        ProtocolTemplateGroup(title: "Sleep", symbol: "moon.fill", templates: [
            ProtocolTemplate(name: "Wind‑down", kind: "winddown", windowStart: "21:30", windowEnd: "23:00"),
            ProtocolTemplate(name: "Bed", kind: "winddown", windowStart: "22:30", windowEnd: "23:59"),
            ProtocolTemplate(name: "Nap", kind: "winddown", windowStart: "13:00", windowEnd: "15:00"),
        ]),
        ProtocolTemplateGroup(title: "Screens", symbol: "display", templates: [
            ProtocolTemplate(name: "Screens off", kind: "winddown", windowStart: "21:30", windowEnd: "22:30"),
            ProtocolTemplate(name: "Screens off early", kind: "winddown", windowStart: "19:30", windowEnd: "20:30"),
            ProtocolTemplate(name: "No screens in bed", kind: "winddown", windowStart: "22:30", windowEnd: "23:59"),
        ]),
    ]

    /// Already in today's protocol under the same name (case and spacing ignored).
    static func isAdded(_ template: ProtocolTemplate, in items: [ProtocolItem]) -> Bool {
        let key = normalized(template.name)
        return items.contains { normalized($0.name) == key }
    }

    private static func normalized(_ name: String) -> String {
        name.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }
}
