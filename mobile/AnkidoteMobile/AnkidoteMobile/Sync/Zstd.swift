import Foundation

enum ZstdError: LocalizedError {
    case compressFailed(String)
    case decompressFailed(String)
    case unknownSize

    var errorDescription: String? {
        switch self {
        case .compressFailed(let m): return "zstd compress failed: \(m)"
        case .decompressFailed(let m): return "zstd decompress failed: \(m)"
        case .unknownSize: return "zstd: unknown decompressed size"
        }
    }
}

/// Thin Swift wrapper over the vendored libzstd (one-shot API).
///
/// Anki sync v11 zstd-compresses every request body and the server returns a
/// zstd-compressed response with the decompressed length in the
/// `anki-original-size` header.
enum Zstd {
    static func compress(_ data: Data, level: Int32 = 3) throws -> Data {
        let bound = ZSTD_compressBound(data.count)
        var dst = Data(count: bound)
        let written: Int = dst.withUnsafeMutableBytes { dstRaw in
            data.withUnsafeBytes { srcRaw in
                ZSTD_compress(
                    dstRaw.baseAddress, bound,
                    srcRaw.baseAddress, data.count,
                    level
                )
            }
        }
        if ZSTD_isError(written) != 0 {
            throw ZstdError.compressFailed(String(cString: ZSTD_getErrorName(written)))
        }
        dst.removeSubrange(written ..< dst.count)
        return dst
    }

    /// Decompress a full zstd frame. `expectedSize` should be the value of the
    /// `anki-original-size` response header when available; otherwise the frame
    /// header content size is used.
    static func decompress(_ data: Data, expectedSize: Int? = nil) throws -> Data {
        var capacity = expectedSize ?? 0
        if capacity <= 0 {
            let frameSize: UInt64 = data.withUnsafeBytes { raw in
                ZSTD_getFrameContentSize(raw.baseAddress, data.count)
            }
            // ZSTD_CONTENTSIZE_UNKNOWN == -1, ZSTD_CONTENTSIZE_ERROR == -2 as u64.
            if frameSize == UInt64.max || frameSize == UInt64.max - 1 {
                return try decompressStreaming(data)
            }
            capacity = Int(frameSize)
        }
        if capacity == 0 { return Data() }

        var dst = Data(count: capacity)
        let written: Int = dst.withUnsafeMutableBytes { dstRaw in
            data.withUnsafeBytes { srcRaw in
                ZSTD_decompress(
                    dstRaw.baseAddress, capacity,
                    srcRaw.baseAddress, data.count
                )
            }
        }
        if ZSTD_isError(written) != 0 {
            // Fall back to streaming (handles size mismatches gracefully).
            return try decompressStreaming(data)
        }
        if written != capacity {
            dst.removeSubrange(written ..< dst.count)
        }
        return dst
    }

    /// Streaming decompression fallback for frames without a known size.
    private static func decompressStreaming(_ data: Data) throws -> Data {
        guard let dStream = ZSTD_createDStream() else {
            throw ZstdError.decompressFailed("could not create DStream")
        }
        defer { ZSTD_freeDStream(dStream) }
        _ = ZSTD_initDStream(dStream)

        let outCapacity = ZSTD_DStreamOutSize()
        var output = Data()
        let outBuffer = UnsafeMutableRawPointer.allocate(byteCount: outCapacity, alignment: 16)
        defer { outBuffer.deallocate() }

        let result: Result<Data, ZstdError> = data.withUnsafeBytes { srcRaw -> Result<Data, ZstdError> in
            guard let srcBase = srcRaw.baseAddress else { return .success(Data()) }
            var input = ZSTD_inBuffer(src: srcBase, size: data.count, pos: 0)
            while input.pos < input.size {
                var out = ZSTD_outBuffer(dst: outBuffer, size: outCapacity, pos: 0)
                let r = ZSTD_decompressStream(dStream, &out, &input)
                if ZSTD_isError(r) != 0 {
                    return .failure(.decompressFailed(String(cString: ZSTD_getErrorName(r))))
                }
                if out.pos > 0 {
                    output.append(outBuffer.assumingMemoryBound(to: UInt8.self), count: out.pos)
                }
            }
            return .success(output)
        }
        switch result {
        case .success(let d): return d
        case .failure(let e): throw e
        }
    }
}
