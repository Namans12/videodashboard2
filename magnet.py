"""Magnet-link metadata fetcher.

Fetches a torrent's metadata (file list) via libtorrent without downloading
full files, then pulls the header slice of each video file so ffprobe can
read the real codec/HDR/audio data. Returns a structured verdict for each
file. Always cleans up the temporary download directory.

The libtorrent import is intentionally lazy so the rest of the FastAPI app
keeps booting even when the binding is missing on this machine.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
import time
from typing import Any, Callable

from analysis import analyze_file

logger = logging.getLogger("video-analyzer.magnet")

VIDEO_EXTS = (".mkv", ".mp4", ".ts", ".m2ts", ".hevc", ".h265")
JUNK_NAME_PATTERNS = (
    r"\bsample\b", r"\btrailer\b", r"\brarbg\b\.txt",
)
JUNK_EXTS = (".exe", ".rar", ".zip", ".7z", ".iso", ".nfo", ".txt", ".srr", ".srt")

# MKV: EBML header (codec/HDR/audio) is always at the start → head only.
# MP4/M2TS: moov atom may be at the end → download both head and tail.
HEAD_BYTES = 8 * 1024 * 1024   # 8 MB head — codec info, DV signalling, audio headers
TAIL_BYTES = 6 * 1024 * 1024   # 6 MB tail — MP4 moov-at-end

METADATA_TIMEOUT_S = 90
PIECE_TIMEOUT_S    = 180

# All well-known public DHT bootstrap nodes.
_DHT_ROUTERS = [
    ("router.bittorrent.com",  6881),
    ("dht.transmissionbt.com", 6881),
    ("router.utorrent.com",    6881),
    ("dht.libtorrent.org",    25401),
    ("dht.aelitis.com",        6881),
    ("router.bitcomet.com",    6881),
]


class MagnetUnavailable(RuntimeError):
    """Raised when libtorrent isn't installed."""


def _load_libtorrent():
    try:
        import libtorrent as lt  # type: ignore
    except ImportError as exc:
        raise MagnetUnavailable(
            "libtorrent is not installed. Install it with "
            "`pip install libtorrent` (you may also need the system "
            "package, e.g. `brew install libtorrent-rasterbar` on macOS "
            "or `apt install python3-libtorrent` on Debian/Ubuntu)."
        ) from exc
    return lt


def _classify_file(name: str, size: int) -> dict[str, Any]:
    base = os.path.basename(name).lower()
    ext = os.path.splitext(base)[1]
    reasons: list[str] = []
    verdict = "good"

    if ext in JUNK_EXTS:
        verdict = "bad"
        reasons.append(f"non-video extension ({ext})")
    elif ext not in VIDEO_EXTS:
        verdict = "skip"
        reasons.append(f"unsupported extension ({ext or 'none'})")

    for pattern in JUNK_NAME_PATTERNS:
        if re.search(pattern, base, re.IGNORECASE):
            verdict = "bad"
            reasons.append(f"matches junk pattern '{pattern}'")

    if ext in VIDEO_EXTS and size < 50 * 1024 * 1024:
        verdict = "bad"
        reasons.append(f"suspiciously small for a video ({size/1024/1024:.1f} MB)")

    if size > 200 * 1024 * 1024 * 1024:
        reasons.append(f"very large ({size/1024**3:.1f} GB)")

    return {"verdict": verdict, "reasons": reasons, "ext": ext}


def _pieces_for_range(piece_length: int, file_offset: int,
                      file_size: int, head_bytes: int, tail_bytes: int) -> list[int]:
    """Piece indexes covering the first `head_bytes` and last `tail_bytes` of a file."""
    if file_size <= 0:
        return []
    first_piece        = file_offset // piece_length
    last_piece_of_file = (file_offset + file_size - 1) // piece_length

    pieces: set[int] = set()

    if head_bytes > 0:
        head_end_byte  = file_offset + min(head_bytes, file_size) - 1
        head_last_piece = head_end_byte // piece_length
        pieces.update(range(first_piece, head_last_piece + 1))

    if tail_bytes > 0:
        tail_start_byte  = file_offset + max(0, file_size - tail_bytes)
        tail_first_piece = tail_start_byte // piece_length
        pieces.update(range(tail_first_piece, last_piece_of_file + 1))

    return sorted(pieces)


def _pieces_for_file(piece_length: int, file_offset: int,
                     file_size: int, ext: str) -> list[int]:
    """Select head-only pieces for MKV (EBML header at start) or
    head+tail pieces for MP4/M2TS (moov atom may be at the end)."""
    if ext in (".mp4", ".m2ts"):
        return _pieces_for_range(piece_length, file_offset, file_size, HEAD_BYTES, TAIL_BYTES)
    # .mkv, .ts, .hevc, .h265 — all metadata in the front
    return _pieces_for_range(piece_length, file_offset, file_size, HEAD_BYTES, 0)


def fetch_magnet_metadata(
    magnet_uri: str,
    skip_dovi_scan: bool = True,
    emit: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Download torrent metadata + a small header slice of each video file,
    then run ffprobe for real codec/HDR/audio results.

    Returns a dict with `files` (per-file verdicts), `analyses` (full VideoData
    for files that survived ffprobe), and `info_hash`/`name` torrent details.

    Always tears down the libtorrent session and removes the temp directory.
    """
    lt = _load_libtorrent()
    emit = emit or (lambda _msg: None)
    cancel_check = cancel_check or (lambda: False)

    workdir = tempfile.mkdtemp(prefix="videolyzer-magnet-")
    session: Any = None
    handle: Any = None

    try:
        emit("Starting libtorrent session…")

        # Fix 3: richer session settings for faster peer discovery.
        session = lt.session({
            "listen_interfaces":        "0.0.0.0:6881",
            "alert_mask":               lt.alert.category_t.all_categories,
            "enable_dht":               True,
            "enable_lsd":               True,    # local service discovery
            "enable_natpmp":            True,
            "enable_upnp":              True,
            "connection_speed":         100,     # connect to more peers/sec
            "peer_connect_timeout":     5,
            "handshake_timeout":        10,
            "request_timeout":          15,
            "min_reconnect_time":       1,
            "announce_to_all_tiers":    True,    # use every tracker tier
            "announce_to_all_trackers": True,
        })

        for host, port in _DHT_ROUTERS:
            session.add_dht_router(host, port)

        try:
            params = lt.parse_magnet_uri(magnet_uri)
            params.save_path = workdir
            handle = session.add_torrent(params)
            # Fix 1: do NOT set upload_limit to 1 byte/sec — that triggers
            # tit-for-tat choking and peers stop sending us data entirely.
            # We remove the torrent within minutes so brief uploads are fine.
        except Exception as exc:
            raise ValueError(f"Invalid magnet URI: {exc}") from exc

        # Fix 2: alert-based metadata detection (100ms polling instead of 500ms).
        emit("Fetching torrent metadata via DHT/peers…")
        deadline = time.time() + METADATA_TIMEOUT_S
        while True:
            if cancel_check():
                raise RuntimeError("Cancelled")
            # Drain the alert queue — fires metadata_received_alert as soon as ready.
            for alert in session.pop_alerts():
                if type(alert).__name__ == "metadata_received_alert":
                    break
            if handle.status().has_metadata:
                break
            if time.time() > deadline:
                raise TimeoutError(
                    f"No metadata received within {METADATA_TIMEOUT_S}s. "
                    "The torrent may have no live peers — check that your "
                    "firewall allows UDP on port 6881."
                )
            time.sleep(0.1)

        torrent_info = handle.torrent_file()
        files        = torrent_info.files()
        piece_length = torrent_info.piece_length()
        torrent_name = torrent_info.name()
        info_hash    = str(torrent_info.info_hash())
        num_files    = files.num_files()
        emit(f"Metadata received: '{torrent_name}' — {num_files} file(s)")

        file_records: list[dict[str, Any]] = []
        video_indexes: list[int] = []
        for i in range(num_files):
            f_path = files.file_path(i)
            f_size = files.file_size(i)
            cls = _classify_file(f_path, f_size)
            rec = {
                "index":         i,
                "name":          f_path,
                "size_bytes":    f_size,
                "size_gb":       round(f_size / (1024**3), 3),
                "ext":           cls["ext"],
                "verdict":       cls["verdict"],
                "reasons":       cls["reasons"],
                "ffprobe_ok":    False,
                "analysis_path": None,
            }
            file_records.append(rec)
            if cls["verdict"] == "good":
                video_indexes.append(i)

        if not video_indexes:
            emit("No playable video files in this torrent.")
            return {
                "torrent_name": torrent_name,
                "info_hash":    info_hash,
                "files":        file_records,
                "analyses":     [],
            }

        # File priorities ≥1 so libtorrent allocates storage and writes pieces to disk.
        # Non-video files stay at 0 (skipped entirely).
        file_priorities = [1 if i in set(video_indexes) else 0 for i in range(num_files)]
        handle.prioritize_files(file_priorities)

        # Fix 5: MKV → head only; MP4/M2TS → head + tail.
        priority_pieces: list[int] = []
        for idx in video_indexes:
            ext      = file_records[idx]["ext"]
            f_offset = files.file_offset(idx)
            f_size   = files.file_size(idx)
            priority_pieces.extend(_pieces_for_file(piece_length, f_offset, f_size, ext))
        priority_pieces = sorted(set(priority_pieces))

        est_mb = len(priority_pieces) * piece_length / 1024 / 1024
        emit(f"Downloading {len(priority_pieces)} piece(s) (~{est_mb:.1f} MB) for verification…")

        # Fix 4: 2-second deadline per piece instead of 0 (fail-fast).
        # Gives peers a moment to unchoke us before the request expires.
        for p in priority_pieces:
            handle.piece_priority(p, 7)
            handle.set_piece_deadline(p, 2000)

        deadline = time.time() + PIECE_TIMEOUT_S
        while True:
            if cancel_check():
                raise RuntimeError("Cancelled")
            if all(handle.have_piece(p) for p in priority_pieces):
                break
            if time.time() > deadline:
                missing = sum(1 for p in priority_pieces if not handle.have_piece(p))
                emit(f"⚠ Timed out — {missing} piece(s) still missing. "
                     "Running ffprobe on what arrived.")
                break
            time.sleep(0.5)

        analyses: list[dict[str, Any]] = []
        for idx in video_indexes:
            rec        = file_records[idx]
            local_path = os.path.join(workdir, files.file_path(idx))
            if not os.path.isfile(local_path):
                rec["verdict"] = "bad"
                rec["reasons"].append("header slice not written to disk")
                continue
            emit(f"Probing {os.path.basename(rec['name'])}…")
            try:
                result = analyze_file(local_path, skip_dovi_scan=skip_dovi_scan)
            except Exception as exc:
                logger.warning("ffprobe failed on partial %s: %s", local_path, exc)
                result = None
            if result:
                result["file"]           = os.path.basename(rec["name"])
                result["path"]           = rec["name"]
                result["magnet_partial"] = True
                rec["ffprobe_ok"]        = True
                rec["analysis_path"]     = result["path"]
                analyses.append(result)
            else:
                rec["verdict"] = "bad"
                rec["reasons"].append("ffprobe could not parse the header slice")

        return {
            "torrent_name": torrent_name,
            "info_hash":    info_hash,
            "files":        file_records,
            "analyses":     analyses,
        }
    finally:
        emit("Cleaning up torrent session and temp files…")
        if session is not None and handle is not None:
            try:
                session.remove_torrent(handle, lt.session.delete_files)  # type: ignore[attr-defined]
            except Exception:
                try:
                    session.remove_torrent(handle)
                except Exception:
                    pass
        if session is not None:
            try:
                session.pause()
            except Exception:
                pass
        time.sleep(0.5)
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception as exc:
            logger.warning("Failed to remove magnet workdir %s: %s", workdir, exc)
        session = None


def run_magnet_job_threaded(magnet_uri: str, skip_dovi_scan: bool,
                            emit: Callable[[str], None],
                            cancel_check: Callable[[], bool]) -> dict[str, Any]:
    """Wrapper that runs fetch_magnet_metadata with a hard outer timeout."""
    result_holder: list[Any]                 = [None]
    error_holder:  list[BaseException | None] = [None]

    def _run() -> None:
        try:
            result_holder[0] = fetch_magnet_metadata(
                magnet_uri, skip_dovi_scan=skip_dovi_scan,
                emit=emit, cancel_check=cancel_check,
            )
        except BaseException as e:  # noqa: BLE001
            error_holder[0] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=METADATA_TIMEOUT_S + PIECE_TIMEOUT_S + 30)
    if t.is_alive():
        raise TimeoutError(
            "Magnet job exceeded the hard timeout. The torrent likely has "
            "no live seeders."
        )
    if error_holder[0]:
        raise error_holder[0]
    return result_holder[0]
