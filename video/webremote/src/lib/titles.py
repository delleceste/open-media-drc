"""Deduce a movie title (and year) from a file or folder name.

Real examples from the drive this targets:
    One.Battle.After.Another.2025.2160p.MA.WEB-DL.DDP5.1.Atmos.DV.HDR-DVT.mkv
    Caught by the Tides 2024 1080p WEB-DL DD5.1 H.264.mkv
    Fallen.Angels.BD-Remux.mkv            (no year)
    The.Dorm.avi                          (no year)
    A.Dirty.Carnival.2006.Blu-ray.AV/     (BD rip folder)
    Orfeo.Ed.Euridice.2011 (Castell de Peralada Festival) (Blu-ray 1080i, ...)/
    Late Spring (1949)/                   (parenthetical year)
    Wong Kar Wai/  China/  Pop Rock/      (category / director folders -> not titles)

Strategy: cut at the year if present, otherwise cut at the first release/quality
tag; drop parenthetical groups; normalise dot/underscore separators.
"""
import os
import re
import urllib.parse

_SEP = re.compile(r"[._]+")
_YEAR = re.compile(r"(19\d{2}|20\d{2})")
_PAREN = re.compile(r"\([^)]*\)")
_MULTISPACE = re.compile(r"\s{2,}")

# Release / quality / source tokens. A name is cut at the first of these when no
# year delimits the title. Hyphenated and de-hyphenated forms both included.
_TAGS = frozenset({
    "remux", "bdremux", "bd-remux", "brremux",
    "bdrip", "brrip", "bluray", "blu-ray", "webrip", "web-dl", "webdl", "web",
    "hdtv", "dvdrip", "dvdscr", "hdrip", "remastered",
    "dvd", "dvd5", "dvd9", "dvd-5", "dvd-9", "ntsc", "pal",
    "480p", "576p", "720p", "1080p", "1080i", "2160p", "4k", "uhd",
    "x264", "x265", "h264", "h265", "h", "hevc", "avc", "xvid", "divx",
    "aac", "ac3", "eac3", "dd5", "ddp5", "dd", "ddp", "dts", "dts-hd", "truehd",
    "atmos", "flac", "mp3",
    "dv", "hdr", "hdr10", "sdr",
    "nf", "ma", "amzn", "dsnp", "atvp", "hmax", "pcok",
    "criterion", "repack", "proper", "internal", "limited", "extended",
    "unrated", "multi", "complete", "usbhd01",
})


def parse_title(name: str, is_file: bool) -> tuple[str, str | None]:
    """Return (title, year|None) deduced from a file/folder name."""
    if is_file:
        name = os.path.splitext(name)[0]

    s = _SEP.sub(" ", name)

    year = None
    ym = _YEAR.search(s)
    if ym:
        year = ym.group(1)
        s = s[: ym.start()]        # title is everything before the year

    s = _PAREN.sub(" ", s)         # drop complete "(Royal Ballet)" groups
    s = s.replace("(", " ").replace(")", " ")  # and any dangling paren left by the year cut

    tokens = [t for t in s.split() if t]
    out: list[str] = []
    for t in tokens:
        tl = t.lower()
        if tl in _TAGS or tl.strip("-") in _TAGS or tl.replace("-", "") in _TAGS:
            break
        out.append(t)

    title = " ".join(out).strip(" -–—_")
    title = _MULTISPACE.sub(" ", title)
    return title, year


def can_deduce(title: str, year: str | None, kind: str) -> bool:
    """Whether we're confident enough to offer an IMDb action.

    Concrete media units (file / Blu-ray / DVD rip) always qualify. A plain
    directory only qualifies when it carries a year — that distinguishes a movie
    folder ("Late Spring (1949)") from a category/director folder ("Wong Kar
    Wai") which has none.
    """
    if not title:
        return False
    if kind in ("file", "bdmv", "dvd"):
        return True
    return year is not None


def imdb_find_url(title: str, year: str | None) -> str:
    """Fallback IMDb search URL (no API key needed)."""
    q = f"{title} {year}".strip() if year else title
    return "https://www.imdb.com/find/?" + urllib.parse.urlencode({"q": q, "s": "tt"})


def imdb_title_url(imdb_id: str) -> str:
    return f"https://www.imdb.com/title/{imdb_id}/"
