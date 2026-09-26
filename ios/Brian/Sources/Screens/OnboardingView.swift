// First-launch questionnaire: a welcome step, then one multiple-choice question per
// screen. Single choice advances on tap after a short beat; multi choice toggles any
// number (or up to `maxPicks`) and continues with a button. An "Other" option opens a
// text field and waits for Continue. The answers feed Bryan's persona only. Full
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
    @FocusState private var textFocused: Bool

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
            ScrollViewReader { proxy in
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
                            if option.id == question.freeTextOptionID, picked.contains(option.id) {
                                freeTextField(question)
                                    .id(question.textKey)
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
            // The keyboard's own avoidance ignores the Continue inset; scroll the field
            // clear of it once the keyboard has come up.
            .onChange(of: textFocused) { _, focused in
                guard focused else { return }
                Task { @MainActor in
                    try? await Task.sleep(for: .milliseconds(350))
                    withAnimation(reduceMotion ? nil : .snappy(duration: 0.25)) {
                        proxy.scrollTo(question.textKey, anchor: .bottom)
                    }
                }
            }
            }
        }
        .safeAreaInset(edge: .bottom) {
            if question.isMulti || freeTextPicked(question) {
                primaryButton("Continue", enabled: canContinue(question)) { advance() }
            }
        }
        .sensoryFeedback(.selection, trigger: picked)
    }

    /// The wearer's own words under an "Other" option. Same shape as an answer row.
    private func freeTextField(_ question: PersonaQuestion) -> some View {
        TextField(question.freeTextPlaceholder, text: Binding(
            get: { answers[question.textKey]?.first ?? "" },
            set: { answers[question.textKey] = [String($0.prefix(40))] }))
            .font(BrianType.body)
            .foregroundStyle(Brian.ink)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .submitLabel(.done)
            .focused($textFocused)
            .onSubmit { if canContinue(question) { advance() } }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 56)
            .background(Brian.surface, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(Brian.ink, lineWidth: 1.5)
            }
            .accessibilityLabel(question.freeTextPlaceholder)
            .onAppear { textFocused = true }
    }

    private func freeTextPicked(_ question: PersonaQuestion) -> Bool {
        guard let free = question.freeTextOptionID else { return false }
        return answers[question.id]?.contains(free) == true
    }

    /// Multi: at least one pick. Free text picked: the text must not be empty.
    private func canContinue(_ question: PersonaQuestion) -> Bool {
        let picked = answers[question.id] ?? []
        guard !picked.isEmpty else { return false }
        if freeTextPicked(question) { return answers.freeText(for: question) != nil }
        return true
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
            // "Other" waits for the text and Continue instead of advancing on the tap.
            if option.id == question.freeTextOptionID { return }
            textFocused = false
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
            guard question.isUnlimited || picked.count < question.maxPicks else { return }
            picked.append(option.id)
        }
        // Keep the order the options are shown in.
        let order = question.options.map(\.id)
        answers[question.id] = order.filter(picked.contains)
    }

    private func advance() {
        textFocused = false
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
