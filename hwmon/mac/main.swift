// ai-usage-hwmon — leitor de hardware do Mac para o painel ai-usage.
//
// Escreve uma linha JSON por ciclo no stdout e termina sozinho quando o
// stdin fecha (o painel que o abriu morreu). Nada aqui pede senha de
// administrador: CPU e memoria vem do kernel (host_*), GPU do IOAccelerator,
// disco do IOBlockStorageDriver, rede do sysctl, bateria do AppleSmartBattery
// e temperaturas dos sensores HID (os mesmos que apps de monitor usam).
//
// Uso: ai-usage-hwmon [segundos]      (padrao 2)
//      ai-usage-hwmon --once

import Foundation
import IOKit
import Darwin
import AppKit

// MARK: - utilitarios

func sysctlInt(_ name: String) -> Int? {
    var v: Int64 = 0
    var size = MemoryLayout<Int64>.size
    if sysctlbyname(name, &v, &size, nil, 0) == 0 { return Int(v) }
    var v32: Int32 = 0
    size = MemoryLayout<Int32>.size
    if sysctlbyname(name, &v32, &size, nil, 0) == 0 { return Int(v32) }
    return nil
}

func sysctlString(_ name: String) -> String? {
    var size = 0
    guard sysctlbyname(name, nil, &size, nil, 0) == 0, size > 0 else { return nil }
    var buf = [CChar](repeating: 0, count: size)
    guard sysctlbyname(name, &buf, &size, nil, 0) == 0 else { return nil }
    return String(cString: buf)
}

func props(_ entry: io_registry_entry_t) -> [String: Any] {
    var dict: Unmanaged<CFMutableDictionary>?
    guard IORegistryEntryCreateCFProperties(entry, &dict, kCFAllocatorDefault, 0) == KERN_SUCCESS,
          let d = dict?.takeRetainedValue() as? [String: Any] else { return [:] }
    return d
}

func eachService(_ cls: String, _ body: ([String: Any]) -> Void) {
    var it: io_iterator_t = 0
    guard IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching(cls), &it) == KERN_SUCCESS else { return }
    defer { IOObjectRelease(it) }
    var s = IOIteratorNext(it)
    while s != 0 {
        body(props(s))
        IOObjectRelease(s)
        s = IOIteratorNext(it)
    }
}

func num(_ v: Any?) -> Double? {
    if let n = v as? NSNumber { return n.doubleValue }
    return nil
}

func r1(_ v: Double?) -> Double? {
    guard let v = v, v.isFinite else { return nil }
    return (v * 10).rounded() / 10
}

// MARK: - temperaturas (sensores HID, sem root)

@_silgen_name("IOHIDEventSystemClientCreate")
func IOHIDEventSystemClientCreate(_ a: CFAllocator?) -> Unmanaged<AnyObject>?
@_silgen_name("IOHIDEventSystemClientSetMatching")
func IOHIDEventSystemClientSetMatching(_ c: AnyObject, _ m: CFDictionary) -> Int32
@_silgen_name("IOHIDEventSystemClientCopyServices")
func IOHIDEventSystemClientCopyServices(_ c: AnyObject) -> Unmanaged<CFArray>?
@_silgen_name("IOHIDServiceClientCopyProperty")
func IOHIDServiceClientCopyProperty(_ s: AnyObject, _ k: CFString) -> Unmanaged<AnyObject>?
@_silgen_name("IOHIDServiceClientCopyEvent")
func IOHIDServiceClientCopyEvent(_ s: AnyObject, _ t: Int64, _ a: Int32, _ b: Int64) -> Unmanaged<AnyObject>?
@_silgen_name("IOHIDEventGetFloatValue")
func IOHIDEventGetFloatValue(_ e: AnyObject, _ f: Int32) -> Double

let kTempEvent: Int64 = 15
let hidClient: AnyObject? = {
    guard let c = IOHIDEventSystemClientCreate(kCFAllocatorDefault)?.takeRetainedValue() else { return nil }
    _ = IOHIDEventSystemClientSetMatching(c, ["PrimaryUsagePage": 0xff00, "PrimaryUsage": 5] as CFDictionary)
    return c
}()

struct Temps { var chip: [Double] = []; var ssd: [Double] = []; var battery: [Double] = []; var other: [String: Double] = [:] }

func readTemps() -> Temps {
    var t = Temps()
    guard let c = hidClient,
          let arr = IOHIDEventSystemClientCopyServices(c)?.takeRetainedValue() as? [AnyObject] else { return t }
    for s in arr {
        let name = (IOHIDServiceClientCopyProperty(s, "Product" as CFString)?.takeRetainedValue() as? String) ?? ""
        guard let e = IOHIDServiceClientCopyEvent(s, kTempEvent, 0, 0)?.takeRetainedValue() else { continue }
        let v = IOHIDEventGetFloatValue(e, Int32(kTempEvent << 16))
        guard v > 0, v < 130 else { continue }   // sensores de calibracao trazem lixo
        let n = name.lowercased()
        if n.contains("tdie") || n.contains("cpu") || n.contains("soc") || n.contains("gpu") { t.chip.append(v) }
        else if n.contains("nand") || n.contains("ssd") { t.ssd.append(v) }
        else if n.contains("battery") || n.contains("gas gauge") { t.battery.append(v) }
        else { t.other[name] = v }
    }
    return t
}

// MARK: - CPU

var prevTicks: [[UInt32]]? = nil

func readCPU() -> [String: Any] {
    var count: natural_t = 0
    var info: processor_info_array_t?
    var infoCount: mach_msg_type_number_t = 0
    guard host_processor_info(mach_host_self(), PROCESSOR_CPU_LOAD_INFO, &count, &info, &infoCount) == KERN_SUCCESS,
          let info = info else { return [:] }
    defer { vm_deallocate(mach_task_self_, vm_address_t(bitPattern: info), vm_size_t(infoCount) * vm_size_t(MemoryLayout<integer_t>.stride)) }
    var ticks: [[UInt32]] = []
    for i in 0..<Int(count) {
        let base = i * Int(CPU_STATE_MAX)
        ticks.append((0..<Int(CPU_STATE_MAX)).map { UInt32(bitPattern: info[base + $0]) })
    }
    defer { prevTicks = ticks }
    guard let prev = prevTicks, prev.count == ticks.count else { return ["cores": count] }
    var perCore: [Double] = []
    var tu = 0.0, ts = 0.0, tt = 0.0
    for (a, b) in zip(prev, ticks) {
        let d = (0..<Int(CPU_STATE_MAX)).map { Double(b[$0] &- a[$0]) }
        let user = d[Int(CPU_STATE_USER)] + d[Int(CPU_STATE_NICE)]
        let sys = d[Int(CPU_STATE_SYSTEM)]
        let total = d.reduce(0, +)
        perCore.append(total > 0 ? (user + sys) / total * 100 : 0)
        tu += user; ts += sys; tt += total
    }
    // no Apple Silicon os nucleos de eficiencia vem primeiro na numeracao
    let eff = sysctlInt("hw.perflevel1.logicalcpu") ?? 0
    let perf = sysctlInt("hw.perflevel0.logicalcpu") ?? perCore.count
    func avg(_ xs: ArraySlice<Double>) -> Double? { xs.isEmpty ? nil : xs.reduce(0, +) / Double(xs.count) }
    var out: [String: Any] = [
        "usage": r1(tt > 0 ? (tu + ts) / tt * 100 : 0)!,
        "user": r1(tt > 0 ? tu / tt * 100 : 0)!,
        "system": r1(tt > 0 ? ts / tt * 100 : 0)!,
        "cores": perCore.count,
        "per_core": perCore.map { r1($0)! },
    ]
    if eff > 0 && eff + perf == perCore.count {
        out["efficiency_usage"] = r1(avg(perCore[0..<eff]))
        out["performance_usage"] = r1(avg(perCore[eff...]))
        out["efficiency_cores"] = eff
        out["performance_cores"] = perf
        out["performance_name"] = sysctlString("hw.perflevel0.name") ?? "Performance"
    }
    var load = [Double](repeating: 0, count: 3)
    if getloadavg(&load, 3) == 3 { out["load"] = load.map { r1($0)! } }
    return out
}

// MARK: - memoria

func readMemory() -> [String: Any] {
    let total = Double(ProcessInfo.processInfo.physicalMemory)
    var stats = vm_statistics64()
    var n = mach_msg_type_number_t(MemoryLayout<vm_statistics64>.stride / MemoryLayout<integer_t>.stride)
    let ok = withUnsafeMutablePointer(to: &stats) {
        $0.withMemoryRebound(to: integer_t.self, capacity: Int(n)) { host_statistics64(mach_host_self(), HOST_VM_INFO64, $0, &n) }
    }
    guard ok == KERN_SUCCESS else { return ["total": total] }
    let page = Double(vm_kernel_page_size)
    let app = Double(stats.internal_page_count &- stats.purgeable_count) * page
    let wired = Double(stats.wire_count) * page
    let compressed = Double(stats.compressor_page_count) * page
    let cached = Double(stats.external_page_count &+ stats.purgeable_count) * page
    let used = app + wired + compressed
    var swap = xsw_usage()
    var sz = MemoryLayout<xsw_usage>.size
    sysctlbyname("vm.swapusage", &swap, &sz, nil, 0)
    var pressure: Int32 = 0
    var psz = MemoryLayout<Int32>.size
    sysctlbyname("kern.memorystatus_vm_pressure_level", &pressure, &psz, nil, 0)
    return ["total": total, "used": used, "free": max(0, total - used), "app": app, "wired": wired,
            "compressed": compressed, "cached": cached, "usage": r1(used / total * 100)!,
            "swap_used": Double(swap.xsu_used), "pressure_level": Int(pressure)]
}

// MARK: - GPU

func readGPU() -> [String: Any] {
    var out: [String: Any] = [:]
    eachService("IOAccelerator") { p in
        if let cores = num(p["gpu-core-count"]) { out["cores"] = Int(cores) }
        if let model = p["model"] as? String { out["model"] = model }
        guard let ps = p["PerformanceStatistics"] as? [String: Any] else { return }
        if let v = num(ps["Device Utilization %"]) { out["usage"] = v }
        if let v = num(ps["Renderer Utilization %"]) { out["renderer"] = v }
        if let v = num(ps["Tiler Utilization %"]) { out["tiler"] = v }
        if let v = num(ps["In use system memory"]) { out["memory_used"] = v }
        if let v = num(ps["Alloc system memory"]) { out["memory_alloc"] = v }
    }
    return out
}

// MARK: - disco

var prevDisk: (r: Double, w: Double, t: Double)? = nil

func readDisk() -> [String: Any] {
    var out: [String: Any] = [:]
    if let a = try? FileManager.default.attributesOfFileSystem(forPath: "/"),
       let total = num(a[.systemSize]), let free = num(a[.systemFreeSize]) {
        // "importante" = o que o Finder mostra como disponivel (inclui purgavel)
        var avail = free
        if let v = try? URL(fileURLWithPath: "/").resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
           let imp = v.volumeAvailableCapacityForImportantUsage { avail = Double(imp) }
        out["total"] = total
        out["free"] = avail
        out["used"] = total - avail
        out["usage"] = r1((total - avail) / total * 100)
    }
    var rb = 0.0, wb = 0.0
    eachService("IOBlockStorageDriver") { p in
        guard let st = p["Statistics"] as? [String: Any] else { return }
        rb += num(st["Bytes (Read)"]) ?? 0
        wb += num(st["Bytes (Write)"]) ?? 0
    }
    let now = Date().timeIntervalSince1970
    if let pd = prevDisk, now > pd.t {
        out["read_bps"] = max(0, (rb - pd.r) / (now - pd.t))
        out["write_bps"] = max(0, (wb - pd.w) / (now - pd.t))
    }
    prevDisk = (rb, wb, now)
    return out
}

// MARK: - rede (contadores de 64 bits por interface)

var prevNet: (rx: Double, tx: Double, t: Double)? = nil
var firstNet: (rx: Double, tx: Double)? = nil

func readNet() -> [String: Any] {
    var mib: [Int32] = [CTL_NET, PF_ROUTE, 0, 0, NET_RT_IFLIST2, 0]
    var len = 0
    guard sysctl(&mib, 6, nil, &len, nil, 0) == 0, len > 0 else { return [:] }
    var buf = [UInt8](repeating: 0, count: len)
    guard sysctl(&mib, 6, &buf, &len, nil, 0) == 0 else { return [:] }
    var rx = 0.0, tx = 0.0
    var off = 0
    buf.withUnsafeBytes { raw in
        while off + MemoryLayout<if_msghdr>.size <= len {
            let hdr = raw.load(fromByteOffset: off, as: if_msghdr.self)
            if Int32(hdr.ifm_type) == RTM_IFINFO2 {
                let h2 = raw.load(fromByteOffset: off, as: if_msghdr2.self)
                // ignora loopback (lo0): nao e' trafego de internet
                if (h2.ifm_flags & IFF_LOOPBACK) == 0 {
                    rx += Double(h2.ifm_data.ifi_ibytes)
                    tx += Double(h2.ifm_data.ifi_obytes)
                }
            }
            if hdr.ifm_msglen == 0 { break }
            off += Int(hdr.ifm_msglen)
        }
    }
    let now = Date().timeIntervalSince1970
    if firstNet == nil { firstNet = (rx, tx) }
    var out: [String: Any] = ["rx_total": rx - firstNet!.rx, "tx_total": tx - firstNet!.tx]
    if let p = prevNet, now > p.t {
        out["rx_bps"] = max(0, (rx - p.rx) / (now - p.t))
        out["tx_bps"] = max(0, (tx - p.tx) / (now - p.t))
    }
    prevNet = (rx, tx, now)
    return out
}

// MARK: - bateria

func readBattery(_ temps: Temps) -> [String: Any]? {
    var out: [String: Any]? = nil
    eachService("AppleSmartBattery") { p in
        var b: [String: Any] = [:]
        if let cur = num(p["CurrentCapacity"]), let mx = num(p["MaxCapacity"]), mx > 0 {
            b["percent"] = r1(mx <= 100 ? cur : cur / mx * 100)
        }
        b["charging"] = (p["IsCharging"] as? Bool) ?? false
        b["plugged"] = (p["ExternalConnected"] as? Bool) ?? false
        if let c = num(p["CycleCount"]) { b["cycles"] = Int(c) }
        let bd = p["BatteryData"] as? [String: Any] ?? [:]
        if let full = num(bd["NominalChargeCapacity"]) ?? num(p["NominalChargeCapacity"]) ?? num(p["AppleRawMaxCapacity"]),
           let design = num(bd["DesignCapacity"]) ?? num(p["DesignCapacity"]), design > 0 {
            b["health"] = r1(min(100, full / design * 100))
        }
        if let t = num(p["TimeRemaining"]), t > 0, t < 6000 { b["minutes_remaining"] = Int(t) }
        if let w = num(p["Watts"]) { b["watts"] = w }
        if let v = num(p["Voltage"]), let a = num(p["Amperage"]) {
            let amp = a > 32767 ? a - 65536 : a
            b["power_w"] = r1(v * amp / 1_000_000)
        }
        if let rt = num(p["Temperature"]) { b["temp"] = r1(rt / 100) }
        else if !temps.battery.isEmpty { b["temp"] = r1(temps.battery.reduce(0, +) / Double(temps.battery.count)) }
        out = b
    }
    return out
}

// MARK: - laco principal

func thermalState() -> String {
    switch ProcessInfo.processInfo.thermalState {
    case .nominal: return "normal"
    case .fair: return "morno"
    case .serious: return "quente"
    case .critical: return "critico"
    @unknown default: return "?"
    }
}

let machine: [String: Any] = [
    "model": sysctlString("hw.model") ?? "",
    "chip": sysctlString("machdep.cpu.brand_string") ?? "",
    "os": ProcessInfo.processInfo.operatingSystemVersionString,
]

func sample() -> [String: Any] {
    let temps = readTemps()
    var cpu = readCPU()
    var gpu = readGPU()
    var disk = readDisk()
    let chipMax = temps.chip.max()
    let chipAvg = temps.chip.isEmpty ? nil : temps.chip.reduce(0, +) / Double(temps.chip.count)
    // no Apple Silicon CPU e GPU sao o mesmo chip; os sensores nao separam
    if let m = chipMax { cpu["temp"] = r1(m); gpu["temp"] = r1(m) }
    if let s = temps.ssd.max() { disk["temp"] = r1(s) }
    var out: [String: Any] = [
        "ts": Date().timeIntervalSince1970,
        "platform": "macos",
        "machine": machine,
        "cpu": cpu, "gpu": gpu, "memory": readMemory(), "disk": disk, "net": readNet(),
        "thermal": ["state": thermalState(), "chip_max": r1(chipMax) as Any, "chip_avg": r1(chipAvg) as Any],
    ]
    if let b = readBattery(temps) { out["battery"] = b }
    return out
}

func emit(_ o: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: o, options: []) else { return }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

let args = CommandLine.arguments

// --icon <app> <saida.png> [tamanho]: o icone do app como o Finder mostra
// (inclui o selo das copias do Parall). Usado nas teclas do Stream Deck.
if args.count >= 4 && args[1] == "--icon" {
    let size = CGFloat(Double(args.count > 4 ? args[4] : "144") ?? 144)
    let img = NSWorkspace.shared.icon(forFile: args[2])
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size),
                                     bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                     colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { exit(1) }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    img.draw(in: NSRect(x: 0, y: 0, width: size, height: size))
    NSGraphicsContext.restoreGraphicsState()
    guard let png = rep.representation(using: .png, properties: [:]),
          (try? png.write(to: URL(fileURLWithPath: args[3]))) != nil else { exit(1) }
    exit(0)
}

if args.contains("--once") {
    _ = sample()
    Thread.sleep(forTimeInterval: 0.5)
    emit(sample())
    exit(0)
}
let interval = max(0.5, Double(args.dropFirst().first ?? "2") ?? 2)

// pai morreu (stdin fechado) = encerra
Thread.detachNewThread {
    while FileHandle.standardInput.availableData.count > 0 {}
    exit(0)
}

_ = sample()
Thread.sleep(forTimeInterval: 0.5)
while true {
    emit(sample())
    Thread.sleep(forTimeInterval: interval)
}
