import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuItemValidation {
    private var window: NSWindow!
    private let supervisor = ProcessSupervisor()
    private let web = WebViewController()
    private let captions = DisplayWindowController()   // the captions display window
    private let live = LiveActivity()
    private let updater: Updater = StubUpdater()
    private let launch = LaunchModel()
    private var overlay: NSHostingView<LaunchView>!
    private var loadedOrigin: URL?
    private var quitting = false
    private var shutdownDone = false
    private var skipQuitConfirmation = false
    private var signalSources: [DispatchSourceSignal] = []

    /// "ui" (the new page at /ui/, the default) or "classic" (the old control page at /).
    private var pageChoice: String {
        get { UserDefaults.standard.string(forKey: "LivePage") ?? "ui" }
        set { UserDefaults.standard.set(newValue, forKey: "LivePage") }
    }

    // MARK: launch

    func applicationWillFinishLaunching(_ notification: Notification) {
        // one copy only: a second one would "clean up" the first one's processes
        if let id = Bundle.main.bundleIdentifier {
            let others = NSRunningApplication.runningApplications(withBundleIdentifier: id)
                .filter { $0.processIdentifier != getpid() }
            if let first = others.first {
                first.activate()
                exit(0)
            }
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        ShellLog.write("shell \(getpid()) launched (\(Bundle.main.bundlePath))")
        NSApp.mainMenu = buildMenu()
        buildWindow()
        installSignalHandlers()

        launch.onRetry = { [weak self] in
            self?.launch.failure = nil
            self?.supervisor.retry()
        }
        launch.onShowLog = { [weak self] in
            guard let self else { return }
            NSWorkspace.shared.activateFileViewerSelecting([self.supervisor.logURL])
        }
        web.onLoaded = { [weak self] in self?.setOverlay(visible: false) }
        web.onOpenDisplay = { [weak self] url in self?.captions.show(url) }
        supervisor.onStatus = { [weak self] in self?.statusChanged($0) }
        supervisor.onState = { [weak self] in self?.live.update($0) }
        live.onChange = { [weak self] isLive in
            self?.updater.setLive(isLive)
            self?.window.isDocumentEdited = isLive       // the close button shows a dot while live
        }
        supervisor.start()
        NSApp.activate(ignoringOtherApps: true)
    }

    private func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 800),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Live RO2EN"
        window.minSize = NSSize(width: 600, height: 500)
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        window.setFrameAutosaveName("LiveMainWindow")

        let root = NSView()
        web.webView.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(web.webView)
        overlay = NSHostingView(rootView: LaunchView(model: launch))
        overlay.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(overlay)
        for v in [web.webView, overlay!] as [NSView] {
            NSLayoutConstraint.activate([
                v.leadingAnchor.constraint(equalTo: root.leadingAnchor),
                v.trailingAnchor.constraint(equalTo: root.trailingAnchor),
                v.topAnchor.constraint(equalTo: root.topAnchor),
                v.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            ])
        }
        window.contentView = root
        window.makeKeyAndOrderFront(nil)
    }

    private func setOverlay(visible: Bool) {
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.2
            overlay.animator().alphaValue = visible ? 1 : 0
        } completionHandler: { [weak self] in
            self?.overlay.isHidden = !visible
        }
        if visible { overlay.isHidden = false }
    }

    private func statusChanged(_ s: ProcessSupervisor.Status) {
        guard !quitting else { return }
        switch s {
        case .launching(let msg):
            launch.failure = nil
            launch.message = msg
            if loadedOrigin == nil { setOverlay(visible: true) }
            // once the page is up it shows its own "reconnecting"; keep it visible during a
            // restart rather than covering the transcript
        case .ready(let port):
            launch.message = "Opening…"
            openPage(port: port)
        case .failed(let msg, let tail):
            live.set(false)
            launch.failure = msg
            launch.details = tail
            setOverlay(visible: true)
        }
    }

    private func openPage(port: Int) {
        let base = URL(string: "http://127.0.0.1:\(port)/")!
        if loadedOrigin == base {                // restarted on the same port: just refresh
            web.reload(); return
        }
        loadedOrigin = base
        captions.retarget(origin: base)          // an open captions display follows a new port
        if pageChoice == "classic" {
            web.load(base); return
        }
        supervisor.probe("/ui/") { [weak self] code in
            self?.web.load(code == 200 ? base.appendingPathComponent("ui/") : base)
        }
    }

    // MARK: quit

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)       // closing the only window quits, through the same confirmation
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if shutdownDone { return .terminateNow }
        if quitting { return .terminateLater }
        if live.isLive && !skipQuitConfirmation {
            let a = NSAlert()
            a.alertStyle = .warning
            a.messageText = "Captions are live"
            a.informativeText = "Quitting stops the translation and the captions on the screen."
            a.addButton(withTitle: "Keep Running")
            a.addButton(withTitle: "Stop and Quit")
            a.buttons[1].hasDestructiveAction = true
            NSApp.activate(ignoringOtherApps: true)
            let answer = a.runModal()
            // a signal while the alert is up aborts it and means "quit" (see installSignalHandlers)
            if answer != .alertSecondButtonReturn && !skipQuitConfirmation { return .terminateCancel }
        }
        quitting = true
        captions.close()
        launch.failure = nil
        launch.message = "Stopping…"
        setOverlay(visible: true)
        supervisor.shutdown { [weak self] in
            self?.shutdownDone = true
            self?.live.set(false)
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    /// SIGTERM/SIGINT/SIGHUP (logout, `kill`): stop the processes and quit without asking.
    private func installSignalHandlers() {
        for sig in [SIGTERM, SIGINT, SIGHUP] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler { [weak self] in
                ShellLog.write("shell got signal \(sig); quitting")
                guard let self else { return }
                self.skipQuitConfirmation = true
                if NSApp.modalWindow != nil {
                    NSApp.abortModal()           // the pending quit confirmation then proceeds
                } else {
                    NSApp.terminate(nil)
                }
            }
            src.resume()
            signalSources.append(src)
        }
    }

    // MARK: menu

    private func buildMenu() -> NSMenu {
        let main = NSMenu()
        let name = "Live RO2EN"

        let app = NSMenu(title: name)
        app.addItem(withTitle: "About \(name)", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(withTitle: "Check for Updates…", action: #selector(checkForUpdates), keyEquivalent: "").target = self
        app.addItem(.separator())
        app.addItem(withTitle: "Hide \(name)", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let others = app.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        others.keyEquivalentModifierMask = [.command, .option]
        app.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "Quit \(name)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        main.addItem(submenu(app))

        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        main.addItem(submenu(edit))

        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload Page", action: #selector(reloadPage), keyEquivalent: "r").target = self
        view.addItem(withTitle: "Use Classic Control Page", action: #selector(toggleClassic), keyEquivalent: "").target = self
        view.addItem(.separator())
        let fs = view.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fs.keyEquivalentModifierMask = [.command, .control]
        main.addItem(submenu(view))

        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        main.addItem(submenu(win))
        NSApp.windowsMenu = win

        let help = NSMenu(title: "Help")
        help.addItem(withTitle: "Show Logs in Finder", action: #selector(showLogs), keyEquivalent: "").target = self
        main.addItem(submenu(help))
        NSApp.helpMenu = help
        return main
    }

    private func submenu(_ m: NSMenu) -> NSMenuItem {
        let item = NSMenuItem(title: m.title, action: nil, keyEquivalent: "")
        item.submenu = m
        return item
    }

    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        switch item.action {
        case #selector(toggleClassic):
            item.state = pageChoice == "classic" ? .on : .off
            return loadedOrigin != nil
        case #selector(reloadPage):
            return loadedOrigin != nil
        default:
            return true
        }
    }

    @objc private func checkForUpdates() { updater.checkForUpdates() }

    @objc private func reloadPage() { web.reload() }

    @objc private func toggleClassic() {
        pageChoice = pageChoice == "classic" ? "ui" : "classic"
        loadedOrigin = nil
        openPage(port: supervisor.port)
    }

    @objc private func showLogs() {
        NSWorkspace.shared.activateFileViewerSelecting([supervisor.logURL])
    }
}
