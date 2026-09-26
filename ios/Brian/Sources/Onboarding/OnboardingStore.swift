import Foundation

struct OnboardingStore {
    private enum Key {
        static let complete = "onboarding.complete"
        static let answers = "onboarding.answers"
        static let persona = "onboarding.persona"
        static let needsSync = "onboarding.needsSync"
    }

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    var isComplete: Bool { defaults.bool(forKey: Key.complete) }

    func save(_ answers: PersonaAnswers) {
        if let data = try? JSONEncoder().encode(answers) { defaults.set(data, forKey: Key.answers) }
        defaults.set(PersonaComposer.compose(answers), forKey: Key.persona)
        defaults.set(true, forKey: Key.complete)
        defaults.set(true, forKey: Key.needsSync)
    }

    var answers: PersonaAnswers {
        guard let data = defaults.data(forKey: Key.answers) else { return [:] }
        return (try? JSONDecoder().decode(PersonaAnswers.self, from: data)) ?? [:]
    }

    var persona: String? { defaults.string(forKey: Key.persona) }
    var needsSync: Bool { defaults.bool(forKey: Key.needsSync) }
    func markSynced() { defaults.set(false, forKey: Key.needsSync) }

    func reset() {
        defaults.removeObject(forKey: Key.complete)
        defaults.removeObject(forKey: Key.answers)
        defaults.removeObject(forKey: Key.persona)
        defaults.removeObject(forKey: Key.needsSync)
    }
}
