class DomainError(Exception):
    """A broken business rule. The API turns it into 400 (like DomainException in .NET)."""
