"""One local text contract for generated replies and their IM wire encoding."""

MAX_TEXT_CHARACTERS = 1200
MAX_TEXT_BYTES = 4096


def encode_text(value):
    """Validate without truncating, normalizing, or exposing the original body in an error."""
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_CHARACTERS or '\x00' in value:
        raise ValueError('im_text_invalid')
    try:
        encoded = value.encode('utf-8')
    except UnicodeError:
        raise ValueError('im_text_invalid') from None
    if len(encoded) > MAX_TEXT_BYTES:
        raise ValueError('im_text_invalid')
    return encoded
