// Models the screens read. Field names follow the backend's snake_case JSON via a
// decoder set to .convertFromSnakeCase; every field the spec does not name is optional
// (IOS_SPEC.md "Data layer"). Coder A may ADD fields; the ones here are the contract
// the Screens coder builds against and must not be renamed.
//
// Verified against Fixtures/*.json (captured from the backend in --source sim) and the
// shapes in docs/API.md. Where the wire shape differs from the Swift contract, a custom
// init(from:) maps it; the Swift property names are unchanged. Decoding is lenient on
// purpose: one odd field in one row must never blank the whole Today screen.
import Foundation

// MARK: - Healthspan (GET /api/healthspan)

struct Healthspan: Codable, Equatable {
    let overall: Double          // 0-100
    let hoursToday: Double       // signed healthy-life hours today
    let yearsDelta: Double?
    let day: String?
    /// True when at least one factor was measured. The wire sends `measured` as
    /// `{count, total}`; `measuredCount` / `measuredTotal` carry the numbers.
    let measured: Bool?
    /// "glasses" | "whoop" | "health" | "seeded" (chip text). The wire sends a
    /// per-factor map `{factor: {source, basis, detail}}`; this is the one word the
    /// chip shows, derived by `Healthspan.chip(fromProvenance:)`.
    let provenance: String?
    // Added by Coder A (not in the original contract):
    let measuredCount: Int?
    let measuredTotal: Int?

    init(overall: Double, hoursToday: Double, yearsDelta: Double? = nil, day: String? = nil,
         measured: Bool? = nil, provenance: String? = nil,
         measuredCount: Int? = nil, measuredTotal: Int? = nil) {
        self.overall = overall
        self.hoursToday = hoursToday
        self.yearsDelta = yearsDelta
        self.day = day
        self.measured = measured
        self.provenance = provenance
        self.measuredCount = measuredCount
        self.measuredTotal = measuredTotal
    }

    /// A copy with a different chip word. Demo mode uses it to force "seeded".
    func with(provenance: String?) -> Healthspan {
        Healthspan(overall: overall, hoursToday: hoursToday, yearsDelta: yearsDelta, day: day,
                   measured: measured, provenance: provenance,
                   measuredCount: measuredCount, measuredTotal: measuredTotal)
    }

    private enum CodingKeys: String, CodingKey {
        case overall, hoursToday, yearsDelta, day, measured, provenance, measuredCount, measuredTotal
    }

    private struct MeasuredCount: Decodable { let count: Int?; let total: Int? }
    private struct ProvenanceEntry: Decodable { let source: String?; let basis: String? }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        overall = try c.decode(Double.self, forKey: .overall)
        hoursToday = try c.decode(Double.self, forKey: .hoursToday)
        yearsDelta = try? c.decodeIfPresent(Double.self, forKey: .yearsDelta)
        day = try? c.decodeIfPresent(String.self, forKey: .day)

        // measured: {count, total} on the wire; a plain Bool is accepted too.
        if let flag = try? c.decodeIfPresent(Bool.self, forKey: .measured) {
            measured = flag
            measuredCount = try? c.decodeIfPresent(Int.self, forKey: .measuredCount)
            measuredTotal = try? c.decodeIfPresent(Int.self, forKey: .measuredTotal)
        } else if let m = try? c.decodeIfPresent(MeasuredCount.self, forKey: .measured) {
            measuredCount = m.count
            measuredTotal = m.total
            measured = m.count.map { $0 > 0 }
        } else {
            measured = nil
            measuredCount = try? c.decodeIfPresent(Int.self, forKey: .measuredCount)
            measuredTotal = try? c.decodeIfPresent(Int.self, forKey: .measuredTotal)
        }

        // provenance: a {factor: {source, basis}} map on the wire; a plain string is
        // accepted too (it is what `encode(to:)` writes).
        if let word = try? c.decodeIfPresent(String.self, forKey: .provenance) {
            provenance = word
        } else if let map = try? c.decodeIfPresent([String: ProvenanceEntry].self, forKey: .provenance) {
            provenance = Healthspan.chip(fromProvenance: map.values.map { ($0.source, $0.basis) })
        } else {
            provenance = nil
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(overall, forKey: .overall)
        try c.encode(hoursToday, forKey: .hoursToday)
        try c.encodeIfPresent(yearsDelta, forKey: .yearsDelta)
        try c.encodeIfPresent(day, forKey: .day)
        try c.encodeIfPresent(measured, forKey: .measured)
        try c.encodeIfPresent(provenance, forKey: .provenance)
        try c.encodeIfPresent(measuredCount, forKey: .measuredCount)
        try c.encodeIfPresent(measuredTotal, forKey: .measuredTotal)
    }

    /// The chip word. A `live` row is one a connected device (or the glasses) wrote;
    /// anything else (`seeded`, `derived`, `missing`) is not a live measurement. With
    /// no live row at all the chip reads "seeded". Otherwise the most direct live
    /// source wins: glasses, then WHOOP, then Apple Health (watch / phone).
    static func chip(fromProvenance entries: [(source: String?, basis: String?)]) -> String {
        let liveBases = Set(entries.filter { $0.source == "live" }.map { ($0.basis ?? "glasses").lowercased() })
        if liveBases.isEmpty { return "seeded" }
        if liveBases.contains("glasses") { return "glasses" }
        if liveBases.contains("whoop") { return "whoop" }
        if !liveBases.isDisjoint(with: ["health", "apple_watch", "phone", "apple_health"]) { return "health" }
        return "glasses"
    }
}

// MARK: - Episodes (GET /api/episodes)

struct Reported: Codable, Equatable {
    let confirmed: Bool?
    let count: Double?
    let foodType: String?
    let note: String?
}

struct Episode: Codable, Identifiable, Equatable {
    let id: String
    let kind: String             // meal, conversation, screen_block, ...
    let label: String?
    let startT: Double
    let endT: Double?
    let reported: Reported?
}

// MARK: - Decisions (GET /api/decisions)

struct DecisionAction: Codable, Equatable {
    let type: String             // annotate, log_insight, watch, speak, ask, remember, act, nothing
    let text: String?
    let line: String?
    let outcome: String?         // handed_off:<id>, fast_pathed, conversation_active, sent, acted, ...
    // Added by Coder A:
    let urgency: String?         // speak: low | normal | high
    let kind: String?            // act: calendar_block | screen_shield

    init(type: String, text: String? = nil, line: String? = nil, outcome: String? = nil,
         urgency: String? = nil, kind: String? = nil) {
        self.type = type
        self.text = text
        self.line = line
        self.outcome = outcome
        self.urgency = urgency
        self.kind = kind
    }

    private enum CodingKeys: String, CodingKey { case type, text, line, outcome, urgency, kind }

    // Actions are `dict[str, Any]` on the backend; read each field only if it is a string.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = (try? c.decodeIfPresent(String.self, forKey: .type)) ?? "nothing"
        text = try? c.decodeIfPresent(String.self, forKey: .text)
        line = try? c.decodeIfPresent(String.self, forKey: .line)
        outcome = try? c.decodeIfPresent(String.self, forKey: .outcome)
        urgency = try? c.decodeIfPresent(String.self, forKey: .urgency)
        kind = try? c.decodeIfPresent(String.self, forKey: .kind)
    }

    /// A proposal to say something to the wearer.
    var proposesSpeech: Bool { type == "speak" || type == "ask" }
    /// The voice agent took it over, so it was said in a conversation, not held back.
    var handedOff: Bool { outcome?.hasPrefix("handed_off") == true }
}

struct Decision: Codable, Identifiable, Equatable {
    let id: String
    let t: Double
    let trigger: String
    let interpretation: String
    let actions: [DecisionAction]
    let spoke: Bool
    let episodeId: String?
    // Added by Coder A:
    let dropped: Bool?

    init(id: String, t: Double, trigger: String, interpretation: String = "",
         actions: [DecisionAction] = [], spoke: Bool = false, episodeId: String? = nil,
         dropped: Bool? = nil) {
        self.id = id
        self.t = t
        self.trigger = trigger
        self.interpretation = interpretation
        self.actions = actions
        self.spoke = spoke
        self.episodeId = episodeId
        self.dropped = dropped
    }

    private enum CodingKeys: String, CodingKey {
        case id, t, trigger, interpretation, actions, spoke, episodeId, dropped
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        t = try c.decode(Double.self, forKey: .t)
        trigger = (try? c.decodeIfPresent(String.self, forKey: .trigger)) ?? ""
        interpretation = (try? c.decodeIfPresent(String.self, forKey: .interpretation)) ?? ""
        actions = (try? c.decodeIfPresent([DecisionAction].self, forKey: .actions)) ?? []
        spoke = (try? c.decodeIfPresent(Bool.self, forKey: .spoke)) ?? false
        episodeId = try? c.decodeIfPresent(String.self, forKey: .episodeId)
        dropped = try? c.decodeIfPresent(Bool.self, forKey: .dropped)
    }

    /// Proposed speech (a speak or ask the voice agent did not take over) that did not
    /// reach the wearer. What "Held back N today" counts (brian-ui law 4).
    var heldBack: Bool {
        !spoke && actions.contains { $0.proposesSpeech && !$0.handedOff }
    }
}

// MARK: - Protocol (GET /api/protocol/today)

struct ProtocolItem: Codable, Identifiable, Equatable {
    let id: String
    let name: String
    let kind: String             // dose, meal, winddown, walk (backend PROTOCOL_KINDS)
    let windowStart: String      // "08:00"
    let windowEnd: String
    let days: [Int]              // 0 = Monday
    let status: String           // waiting, seen, missed, done, undone
    let seenAt: Double?          // wire: seen_t
    let evidenceRef: String?     // "<decision_id>/<frame_ref>"; thumbnail is GET /api/evidence/<evidence_ref>

    init(id: String, name: String, kind: String, windowStart: String, windowEnd: String,
         days: [Int], status: String, seenAt: Double? = nil, evidenceRef: String? = nil) {
        self.id = id
        self.name = name
        self.kind = kind
        self.windowStart = windowStart
        self.windowEnd = windowEnd
        self.days = days
        self.status = status
        self.seenAt = seenAt
        self.evidenceRef = evidenceRef
    }

    /// Raw values are the camelCase the decoder sees after .convertFromSnakeCase.
    private enum CodingKeys: String, CodingKey {
        case id, name, kind, windowStart, windowEnd, days, status
        case seenAt = "seenT"
        case evidenceRef
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        kind = (try? c.decodeIfPresent(String.self, forKey: .kind)) ?? "dose"
        windowStart = (try? c.decodeIfPresent(String.self, forKey: .windowStart)) ?? ""
        windowEnd = (try? c.decodeIfPresent(String.self, forKey: .windowEnd)) ?? ""
        days = (try? c.decodeIfPresent([Int].self, forKey: .days)) ?? Array(0...6)
        status = (try? c.decodeIfPresent(String.self, forKey: .status)) ?? "waiting"
        seenAt = try? c.decodeIfPresent(Double.self, forKey: .seenAt)
        evidenceRef = try? c.decodeIfPresent(String.self, forKey: .evidenceRef)
    }

    /// A copy with a new status (fixture mode's Mark done / Undo).
    func with(status: String) -> ProtocolItem {
        ProtocolItem(id: id, name: name, kind: kind, windowStart: windowStart, windowEnd: windowEnd,
                     days: days, status: status, seenAt: seenAt, evidenceRef: evidenceRef)
    }
}

/// `GET /api/protocol/today` wraps the items: `{day, items}`.
struct ProtocolToday: Codable, Equatable {
    let day: String?
    let items: [ProtocolItem]
}

// MARK: - Small responses

/// `GET /healthz`: process liveness only, answers without a token.
struct Healthz: Codable, Equatable {
    let ok: Bool
    let uptimeS: Double?
}

/// One row of `GET /api/evidence/{decision_id}`: frame metadata, no bytes.
struct EvidenceFrame: Codable, Equatable {
    let decisionId: String?
    let frameRef: String
    let t: Double?
}
