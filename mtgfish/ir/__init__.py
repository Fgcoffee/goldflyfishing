"""The parser's output as versioned JSON: the boundary to the TypeScript engine.

``codec`` turns the IR objects (abilities, effects, filters, costs, ...) into
plain JSON and back, exactly; ``schema`` describes that JSON as a JSON Schema
and as TypeScript types; ``mtgfish.tools.ir_export`` writes it per card face.
See docs/ir-json.md.
"""

from .codec import IR_VERSION, decode, decode_face, encode, encode_face

__all__ = ["IR_VERSION", "decode", "decode_face", "encode", "encode_face"]
