// DEMO_UI_PRD.md "Protocol tab" (D-005): "3 of 5 today" + Add, today's items with a
// checkbox each (Mark done / Undo), swipe actions, tap a name to edit, then Templates.
import SwiftUI
import UIKit

struct ProtocolView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var showAddItem = false
    @State private var editing: ProtocolItem?
    @State private var expanded: Set<String> = []

    var body: some View {
        ScrollViewReader { proxy in
            TimelineView(.periodic(from: .now, by: 60)) { context in
                list(summary: appState.protocolSummary(now: context.date))
            }
            .task {
                await appState.refreshProtocol()
                switch appState.protocolLaunch {
                case .protocolTemplates:
                    expanded = [ProtocolTemplates.groups[0].id]
                    try? await Task.sleep(for: .milliseconds(300))
                    proxy.scrollTo(ProtocolTemplates.groups[0].id, anchor: .top)
                case .protocolEdit:
                    editing = appState.protocolItems.first
                default: break
                }
                appState.protocolLaunch = nil
            }
        }
        .navigationTitle("Protocol")
        // The tab bar names the tab; beside a short status pill the inline title squeezed to "P…".
        .toolbar(removing: .title)
        .refreshable { await appState.refreshProtocol() }
        .sheet(isPresented: $showAddItem) { AddItemView() }
        .sheet(item: $editing) { AddItemView(editing: $0) }
    }

    private func list(summary: ProtocolSummary) -> some View {
        let items = Dictionary(appState.protocolItems.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        return List {
            // The toolbar is the shared header, so the count and Add sit above the list.
            Section {
                let header = typeSize.isAccessibilitySize
                    ? AnyLayout(VStackLayout(alignment: .leading, spacing: 16))
                    : AnyLayout(HStackLayout(spacing: 16))
                header {
                    Text(summary.isEmpty ? "Nothing yet today" : "\(summary.count) today")
                        .font(BrianType.title)
                        .foregroundStyle(Brian.ink)
                        .accessibilityAddTraits(.isHeader)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button { showAddItem = true } label: {
                        Label("Add", systemImage: "plus").labelStyle(.titleAndIcon)
                    }
                    .buttonStyle(.glass)
                    .fixedSize()
                }
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets(top: 0, leading: 4, bottom: 0, trailing: 0))
            }

            Section {
                if summary.isEmpty {
                    Text("No protocol yet. Add an item, or start from a template below.")
                        .foregroundStyle(Brian.muted)
                } else {
                    ForEach(summary.rows) { row in
                        if let item = items[row.id] {
                            protocolRow(row, item: item)
                                .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                    Button(role: .destructive) {
                                        Task { await appState.deleteProtocolItem(item) }
                                    } label: { Label("Delete", systemImage: "trash") }
                                }
                                .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                    if row.checked {
                                        Button("Undo") { Task { await appState.undo(item) } }
                                            .tint(Brian.muted)
                                    } else {
                                        Button("Mark done") { Task { await appState.markDone(item) } }
                                            .tint(Brian.ink)
                                    }
                                }
                        }
                    }
                }
            }

            Section {
                ForEach(ProtocolTemplates.groups) { group in
                    DisclosureGroup(isExpanded: binding(for: group.id)) {
                        ForEach(group.templates) { templateRow($0) }
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: group.symbol)
                                .foregroundStyle(Brian.muted)
                                .frame(width: 28)
                                .accessibilityHidden(true)
                            Text(group.title).foregroundStyle(Brian.text)
                        }
                        .frame(minHeight: 44)
                    }
                }
            } header: {
                Text("Templates")
            }
        }
        .listStyle(.insetGrouped)
        .tint(Brian.ink)
    }

    private func binding(for id: String) -> Binding<Bool> {
        Binding(get: { expanded.contains(id) },
                set: { open in if open { expanded.insert(id) } else { expanded.remove(id) } })
    }

    private func protocolRow(_ row: ProtocolSummary.Row, item: ProtocolItem) -> some View {
        HStack(alignment: typeSize.isAccessibilitySize ? .top : .center, spacing: 8) {
            Button {
                Task { await appState.toggleProtocolItem(item) }
            } label: {
                Image(systemName: row.checked ? "checkmark.circle.fill" : "circle")
                    .font(.title2)
                    .foregroundStyle(Brian.ink)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .accessibilityLabel(row.name)
            .accessibilityValue(row.checked ? "Done" : "Not done")
            .accessibilityHint(row.checked ? "Undo" : "Mark done")

            Button {
                editing = item
            } label: {
                details(row)
            }
            .buttonStyle(.borderless)
            .accessibilityHint("Edit")

            if !typeSize.isAccessibilitySize, row.stateSymbol == "checkmark.circle.fill", item.evidenceRef != nil {
                ProtocolEvidenceThumbnail(item: item)
            }
        }
        .frame(minHeight: 52)
    }

    /// Name, window and state; the state moves under the name at accessibility sizes.
    private func details(_ row: ProtocolSummary.Row) -> some View {
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4))
            : AnyLayout(HStackLayout(spacing: 8))
        return layout {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    // The checkbox already leads the row; at accessibility sizes the kind
                    // symbol would squeeze the name to a word per line.
                    if !typeSize.isAccessibilitySize {
                        Image(systemName: BrianSymbol.protocolKind(row.kind.lowercased()))
                            .foregroundStyle(Brian.muted)
                            .frame(width: 28)
                            .accessibilityHidden(true)
                    }
                    VStack(alignment: .leading, spacing: 4) {
                        Text(row.name).foregroundStyle(Brian.text)
                        Text(row.window)
                            .font(BrianType.secondary)
                            .foregroundStyle(Brian.muted)
                            .monospacedDigit()
                    }
                }
                .font(BrianType.body)
            }
            .frame(maxWidth: typeSize.isAccessibilitySize ? nil : .infinity, alignment: .leading)
            ProtocolStateLabel(row: row)
        }
        .multilineTextAlignment(.leading)
        .contentShape(Rectangle())
    }

    private func templateRow(_ template: ProtocolTemplate) -> some View {
        let window = ProtocolSummary.windowText(start: ProtocolSummary.minutes(template.windowStart),
                                                end: ProtocolSummary.minutes(template.windowEnd))
            ?? "\(template.windowStart)–\(template.windowEnd)"
        let added = ProtocolTemplates.isAdded(template, in: appState.protocolItems)
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(spacing: 16))
        return layout {
            VStack(alignment: .leading, spacing: 4) {
                Text(template.name).foregroundStyle(Brian.text)
                Text([window, template.daysText].compactMap { $0 }.joined(separator: " · "))
                    .font(BrianType.secondary)
                    .foregroundStyle(Brian.muted)
                    .monospacedDigit()
            }
            .frame(maxWidth: typeSize.isAccessibilitySize ? nil : .infinity, alignment: .leading)
            if added {
                Text("Added")
                    .font(BrianType.secondary)
                    .foregroundStyle(Brian.muted)
            } else {
                Button("Add") {
                    Task {
                        await appState.addProtocolItem(name: template.name, kind: template.kind,
                                                       windowStart: template.windowStart,
                                                       windowEnd: template.windowEnd, days: template.days)
                    }
                }
                .buttonStyle(.glass)
                .accessibilityLabel("Add \(template.name)")
            }
        }
        .frame(minHeight: 52)
    }
}

/// The camera's evidence frame for a seen item. Shown only when the image loads: no
/// placeholder box when it is missing (DEMO_UI_PRD.md).
private struct ProtocolEvidenceThumbnail: View {
    @Environment(AppState.self) private var appState
    let item: ProtocolItem
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
                    .frame(width: 44, height: 44)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    .accessibilityLabel("What the camera saw")
            } else {
                Color.clear.frame(width: 0, height: 0).accessibilityHidden(true)
            }
        }
        .task(id: item.id) {
            image = await appState.evidenceThumbnail(for: item).flatMap(UIImage.init(data:))
        }
    }
}
