import AppKit

/// The main app's tokens (src/live/ui/styles/tokens.css): dark teal panel, cyan accents.
enum Theme {
    static func hex(_ v: UInt32, _ a: CGFloat = 1) -> NSColor {
        NSColor(srgbRed: CGFloat((v >> 16) & 0xff) / 255, green: CGFloat((v >> 8) & 0xff) / 255,
                blue: CGFloat(v & 0xff) / 255, alpha: a)
    }
    static let bg1 = hex(0x0a1a22), bg2 = hex(0x10262f), bg3 = hex(0x16323d)
    static let edge = hex(0x2b6f7e), edgeSoft = hex(0x2b6f7e, 0.45), glow = hex(0x46d6ec, 0.28)
    static let cyan = hex(0x46d6ec), cyanHi = hex(0x9df1ff), cyanDim = hex(0x1f7f92)
    static let lime = hex(0xa3e635), amber = hex(0xf5b83d), red = hex(0xff5d52)
    static let ink = hex(0xd9eef2), label = hex(0xb9cbd0), mute = hex(0x6f8b93)
    static let keyTop = hex(0x1a2024), keyBottom = hex(0x101417), keyEdge = hex(0x2c3438)
    static let onTop = hex(0x123742), onBottom = hex(0x0b2028)
    static let amberTop = hex(0x4a3510), amberBottom = hex(0x2a1e08), amberInk = hex(0xffe1a6)
    static let well = hex(0x050b0e)

    static func font(_ size: CGFloat, _ weight: NSFont.Weight = .regular) -> NSFont {
        .systemFont(ofSize: size, weight: weight)
    }
    static func rounded(_ size: CGFloat, _ weight: NSFont.Weight = .semibold) -> NSFont {
        let f = NSFont.systemFont(ofSize: size, weight: weight)
        if let d = f.fontDescriptor.withDesign(.rounded), let r = NSFont(descriptor: d, size: size) { return r }
        return f
    }

    /// UPPERCASE with the main app's letter spacing.
    static func caps(_ s: String, size: CGFloat, color: NSColor, weight: NSFont.Weight = .semibold,
                     tracking: CGFloat = 0.12) -> NSAttributedString {
        NSAttributedString(string: s.uppercased(), attributes: [
            .font: font(size, weight), .foregroundColor: color, .kern: size * tracking])
    }
}

/// The panel background: radial teal gradient, cyan edge and glow.
class PanelBackground: NSView {
    var radius: CGFloat = 10
    override var isFlipped: Bool { true }
    override var mouseDownCanMoveWindow: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        let r = bounds.insetBy(dx: 0.5, dy: 0.5)
        let path = NSBezierPath(roundedRect: r, xRadius: radius, yRadius: radius)
        NSGradient(colors: [Theme.bg3, Theme.bg2, Theme.bg1], atLocations: [0, 0.4, 1], colorSpace: .sRGB)?
            .draw(in: path, relativeCenterPosition: NSPoint(x: 0, y: 0.1))
        Theme.edge.setStroke()
        path.lineWidth = 1
        path.stroke()
        Theme.hex(0x46d6ec, 0.08).setStroke()
        NSBezierPath(roundedRect: r.insetBy(dx: 1, dy: 1), xRadius: radius - 1, yRadius: radius - 1).stroke()
    }
}

/// A thin divider in the panel's edge colour.
final class Divider: NSView {
    init(vertical: Bool) {
        super.init(frame: .zero)
        wantsLayer = true
        layer?.backgroundColor = Theme.edgeSoft.cgColor
        translatesAutoresizingMaskIntoConstraints = false
        if vertical { widthAnchor.constraint(equalToConstant: 1).isActive = true }
        else { heightAnchor.constraint(equalToConstant: 1).isActive = true }
    }
    required init?(coder: NSCoder) { nil }
    override var mouseDownCanMoveWindow: Bool { true }
}

/// A round status light.
final class Led: NSView {
    var color: NSColor = Theme.mute { didSet { needsDisplay = true } }
    var hollow = false { didSet { needsDisplay = true } }
    init(size: CGFloat) {
        super.init(frame: NSRect(x: 0, y: 0, width: size + 8, height: size + 8))
        translatesAutoresizingMaskIntoConstraints = false
        widthAnchor.constraint(equalToConstant: size + 8).isActive = true
        heightAnchor.constraint(equalToConstant: size + 8).isActive = true
    }
    required init?(coder: NSCoder) { nil }
    override var mouseDownCanMoveWindow: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        let d = bounds.width - 8
        let r = NSRect(x: 4, y: 4, width: d, height: d)
        if hollow {
            Theme.hex(0x0d2a33).setFill()
            NSBezierPath(ovalIn: r).fill()
            Theme.hex(0x2b5d68).setStroke()
            let p = NSBezierPath(ovalIn: r.insetBy(dx: 1, dy: 1)); p.lineWidth = 2; p.stroke()
            return
        }
        NSGraphicsContext.saveGraphicsState()
        let sh = NSShadow(); sh.shadowColor = color; sh.shadowBlurRadius = 6; sh.shadowOffset = .zero; sh.set()
        color.setFill()
        NSBezierPath(ovalIn: r).fill()
        NSGraphicsContext.restoreGraphicsState()
    }
}

/// A label that lets the window be dragged through it.
final class PassLabel: NSTextField {
    override var mouseDownCanMoveWindow: Bool { true }
    static func make(_ s: String = "", size: CGFloat = 12, color: NSColor = Theme.label, lines: Int = 1) -> PassLabel {
        let l = PassLabel(labelWithString: s)
        l.font = Theme.font(size)
        l.textColor = color
        l.maximumNumberOfLines = lines
        l.lineBreakMode = lines == 1 ? .byTruncatingTail : .byWordWrapping
        l.translatesAutoresizingMaskIntoConstraints = false
        l.isSelectable = false
        return l
    }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
}
