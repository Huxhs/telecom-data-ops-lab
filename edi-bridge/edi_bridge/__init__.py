"""edi-bridge: a small, readable ANSI X12 toolkit for learning EDI.

Modules, in the order data flows through them:
  x12       parse + validate an interchange (ISA/GS/ST ... SE/GE/IEA)
  pretty    human-readable dump of any X12 file
  m850      850 purchase order -> Order object
  mapping   Order -> ERP (SyteLine-style) customer order via cross-reference CSVs
  ack997    997 functional acknowledgment (syntax answer)
  ack855    855 PO acknowledgment (business answer)
  asn856    856 advance ship notice from a simgen shipment summary
  envelope  ISA/GS/ST wrapping and control numbers for everything outbound
"""

__version__ = "0.1.0"
