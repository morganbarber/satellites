"""
Custom exception classes for CCSDS protocol handling and transport.
"""

class CCSDSError(Exception):
    """Base exception for all CCSDS processing errors."""


class ValidationError(CCSDSError, ValueError):
    """Raised when header fields or payload boundaries fail validation rules."""


class CRCError(CCSDSError, ValueError):
    """Raised when Frame Error Control Field (FECF) CRC check fails."""


class TransmissionError(CCSDSError):
    """Raised when socket transmission or reception fails."""


class PayloadError(CCSDSError, ValueError):
    """Raised when payload parsing or counter extraction fails."""
