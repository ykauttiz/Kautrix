// acphal.dll: Audio Control Processor HAL. Scalable Hardware Audio Processing Engine
// (SHAPE) and APU (Audio Processing Unit) physical memory management for Xbox Series.
//
// On a PC there is no SHAPE/ACP hardware and APU physical memory does not exist.
// This shim creates the APU heap from process memory and manages allocations within it.
// The SHAPE context arrays are allocated on the process heap; the ACP interface
// accepts and acknowledges commands to allow audio bring-up to proceed cleanly.
//
// Signatures from the public GDK reference (devdocs.xbox.com):
//   AcpHal, APU reference, SHAPE_CONTEXTS, APU_ADDRESS.
// Measured call-site shapes and findings from the retail executable (1.0.2.1):
//   - docs/retail_boot_chain.md, docs/retail_boot_frame_loop.md (Stop 28)
//   - PR #6885 (call sites and parameter analysis), PR #7009 (audio/HAL bring-up).

#include <windows.h>

#include <atomic>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <vector>

#include "shims.h"
#include "startup_trace.h"

namespace {

// ---- APU_ADDRESS (from the GDK) ----

// The console APU uses a 40-bit physical address space.
// On PC we translate to a 32-bit APU address offset.
struct APU_ADDRESS {
    uint64_t value;  // 40-bit physical address
};

// ---- SHAPE context structures (from the public GDK documentation at devdocs.xbox.com) ----
//
// These structures are defined here with their documented GDK field layouts so that
// sizeof() returns the correct per-context byte count. Without the real layouts the
// empty structs would be 1 byte each, and the title's writes (e.g. memset with the
// real stride) would overflow the allocation.

// UINT32 bitfields packed into 32-bit words
struct SHAPE_SRC_CONTEXT {
    uint32_t timestamp : 21;
    uint32_t blocksToSkip : 2;
    uint32_t internalSaturate : 1;
    uint32_t mixBufPeakMag : 4;
    uint32_t peakMag : 4;
    uint32_t samplingIncrement : 21;
    uint32_t reserved0 : 1;
    uint32_t cmd : 2;
    uint32_t mixBufPeakMag2 : 4;
    uint32_t peakMag2 : 4;
    uint32_t sampleCount;
    uint32_t samplePointer : 25;
    uint32_t reserved1 : 7;
    uint32_t samplingIncrementTarget : 21;
    uint32_t reserved2 : 11;
    uint32_t reserved[3];
};
static_assert(sizeof(SHAPE_SRC_CONTEXT) == 32, "SHAPE_SRC_CONTEXT is 32 bytes per GDK");

// The EQ/Comp context: 88 UINT32s = 352 bytes per GDK definition.
#pragma pack(push, 1)
struct alignas(4) SHAPE_EQCOMP_CONTEXT {
    uint8_t data[352];
};
#pragma pack(pop)
static_assert(sizeof(SHAPE_EQCOMP_CONTEXT) == 352, "SHAPE_EQCOMP_CONTEXT is 352 bytes per GDK");

struct SHAPE_FILTVOL_CONTEXT {
    uint32_t timestamp : 21;
    uint32_t reserved0 : 2;
    uint32_t internalSaturate : 1;
    uint32_t mixBufPeakMag : 4;
    uint32_t peakMag : 4;
    uint32_t delay1 : 30;
    uint32_t headroom : 2;
    uint32_t delay2 : 30;
    uint32_t svfMode : 2;
    uint32_t gainTarget : 16;
    uint32_t gain : 16;
    uint32_t qRecip : 24;
    uint32_t reserved1 : 8;
    uint32_t qRecipTarget : 24;
    uint32_t reserved2 : 8;
    uint32_t fc : 24;
    uint32_t reserved3 : 8;
    uint32_t fcTarget : 24;
    uint32_t reserved4 : 8;
};
static_assert(sizeof(SHAPE_FILTVOL_CONTEXT) == 32, "SHAPE_FILTVOL_CONTEXT is 32 bytes per GDK");

struct SHAPE_DMA_CONTEXT {
    uint32_t timestamp : 21;
    uint32_t reserved0 : 3;
    uint32_t mixBufPeakMag : 4;
    uint32_t peakMag : 4;
    uint32_t readPointer : 5;
    uint32_t reserved1 : 3;
    uint32_t writePointer : 5;
    uint32_t reserved2 : 3;
    uint32_t full : 1;
    uint32_t reserved3 : 15;
    uint32_t reserved4 : 9;
    uint32_t address : 23;
    uint32_t numFrames : 5;
    uint32_t reserved5 : 3;
    uint32_t floatConvert : 1;
    uint32_t reserved6 : 7;
    uint32_t numChannels : 3;
    uint32_t reserved7 : 5;
    uint32_t channel : 3;
    uint32_t reserved8 : 5;
};
static_assert(sizeof(SHAPE_DMA_CONTEXT) == 16, "SHAPE_DMA_CONTEXT is 16 bytes per GDK");

struct SHAPE_XMA_CONTEXT {
    uint32_t sizeRead0 : 12;
    uint32_t numLoops : 8;
    uint32_t validBuffer : 2;
    uint32_t sizeWrite : 5;
    uint32_t offsetWrite : 5;
    uint32_t sizeRead1 : 12;
    uint32_t sequenceNumber : 12;
    uint32_t stopSampleAddress : 8;
    uint32_t subframeOffset : 4;
    uint32_t reserved0 : 1;
    uint32_t isLooping : 1;
    uint32_t subframeSkip : 2;
    uint32_t packetMetadata : 16;
    uint32_t currentByteOffset : 26;
    uint32_t reserved1 : 6;
    uint32_t loopStartOffset : 26;
    uint32_t loopEndOffset : 6;
    uint32_t reserved[11];
};
static_assert(sizeof(SHAPE_XMA_CONTEXT) == 64, "SHAPE_XMA_CONTEXT is 64 bytes per GDK");

struct SHAPE_PCM_CONTEXT {
    uint32_t timestamp : 21;
    uint32_t reserved0 : 11;
    uint32_t loopCount : 8;
    uint32_t reserved1 : 8;
    uint32_t full : 1;
    uint32_t reserved2 : 15;
    uint32_t readPointer : 21;
    uint32_t reserved3 : 11;
    uint32_t loopStartWritePointer : 21;
    uint32_t reserved4 : 11;
    uint32_t loopEnd : 21;
    uint32_t reserved5 : 11;
    uint32_t mode : 1;
    uint32_t reserved6 : 15;
    uint32_t format : 2;
    uint32_t reserved7 : 14;
    uint32_t reserved[2];
};
static_assert(sizeof(SHAPE_PCM_CONTEXT) == 32, "SHAPE_PCM_CONTEXT is 32 bytes per GDK");

// The SHAPE_CONTEXTS structure (from the GDK)
struct SHAPE_CONTEXTS {
    uint32_t numSrcContexts;
    uint32_t numEqCompContexts;
    uint32_t numFiltVolContexts;
    uint32_t numDmaContexts;
    uint32_t numXmaContexts;
    uint32_t numPcmContexts;
    SHAPE_SRC_CONTEXT* srcContextArray;
    SHAPE_EQCOMP_CONTEXT* eqCompContextArray;
    SHAPE_FILTVOL_CONTEXT* filtVolContextArray;
    SHAPE_DMA_CONTEXT* dmaContextArray;
    SHAPE_XMA_CONTEXT* xmaContextArray;
    SHAPE_PCM_CONTEXT* pcmContextArray;
    APU_ADDRESS apuSrcContextArray;
    APU_ADDRESS apuEqCompContextArray;
    APU_ADDRESS apuFiltVolContextArray;
    APU_ADDRESS apuDmaContextArray;
    APU_ADDRESS apuXmaContextArray;
    APU_ADDRESS apuPcmContextArray;
};

static_assert(sizeof(SHAPE_CONTEXTS) == 120, "SHAPE_CONTEXTS is 120 bytes per GDK");
static_assert(offsetof(SHAPE_CONTEXTS, xmaContextArray) == 0x38,
              "xmaContextArray is at offset +0x38 per retail binary measurement");

// ---- Startup trace helper ----

void trace_call(const char* api, const std::string& detail) {
    if (!rt::startup_trace::enabled()) return;
    static std::mutex mutex;
    static std::set<std::string> seen;
    {
        const std::lock_guard lock(mutex);
        if (!seen.insert(std::string(api) + detail).second) return;
    }
    rt::startup_trace::event("acphal",
                             std::string("\"api\":\"") + api + "\"" + detail);
}

// ---- Genuine APU Heap Implementation ----

constexpr uint32_t kApuBaseAddress = 0x40000000u;

struct ApuRegion {
    void* base = nullptr;
    uint64_t size = 0;
    std::map<uint32_t, uint32_t> blocks;  // offset -> size of active allocations

    bool contains(const void* ptr) const {
        if (!base || !ptr) return false;
        const uintptr_t p = reinterpret_cast<uintptr_t>(ptr);
        const uintptr_t b = reinterpret_cast<uintptr_t>(base);
        return p >= b && p < b + size;
    }

    bool is_valid_address(const void* ptr, uint32_t alignment) const {
        if (!base || !ptr) return false;
        const uintptr_t p = reinterpret_cast<uintptr_t>(ptr);
        const uintptr_t b = reinterpret_cast<uintptr_t>(base);
        if (p < b || p >= b + size) return false;
        if (alignment == 0) alignment = 1;
        return ((p - b) & (static_cast<uintptr_t>(alignment) - 1)) == 0;
    }

    uint32_t to_offset(const void* ptr) const {
        return static_cast<uint32_t>(reinterpret_cast<uintptr_t>(ptr) -
                                     reinterpret_cast<uintptr_t>(base));
    }

    void* from_offset(uint32_t offset) const {
        if (offset >= size) return nullptr;
        return static_cast<uint8_t*>(base) + offset;
    }

    void* allocate(uint32_t alloc_size, uint32_t alignment, uint32_t* out_offset) {
        if (alloc_size == 0 || alignment == 0) return nullptr;
        uint64_t at = 0;
        for (auto it = blocks.begin();; ++it) {
            at = (at + alignment - 1) & ~static_cast<uint64_t>(alignment - 1);
            const uint64_t limit = (it == blocks.end()) ? size : it->first;
            if (at + alloc_size <= limit) break;
            if (it == blocks.end()) return nullptr;  // out of memory
            at = static_cast<uint64_t>(it->first) + it->second;
        }
        const uint32_t offset = static_cast<uint32_t>(at);
        blocks.emplace(offset, alloc_size);
        uint8_t* ptr = static_cast<uint8_t*>(base) + offset;
        std::memset(ptr, 0, alloc_size);
        if (out_offset) *out_offset = offset;
        return ptr;
    }

    bool free_block(void* ptr) {
        if (!contains(ptr)) return false;
        const uint32_t offset = to_offset(ptr);
        return blocks.erase(offset) > 0;
    }

    void release() {
        if (base) {
            VirtualFree(base, 0, MEM_RELEASE);
            base = nullptr;
        }
        size = 0;
        blocks.clear();
    }
};

std::mutex g_apu_mutex;
bool g_apu_heap_created = false;
ApuRegion g_cached_heap;
ApuRegion g_noncached_heap;

// Tracked SHAPE context arrays
std::vector<void*> g_active_shape_arrays;

// ---- IAcpHal Interface Implementation ----

struct AcpHalImpl {
    // Slot 0: Connect (called at boot with (0x1000, 0x1000), result checked)
    virtual HRESULT __stdcall connect(uint32_t, uint32_t) {
        trace_call("AcpHal::Connect", "");
        return S_OK;
    }

    // Slot 1: Disconnect (called at shutdown)
    virtual HRESULT __stdcall disconnect() {
        trace_call("AcpHal::Disconnect", "");
        return S_OK;
    }

    // Slot 2: SubmitCommand
    // Critical: The title calls slot 2 as (2, 0xf0f0f0f0, -1, config, 0) and
    // a negative result triggers a fatal int3 kernel panic (+0x43ad94e).
    virtual HRESULT __stdcall submit_command(uint32_t cmd, uint32_t d1, uint32_t d2,
                                            const void* config, uint32_t d3) {
        (void)cmd;
        (void)d1;
        (void)d2;
        (void)config;
        (void)d3;
        static std::atomic<bool> warned{false};
        if (!warned.exchange(true, std::memory_order_relaxed)) {
            std::fprintf(stderr,
                         "[rt] acphal: command accepted and dropped: no audio DSP hardware on PC\n");
        }
        trace_call("AcpHal::SubmitCommand", "");
        return S_OK;
    }

    // Slots 3 and 4: PopMessage and GetNumMessages (message polling, not called by title)
    virtual uint32_t __stdcall pop_message() { return 0; }
    virtual uint32_t __stdcall get_num_messages() { return 0; }

    // Slot 5: Release
    virtual HRESULT __stdcall release() {
        trace_call("AcpHal::Release", "");
        delete this;
        return S_OK;
    }
};

// ---- AcpHalCreate ----

HRESULT __stdcall AcpHalCreate(void** ppAcpHal) {
    if (!ppAcpHal) return E_POINTER;
    *ppAcpHal = new AcpHalImpl();
    trace_call("AcpHalCreate", "");
    return S_OK;
}

// ---- AcpHalAllocateShapeContexts ----

HRESULT __stdcall AcpHalAllocateShapeContexts(SHAPE_CONTEXTS* ctx) {
    if (!ctx) return E_POINTER;

    std::lock_guard lock(g_apu_mutex);

    auto alloc_array = [](uint32_t count, size_t elem_size) -> void* {
        if (count == 0) return nullptr;
        void* ptr = std::calloc(count, elem_size);
        if (ptr) g_active_shape_arrays.push_back(ptr);
        return ptr;
    };

    ctx->srcContextArray = static_cast<SHAPE_SRC_CONTEXT*>(
        alloc_array(ctx->numSrcContexts, sizeof(SHAPE_SRC_CONTEXT)));
    ctx->eqCompContextArray = static_cast<SHAPE_EQCOMP_CONTEXT*>(
        alloc_array(ctx->numEqCompContexts, sizeof(SHAPE_EQCOMP_CONTEXT)));
    ctx->filtVolContextArray = static_cast<SHAPE_FILTVOL_CONTEXT*>(
        alloc_array(ctx->numFiltVolContexts, sizeof(SHAPE_FILTVOL_CONTEXT)));
    ctx->dmaContextArray = static_cast<SHAPE_DMA_CONTEXT*>(
        alloc_array(ctx->numDmaContexts, sizeof(SHAPE_DMA_CONTEXT)));
    ctx->xmaContextArray = static_cast<SHAPE_XMA_CONTEXT*>(
        alloc_array(ctx->numXmaContexts, sizeof(SHAPE_XMA_CONTEXT)));
    ctx->pcmContextArray = static_cast<SHAPE_PCM_CONTEXT*>(
        alloc_array(ctx->numPcmContexts, sizeof(SHAPE_PCM_CONTEXT)));

    // On PC there is no physical APU address translation for host context arrays;
    // assign unique synthetic addresses from the APU base.
    ctx->apuSrcContextArray = {ctx->srcContextArray ? kApuBaseAddress : 0};
    ctx->apuEqCompContextArray = {ctx->eqCompContextArray ? kApuBaseAddress + 0x1000 : 0};
    ctx->apuFiltVolContextArray = {ctx->filtVolContextArray ? kApuBaseAddress + 0x2000 : 0};
    ctx->apuDmaContextArray = {ctx->dmaContextArray ? kApuBaseAddress + 0x3000 : 0};
    ctx->apuXmaContextArray = {ctx->xmaContextArray ? kApuBaseAddress + 0x4000 : 0};
    ctx->apuPcmContextArray = {ctx->pcmContextArray ? kApuBaseAddress + 0x5000 : 0};

    trace_call("AcpHalAllocateShapeContexts",
               ",\"src\":" + std::to_string(ctx->numSrcContexts) +
               ",\"xma\":" + std::to_string(ctx->numXmaContexts) +
               ",\"pcm\":" + std::to_string(ctx->numPcmContexts));
    return S_OK;
}

// ---- AcpHalReleaseShapeContexts ----

HRESULT __stdcall AcpHalReleaseShapeContexts(SHAPE_CONTEXTS* ctx = nullptr) {
    std::lock_guard lock(g_apu_mutex);

    // Free all recorded context allocations
    for (void* p : g_active_shape_arrays) {
        if (p) std::free(p);
    }
    g_active_shape_arrays.clear();

    if (ctx) {
        ctx->srcContextArray = nullptr;
        ctx->eqCompContextArray = nullptr;
        ctx->filtVolContextArray = nullptr;
        ctx->dmaContextArray = nullptr;
        ctx->xmaContextArray = nullptr;
        ctx->pcmContextArray = nullptr;
    }

    trace_call("AcpHalReleaseShapeContexts", "");
    return S_OK;
}

// ---- ApuCreateHeap ----

HRESULT __stdcall ApuCreateHeap(uint32_t cachedSizeInBytes,
                                uint32_t nonCachedSizeInBytes) {
    std::lock_guard lock(g_apu_mutex);
    if (g_apu_heap_created) return E_FAIL;

    if (cachedSizeInBytes > 0) {
        g_cached_heap.base = VirtualAlloc(
            nullptr, cachedSizeInBytes, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        if (!g_cached_heap.base) return E_OUTOFMEMORY;
        g_cached_heap.size = cachedSizeInBytes;
    }

    if (nonCachedSizeInBytes > 0) {
        g_noncached_heap.base = VirtualAlloc(
            nullptr, nonCachedSizeInBytes, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        if (!g_noncached_heap.base) {
            g_cached_heap.release();
            return E_OUTOFMEMORY;
        }
        g_noncached_heap.size = nonCachedSizeInBytes;
    }

    g_apu_heap_created = true;
    trace_call("ApuCreateHeap",
               ",\"cachedSize\":" + std::to_string(cachedSizeInBytes) +
               ",\"nonCachedSize\":" + std::to_string(nonCachedSizeInBytes));
    return S_OK;
}

// ---- ApuAlloc ----

HRESULT __stdcall ApuAlloc(void** virtualAddress, uint32_t* physicalAddress,
                            uint32_t sizeInBytes, uint32_t alignmentInBytes,
                            uint32_t flags) {
    if (!virtualAddress) return E_POINTER;
    *virtualAddress = nullptr;
    if (physicalAddress) *physicalAddress = 0;

    if (sizeInBytes == 0) return E_INVALIDARG;
    if (alignmentInBytes == 0) alignmentInBytes = 1;
    if ((alignmentInBytes & (alignmentInBytes - 1)) != 0) return E_INVALIDARG;

    std::lock_guard lock(g_apu_mutex);

    // Routing: flags=2 routes to non-cached heap; flags=0 or 1 routes to cached heap.
    // If non-cached heap wasn't created or has no room, fall back to cached heap
    // because PC memory is unified and cache-coherent.
    const bool prefer_noncached = (flags == 2);
    ApuRegion* target = prefer_noncached ? &g_noncached_heap : &g_cached_heap;

    if (!target->base) {
        if (!prefer_noncached) {
            // Lazy default cached heap if ApuCreateHeap wasn't called yet
            constexpr uint32_t kDefaultHeapSize = 64 * 1024 * 1024;
            target->base = VirtualAlloc(nullptr, kDefaultHeapSize,
                                        MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
            if (!target->base) return E_OUTOFMEMORY;
            target->size = kDefaultHeapSize;
            g_apu_heap_created = true;
        } else {
            // Fall back to cached heap
            target = &g_cached_heap;
            if (!target->base) return E_OUTOFMEMORY;
        }
    }

    uint32_t offset = 0;
    void* ptr = target->allocate(sizeInBytes, alignmentInBytes, &offset);
    if (!ptr && prefer_noncached && g_cached_heap.base) {
        // Fall back to cached heap if non-cached heap is full
        target = &g_cached_heap;
        ptr = target->allocate(sizeInBytes, alignmentInBytes, &offset);
    }

    if (!ptr) return E_OUTOFMEMORY;

    *virtualAddress = ptr;
    if (physicalAddress) {
        // Disjoint APU address ranges:
        //   cached:     0x40000000 .. 0x40000000 + cached_size - 1
        //   non-cached: 0x40000000 + cached_size .. 0x40000000 + cached_size + noncached_size - 1
        uint32_t apu = kApuBaseAddress + offset;
        if (target == &g_noncached_heap) {
            apu += static_cast<uint32_t>(g_cached_heap.size);
        }
        *physicalAddress = apu;
    }

    return S_OK;
}

// ---- ApuFree ----

void __stdcall ApuFree(void* virtualAddress) {
    if (!virtualAddress) return;
    std::lock_guard lock(g_apu_mutex);
    if (!g_cached_heap.free_block(virtualAddress)) {
        g_noncached_heap.free_block(virtualAddress);
    }
    trace_call("ApuFree", "");
}

// ---- ApuIsVirtualAddressValid ----

BOOL __stdcall ApuIsVirtualAddressValid(void* virtualAddress, uint32_t alignment) {
    if (!virtualAddress) return FALSE;
    if (alignment == 0) alignment = 1;
    std::lock_guard lock(g_apu_mutex);
    if (g_cached_heap.is_valid_address(virtualAddress, alignment)) return TRUE;
    if (g_noncached_heap.is_valid_address(virtualAddress, alignment)) return TRUE;
    return FALSE;
}

// ---- ApuMapApuAddress ----

void* __stdcall ApuMapApuAddress(uint32_t apu) {
    if (apu == 0 || apu < kApuBaseAddress) return nullptr;
    std::lock_guard lock(g_apu_mutex);
    const uint32_t offset = apu - kApuBaseAddress;
    if (g_cached_heap.base && offset < g_cached_heap.size) {
        return g_cached_heap.from_offset(offset);
    }
    if (g_noncached_heap.base) {
        const uint32_t cached_span = static_cast<uint32_t>(g_cached_heap.size);
        if (offset >= cached_span && offset - cached_span < g_noncached_heap.size) {
            return g_noncached_heap.from_offset(offset - cached_span);
        }
    }
    return nullptr;
}

// ---- ApuMapVirtualAddress ----

uint32_t __stdcall ApuMapVirtualAddress(void* virtualAddress) {
    if (!virtualAddress) return 0;
    std::lock_guard lock(g_apu_mutex);
    if (g_cached_heap.contains(virtualAddress)) {
        return kApuBaseAddress + g_cached_heap.to_offset(virtualAddress);
    }
    if (g_noncached_heap.contains(virtualAddress)) {
        return kApuBaseAddress + static_cast<uint32_t>(g_cached_heap.size) +
               g_noncached_heap.to_offset(virtualAddress);
    }
    return 0;
}

}  // namespace

SHIM_TABLE(acphal,
    {"acphal.dll", "AcpHalAllocateShapeContexts", reinterpret_cast<void*>(&AcpHalAllocateShapeContexts)},
    {"acphal.dll", "AcpHalCreate", reinterpret_cast<void*>(&AcpHalCreate)},
    {"acphal.dll", "AcpHalReleaseShapeContexts", reinterpret_cast<void*>(&AcpHalReleaseShapeContexts)},
    {"acphal.dll", "ApuAlloc", reinterpret_cast<void*>(&ApuAlloc)},
    {"acphal.dll", "ApuCreateHeap", reinterpret_cast<void*>(&ApuCreateHeap)},
    {"acphal.dll", "ApuFree", reinterpret_cast<void*>(&ApuFree)},
    {"acphal.dll", "ApuIsVirtualAddressValid", reinterpret_cast<void*>(&ApuIsVirtualAddressValid)},
    {"acphal.dll", "ApuMapApuAddress", reinterpret_cast<void*>(&ApuMapApuAddress)},
    {"acphal.dll", "ApuMapVirtualAddress", reinterpret_cast<void*>(&ApuMapVirtualAddress)},
);
