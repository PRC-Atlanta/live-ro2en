// swift-tools-version:5.10
// Live Remote: the toolbar for the ProPresenter Mac. `./build.sh` builds "Live Remote.app".
import PackageDescription

let package = Package(
    name: "LiveRemote",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "LiveRemote",
            path: "Sources/LiveRemote",
            linkerSettings: [.linkedFramework("AppKit"), .linkedFramework("Carbon"), .linkedFramework("WebKit")]
        )
    ],
    swiftLanguageVersions: [.v5]
)
