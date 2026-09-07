"""
QR code as an SVG string, with nothing but the standard library.

The hub pairs a phone by showing a code on screen, and pulling in a QR package
would be the first runtime dependency this project has. So the encoder lives
here: byte mode, error correction level M, versions 1-14 (up to 362 bytes),
which is far more than the pairing URL ever needs.

Reference: ISO/IEC 18004. Verified round-trip against cv2.QRCodeDetector.
"""

# Level M layout per version, index = version - 1:
# (ec codewords per block, [(blocks, data codewords per block), ...]).
EC_LAYOUT_M = [
    (10, [(1, 16)]),
    (16, [(1, 28)]),
    (26, [(1, 44)]),
    (18, [(2, 32)]),
    (24, [(2, 43)]),
    (16, [(4, 27)]),
    (18, [(4, 31)]),
    (22, [(2, 38), (2, 39)]),
    (22, [(3, 36), (2, 37)]),
    (26, [(4, 43), (1, 44)]),
    (30, [(1, 50), (4, 51)]),
    (22, [(6, 36), (2, 37)]),
    (22, [(8, 37), (1, 38)]),
    (24, [(4, 40), (5, 41)]),
]

ALIGN_CENTERS = [
    [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42],
    [6, 26, 46], [6, 28, 50], [6, 30, 54], [6, 32, 58], [6, 34, 62],
    [6, 26, 46, 66],
]

# Pre-computed 18-bit version information, versions 7-14 (below 7 there is none).
VERSION_BITS = {7: 0x07C94, 8: 0x085BC, 9: 0x09A99, 10: 0x0A4D3,
                11: 0x0BBF6, 12: 0x0C762, 13: 0x0D847, 14: 0x0E60D}

EC_LEVEL_M = 0b00


# ── GF(256) arithmetic for Reed-Solomon ──────────────────────────────────────
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:                      # x^8 + x^4 + x^3 + x^2 + 1
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(degree):
    """Generator polynomial (x - a^0)(x - a^1)...(x - a^(degree-1))."""
    poly = [1]
    for i in range(degree):
        poly.append(0)
        for j in range(len(poly) - 1, 0, -1):
            poly[j] ^= _mul(poly[j - 1], _EXP[i])
    return poly


def _ec_codewords(data, count):
    """Reed-Solomon remainder of `data` for `count` error correction bytes."""
    gen = _generator(count)
    rem = list(data) + [0] * count
    for i in range(len(data)):
        factor = rem[i]
        if factor:
            for j, g in enumerate(gen):
                rem[i + j] ^= _mul(g, factor)
    return rem[len(data):]


# ── Encoding ─────────────────────────────────────────────────────────────────
def _capacity(version):
    ec_per_block, groups = EC_LAYOUT_M[version - 1]
    return sum(blocks * data for blocks, data in groups)


def _pick_version(length):
    for version in range(1, len(EC_LAYOUT_M) + 1):
        count_bits = 8 if version <= 9 else 16
        # mode indicator (4) + character count + payload + terminator room
        if 4 + count_bits + length * 8 <= _capacity(version) * 8:
            return version
    raise ValueError("Text je na QR kód moc dlouhý.")


def _bitstream(data, version):
    bits = []

    def put(value, width):
        for shift in range(width - 1, -1, -1):
            bits.append((value >> shift) & 1)

    put(0b0100, 4)                                   # byte mode
    put(len(data), 8 if version <= 9 else 16)
    for byte in data:
        put(byte, 8)

    capacity_bits = _capacity(version) * 8
    put(0, min(4, capacity_bits - len(bits)))        # terminator
    bits.extend([0] * (-len(bits) % 8))              # pad to a byte boundary

    codewords = [int("".join(str(b) for b in bits[i:i + 8]), 2)
                 for i in range(0, len(bits), 8)]
    for pad in _pad_cycle(_capacity(version) - len(codewords)):
        codewords.append(pad)
    return codewords


def _pad_cycle(count):
    return [0xEC if i % 2 == 0 else 0x11 for i in range(count)]


def _interleave(codewords, version):
    """Split into RS blocks, then read data and EC back column by column."""
    ec_per_block, groups = EC_LAYOUT_M[version - 1]
    data_blocks, ec_blocks, pos = [], [], 0
    for blocks, data_len in groups:
        for _ in range(blocks):
            block = codewords[pos:pos + data_len]
            pos += data_len
            data_blocks.append(block)
            ec_blocks.append(_ec_codewords(block, ec_per_block))

    out = []
    for i in range(max(len(b) for b in data_blocks)):
        for block in data_blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(ec_per_block):
        for block in ec_blocks:
            out.append(block[i])
    return out


# ── Matrix ───────────────────────────────────────────────────────────────────
class _Matrix:
    def __init__(self, version):
        self.version = version
        self.size = version * 4 + 17
        self.cells = [[0] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def set(self, row, col, value, fixed=True):
        self.cells[row][col] = value
        if fixed:
            self.fixed[row][col] = True

    def _finder(self, row, col):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = row + dr, col + dc
                if not (0 <= r < self.size and 0 <= c < self.size):
                    continue
                inside = 0 <= dr <= 6 and 0 <= dc <= 6
                ring = dr in (0, 6) or dc in (0, 6)
                core = 2 <= dr <= 4 and 2 <= dc <= 4
                self.set(r, c, 1 if inside and (ring or core) else 0)

    def _alignment(self):
        centers = ALIGN_CENTERS[self.version - 1]
        for row in centers:
            for col in centers:
                # The three finder corners already own their area.
                if (row, col) in ((6, 6), (6, self.size - 7), (self.size - 7, 6)):
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        edge = max(abs(dr), abs(dc))
                        self.set(row + dr, col + dc, 1 if edge != 1 else 0)

    def draw_function_patterns(self):
        self._finder(0, 0)
        self._finder(0, self.size - 7)
        self._finder(self.size - 7, 0)
        self._alignment()
        for i in range(8, self.size - 8):        # timing patterns
            bit = 1 if i % 2 == 0 else 0
            self.set(6, i, bit)
            self.set(i, 6, bit)
        self.set(self.size - 8, 8, 1)            # dark module

        # Reserve the format areas so data placement skips them.
        for i in range(9):
            if not self.fixed[8][i]:
                self.set(8, i, 0)
            if not self.fixed[i][8]:
                self.set(i, 8, 0)
        for i in range(8):
            self.set(8, self.size - 1 - i, 0)
            self.set(self.size - 1 - i, 8, 0)

        if self.version >= 7:
            bits = VERSION_BITS[self.version]
            for i in range(18):
                bit = (bits >> i) & 1
                self.set(i // 3, self.size - 11 + i % 3, bit)
                self.set(self.size - 11 + i % 3, i // 3, bit)

    def place_data(self, codewords):
        bits = [(byte >> shift) & 1
                for byte in codewords for shift in range(7, -1, -1)]
        index, upward, col = 0, True, self.size - 1
        while col > 0:
            if col == 6:                 # the vertical timing column is skipped
                col -= 1
            rows = range(self.size - 1, -1, -1) if upward else range(self.size)
            for row in rows:
                for c in (col, col - 1):
                    if self.fixed[row][c]:
                        continue
                    self.cells[row][c] = bits[index] if index < len(bits) else 0
                    index += 1
            upward = not upward
            col -= 2

    def apply_mask(self, mask):
        out = _Matrix(self.version)
        out.cells = [row[:] for row in self.cells]
        out.fixed = [row[:] for row in self.fixed]
        for row in range(self.size):
            for col in range(self.size):
                if out.fixed[row][col] or not _mask_hit(mask, row, col):
                    continue
                out.cells[row][col] ^= 1
        return out

    def draw_format(self, mask):
        """Both copies of the 15 format bits, LSB first."""
        bits = _format_bits(mask)
        for i in range(15):
            bit = (bits >> i) & 1
            # Copy 1 runs down the left of the top-left finder and then along
            # its bottom edge; both hops skip the timing row and column at 6.
            if i < 6:
                self.set(i, 8, bit)
            elif i < 8:
                self.set(i + 1, 8, bit)
            elif i == 8:
                self.set(8, 7, bit)
            else:
                self.set(8, 14 - i, bit)
            # Copy 2 is split between the other two finders.
            if i < 8:
                self.set(8, self.size - 1 - i, bit)
            else:
                self.set(self.size - 15 + i, 8, bit)

    def penalty(self):
        size, cells, score = self.size, self.cells, 0

        # Rule 1: runs of five or more same-coloured modules in a line.
        for line in list(cells) + [list(col) for col in zip(*cells)]:
            run, prev = 1, line[0]
            for value in line[1:]:
                if value == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run, prev = 1, value
            if run >= 5:
                score += 3 + (run - 5)

        # Rule 2: 2x2 blocks of one colour.
        for row in range(size - 1):
            for col in range(size - 1):
                block = (cells[row][col], cells[row][col + 1],
                         cells[row + 1][col], cells[row + 1][col + 1])
                if len(set(block)) == 1:
                    score += 3

        # Rule 3: the finder-like 1:1:3:1:1 pattern with four light modules.
        pattern_a = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
        pattern_b = list(reversed(pattern_a))
        for line in list(cells) + [list(col) for col in zip(*cells)]:
            for start in range(size - 10):
                window = line[start:start + 11]
                if window == pattern_a or window == pattern_b:
                    score += 40

        # Rule 4: deviation from an even split of dark and light.
        dark = sum(sum(row) for row in cells)
        ratio = dark * 100 // (size * size)
        score += 10 * min(abs(ratio - 50) // 5, abs(ratio - 50 + 4) // 5)
        return score


def _mask_hit(mask, row, col):
    if mask == 0:
        return (row + col) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return col % 3 == 0
    if mask == 3:
        return (row + col) % 3 == 0
    if mask == 4:
        return (row // 2 + col // 3) % 2 == 0
    if mask == 5:
        return (row * col) % 2 + (row * col) % 3 == 0
    if mask == 6:
        return ((row * col) % 2 + (row * col) % 3) % 2 == 0
    return ((row + col) % 2 + (row * col) % 3) % 2 == 0


def _format_bits(mask):
    """BCH(15,5) over the level+mask pair, then the spec's fixed XOR mask."""
    value = (EC_LEVEL_M << 3) | mask
    rem = value << 10
    while rem.bit_length() >= 11:
        rem ^= 0x537 << (rem.bit_length() - 11)
    return ((value << 10) | rem) ^ 0x5412


def _build(data):
    version = _pick_version(len(data))
    codewords = _interleave(_bitstream(data, version), version)

    base = _Matrix(version)
    base.draw_function_patterns()
    base.place_data(codewords)

    best, best_score = None, None
    for mask in range(8):
        candidate = base.apply_mask(mask)
        candidate.draw_format(mask)
        score = candidate.penalty()
        if best_score is None or score < best_score:
            best, best_score = candidate, score
    return best


def svg(text, quiet=4, scale=6):
    """QR code for `text` as a standalone SVG element (dark = currentColor)."""
    matrix = _build(text.encode("utf-8"))
    size = matrix.size
    span = (size + quiet * 2) * scale
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {span} {span}" '
        f'width="{span}" height="{span}" shape-rendering="crispEdges">',
        f'<rect width="{span}" height="{span}" fill="#fff"/>',
        '<path fill="#000" d="',
    ]
    for row in range(size):
        for col in range(size):
            if matrix.cells[row][col]:
                x = (col + quiet) * scale
                y = (row + quiet) * scale
                parts.append(f"M{x} {y}h{scale}v{scale}h-{scale}z")
    parts.append('"/></svg>')
    return "".join(parts)


def matrix(text):
    """The raw module grid — used by the tests and by anything that renders."""
    return _build(text.encode("utf-8")).cells
