"""Experimental sampled MIMG TFE lowering; no device feature negotiation here."""

TEXEL_TYPES = ("%v4float", "%v4uint_st", "%v4int_st")


def status_register(first: int, count: int, dmask: int, d16: bool) -> int:
    """Return the trailing status VGPR for an explicit, nonzero MIMG data mask."""
    if not 1 <= dmask <= 15:
        raise ValueError("TFE requires a nonzero four-bit dmask")
    components = dmask.bit_count()
    words = (components + 1) // 2 if d16 else components
    if count != words + 1 or first < 0 or first + words >= 256:
        raise ValueError("TFE destination range does not contain exactly data and status")
    return first + words


def emit_fetch(gen, image, coords, lod, ty, idx, status):
    """Emit a typed sparse fetch and normalize the residency code to a fault bit.

    A descriptor lookup miss is conservatively a fault, not successful access to
    the fallback image. Nonresident texel data semantics still need GPU validation.
    """
    if ty not in TEXEL_TYPES:
        raise ValueError("unsupported sparse texel type")
    result, code, texel, resident, hit, ok, fault = (gen.tmp() for _ in range(7))
    zero, one = gen.cu(0), gen.cu(1)
    gen.emit(f"{result} = OpImageSparseFetch %tfe_result_{TEXEL_TYPES.index(ty)} {image} {coords} Lod {lod}")
    gen.emit(f"{code} = OpCompositeExtract %uint {result} 0")
    gen.emit(f"{texel} = OpCompositeExtract {ty} {result} 1")
    gen.emit(f"{resident} = OpImageSparseTexelsResident %bool {code}")
    gen.emit(f"{hit} = OpINotEqual %bool {idx} {zero}")
    gen.emit(f"{ok} = OpLogicalAnd %bool {resident} {hit}")
    gen.emit(f"{fault} = OpSelect %uint {ok} {zero} {one}")
    gen.dst({"k": "vgpr", "r": status, "n": 1}, fault)
    return texel
