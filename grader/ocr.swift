// Local Apple Vision text extraction. No student program is executed.
import Foundation
import Vision
import CoreGraphics
import ImageIO

func recognize(_ image: CGImage, page: Int) throws -> [String: Any] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesCPUOnly = true
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    let observations = (request.results ?? []).sorted {
        if abs($0.boundingBox.maxY - $1.boundingBox.maxY) > 0.012 {
            return $0.boundingBox.maxY > $1.boundingBox.maxY
        }
        return $0.boundingBox.minX < $1.boundingBox.minX
    }
    let lines = observations.compactMap { $0.topCandidates(1).first }
    let confidence = lines.isEmpty ? 0 : lines.map { Double($0.confidence) }.reduce(0, +) / Double(lines.count)
    return ["page": page, "text": lines.map { $0.string }.joined(separator: "\n"), "confidence": confidence]
}

do {
    guard CommandLine.arguments.count == 2 else { throw NSError(domain: "OCR", code: 1) }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    var pages: [[String: Any]] = []
    if url.pathExtension.lowercased() == "pdf" {
        guard let document = CGPDFDocument(url as CFURL), document.numberOfPages <= 100 else {
            throw NSError(domain: "OCR", code: 2)
        }
        for number in 1...max(document.numberOfPages, 1) {
            guard let page = document.page(at: number) else { continue }
            let box = page.getBoxRect(.mediaBox)
            let scale = min(2.0, 3000.0 / max(box.width, box.height))
            let width = max(1, Int(box.width * scale))
            let height = max(1, Int(box.height * scale))
            guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { continue }
            context.setFillColor(CGColor(gray: 1, alpha: 1))
            context.fill(CGRect(x: 0, y: 0, width: width, height: height))
            context.concatenate(page.getDrawingTransform(.mediaBox,
                rect: CGRect(x: 0, y: 0, width: width, height: height), rotate: 0, preserveAspectRatio: true))
            context.drawPDFPage(page)
            if let image = context.makeImage() { pages.append(try recognize(image, page: number)) }
        }
    } else {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil),
              image.width * image.height <= 40_000_000 else { throw NSError(domain: "OCR", code: 3) }
        pages.append(try recognize(image, page: 1))
    }
    let data = try JSONSerialization.data(withJSONObject: ["pages": pages])
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data(("Local OCR failed: " + String(describing: error) + "\n").utf8))
    exit(1)
}
