"""Input parsers for instrument-exported data."""

from .qpcr_parser import QPCRParseResult, parse_qpcr_file

__all__ = ["QPCRParseResult", "parse_qpcr_file"]

