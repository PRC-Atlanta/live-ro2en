// swift-tools-version:5.10
// The Live RO2EN Mac shell. `./build.sh` builds it and assembles the ad-hoc signed .app.
import PackageDescription

let package = Package(
    name: "LiveRO2EN",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "LiveRO2EN",
            path: "Sources/LiveRO2EN",
            linkerSettings: [.linkedFramework("WebKit"), .linkedFramework("AppKit")]
        )
    ],
    swiftLanguageVersions: [.v5]
)
