import logging
import re
from numbers import Number
from .formatbase import FormatBase
from .ssaevent import SSAEvent
from .ssastyle import SSAStyle
from .common import Color
from .time import make_time, ms_to_times, timestamp_to_ms, TIMESTAMP

SSA_ALIGNMENT = (1, 2, 3, 9, 10, 11, 5, 6, 7)

def ass_to_ssa_alignment(i):
    return SSA_ALIGNMENT[i-1]

def ssa_to_ass_alignment(i):
    return SSA_ALIGNMENT.index(i) + 1

SECTION_HEADING = re.compile(
    r"^.{,3}"  # allow 3 chars at start of line for BOM
    r"\["  # open square bracket
    r"[^]]*[a-z][^]]*"  # inside square brackets, at least one lowercase letter (this guards vs. uuencoded font data)
    r"]"  # close square bracket
)

FONT_FILE_HEADING = re.compile(r"fontname:\s+(\S+)")

STYLE_FORMAT_LINE = {
    "ass": "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic,"
           " Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment,"
           " MarginL, MarginR, MarginV, Encoding",
    "ssa": "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, TertiaryColour, BackColour, Bold, Italic,"
           " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, AlphaLevel, Encoding"
}

STYLE_FIELDS = {
    "ass": ["fontname", "fontsize", "primarycolor", "secondarycolor", "outlinecolor", "backcolor", "bold", "italic",
            "underline", "strikeout", "scalex", "scaley", "spacing", "angle", "borderstyle", "outline", "shadow",
            "alignment", "marginl", "marginr", "marginv", "encoding"],
    "ssa": ["fontname", "fontsize", "primarycolor", "secondarycolor", "tertiarycolor", "backcolor", "bold", "italic",
            "borderstyle", "outline", "shadow", "alignment", "marginl", "marginr", "marginv", "alphalevel", "encoding"]
}

EVENT_FORMAT_LINE = {
    "ass": "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    "ssa": "Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
}

EVENT_FIELDS = {
    "ass": ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"],
    "ssa": ["marked", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
}

#: Largest timestamp allowed in SubStation, ie. 9:59:59.99.
MAX_REPRESENTABLE_TIME = make_time(h=10) - 10

def ms_to_timestamp(ms: int) -> str:
    """Convert ms to 'H:MM:SS.cc'"""
    # XXX throw on overflow/underflow?
    if ms < 0: ms = 0
    if ms > MAX_REPRESENTABLE_TIME: ms = MAX_REPRESENTABLE_TIME
    h, m, s, ms = ms_to_times(ms)
    return "%01d:%02d:%02d.%02d" % (h, m, s, ms//10)

def color_to_ass_rgba(c: Color) -> str:
    return "&H%08X" % ((c.a << 24) | (c.b << 16) | (c.g << 8) | c.r)

def color_to_ssa_rgb(c: Color) -> str:
    return "%d" % ((c.b << 16) | (c.g << 8) | c.r)

def rgba_to_color(s: str) -> Color:
    if s[0] == '&':
        x = int(s[2:], base=16)
    else:
        x = int(s)
    r = x & 0xff
    g = (x >> 8) & 0xff
    b = (x >> 16) & 0xff
    a = (x >> 24) & 0xff
    return Color(r, g, b, a)

def is_valid_field_content(s: str) -> bool:
    """
    Returns True if string s can be stored in a SubStation field.

    Fields are written in CSV-like manner, thus commas and/or newlines
    are not acceptable in the string.

    """
    return "\n" not in s and "," not in s


def parse_tags(text, style=SSAStyle.DEFAULT_STYLE, styles={}):
    """
    Split text into fragments with computed SSAStyles.
    
    Returns list of tuples (fragment, style), where fragment is a part of text
    between two brace-delimited override sequences, and style is the computed
    styling of the fragment, ie. the original style modified by all override
    sequences before the fragment.
    
    Newline and non-breakable space overrides are left as-is.
    
    Supported override tags:
    
    - i, b, u, s
    - r (with or without style name)
    
    """
    
    fragments = SSAEvent.OVERRIDE_SEQUENCE.split(text)
    if len(fragments) == 1:
        return [(text, style)]
    
    def apply_overrides(all_overrides):
        s = style.copy()
        for tag in re.findall(r"\\[ibusp][0-9]|\\r[a-zA-Z_0-9 ]*", all_overrides):
            if tag == r"\r":
                s = style.copy() # reset to original line style
            elif tag.startswith(r"\r"):
                name = tag[2:]
                if name in styles:
                    s = styles[name].copy() # reset to named style
            else:
                if "i" in tag: s.italic = "1" in tag
                elif "b" in tag: s.bold = "1" in tag
                elif "u" in tag: s.underline = "1" in tag
                elif "s" in tag: s.strikeout = "1" in tag
                elif "p" in tag:
                    try:
                        scale = int(tag[2:])
                    except (ValueError, IndexError):
                        continue

                    s.drawing = scale > 0
        return s
    
    overrides = SSAEvent.OVERRIDE_SEQUENCE.findall(text)
    overrides_prefix_sum = ["".join(overrides[:i]) for i in range(len(overrides) + 1)]
    computed_styles = map(apply_overrides, overrides_prefix_sum)
    return list(zip(fragments, computed_styles))


NOTICE = "Script generated by pysubs2\nhttps://pypi.python.org/pypi/pysubs2"

class SubstationFormat(FormatBase):
    """SubStation Alpha (ASS, SSA) subtitle format implementation"""
    @classmethod
    def guess_format(cls, text):
        """See :meth:`pysubs2.formats.FormatBase.guess_format()`"""
        if "V4+ Styles" in text:
            return "ass"
        elif "V4 Styles" in text:
            return "ssa"

    @classmethod
    def from_file(cls, subs, fp, format_, **kwargs):
        """See :meth:`pysubs2.formats.FormatBase.from_file()`"""

        def string_to_field(f, v):
            if f in {"start", "end"}:
                if v.startswith("-"):
                    # handle negative timestamps
                    v = v[1:]
                    return -timestamp_to_ms(TIMESTAMP.match(v).groups())
                else:
                    return timestamp_to_ms(TIMESTAMP.match(v).groups())
            elif "color" in f:
                return rgba_to_color(v)
            elif f in {"bold", "underline", "italic", "strikeout"}:
                return v == "-1"
            elif f in {"borderstyle", "encoding", "marginl", "marginr", "marginv", "layer", "alphalevel"}:
                return int(v)
            elif f in {"fontsize", "scalex", "scaley", "spacing", "angle", "outline", "shadow"}:
                return float(v)
            elif f == "marked":
                return v.endswith("1")
            elif f == "alignment":
                i = int(v)
                if format_ == "ass":
                    return i
                else:
                    return ssa_to_ass_alignment(i)
            else:
                return v

        subs.info.clear()
        subs.aegisub_project.clear()
        subs.styles.clear()
        subs.fonts_opaque.clear()

        inside_info_section = False
        inside_aegisub_section = False
        inside_font_section = False
        current_font_name = None
        current_font_lines_buffer = []

        for lineno, line in enumerate(fp, 1):
            line = line.strip()

            if SECTION_HEADING.match(line):
                logging.debug("at line %d: section heading %s", lineno, line)
                inside_info_section = "Info" in line
                inside_aegisub_section = "Aegisub" in line
                inside_font_section = "Fonts" in line
            elif inside_info_section or inside_aegisub_section:
                if line.startswith(";"): continue # skip comments
                try:
                    k, v = line.split(":", 1)
                    if inside_info_section:
                        subs.info[k] = v.strip()
                    elif inside_aegisub_section:
                        subs.aegisub_project[k] = v.strip()
                except ValueError:
                    pass
            elif inside_font_section:
                m = FONT_FILE_HEADING.match(line)

                if current_font_name and (m or not line):
                    # flush last font on newline or new font name
                    font_data = current_font_lines_buffer[:]
                    subs.fonts_opaque[current_font_name] = font_data
                    logging.debug("at line %d: finished font definition %s", lineno, current_font_name)
                    current_font_lines_buffer.clear()
                    current_font_name = None

                if m:
                    # start new font
                    font_name = m.group(1)
                    current_font_name = font_name
                elif line:
                    # add non-empty line to current buffer
                    current_font_lines_buffer.append(line)
            elif line.startswith("Style:"):
                _, rest = line.split(":", 1)
                buf = rest.strip().split(",")
                name, raw_fields = buf[0], buf[1:] # splat workaround for Python 2.7
                field_dict = {f: string_to_field(f, v) for f, v in zip(STYLE_FIELDS[format_], raw_fields)}
                sty = SSAStyle(**field_dict)
                subs.styles[name] = sty
            elif line.startswith("Dialogue:") or line.startswith("Comment:"):
                ev_type, rest = line.split(":", 1)
                raw_fields = rest.strip().split(",", len(EVENT_FIELDS[format_])-1)
                field_dict = {f: string_to_field(f, v) for f, v in zip(EVENT_FIELDS[format_], raw_fields)}
                field_dict["type"] = ev_type
                ev = SSAEvent(**field_dict)
                subs.events.append(ev)

        # cleanup fonts
        if current_font_name:
            # flush last font on EOF or new section w/o newline
            font_data = current_font_lines_buffer[:]
            subs.fonts_opaque[current_font_name] = font_data
            logging.debug("at EOF: finished font definition %s", current_font_name)
            current_font_lines_buffer.clear()
            current_font_name = None

    @classmethod
    def to_file(cls, subs, fp, format_, header_notice=NOTICE, **kwargs):
        """See :meth:`pysubs2.formats.FormatBase.to_file()`"""
        print("[Script Info]", file=fp)
        for line in header_notice.splitlines(False):
            print(";", line, file=fp)

        subs.info["ScriptType"] = "v4.00+" if format_ == "ass" else "v4.00"
        for k, v in subs.info.items():
            print(k, v, sep=": ", file=fp)

        if subs.aegisub_project:
            print("\n[Aegisub Project Garbage]", file=fp)
            for k, v in subs.aegisub_project.items():
                print(k, v, sep=": ", file=fp)

        def field_to_string(f, v, line):
            if f in {"start", "end"}:
                return ms_to_timestamp(v)
            elif f == "marked":
                return "Marked=%d" % v
            elif f == "alignment" and format_ == "ssa":
                return str(ass_to_ssa_alignment(v))
            elif isinstance(v, bool):
                return "-1" if v else "0"
            elif isinstance(v, (str, Number)):
                return str(v)
            elif isinstance(v, Color):
                if format_ == "ass":
                    return color_to_ass_rgba(v)
                else:
                    return color_to_ssa_rgb(v)
            else:
                raise TypeError("Unexpected type when writing a SubStation field {!r} for line {!r}".format(f, line))

        print("\n[V4+ Styles]" if format_ == "ass" else "\n[V4 Styles]", file=fp)
        print(STYLE_FORMAT_LINE[format_], file=fp)
        for name, sty in subs.styles.items():
            fields = [field_to_string(f, getattr(sty, f), sty) for f in STYLE_FIELDS[format_]]
            print("Style: %s" % name, *fields, sep=",", file=fp)

        if subs.fonts_opaque:
            print("\n[Fonts]", file=fp)
            for font_name, font_lines in sorted(subs.fonts_opaque.items()):
                print("fontname: {}".format(font_name), file=fp)
                for line in font_lines:
                    print(line, file=fp)
                print(file=fp)

        print("\n[Events]", file=fp)
        print(EVENT_FORMAT_LINE[format_], file=fp)
        for ev in subs.events:
            fields = [field_to_string(f, getattr(ev, f), ev) for f in EVENT_FIELDS[format_]]
            print(ev.type, end=": ", file=fp)
            print(*fields, sep=",", file=fp)
