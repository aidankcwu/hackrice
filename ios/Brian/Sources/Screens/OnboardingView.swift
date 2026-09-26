// First-launch questionnaire: a welcome step, then one multiple-choice question per
// screen. Single choice advances on tap after a short beat; multi choice toggles up to
// `maxPicks` and continues with a button. The answers feed Bryan's persona only. Full
// screen before Connect on the first launch, and again from Settings' "Redo questions".
import SwiftUI

struct OnboardingView: View {
    let questions: [PersonaQuestion]
    let onFinish: (PersonaAnswers) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// -1 is the welcome step; 0..<questions.count are the questions.
    @State private var step = -1
    /// Picks survive Back, so earlier answers are still selected when revisited.
    @State private var answers: PersonaAnswers = [:]
    /// The direction of the last move, read by the step transition.
    @State private var forward = true
    /// Set during the beat after a single-choice tap, so a second tap cannot double-advance.
    @State private var advancing = false

    var body: some View {
        ZStack {
            Brian.page.ignoresSafeArea()
            Group {
                if step < 0 {
                    welcome
                } else if questions.indices.contains(step) {
                    questionStep(questions[step])
                }
            }
            .id(step)
            .transition(stepTransition)
        }
        .tint(Brian.ink)
    }

    // MARK: Welcome

    private var welcome: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Welcome to Zeroist")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(Brian.ink)
                    .accessibilityAddTraits(.isHeader)
                Text("A few quick questions so Bryan knows how to help. About a minute.")
                    .font(BrianType.body)
                    .foregroundStyle(Brian.text)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Space.gutter)
            .padding(.top, 96)
        }
        .scrollBounceBehavior(.basedOnSize)
        .safeAreaInset(edge: .bottom) {
            primaryButton("Start", enabled: !questions.isEmpty) { move(to: 0) }
        }
    }

    // MARK: Question

    private func questionStep(_ question: PersonaQuestion) -> some View {
        let picked = answers[question.id] ?? []
        return VStack(spacing: 0) {
            header
            ScrollView {
                VStack(alignment: .leading, spacing: Space.panel) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(question.prompt)
                            .font(.largeTitle.weight(.bold))
                            .foregroundStyle(Brian.ink)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityAddTraits(.isHeader)
                        if let detail = question.detail {
                            Text(detail)
                                .font(BrianType.secondary)
                                .foregroundStyle(Brian.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    VStack(spacing: 8) {
                        ForEach(question.options) { option in
                            OptionRow(
                                label: option.label,
                                isPicked: picked.contains(option.id),
                                isMulti: question.isMulti
                            ) {
                                tap(option, in: question)
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Space.gutter)
                .padding(.top, Space.panel)
                .padding(.bottom, Space.section)
            }
            .scrollBounceBehavior(.basedOnSize)
        }
        .safeAreaInset(edge: .bottom) {
            if question.isMulti {
                primaryButton("Continue", enabled: !picked.isEmpty) { advance() }
            }
        }
        .sensoryFeedback(.selection, trigger: picked)
    }

    /// Back, then "3 of 11" and a thin bar.
    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Button {
                    move(to: step - 1)
                } label: {
                    Label("Back", systemImage: "chevron.left")
                        .labelStyle(.titleAndIcon)
                }
                .buttonStyle(.glass)
                .controlSize(.large)
                .disabled(advancing)
                Spacer()
                Text("\(step + 1) of \(questions.count)")
                    .font(BrianType.secondary.monospacedDigit())
                    .foregroundStyle(Brian.muted)
                    .accessibilityLabel("Question \(step + 1) of \(questions.count)")
            }
            // Bar text stops growing where the system's does ("Back" would break mid-word);
            // a long press shows it large, as the header's Start watching does.
            .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
            .accessibilityShowsLargeContentViewer()
            ProgressView(value: Double(step + 1), total: Double(max(questions.count, 1)))
                .tint(Brian.ink)
                .accessibilityHidden(true)
        }
        .padding(.horizontal, Space.gutter)
        .padding(.top, 8)
    }

    private func primaryButton(_ title: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(BrianType.body.weight(.semibold))
                // Ink fill flips with the appearance, so the label is page-coloured (as in Connect).
                .foregroundStyle(Brian.page)
                .frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.glassProminent)
        .controlSize(.large)
        .disabled(!enabled)
        .padding(.horizontal, Space.gutter)
        .padding(.bottom, 16)
    }

    // MARK: Behaviour

    private func tap(_ option: PersonaOption, in question: PersonaQuestion) {
        guard !advancing else { return }
        var picked = answers[question.id] ?? []
        if !question.isMulti {
            answers[question.id] = [option.id]
            advancing = true
            Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(250))
                advancing = false
                advance()
            }
            return
        }
        if let index = picked.firstIndex(of: option.id) {
            picked.remove(at: index)
        } else if option.id == question.exclusiveOptionID {
            picked = [option.id]
        } else {
            if let exclusive = question.exclusiveOptionID { picked.removeAll { $0 == exclusive } }
            guard picked.count < question.maxPicks else { return }
            picked.append(option.id)
        }
        // Keep the order the options are shown in.
        let order = question.options.map(\.id)
        answers[question.id] = order.filter(picked.contains)
    }

    private func advance() {
        if step + 1 >= questions.count {
            onFinish(answers)
        } else {
            move(to: step + 1)
        }
    }

    private func move(to newStep: Int) {
        guard newStep >= -1, newStep < questions.count else { return }
        forward = newStep > step
        // One render with the new direction first, so the outgoing step leaves the right way.
        Task { @MainActor in
            withAnimation(reduceMotion ? nil : .snappy(duration: 0.3)) { step = newStep }
        }
    }

    private var stepTransition: AnyTransition {
        if reduceMotion { return .identity }
        return .asymmetric(
            insertion: .move(edge: forward ? .trailing : .leading).combined(with: .opacity),
            removal: .move(edge: forward ? .leading : .trailing).combined(with: .opacity))
    }
}

/// One full-width answer row: label, then an empty or filled mark. Min 56 pt.
private struct OptionRow: View {
    let label: String
    let isPicked: Bool
    let isMulti: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 16) {
                Text(label)
                    .font(BrianType.body)
                    .foregroundStyle(Brian.ink)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: mark)
                    .font(.title3)
                    .foregroundStyle(isPicked ? Brian.ink : Brian.muted)
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, minHeight: 56)
            .background(isPicked ? Brian.surface2 : Brian.surface,
                        in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(Brian.ink, lineWidth: isPicked ? 1.5 : 0)
            }
            .contentShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isPicked ? .isSelected : [])
        .accessibilityHint(isMulti ? "Pick any that apply" : "")
    }

    private var mark: String {
        if isMulti { return isPicked ? "checkmark.square.fill" : "square" }
        return isPicked ? "checkmark.circle.fill" : "circle"
    }
}

#Preview {
    OnboardingView(questions: [
        PersonaQuestion(
            id: "tone", prompt: "How should Bryan talk to you?", detail: "You can redo this in Settings.",
            options: [
                PersonaOption(id: "gentle", label: "Gently"),
                PersonaOption(id: "direct", label: "Straight to the point"),
                PersonaOption(id: "coach", label: "Like a coach"),
            ],
            maxPicks: 1, exclusiveOptionID: nil),
        PersonaQuestion(
            id: "cutting", prompt: "What are you cutting back on?", detail: "Pick up to three.",
            options: [
                PersonaOption(id: "caffeine", label: "Caffeine"),
                PersonaOption(id: "alcohol", label: "Alcohol"),
                PersonaOption(id: "nicotine", label: "Nicotine"),
                PersonaOption(id: "screens", label: "Late screens"),
                PersonaOption(id: "none", label: "None of these"),
            ],
            maxPicks: 3, exclusiveOptionID: "none"),
    ]) { answers in
        print(answers)
    }
}
