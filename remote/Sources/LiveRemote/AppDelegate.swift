import AppKit

/// Live Remote: a floating toolbar (and a menu-bar item) that drives the captions Mac.
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let d = UserDefaults.standard
    private let client = RemoteClient()
    private let discovery = Discovery()
    private let hotKeys = HotKeys()
    private let toolbar = ToolbarController()
    private var status: NSStatusItem!
    private var connectWindow: ConnectWindow?
    private var namesWindow: NamesWindow?
    private var shortcutsWindow: ShortcutsWindow?
    private var fullWindow: FullWindow?               // the full view: the captions Mac's Live screen
    private var previousApp: NSRunningApplication?    // in front before one of our windows opened
    private var closeConnectOnSuccess = false          // the user pressed Connect: close it once it works

    /// Bring one of our windows forward, remembering the app to give the keyboard back to.
    private func present(_ w: NSWindow?) {
        if !NSApp.isActive, let front = NSWorkspace.shared.frontmostApplication,
           front.processIdentifier != ProcessInfo.processInfo.processIdentifier { previousApp = front }
        NSApp.activate(ignoringOtherApps: true)
        w?.makeKeyAndOrderFront(nil)
    }

    private var modifiers: HotKeys.Modifiers {
        get { HotKeys.Modifiers(rawValue: d.string(forKey: "shortcuts") ?? "") ?? .ctrlOpt }
        set { d.set(newValue.rawValue, forKey: "shortcuts") }
    }

    func applicationDidFinishLaunching(_ n: Notification) {
        d.register(defaults: ["port": 8766, "vertical": false, "peek": true, "onTop": true, "full": false])
        toolbar.vertical = d.bool(forKey: "vertical")
        toolbar.showPeek = d.bool(forKey: "peek")
        toolbar.panel.level = d.bool(forKey: "onTop") ? .floating : .normal
        toolbar.panel.setFrameAutosaveName("LiveRemoteToolbar")
        if toolbar.panel.frame.origin == NSPoint(x: 200, y: 200), let s = NSScreen.main {
            toolbar.panel.setFrameTopLeftPoint(NSPoint(x: s.visibleFrame.minX + 40, y: s.visibleFrame.maxY - 40))
        }
        toolbar.onAction = { [weak self] in self?.act($0) }
        toolbar.menuProvider = { [weak self] in self?.menu() ?? NSMenu() }
        if d.bool(forKey: "full") { showFull(activate: false) } else { toolbar.panel.orderFrontRegardless() }

        status = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        status.button?.attributedTitle = NSAttributedString(string: "LT", attributes: [.font: Theme.font(12, .heavy)])
        status.menu = NSMenu()
        status.menu?.delegate = self

        hotKeys.onPress = { [weak self] in self?.act($0) }
        applyShortcuts()

        client.onChange = { [weak self] link in self?.linkChanged(link) }
        // a closed window hands the keyboard back to the app in front before (ProPresenter)
        NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification, object: nil, queue: .main) { [weak self] n in
            guard let self, let w = n.object as? NSWindow, w !== self.toolbar.panel else { return }
            DispatchQueue.main.async {
                let open = [self.connectWindow, self.namesWindow, self.shortcutsWindow, self.fullWindow].contains { $0?.isVisible == true }
                if !open, NSApp.isActive, let prev = self.previousApp, !prev.isTerminated { prev.activate() }
                if !open { self.previousApp = nil }
            }
        }
        discovery.onChange = { [weak self] macs in self?.found(macs) }
        discovery.start()

        if let host = d.string(forKey: "host"), let pin = d.string(forKey: "pin") {
            client.configure(host: host, port: d.integer(forKey: "port"), pin: pin)
        } else {
            linkChanged(.notSet)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.openConnect() }
        }
    }

    // MARK: state
    private func linkChanged(_ link: Link) {
        toolbar.show(link, host: client.host)
        namesWindow?.refresh(client.feed)
        if fullWindow?.isVisible == true { loadFull(link) }
        let tint: NSColor = Look.of(link, host: client.host).color
        status.button?.attributedTitle = NSAttributedString(string: "LT", attributes: [.font: Theme.font(12, .heavy), .foregroundColor: tint])
        switch link {
        case .connected:
            if closeConnectOnSuccess { closeConnectOnSuccess = false; connectWindow?.close() }
        case .wrongPin:
            // never pops up by itself: ProPresenter keeps the keyboard mid-service
            connectWindow?.say("Wrong PIN. Check Settings → ProPresenter → Live Remote on the captions Mac.")
        case .remoteOff:
            connectWindow?.say("Live Remote is off on the captions Mac: Settings → ProPresenter → Allow Live Remote.")
        case .lockedOut:
            connectWindow?.say("Too many wrong PINs. Wait a minute, then try again.")
        case .unreachable(let why):
            connectWindow?.say("Can't reach it: \(why)")
        default: break
        }
    }

    private func found(_ macs: [FoundMac]) {
        connectWindow?.update(macs)
    }

    private func act(_ action: String) {
        if action == "names" { openNames(); return }
        if action == "full" { showFull(); return }
        guard let f = client.feed else { NSSound.beep(); return }   // also wrong PIN / locked: nothing is sent
        let body: [String: Any]
        switch action {
        case "pause": body = ["on": !f.paused]
        case "hold": body = ["on": !f.held]
        case "song": body = ["on": !f.song]
        default: body = [:]
        }
        client.send(action, body) { err in
            if let err { NSSound.beep(); NSLog("Live Remote: \(action): \(err)") }
        }
    }

    private func applyShortcuts() {
        hotKeys.install(modifiers)
        toolbar.setShortcuts(modifiers)
        shortcutsWindow?.show(modifiers, failed: hotKeys.failed)
    }

    // MARK: windows
    @objc private func openConnect() {
        if connectWindow == nil {
            let w = ConnectWindow()
            w.onConnect = { [weak self] host, port, pin in
                guard let self, (1...65535).contains(port) else { return }
                self.d.set(host, forKey: "host"); self.d.set(port, forKey: "port"); self.d.set(pin, forKey: "pin")
                self.closeConnectOnSuccess = true
                self.client.configure(host: host, port: port, pin: pin)
            }
            connectWindow = w
        }
        connectWindow?.prefill(host: client.host, port: client.port, pin: client.pin)
        connectWindow?.currentHost = client.host
        connectWindow?.update(discovery.found)
        present(connectWindow)
    }

    @objc private func openNames() {
        if namesWindow == nil { namesWindow = NamesWindow(client: client) }
        namesWindow?.refresh(client.feed)
        present(namesWindow)
    }

    @objc private func openShortcuts() {
        if shortcutsWindow == nil {
            let w = ShortcutsWindow(current: modifiers)
            w.onChange = { [weak self] m in self?.modifiers = m; self?.applyShortcuts() }
            shortcutsWindow = w
        }
        shortcutsWindow?.show(modifiers, failed: hotKeys.failed)
        present(shortcutsWindow)
    }

    // MARK: full view and the small toolbar: one or the other is on show
    @objc private func showFull() { showFull(activate: true) }

    private func showFull(activate: Bool) {
        if fullWindow == nil {
            let w = FullWindow()
            w.menuProvider = { [weak self] in self?.menu() ?? NSMenu() }
            w.onSmall = { [weak self] in self?.showSmall() }
            fullWindow = w
            // its close button also goes back to the toolbar
            NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification, object: w, queue: .main) { [weak self] _ in
                guard let self, self.d.bool(forKey: "full") else { return }
                self.d.set(false, forKey: "full")
                self.toolbar.panel.orderFrontRegardless()
            }
        }
        d.set(true, forKey: "full")
        toolbar.panel.orderOut(nil)
        loadFull(client.link)
        if activate { present(fullWindow) } else { fullWindow?.orderFrontRegardless() }
    }

    @objc private func showSmall() {
        d.set(false, forKey: "full")
        fullWindow?.close()
        toolbar.panel.orderFrontRegardless()
    }

    private func loadFull(_ link: Link) {
        guard let w = fullWindow else { return }
        if case .connected = link, let host = client.host {
            w.load(host: host, port: client.port, pin: client.pin)
        } else if case .connecting = link {
            return                                    // keep what is on show until it answers
        } else {
            w.say(Look.of(link, host: client.host).problem ?? "Not connected")
        }
    }

    // MARK: the menu (toolbar right-click, ⋯ and the menu bar)
    private func item(_ title: String, _ sel: Selector?, key: String = "", on: Bool = false, tag: Int = 0) -> NSMenuItem {
        let i = NSMenuItem(title: title, action: sel, keyEquivalent: key)
        i.target = self
        i.state = on ? .on : .off
        i.tag = tag
        return i
    }

    fileprivate func fill(_ m: NSMenu) {
        m.removeAllItems()
        m.addItem(item("Horizontal", #selector(setOrientation(_:)), on: !toolbar.vertical, tag: 0))
        m.addItem(item("Vertical", #selector(setOrientation(_:)), on: toolbar.vertical, tag: 1))
        m.addItem(.separator())
        let peek = item("Show the caption line", #selector(togglePeek), on: toolbar.showPeek)
        peek.isEnabled = !toolbar.vertical
        m.addItem(peek)
        m.addItem(item("Keep above all windows", #selector(toggleOnTop), on: toolbar.panel.level == .floating))
        if fullWindow?.isVisible == true {
            m.addItem(item("Small toolbar", #selector(showSmall)))
        } else {
            m.addItem(item("Full app view", #selector(showFull as () -> Void)))
            m.addItem(item(toolbar.panel.isVisible ? "Hide the toolbar" : "Show the toolbar", #selector(toggleToolbar)))
        }
        m.addItem(.separator())
        let where_ = client.host.map { $0.hasSuffix(".") ? String($0.dropLast()) : $0 }
        let head = NSMenuItem(title: client.feed != nil ? "Connected to \(where_ ?? "")" : where_.map { "Not connected to \($0)" } ?? "Not connected",
                              action: nil, keyEquivalent: "")
        head.isEnabled = false
        m.addItem(head)
        m.addItem(item("Connect to another Mac…", #selector(openConnect)))
        m.addItem(item("Enter PIN…", #selector(openConnect)))
        m.addItem(item("Today's names & words…", #selector(openNames)))
        m.addItem(.separator())
        m.addItem(item("Keyboard shortcuts…", #selector(openShortcuts), key: ","))
        m.addItem(item("Quit Live Remote", #selector(quit), key: "q"))
    }

    private func menu() -> NSMenu {
        let m = NSMenu()
        fill(m)
        return m
    }

    @objc private func setOrientation(_ sender: NSMenuItem) {
        toolbar.vertical = sender.tag == 1
        d.set(toolbar.vertical, forKey: "vertical")
    }
    @objc private func togglePeek() { toolbar.showPeek.toggle(); d.set(toolbar.showPeek, forKey: "peek") }
    @objc private func toggleOnTop() {
        let on = toolbar.panel.level != .floating
        toolbar.panel.level = on ? .floating : .normal
        d.set(on, forKey: "onTop")
    }
    @objc private func toggleToolbar() {
        if toolbar.panel.isVisible { toolbar.panel.orderOut(nil) } else { toolbar.panel.orderFrontRegardless() }
    }
    @objc private func quit() { NSApp.terminate(nil) }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if d.bool(forKey: "full") { showFull() } else { toolbar.panel.orderFrontRegardless() }
        return true
    }
}

extension AppDelegate: NSMenuDelegate {
    func menuNeedsUpdate(_ menu: NSMenu) { fill(menu) }
}
