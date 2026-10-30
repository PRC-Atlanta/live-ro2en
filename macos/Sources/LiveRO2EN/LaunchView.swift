import SwiftUI

/// What the window shows until the control page has loaded, and when something went wrong.
final class LaunchModel: ObservableObject {
    @Published var message = "Starting…"
    @Published var failure: String?
    @Published var details = ""
    @Published var showDetails = false   // here, not @State: that macro needs full Xcode to build
    var onRetry: () -> Void = {}
    var onShowLog: () -> Void = {}
}

struct LaunchView: View {
    @ObservedObject var model: LaunchModel

    var body: some View {
        ZStack {
            Color(nsColor: .windowBackgroundColor).ignoresSafeArea()
            VStack(spacing: 18) {
                Text("RO ⇄ EN")
                    .font(.system(size: 34, weight: .semibold, design: .rounded))
                    .foregroundStyle(.secondary)
                if let failure = model.failure {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 30)).foregroundStyle(.orange)
                    Text(failure).font(.title3).multilineTextAlignment(.center)
                    HStack(spacing: 12) {
                        Button("Try Again") { model.onRetry() }.keyboardShortcut(.defaultAction)
                        Button("Show Log") { model.onShowLog() }
                    }
                    if !model.details.isEmpty {
                        DisclosureGroup("Details", isExpanded: $model.showDetails) {
                            ScrollView {
                                Text(model.details)
                                    .font(.system(.caption, design: .monospaced))
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }.frame(maxHeight: 160)
                        }.frame(maxWidth: 520)
                    }
                } else {
                    ProgressView().controlSize(.large)
                    Text(model.message).font(.title3).foregroundStyle(.secondary)
                }
            }
            .padding(32)
        }
    }
}
