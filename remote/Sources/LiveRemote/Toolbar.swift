import AppKit

/// One toolbar key: an SF Symbol, an uppercase label, its shortcut in the corner, and an
/// optional count badge (Names). `on` lights it cyan, or amber for Hide.
final class KeyButton: NSView {
    let action: String
    var symbol: String { didSet { needsDisplay = true } }
    var title: String { didSet { needsDisplay = true } }
    var shortcut = "" { didSet { needsDisplay = true } }
    var badge: Int? { didSet { needsDisplay = true } }
    var on = false { didSet { needsDisplay = true } }
    var amberWhenOn = false
    var enabled = true { didSet { needsDisplay = true } }
    var onPress: ((String) -> Void)?
    private var down = false { didSet { needsDisplay = true } }
    private var hover = false { didSet { needsDisplay = true } }
    private var widthConstraint: NSLayoutConstraint!
    var width: CGFloat { get { widthConstraint.constant } set { widthConstraint.constant = newValue } }

    init(action: String, symbol: String, title: String, width: CGFloat = 58) {
        self.action = action; self.symbol = symbol; self.title = title
        super.init(frame: NSRect(x: 0, y: 0, width: width, height: 52))
        translatesAutoresizingMaskIntoConstraints = false
        widthConstraint = widthAnchor.constraint(equalToConstant: width)
        widthConstraint.isActive = true
        heightAnchor.constraint(equalToConstant: 52).isActive = true
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
    }
    required init?(coder: NSCoder) { nil }

    override var isFlipped: Bool { true }
    override var mouseDownCanMoveWindow: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }   // never needs focus first
    override func accessibilityLabel() -> String? { title }
    override func accessibilityPerformPress() -> Bool { press(); return true }

    override func updateTrackingAreas() {
        trackingAreas.forEach(removeTrackingArea)
        addTrackingArea(NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways], owner: self))
    }
    override func mouseEntered(with e: NSEvent) { hover = true }
    override func mouseExited(with e: NSEvent) { hover = false; down = false }
    override func mouseDown(with e: NSEvent) { if enabled { down = true } }
    override func mouseUp(with e: NSEvent) {
        let inside = bounds.contains(convert(e.locationInWindow, from: nil))
        if down && inside { press() }
        down = false
    }
    private func press() { if enabled { onPress?(action) } }

    override func draw(_ dirtyRect: NSRect) {
        let alpha: CGFloat = enabled ? 1 : 0.35
        let r = bounds.insetBy(dx: 0.5, dy: down ? 1.5 : 0.5).offsetBy(dx: 0, dy: down ? 1 : 0)
        let path = NSBezierPath(roundedRect: r, xRadius: 6, yRadius: 6)
        let amber = on && amberWhenOn
        let (top, bottom, edge, ink): (NSColor, NSColor, NSColor, NSColor) =
            amber ? (Theme.amberTop, Theme.amberBottom, Theme.amber, Theme.amberInk)
            : on ? (Theme.onTop, Theme.onBottom, Theme.cyanDim, Theme.cyanHi)
            : (Theme.keyTop, Theme.keyBottom, hover && enabled ? Theme.cyanDim : Theme.keyEdge, hover && enabled ? Theme.ink : Theme.label)
        NSGraphicsContext.saveGraphicsState()
        if amber {
            let sh = NSShadow(); sh.shadowColor = Theme.hex(0xf5b83d, 0.4); sh.shadowBlurRadius = 8; sh.shadowOffset = .zero; sh.set()
        }
        NSGradient(starting: top.withAlphaComponent(alpha), ending: bottom.withAlphaComponent(alpha))?.draw(in: path, angle: -90)
        NSGraphicsContext.restoreGraphicsState()
        edge.withAlphaComponent(alpha).setStroke()
        path.lineWidth = 1
        path.stroke()

        let tint = ink.withAlphaComponent(alpha)
        if let img = NSImage(systemSymbolName: symbol, accessibilityDescription: title)?
            .withSymbolConfiguration(.init(pointSize: 16, weight: .medium)) {
            let tinted = NSImage(size: img.size, flipped: false) { rect in
                img.draw(in: rect); tint.set(); rect.fill(using: .sourceAtop); return true
            }
            let s = tinted.size
            tinted.draw(in: NSRect(x: (bounds.width - s.width) / 2, y: 11 + (20 - s.height) / 2, width: s.width, height: s.height))
        }
        let t = Theme.caps(title, size: 9.5, color: tint, tracking: 0.1)
        let ts = t.size()
        t.draw(at: NSPoint(x: (bounds.width - ts.width) / 2, y: 34))
        if !shortcut.isEmpty {
            let k = NSAttributedString(string: shortcut, attributes: [.font: Theme.font(8.5, .medium), .foregroundColor: Theme.mute.withAlphaComponent(alpha)])
            k.draw(at: NSPoint(x: bounds.width - k.size().width - 4, y: 2))
        }
        if let badge, badge > 0 {
            let b = NSAttributedString(string: "\(badge)", attributes: [.font: Theme.rounded(9, .bold), .foregroundColor: Theme.hex(0x04161c)])
            let w = max(13, b.size().width + 8)
            let br = NSRect(x: 4, y: 3, width: w, height: 13)
            Theme.cyan.withAlphaComponent(alpha).setFill()
            NSBezierPath(roundedRect: br, xRadius: 6.5, yRadius: 6.5).fill()
            b.draw(at: NSPoint(x: br.midX - b.size().width / 2, y: br.minY + (13 - b.size().height) / 2))
        }
    }
}

/// The "LT" handle you drag the toolbar by.
final class Grip: NSView {
    override var isFlipped: Bool { true }
    override var mouseDownCanMoveWindow: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        let s = NSAttributedString(string: "LT", attributes: [.font: Theme.font(13, .heavy), .foregroundColor: Theme.ink, .kern: -1])
        let z = s.size()
        s.draw(at: NSPoint(x: (bounds.width - z.width) / 2, y: (bounds.height - z.height) / 2))
    }
}

/// The ⋯ button: opens the toolbar menu.
final class MoreButton: NSView {
    var menuProvider: (() -> NSMenu)?
    override var isFlipped: Bool { true }
    override var mouseDownCanMoveWindow: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        let s = NSAttributedString(string: "⋯", attributes: [.font: Theme.font(16), .foregroundColor: Theme.mute])
        let z = s.size()
        s.draw(at: NSPoint(x: (bounds.width - z.width) / 2, y: (bounds.height - z.height) / 2))
    }
    override func mouseDown(with e: NSEvent) {
        guard let m = menuProvider?() else { return }
        m.popUp(positioning: nil, at: NSPoint(x: 0, y: bounds.height), in: self)
    }
}

/// The toolbar's root: background, and the right-click menu.
final class ToolbarRoot: PanelBackground {
    var menuProvider: (() -> NSMenu)?
    override func menu(for event: NSEvent) -> NSMenu? { menuProvider?() }
}

/// What the toolbar shows for a link state.
struct Look {
    enum Kind { case live, hidden, held, idle, off }
    var kind: Kind
    var phase: String
    var detail: String
    var peek: String
    var problem: String?

    static func of(_ link: Link, host: String?) -> Look {
        let where_ = host.map { $0.hasSuffix(".") ? String($0.dropLast()) : $0 } ?? "the captions Mac"
        switch link {
        case .notSet: return Look(kind: .off, phase: "Not set up", detail: "choose a Mac", peek: "", problem: "Choose the captions Mac and type its PIN")
        case .connecting: return Look(kind: .off, phase: "Connecting", detail: "…", peek: "", problem: "Connecting to \(where_)…")
        case .wrongPin: return Look(kind: .off, phase: "Wrong PIN", detail: "type it again", peek: "", problem: "The PIN has changed: type the new one")
        case .remoteOff: return Look(kind: .off, phase: "Remote off", detail: "retrying…", peek: "", problem: "Live Remote is off on \(where_)")
        case .lockedOut: return Look(kind: .off, phase: "Locked", detail: "1 minute", peek: "", problem: "Too many wrong PINs: wait a minute")
        case .unreachable: return Look(kind: .off, phase: "Not connected", detail: "retrying…", peek: "", problem: "Can't reach \(where_)")
        case .connected(let f):
            let lag = f.lag_s.map { String(format: "%.1f s behind", $0) } ?? "on screen"
            if f.paused { return Look(kind: .hidden, phase: "Hidden", detail: "not on screen", peek: "Hidden: the big screen is blank", problem: nil) }
            if f.song { return Look(kind: .held, phase: "Song", detail: "♪ on screen", peek: "Song: lyrics are not captioned", problem: nil) }
            if f.held { return Look(kind: .held, phase: "Holding", detail: lag, peek: f.screen, problem: nil) }
            let live = f.phase == "captioning" || f.phase == "listening" || (f.phase == nil && f.running)
            if !live { return Look(kind: .idle, phase: "Ready", detail: "not captioning", peek: "—", problem: nil) }
            return Look(kind: .live, phase: f.phase == "listening" ? "Listening" : "Captioning",
                        detail: f.phase == "listening" ? "waiting for speech" : lag,
                        peek: f.screen.isEmpty ? "—" : f.screen, problem: nil)
        }
    }

    var color: NSColor {
        switch kind { case .live: return Theme.lime; case .hidden: return Theme.amber; case .held: return Theme.cyan
                      case .idle: return Theme.label; case .off: return Theme.red }
    }
}

/// The floating toolbar: a non-activating panel above every window (ProPresenter keeps
/// focus), horizontal or vertical, dragged by its background or the LT handle.
final class ToolbarPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class ToolbarController {
    let panel: ToolbarPanel
    private let root = ToolbarRoot()
    private var keys: [String: KeyButton] = [:]
    private let led = Led(size: 9)
    private let phase = PassLabel.make("", size: 11, color: Theme.label)
    private let detail = PassLabel.make("", size: 11, color: Theme.mute)
    private let peek = PassLabel.make("", size: 13, color: Theme.cyanHi, lines: 2)
    private let problem = PassLabel.make("", size: 12.5, color: Theme.hex(0xffd2cd), lines: 2)
    private let more = MoreButton()
    private var stack: NSStackView?
    var vertical = false { didSet { if vertical != oldValue { rebuild() } } }
    var showPeek = true { didSet { if showPeek != oldValue { rebuild() } } }
    var onAction: ((String) -> Void)?
    var menuProvider: (() -> NSMenu)? { didSet { root.menuProvider = menuProvider; more.menuProvider = menuProvider } }
    private var look = Look.of(.notSet, host: nil)
    private var feed: Feed?
    private var builtOff = false                // the layout built has the problem box

    init() {
        panel = ToolbarPanel(contentRect: NSRect(x: 200, y: 200, width: 600, height: 66),
                             styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.isMovableByWindowBackground = true
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = true
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.contentView = root
        root.wantsLayer = true

        let defs: [(String, String, String, CGFloat)] = [
            ("pause", "eye", "Hide", 70), ("hold", "pause.fill", "Hold", 58), ("blank", "xmark.rectangle", "Clear", 58),
            ("retract", "arrow.uturn.backward", "Remove", 58), ("song", "music.note", "Song", 58),
            ("names", "person.text.rectangle", "Names", 58), ("full", "macwindow", "Full", 58)]
        for (a, sym, t, w) in defs {
            let k = KeyButton(action: a, symbol: sym, title: t, width: w)
            k.amberWhenOn = a == "pause"
            k.onPress = { [weak self] in self?.onAction?($0) }
            keys[a] = k
        }
        phase.font = Theme.font(11, .bold)
        more.translatesAutoresizingMaskIntoConstraints = false
        rebuild()
    }

    func setShortcuts(_ m: HotKeys.Modifiers) {
        for (a, k) in keys { k.shortcut = HotKeys.label(a, m) }
    }

    func show(_ link: Link, host: String?) {
        look = Look.of(link, host: host)
        if case .connected(let f) = link { feed = f } else { feed = nil }
        apply()
        if builtOff != (look.kind == .off) { rebuild() }
    }

    private func apply() {
        led.color = look.color
        led.hollow = look.kind == .idle
        phase.attributedStringValue = Theme.caps(look.phase, size: 11, color: look.color, weight: .bold, tracking: 0.14)
        detail.stringValue = look.detail
        detail.textColor = look.kind == .live || look.kind == .held ? Theme.cyan : Theme.mute
        peek.stringValue = look.peek
        peek.textColor = look.kind == .hidden ? Theme.mute : Theme.cyanHi
        problem.stringValue = look.problem ?? ""
        let connected = look.kind != .off
        let hidden = feed?.paused ?? false
        keys["pause"]?.on = hidden
        keys["pause"]?.symbol = hidden ? "eye.slash" : "eye"
        keys["pause"]?.title = hidden ? "Show" : "Hide"
        keys["hold"]?.on = feed?.held ?? false
        keys["song"]?.on = feed?.song ?? false
        keys["names"]?.badge = feed?.terms_n
        for (a, k) in keys { k.enabled = connected || a == "names" || a == "full" }
        panel.setAccessibilityTitle("Live Remote: \(look.phase)")
    }

    private func rebuild() {
        stack?.removeFromSuperview()
        problem.removeFromSuperview()
        peek.removeFromSuperview()
        builtOff = look.kind == .off
        let v = vertical
        let s = NSStackView()
        s.orientation = v ? .vertical : .horizontal
        s.spacing = 0
        s.alignment = v ? .centerX : .centerY
        s.translatesAutoresizingMaskIntoConstraints = false
        s.edgeInsets = NSEdgeInsets(top: 0, left: 0, bottom: 0, right: 0)

        let grip = Grip()
        grip.translatesAutoresizingMaskIntoConstraints = false
        if v { grip.heightAnchor.constraint(equalToConstant: 26).isActive = true; grip.widthAnchor.constraint(equalToConstant: 76).isActive = true }
        else { grip.widthAnchor.constraint(equalToConstant: 30).isActive = true; grip.heightAnchor.constraint(equalToConstant: 66).isActive = true }
        s.addArrangedSubview(grip)
        s.addArrangedSubview(Divider(vertical: !v))

        // status: LED + phase over the detail line (vertical: LED over the detail)
        let stat = NSStackView()
        stat.orientation = .vertical
        stat.alignment = v ? .centerX : .leading
        stat.spacing = 1
        stat.edgeInsets = v ? NSEdgeInsets(top: 6, left: 4, bottom: 6, right: 4) : NSEdgeInsets(top: 6, left: 10, bottom: 6, right: 12)
        let top = NSStackView(views: v ? [led] : [led, phase])
        top.spacing = 3
        stat.addArrangedSubview(top)
        detail.alignment = v ? .center : .left
        stat.addArrangedSubview(detail)
        if v { stat.widthAnchor.constraint(equalToConstant: 76).isActive = true; detail.preferredMaxLayoutWidth = 70; detail.maximumNumberOfLines = 2 }
        else { stat.widthAnchor.constraint(greaterThanOrEqualToConstant: 118).isActive = true; detail.maximumNumberOfLines = 1 }
        s.addArrangedSubview(stat)
        s.addArrangedSubview(Divider(vertical: !v))

        let ks = NSStackView()
        ks.orientation = v ? .vertical : .horizontal
        ks.spacing = 6
        ks.edgeInsets = v ? NSEdgeInsets(top: 8, left: 7, bottom: 8, right: 7) : NSEdgeInsets(top: 7, left: 8, bottom: 7, right: 8)
        for a in ["pause", "hold", "blank", "retract", "song"] { if let k = keys[a] { ks.addArrangedSubview(k) } }
        let sep = Divider(vertical: !v)
        ks.addArrangedSubview(sep)
        if v { sep.widthAnchor.constraint(equalToConstant: 58).isActive = true } else { sep.heightAnchor.constraint(equalToConstant: 46).isActive = true }
        if let n = keys["names"] { ks.addArrangedSubview(n) }
        if let f = keys["full"] { ks.addArrangedSubview(f) }       // the whole Live screen in a window
        for k in keys.values { k.width = v ? 62 : (k.action == "pause" ? 70 : 58) }
        s.addArrangedSubview(ks)

        if !v && look.kind == .off {
            s.addArrangedSubview(Divider(vertical: true))
            let box = NSStackView(views: [problem])
            box.edgeInsets = NSEdgeInsets(top: 6, left: 12, bottom: 6, right: 12)
            problem.preferredMaxLayoutWidth = 230
            box.widthAnchor.constraint(lessThanOrEqualToConstant: 254).isActive = true
            box.wantsLayer = true
            box.layer?.backgroundColor = Theme.hex(0xff5d52, 0.12).cgColor
            s.addArrangedSubview(box)
        } else if !v && showPeek {
            s.addArrangedSubview(Divider(vertical: true))
            let box = NSStackView(views: [peek])
            box.edgeInsets = NSEdgeInsets(top: 6, left: 12, bottom: 6, right: 12)
            peek.preferredMaxLayoutWidth = 256
            box.widthAnchor.constraint(equalToConstant: 280).isActive = true
            s.addArrangedSubview(box)
        }
        s.addArrangedSubview(Divider(vertical: !v))
        more.removeConstraints(more.constraints)
        if v { more.heightAnchor.constraint(equalToConstant: 22).isActive = true; more.widthAnchor.constraint(equalToConstant: 76).isActive = true }
        else { more.widthAnchor.constraint(equalToConstant: 22).isActive = true; more.heightAnchor.constraint(equalToConstant: 66).isActive = true }
        s.addArrangedSubview(more)

        root.addSubview(s)
        NSLayoutConstraint.activate([
            s.leadingAnchor.constraint(equalTo: root.leadingAnchor), s.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            s.topAnchor.constraint(equalTo: root.topAnchor), s.bottomAnchor.constraint(equalTo: root.bottomAnchor)])
        stack = s
        apply()
        root.layoutSubtreeIfNeeded()
        let size = s.fittingSize
        var f = panel.frame
        let topY = f.maxY                                      // keep the top-left corner where it is
        f.size = size
        f.origin.y = topY - size.height
        panel.setFrame(f, display: true)
        root.needsDisplay = true
    }
}
