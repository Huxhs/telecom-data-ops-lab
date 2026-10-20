"""SIM identifiers: ICCID (card number) and IMSI (subscriber identity).

ICCID  = up to 19-20 digits printed on the card. Starts with 89 (telecom),
         then country/issuer digits, then a serial, then ONE Luhn check digit.
IMSI   = 15 digits: MCC (3, country) + MNC (2-3, network) + MSIN (subscriber).
         MCC 001 / MNC 01 is the ITU test network, so lab data never collides
         with a real operator.
"""


def luhn_check_digit(body: str) -> str:
    """Return the Luhn (mod 10) check digit for a string of digits.

    Same algorithm as credit card numbers: from the right, double every
    second digit, subtract 9 from anything over 9, sum, and pick the digit
    that brings the total to a multiple of 10.
    """
    if not body.isdigit():
        raise ValueError(f"ICCID body must be digits only: {body!r}")
    total = 0
    # The check digit will sit to the right, so the rightmost body digit is doubled.
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def luhn_valid(number: str) -> bool:
    """True when the last digit is the correct Luhn check digit for the rest."""
    return number.isdigit() and len(number) > 1 and luhn_check_digit(number[:-1]) == number[-1]


def iccid_range(start_body: str, count: int):
    """Yield `count` full ICCIDs: sequential bodies, each with its check digit."""
    width = len(start_body)
    start = int(start_body)
    for i in range(count):
        body = str(start + i).zfill(width)
        if len(body) != width:
            raise ValueError("ICCID range overflows its digit width")
        yield body + luhn_check_digit(body)


def imsi_range(start: str, count: int):
    """Yield `count` sequential 15-digit IMSIs starting at `start`."""
    if len(start) != 15 or not start.isdigit():
        raise ValueError(f"IMSI must be 15 digits: {start!r}")
    first = int(start)
    for i in range(count):
        imsi = str(first + i).zfill(15)
        if len(imsi) != 15 or imsi[:5] != start[:5]:
            raise ValueError("IMSI range overflows the MCC/MNC prefix")
        yield imsi
