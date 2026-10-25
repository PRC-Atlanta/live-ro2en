import AppKit

/// Dark teal windows in the main app's look.
class ThemedWindow: NSWindow {
    init(title: String, size: NSSize) {
        super.init(contentRect: NSRect(origin: .zero, size: size), styleMask: [.titled, .closable],
                   backing: .buffered, defer: false)
        self.title = title
        appearance = NSAppearance(named: .darkAqua)
        titlebarAppearsTransparent = true
        isReleasedWhenClosed = false
        let bg = PanelBackground()
        bg.radius = 0
        contentView = bg
        center()
    }

    static func heading(_ s: String) -> NSTextField {
        let l = NSTextField(labelWithAttributedString: Theme.caps(s, size: 12, color: Theme.label, tracking: 0.14))
        l.translatesAutoresizingMaskIntoConstraints = false
        return l
    }
    static func text(_ s: String, size: CGFloat = 13, color: NSColor = Theme.label) -> NSTextField {
        let l = NSTextField(wrappingLabelWithString: s)
        l.font = Theme.font(size)
        l.textColor = color
        l.translatesAutoresizingMaskIntoConstraints = false
        return l
    }
    static func button(_ title: String, primary: Bool = false, _ target: AnyObject, _ sel: Selector) -> NSButton {
        let b = NSButton(title: title, target: target, action: sel)
        b.bezelStyle = .rounded
        b.controlSize = .large
        if primary { b.keyEquivalent = "\r"; b.bezelColor = Theme.cyanDim }
        b.translatesAutoresizingMaskIntoConstraints = false
        return b
    }
}

/// First launch and "Connect to another Mac…": the Macs found on this network, or an
/// address typed by hand, and the PIN from the captions Mac's Settings.
final class ConnectWindow: ThemedWindow, NSTextFieldDelegate {
    private let picker = NSPopUpButton()
    private let address = NSTextField()
    private let pin = NSTextField()
    private let status = ThemedWindow.text("", size: 12, color: Theme.mute)
    private var found: [FoundMac] = []
    var onConnect: ((_ host: String, _ port: Int, _ pin: String) -> Void)?

    init() {
        super.init(title: "Connect to the captions Mac", size: NSSize(width: 400, height: 330))
        let manual = "Type an address…"
        picker.translatesAutoresizingMaskIntoConstraints = false
        picker.target = self
        picker.action = #selector(picked)
        address.placeholderString = "church-mac.local, or 192.168.1.20 (port 8766)"
        address.translatesAutoresizingMaskIntoConstraints = false
        pin.placeholderString = "••••••"
        pin.font = Theme.rounded(24)
        pin.alignment = .center
        pin.delegate = self
        pin.translatesAutoresizingMaskIntoConstraints = false
        picker.addItem(withTitle: manual)

        let connect = ThemedWindow.button("Connect", primary: true, self, #selector(go))
        let cancel = ThemedWindow.button("Cancel", self, #selector(cancelIt))
        cancel.keyEquivalent = "\u{1b}"
        let buttons = NSStackView(views: [cancel, connect])
        let s = NSStackView(views: [
            ThemedWindow.heading("Connect to the captions Mac"),
            ThemedWindow.text("Found on this network:"), picker, address,
            ThemedWindow.text("PIN"), pin,
            ThemedWindow.text("The PIN is on the captions Mac: Settings → ProPresenter → Live Remote. "
                              + "Type its .local name or its IPv4 address (like 192.168.1.20).", size: 12, color: Theme.mute),
            status, buttons])
        s.orientation = .vertical
        s.alignment = .leading
        s.spacing = 8
        s.setCustomSpacing(14, after: address)
        s.translatesAutoresizingMaskIntoConstraints = false
        contentView?.addSubview(s)
        NSLayoutConstraint.activate([
            s.leadingAnchor.constraint(equalTo: contentView!.leadingAnchor, constant: 20),
            s.trailingAnchor.constraint(equalTo: contentView!.trailingAnchor, constant: -20),
            s.topAnchor.constraint(equalTo: contentView!.topAnchor, constant: 34),
            picker.widthAnchor.constraint(equalTo: s.widthAnchor), address.widthAnchor.constraint(equalTo: s.widthAnchor),
            pin.widthAnchor.constraint(equalToConstant: 160), buttons.trailingAnchor.constraint(equalTo: s.trailingAnchor)])
        picked()
    }

    func prefill(host: String?, port: Int, pin p: String) {
        if let host { address.stringValue = port == 8766 ? host : "\(host):\(port)" }
        pin.stringValue = p
        status.stringValue = ""
    }

    func say(_ s: String) { status.stringValue = s }

    /// The Mac in use, so "Enter PIN…" keeps it selected when several are found.
    var currentHost: String?

    func update(_ macs: [FoundMac]) {
        let selName = picker.indexOfSelectedItem < found.count && picker.indexOfSelectedItem >= 0 ? found[picker.indexOfSelectedItem].name : nil
        let wasManual = picker.indexOfSelectedItem == picker.numberOfItems - 1 && picker.numberOfItems > 1
        found = macs
        picker.removeAllItems()
        for m in macs { picker.addItem(withTitle: "\(m.name) · \(m.host.hasSuffix(".") ? String(m.host.dropLast()) : m.host)") }
        picker.addItem(withTitle: "Type an address…")
        let norm = { (h: String) in h.lowercased().hasSuffix(".") ? String(h.lowercased().dropLast()) : h.lowercased() }
        if let i = macs.firstIndex(where: { $0.name == selName }) { picker.selectItem(at: i) }
        else if let cur = currentHost, let i = macs.firstIndex(where: { norm($0.host) == norm(cur) }) { picker.selectItem(at: i) }
        else if macs.isEmpty || wasManual || (currentHost != nil && !address.stringValue.isEmpty) { picker.selectItem(at: picker.numberOfItems - 1) }
        else { picker.selectItem(at: 0) }
        picked()
    }

    @objc private func picked() {
        let i = picker.indexOfSelectedItem
        address.isHidden = i >= 0 && i < found.count
    }

    func controlTextDidChange(_ n: Notification) {
        let digits = pin.stringValue.filter(\.isNumber).prefix(6)
        if pin.stringValue != String(digits) { pin.stringValue = String(digits) }
    }

    @objc private func go() {
        let p = pin.stringValue.filter(\.isNumber)
        guard p.count == 6 else { say("The PIN has 6 digits."); return }
        let i = picker.indexOfSelectedItem
        if i >= 0 && i < found.count {
            onConnect?(found[i].host, found[i].port, p)
        } else {
            let raw = address.stringValue.trimmingCharacters(in: .whitespaces)
            guard !raw.isEmpty else { say("Type the captions Mac's name or address."); return }
            guard let a = RemoteClient.parseAddress(raw) else {
                say("That is not an address: type a name like church-mac.local or an IPv4 address, and a port from 1 to 65535.")
                return
            }
            onConnect?(a.host, a.port, p)
        }
        say("Connecting…")
    }

    @objc private func cancelIt() { close() }
}

/// A list of names & words kept on this Mac.
struct NameList: Codable, Equatable { var name: String; var lines: [String] }

/// Today's names & words: lists kept on the ProPresenter Mac, sent to the captions Mac
/// (the same as typing them into its Names & words box) or pulled back from it.
final class NamesWindow: ThemedWindow, NSTableViewDataSource, NSTableViewDelegate, NSTextViewDelegate {
    private let table = NSTableView()
    private let editor = NSTextView()
    private let keep = NSButton(checkboxWithTitle: "Also keep them for next time on the captions Mac", target: nil, action: nil)
    private let inUse = ThemedWindow.text("", size: 12, color: Theme.mute)
    private let status = ThemedWindow.text("", size: 12, color: Theme.mute)
    private var lists: [NameList] = []
    private var current = 0
    private let client: RemoteClient

    static var file: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Live Remote", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("names.json")
    }

    init(client: RemoteClient) {
        self.client = client
        super.init(title: "Today's names & words", size: NSSize(width: 620, height: 430))
        styleMask.insert(.resizable)
        minSize = NSSize(width: 520, height: 360)
        load()

        let col = NSTableColumn(identifier: .init("name"))
        col.title = "Saved on this Mac"
        table.addTableColumn(col)
        table.headerView = nil
        table.dataSource = self
        table.delegate = self
        table.backgroundColor = .clear
        table.style = .sourceList
        let tableScroll = NSScrollView()
        tableScroll.documentView = table
        tableScroll.drawsBackground = false
        tableScroll.translatesAutoresizingMaskIntoConstraints = false

        let add = NSButton(title: "+ New list", target: self, action: #selector(newList))
        let del = NSButton(title: "Delete", target: self, action: #selector(deleteList))
        let rename = NSButton(title: "Rename…", target: self, action: #selector(renameList))
        [add, del, rename].forEach { $0.bezelStyle = .accessoryBarAction; $0.controlSize = .small }
        let listButtons = NSStackView(views: [add, rename, del])
        listButtons.spacing = 4
        let left = NSStackView(views: [ThemedWindow.text("SAVED ON THIS MAC", size: 11, color: Theme.mute), tableScroll, listButtons])
        left.orientation = .vertical
        left.alignment = .leading
        left.translatesAutoresizingMaskIntoConstraints = false

        editor.font = Theme.font(14)
        editor.textColor = Theme.ink
        editor.backgroundColor = Theme.well
        editor.insertionPointColor = Theme.cyanHi
        editor.isRichText = false
        editor.isAutomaticQuoteSubstitutionEnabled = false
        editor.isAutomaticSpellingCorrectionEnabled = false
        editor.textContainerInset = NSSize(width: 6, height: 6)
        editor.delegate = self
        let edScroll = NSScrollView()
        edScroll.documentView = editor
        edScroll.hasVerticalScroller = true
        edScroll.borderType = .lineBorder
        edScroll.translatesAutoresizingMaskIntoConstraints = false
        editor.autoresizingMask = [.width]
        editor.isVerticallyResizable = true
        editor.textContainer?.widthTracksTextView = true

        let hint = ThemedWindow.text("One per line. Write a name as it should appear (Ionuț Pop), or Romanian = English (Romani = Romans).",
                                     size: 12, color: Theme.mute)
        let right = NSStackView(views: [edScroll, hint, keep])
        right.orientation = .vertical
        right.alignment = .leading
        right.translatesAutoresizingMaskIntoConstraints = false

        let get = NSButton(title: "Get the list in use", target: self, action: #selector(getInUse))
        get.bezelStyle = .accessoryBarAction
        let save = ThemedWindow.button("Save list", self, #selector(saveList))
        let send = ThemedWindow.button("Send for today", primary: true, self, #selector(sendToday))
        let foot = NSStackView(views: [get, status, NSView(), save, send])
        foot.translatesAutoresizingMaskIntoConstraints = false

        let head = NSStackView(views: [ThemedWindow.heading("Today's names & words"), NSView(), inUse])
        head.translatesAutoresizingMaskIntoConstraints = false

        let cv = contentView!
        [head, left, right, foot].forEach(cv.addSubview)
        NSLayoutConstraint.activate([
            head.topAnchor.constraint(equalTo: cv.topAnchor, constant: 34),
            head.leadingAnchor.constraint(equalTo: cv.leadingAnchor, constant: 16),
            head.trailingAnchor.constraint(equalTo: cv.trailingAnchor, constant: -16),
            left.topAnchor.constraint(equalTo: head.bottomAnchor, constant: 12),
            left.leadingAnchor.constraint(equalTo: cv.leadingAnchor, constant: 16),
            left.widthAnchor.constraint(equalToConstant: 170),
            left.bottomAnchor.constraint(equalTo: foot.topAnchor, constant: -12),
            tableScroll.widthAnchor.constraint(equalTo: left.widthAnchor),
            right.topAnchor.constraint(equalTo: left.topAnchor),
            right.leadingAnchor.constraint(equalTo: left.trailingAnchor, constant: 14),
            right.trailingAnchor.constraint(equalTo: cv.trailingAnchor, constant: -16),
            right.bottomAnchor.constraint(equalTo: left.bottomAnchor),
            edScroll.widthAnchor.constraint(equalTo: right.widthAnchor),
            hint.widthAnchor.constraint(equalTo: right.widthAnchor),
            foot.leadingAnchor.constraint(equalTo: cv.leadingAnchor, constant: 16),
            foot.trailingAnchor.constraint(equalTo: cv.trailingAnchor, constant: -16),
            foot.bottomAnchor.constraint(equalTo: cv.bottomAnchor, constant: -14)])
        table.reloadData()
        select(0)
    }

    func refresh(_ feed: Feed?) {
        inUse.stringValue = feed.map { "On the captions Mac now: \($0.terms_n) name\($0.terms_n == 1 ? "" : "s")" } ?? "Not connected"
    }

    // MARK: lists
    private func load() {
        if let d = try? Data(contentsOf: Self.file), let l = try? JSONDecoder().decode([NameList].self, from: d), !l.isEmpty {
            lists = l
        } else {
            lists = [NameList(name: "Sunday morning", lines: [])]
        }
    }

    private func store() {
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let d = try? enc.encode(lists) { try? d.write(to: Self.file, options: .atomic) }
    }

    private var lines: [String] {
        editor.string.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    private func select(_ i: Int) {
        current = max(0, min(i, lists.count - 1))
        table.selectRowIndexes([current], byExtendingSelection: false)
        editor.string = lists[current].lines.joined(separator: "\n")
    }

    func numberOfRows(in tableView: NSTableView) -> Int { lists.count }

    func tableView(_ t: NSTableView, viewFor col: NSTableColumn?, row: Int) -> NSView? {
        let l = NSTextField(labelWithString: "\(lists[row].name)   \(lists[row].lines.count)")
        l.textColor = Theme.label
        l.lineBreakMode = .byTruncatingTail
        return l
    }

    func tableViewSelectionDidChange(_ n: Notification) {
        let r = table.selectedRow
        guard r >= 0, r != current else { return }
        lists[current].lines = lines                       // keep what was typed in the last one
        store()
        table.reloadData()
        select(r)
    }

    func textDidChange(_ n: Notification) { status.stringValue = "" }

    private func ask(_ title: String, _ value: String, _ done: @escaping (String) -> Void) {
        let a = NSAlert()
        a.messageText = title
        let f = NSTextField(frame: NSRect(x: 0, y: 0, width: 240, height: 24))
        f.stringValue = value
        a.accessoryView = f
        a.addButton(withTitle: "OK")
        a.addButton(withTitle: "Cancel")
        a.window.initialFirstResponder = f
        a.beginSheetModal(for: self) { r in
            let v = f.stringValue.trimmingCharacters(in: .whitespaces)
            if r == .alertFirstButtonReturn, !v.isEmpty { done(v) }
        }
    }

    @objc private func newList() {
        ask("Name the new list", "") { [weak self] name in
            guard let self else { return }
            self.lists[self.current].lines = self.lines
            self.lists.append(NameList(name: name, lines: []))
            self.store(); self.table.reloadData(); self.select(self.lists.count - 1)
        }
    }

    @objc private func renameList() {
        ask("Rename the list", lists[current].name) { [weak self] name in
            guard let self else { return }
            self.lists[self.current].name = name
            self.store(); self.table.reloadData(); self.select(self.current)
        }
    }

    @objc private func deleteList() {
        let a = NSAlert()
        a.messageText = "Delete “\(lists[current].name)”?"
        a.informativeText = "Only this Mac's copy. Names already sent to the captions Mac stay there."
        a.addButton(withTitle: "Delete")
        a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: self) { [weak self] r in
            guard let self, r == .alertFirstButtonReturn else { return }
            self.lists.remove(at: self.current)
            if self.lists.isEmpty { self.lists = [NameList(name: "New list", lines: [])] }
            self.store(); self.table.reloadData(); self.select(min(self.current, self.lists.count - 1))
        }
    }

    @objc private func saveList() {
        lists[current].lines = lines
        store()
        table.reloadData()
        table.selectRowIndexes([current], byExtendingSelection: false)
        status.stringValue = "Saved on this Mac"
    }

    @objc private func sendToday() {
        let l = lines
        lists[current].lines = l
        store()
        table.reloadData()
        table.selectRowIndexes([current], byExtendingSelection: false)
        status.stringValue = "Sending…"
        client.sendTerms(l, keep: keep.state == .on) { [weak self] t, err in
            guard let self else { return }
            if let t { self.status.stringValue = t.n == 0 ? "Sent: today's names cleared" : "Sent: \(t.n) name\(t.n == 1 ? "" : "s") for today" }
            else { self.status.stringValue = err ?? "Not sent" }
        }
    }

    @objc private func getInUse() {
        status.stringValue = "Getting…"
        client.getTerms { [weak self] t, err in
            guard let self else { return }
            guard let t else { self.status.stringValue = err ?? "Not received"; return }
            self.editor.string = t.lines.joined(separator: "\n")
            self.status.stringValue = "The list in use (\(t.n)). Save list keeps it here."
        }
    }
}

/// Keyboard shortcuts: which modifier keys, or off.
final class ShortcutsWindow: ThemedWindow {
    private let picker = NSPopUpButton()
    private let rows = NSStackView()
    private let warn = ThemedWindow.text("", size: 12, color: Theme.amber)
    var onChange: ((HotKeys.Modifiers) -> Void)?

    init(current: HotKeys.Modifiers) {
        super.init(title: "Keyboard shortcuts", size: NSSize(width: 380, height: 360))
        for m in HotKeys.Modifiers.allCases { picker.addItem(withTitle: m.title) }
        picker.selectItem(at: HotKeys.Modifiers.allCases.firstIndex(of: current) ?? 0)
        picker.target = self
        picker.action = #selector(changed)
        rows.orientation = .vertical
        rows.alignment = .leading
        let s = NSStackView(views: [ThemedWindow.heading("Keyboard shortcuts"),
                                    ThemedWindow.text("They work in any app, ProPresenter included."),
                                    picker, rows, warn])
        s.orientation = .vertical
        s.alignment = .leading
        s.spacing = 10
        s.translatesAutoresizingMaskIntoConstraints = false
        contentView?.addSubview(s)
        NSLayoutConstraint.activate([
            s.leadingAnchor.constraint(equalTo: contentView!.leadingAnchor, constant: 20),
            s.trailingAnchor.constraint(equalTo: contentView!.trailingAnchor, constant: -20),
            s.topAnchor.constraint(equalTo: contentView!.topAnchor, constant: 34)])
        show(current, failed: [])
    }

    func show(_ m: HotKeys.Modifiers, failed: [String]) {
        rows.arrangedSubviews.forEach { $0.removeFromSuperview() }
        let names = [("pause", "Hide or show captions"), ("hold", "Hold the current caption"), ("blank", "Clear the big screen"),
                     ("retract", "Remove the last caption"), ("song", "Song on or off"), ("names", "Today's names & words")]
        for (a, t) in names {
            let k = NSTextField(labelWithString: m == .off ? "—" : HotKeys.label(a, m))
            k.font = Theme.rounded(13)
            k.textColor = Theme.cyanHi
            k.widthAnchor.constraint(equalToConstant: 60).isActive = true
            let l = NSTextField(labelWithString: t)
            l.textColor = Theme.label
            rows.addArrangedSubview(NSStackView(views: [k, l]))
        }
        warn.stringValue = failed.isEmpty ? "" : "Another app already uses: " + failed.compactMap { a in names.first { $0.0 == a }?.1 }
            .joined(separator: ", ") + ". Pick other keys."
    }

    @objc private func changed() {
        onChange?(HotKeys.Modifiers.allCases[picker.indexOfSelectedItem])
    }
}
