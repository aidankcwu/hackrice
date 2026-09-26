// First-launch questionnaire: answers → the persona paragraph PUT to /api/persona.
enum PersonaComposer {
    static func compose(_ answers: PersonaAnswers) -> String {
        let pronoun = answers["pronouns"]?.first ?? "they"
        let isPlural = pronoun != "he" && pronoun != "she"
        let subject = pronoun == "he" ? "He" : pronoun == "she" ? "She" : "They"
        let possessive = pronoun == "he" ? "his" : pronoun == "she" ? "her" : "their"
        let object = pronoun == "he" ? "him" : pronoun == "she" ? "her" : "them"
        let wants = agree("wants", "want", isPlural: isPlural)
        var sentences = ["The wearer uses \(pronoun == "he" ? "he/him" : pronoun == "she" ? "she/her" : "they/them") pronouns."]

        if let day = first("days", answers) {
            let text = ["student": "classes and studying", "desk": "desk or laptop work",
                        "feet": "being on \(possessive) feet", "mixed": "a mix of activities"][day]
            if let text { sentences.append("Most of \(possessive) day is filled with \(text).") }
        }
        let goalWords = answers["goals", default: []].compactMap { id in
            ["sleep": "sleeping at a consistent time", "daylight": "getting daylight before noon",
             "meals": "eating real meals", "move": "moving more or training", "cut": "cutting back",
             "phone": "using the phone less while working", "people": "spending more time with people"][id]
        }
        if !goalWords.isEmpty { sentences.append("\(subject) \(wants) help with \(list(goalWords)).") }
        if let bedtime = first("bedtime", answers), let text = [
            "early": "usually \(agree("falls", "fall", isPlural: isPlural)) asleep before 11 pm",
            "late": "usually \(agree("falls", "fall", isPlural: isPlural)) asleep between 11 pm and 1 am",
            "verylate": "usually \(agree("falls", "fall", isPlural: isPlural)) asleep between 1 and 3 am",
            "varies": "\(agree("has", "have", isPlural: isPlural)) no consistent bedtime"
        ][bedtime] { sentences.append("\(subject) \(text); treat that as the sleep baseline.") }
        if let training = first("training", answers), let text = [
            "none": "\(agree("is", "are", isPlural: isPlural)) not training for anything",
            "casual": "\(agree("works", "work", isPlural: isPlural)) out casually",
            "event": "\(agree("is", "are", isPlural: isPlural)) training for a race or event"
        ][training] { sentences.append("\(subject) \(text).") }
        if let food = first("food", answers), let text = [
            "regular": "usually \(agree("eats", "eat", isPlural: isPlural)) regular meals",
            "skip": "\(agree("skips", "skip", isPlural: isPlural)) meals when busy",
            "desk": "\(agree("tends", "tend", isPlural: isPlural)) to snack at the desk",
            "night": "\(agree("eats", "eat", isPlural: isPlural)) mostly late at night"
        ][food] { sentences.append("\(subject) \(text).") }
        if let caffeine = first("caffeine", answers), let text = [
            "none": "\(agree("does", "do", isPlural: isPlural)) not drink caffeine",
            "keep": "\(wants) caffeine left as it is",
            "less": "\(wants) less caffeine; when it appears, ask whether it is the first one today",
            "noon": "\(agree("has", "have", isPlural: isPlural)) a firm noon caffeine cutoff; note anything after noon"
        ][caffeine] { sentences.append("\(subject) \(text).") }
        let cuts = answers["cut", default: []].filter { $0 != "nothing" }.compactMap {
            ["alcohol": "alcohol", "nicotine": "nicotine", "weed": "weed", "sugar": "sugar and junk food"][$0]
        }
        if !cuts.isEmpty {
            sentences.append("\(subject) \(wants) to cut back on \(list(cuts)); note each sighting, speak only when a pattern forms, and never moralise.")
        }
        if let frequency = first("frequency", answers), let text = [
            "rarely": "Speak rarely, only when a pattern forms.", "moments": "Speak at the moments that matter.",
            "often": "Speak often and hold \(object) to it."
        ][frequency] { sentences.append(text) }
        if let tone = first("tone", answers), let text = [
            "dry": "Keep the tone short and dry.", "warm": "Be warm and encouraging.",
            "blunt": "Be blunt, with no sugarcoating."
        ][tone] { sentences.append(text) }
        if let people = first("people", answers), let text = [
            "silent": "Stay silent when other people are present.",
            "matters": "When other people are present, speak only if it matters."
        ][people] { sentences.append(text) }
        sentences.append("Say a thing once. Give no medical advice, and never mention calories or macros.")
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
