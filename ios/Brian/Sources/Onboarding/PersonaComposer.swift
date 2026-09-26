// First-launch questionnaire: answers → the wearer paragraph PUT to /api/persona/wearer.
// The backend joins it under the fixed operator persona ("About the wearer:"), so nothing
// here can loosen how Bryan behaves; the closing lines restate the standard anyway.
enum PersonaComposer {
    /// The lines every paragraph ends with, whatever was answered. Tone and timing are the
    /// wearer's; the goal is not.
    static let standard = "Whatever the tone and timing, the standard does not move: every line serves a longer, healthier life, which means consistent sleep, early daylight, real meals, daily movement, fewer harmful habits, and time with people. Say a thing once. Give no medical advice, and never mention calories or macros."

    static func compose(_ answers: PersonaAnswers) -> String {
        let pronoun = answers["pronouns"]?.first ?? "they"
        let custom = pronoun == "other"
            ? (PersonaAnswers.cleanFreeText(answers["pronouns.text"]?.first)) : nil
        // Custom pronouns are quoted, and the wearer is named rather than conjugated for.
        let isPlural = custom == nil && pronoun != "he" && pronoun != "she"
        let subject = custom != nil ? "The wearer" : pronoun == "he" ? "He" : pronoun == "she" ? "She" : "They"
        let possessive = custom != nil ? "the wearer's" : pronoun == "he" ? "his" : pronoun == "she" ? "her" : "their"
        let object = custom != nil ? "the wearer" : pronoun == "he" ? "him" : pronoun == "she" ? "her" : "them"
        let wants = agree("wants", "want", isPlural: isPlural)
        let pronounText = custom ?? (pronoun == "he" ? "he/him" : pronoun == "she" ? "she/her" : "they/them")
        var sentences = ["The wearer uses \(pronounText) pronouns."]

        let dayWords = answers["days", default: []].compactMap { id in
            ["student": "classes and studying", "desk": "desk or laptop work",
             "meetings": "meetings and calls", "feet": "being on \(possessive) feet",
             "physical": "physical or outdoor work", "driving": "driving or commuting",
             "care": "looking after family", "creative": "creative or studio work",
             "travel": "travel", "home": "time at home", "mixed": "days that change a lot"][id]
        }
        if !dayWords.isEmpty { sentences.append("Most of \(possessive) day is filled with \(list(dayWords)).") }

        let goalWords = answers["goals", default: []].compactMap { id in
            ["sleep": "sleeping at a consistent time", "daylight": "getting outside early in the day",
             "meals": "eating real meals at regular times", "move": "moving more or keeping up training",
             "sitting": "breaking up long stretches of sitting or screens", "cut": "cutting back",
             "phone": "using the phone less when it is not helping", "people": "spending more time with people",
             "stress": "winding down and handling stress", "water": "drinking more water",
             "routine": "keeping habits on busy or travel days",
             "meds": "taking medication or supplements on time"][id]
        }
        if !goalWords.isEmpty {
            sentences.append("\(subject) \(wants) help with \(list(goalWords)).")
        } else if answers["goals"]?.contains("notsure") == true {
            sentences.append("\(subject) \(agree("has", "have", isPlural: isPlural)) not picked a focus yet; watch first and name the pattern that matters most.")
        }
        if let bedtime = first("bedtime", answers), let text = [
            "early": "usually \(agree("falls", "fall", isPlural: isPlural)) asleep before 11 pm",
            "late": "usually \(agree("falls", "fall", isPlural: isPlural)) asleep between 11 pm and 1 am",
            "verylate": "usually \(agree("falls", "fall", isPlural: isPlural)) asleep after 1 am",
            "shift": "\(agree("works", "work", isPlural: isPlural)) nights or shifts, so sleep moves around",
            "varies": "\(agree("has", "have", isPlural: isPlural)) no consistent bedtime"
        ][bedtime] { sentences.append("\(subject) \(text); treat that as the sleep baseline.") }
        if let training = first("training", answers), let text = [
            "none": "\(agree("does", "do", isPlural: isPlural)) not move much right now",
            "casual": "\(agree("moves", "move", isPlural: isPlural)) when \(subject.lowercased() == "the wearer" ? "possible" : "\(subject.lowercased()) can")",
            "routine": "\(agree("keeps", "keep", isPlural: isPlural)) a regular training routine",
            "event": "\(agree("is", "are", isPlural: isPlural)) training for a race, sport or event",
            "rehab": "\(agree("is", "are", isPlural: isPlural)) getting back into movement after a break or injury; go gently"
        ][training] { sentences.append("\(subject) \(text).") }
        if let food = first("food", answers), let text = [
            "regular": "usually \(agree("eats", "eat", isPlural: isPlural)) regular meals",
            "cook": "\(agree("cooks", "cook", isPlural: isPlural)) most meals",
            "out": "mostly \(agree("eats", "eat", isPlural: isPlural)) out or orders delivery",
            "skip": "\(agree("skips", "skip", isPlural: isPlural)) meals when busy",
            "desk": "\(agree("snacks", "snack", isPlural: isPlural)) more than \(subject.lowercased() == "the wearer" ? "the wearer eats" : "\(subject.lowercased()) \(agree("eats", "eat", isPlural: isPlural))") meals",
            "night": "\(agree("eats", "eat", isPlural: isPlural)) mostly late at night"
        ][food] { sentences.append("\(subject) \(text).") }
        if let caffeine = first("caffeine", answers), let text = [
            "none": "\(agree("does", "do", isPlural: isPlural)) not drink caffeine",
            "keep": "\(wants) caffeine left as it is",
            "less": "\(wants) less caffeine; when it appears, ask whether it is the first one today",
            "noon": "\(agree("has", "have", isPlural: isPlural)) a firm noon caffeine cutoff; note anything after noon",
            "track": "\(agree("is", "are", isPlural: isPlural)) not sure about caffeine yet; count it for a few days before saying anything"
        ][caffeine] { sentences.append("\(subject) \(text).") }
        let cuts = answers["cut", default: []].compactMap {
            ["alcohol": "alcohol", "nicotine": "nicotine", "weed": "weed", "sugar": "sugar and junk food",
             "energy": "energy drinks", "screens": "late-night screens"][$0]
        }
        if !cuts.isEmpty {
            sentences.append("\(subject) \(wants) to cut back on \(list(cuts)); note each sighting, speak only when a pattern forms, and never moralise.")
        }
        if let frequency = first("frequency", answers), let text = [
            "patterns": "Speak rarely, only when a pattern forms over days.",
            "daily": "Speak once or twice a day, about the things that matter most.",
            "moments": "Speak at the moments that matter, as they happen.",
            "often": "Speak often and hold \(object) to it.",
            "every": "Speak every time something worth noting is spotted."
        ][frequency] { sentences.append(text) }
        let quiet = answers["quiet", default: []].compactMap {
            ["morning": "first thing in the morning", "work": "during work or class hours",
             "meals": "during meals", "driving": "while \(subject.lowercased() == "the wearer" ? "the wearer is" : "\(subject.lowercased()) \(agree("is", "are", isPlural: isPlural))") driving",
             "evening": "late in the evening"][$0]
        }
        if !quiet.isEmpty { sentences.append("Stay quiet \(list(quiet)).") }
        if let tone = first("tone", answers), let text = [
            "dry": "Keep the tone short and dry.", "warm": "Be warm and encouraging.",
            "blunt": "Be blunt, with no sugarcoating.",
            "coach": "Talk like a coach: specific and practical, one concrete next step.",
            "curious": "Ask a short question before telling \(object) anything.",
            "light": "Keep it light, with some humour, never at \(possessive) expense."
        ][tone] { sentences.append(text) }
        if let people = first("people", answers), let text = [
            "silent": "Stay silent when other people are present.",
            "matters": "When other people are present, speak only if it matters.",
            "brief": "When other people are present, keep it to a few words.",
            "noask": "When other people are present, never ask questions; only tell."
        ][people] { sentences.append(text) }
        sentences.append(standard)
        return sentences.joined(separator: " ")
    }

    private static func first(_ key: String, _ answers: PersonaAnswers) -> String? {
        answers[key]?.first
    }

    /// Picks the singular or plural verb form to match the subject's grammatical number.
    private static func agree(_ singular: String, _ plural: String, isPlural: Bool) -> String {
        isPlural ? plural : singular
    }

    private static func list(_ values: [String]) -> String {
        guard values.count > 1 else { return values.first ?? "" }
        if values.count == 2 { return values.joined(separator: " and ") }
        return values.dropLast().joined(separator: ", ") + ", and " + values.last!
    }
}
