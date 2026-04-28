import React, {
  type ChangeEvent,
  type DragEvent,
  useRef,
  useState,
  useEffect
} from "react";
import axios from "axios";
import type { VideoData } from "./types";
import { BRAVIA_8_II_SPEC } from "./types";
import "./App.css";
import Switch from "./Switch";

// ── Constants ────────────────────────────────────────────────────────────────
const VIDEO_EXTS = [".mkv", ".mp4", ".ts", ".m2ts", ".hevc", ".h265"];

const API = "http://127.0.0.1:8000";

const DV_HIERARCHY = [
  { label: "Profile 7 FEL", verdict: "Best",    cls: "r1", notes: "Full dual-layer DV with BL, EL, and RPU — closest to studio master." },
  { label: "Profile 7 MEL", verdict: "Excellent", cls: "r2", notes: "Dual-layer DV with reduced EL data — still disc-grade." },
  { label: "Profile 8.1",   verdict: "Very Good", cls: "r3", notes: "Single-layer DV, HDR10-compat base — best native target for Bravia 8 II." },
  { label: "Profile 8.4",   verdict: "Very Good", cls: "r3", notes: "Single-layer DV on HLG base." },
  { label: "Profile 5",     verdict: "Very Good", cls: "r3", notes: "Single-layer streaming DV — no HDR10 fallback." },
  { label: "Profile 8.2",   verdict: "Good",      cls: "r5", notes: "Single-layer DV, SDR-compat base." },
  { label: "Profile 4",     verdict: "Limited",   cls: "r6", notes: "Older dual-layer — limited consumer support." },
];

const HDR_HIERARCHY = [
  { label: "HDR10+", verdict: "Very Good", cls: "r7",  notes: "Dynamic metadata HDR." },
  { label: "HDR10",  verdict: "Decent",    cls: "r9",  notes: "Static metadata HDR." },
  { label: "HLG",    verdict: "Basic",     cls: "r10", notes: "Broadcast HDR." },
  { label: "SDR",    verdict: "Worst",     cls: "r11", notes: "No HDR." },
];

const FILE_COLORS = ["#ffd700", "#2997ff", "#30d158", "#ff9f0a", "#bf5af2"];

// ── Helpers ──────────────────────────────────────────────────────────────────
// Helper removed — no longer used after unifying analysis flow.

function scoreColor(score: number) {
  if (score >= 70) return "#30d158";
  if (score >= 40) return "#ff9f0a";
  return "#ff453a";
}

function getVerdict(item: VideoData, isBest: boolean) {
  if (isBest) return "BEST CHOICE";
  if ((item.tv_score ?? 0) >= 75 && (item.confidence_score ?? 0) >= 60) return "GREAT";
  if ((item.score ?? 0) < 15) return "AVOID";
  if ((item.confidence_score ?? 0) < 40) return "LOW CONFIDENCE";
  if ((item.tv_score ?? 0) >= 30) return "COMPARABLE";
  return "OK";
}

function getToolStatusLabel(status: string) {
  if (status === "partial")   return "Bounded";
  if (status === "ok")        return "Ready";
  if (status === "error")     return "Issue";
  if (status === "skipped")   return "Skipped";
  return "Missing";
}

// ── Radar chart ──────────────────────────────────────────────────────────────
function RadarChart({ items, size = 280 }: { items: VideoData[]; size?: number }) {
  const N    = 5;
  const cx   = size / 2;
  const cy   = size / 2;
  const r    = (size / 2) * 0.65;
  const labels = ["Quality", "TV Score", "Bitrate", "Audio", "Confidence"];

  const angle = (i: number) => (Math.PI * 2 * i) / N - Math.PI / 2;
  const pt = (i: number, f: number) => ({
    x: cx + r * f * Math.cos(angle(i)),
    y: cy + r * f * Math.sin(angle(i)),
  });
  const polyPts = (fracs: number[]) =>
    fracs
      .map((f, i) => pt(i, Math.min(Math.max(f, 0), 1)))
      .map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`)
      .join(" ");

  const normalize = (item: VideoData): number[] => [
    (item.score ?? 0) / 100,
    (item.tv_score ?? 0) / 100,
    Math.min((item.bitrate_mbps ?? 0) / 80, 1),
    (item.audio_score ?? 0) / 10,
    (item.confidence_score ?? 0) / 100,
  ];

  const gridLevels = [0.25, 0.5, 0.75, 1];

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      style={{ overflow: "visible" }}
    >
      {gridLevels.map((level) => (
        <polygon
          key={level}
          points={Array.from({ length: N }, (_, i) => {
            const p = pt(i, level);
            return `${p.x.toFixed(2)},${p.y.toFixed(2)}`;
          }).join(" ")}
          fill="none"
          stroke={`rgba(255,255,255,${level === 1 ? 0.18 : 0.08})`}
          strokeWidth="1"
        />
      ))}

      {Array.from({ length: N }, (_, i) => {
        const p = pt(i, 1);
        return (
          <line
            key={i}
            x1={cx} y1={cy}
            x2={p.x.toFixed(2)} y2={p.y.toFixed(2)}
            stroke="rgba(255,255,255,0.1)"
            strokeWidth="1"
          />
        );
      })}

      {items.map((item, fi) => {
        const fracs = normalize(item);
        const color = FILE_COLORS[fi % FILE_COLORS.length];
        return (
          <g key={item.path}>
            <polygon
              points={polyPts(fracs)}
              fill={color + "22"}
              stroke={color}
              strokeWidth="2"
              strokeLinejoin="round"
            />
            {fracs.map((f, i) => {
              const p = pt(i, Math.min(Math.max(f, 0), 1));
              return (
                <circle
                  key={i}
                  cx={p.x.toFixed(2)}
                  cy={p.y.toFixed(2)}
                  r={4}
                  fill={color}
                />
              );
            })}
          </g>
        );
      })}

      {labels.map((label, i) => {
        const p = pt(i, 1.28);
        return (
          <text
            key={i}
            x={p.x.toFixed(2)}
            y={p.y.toFixed(2)}
            textAnchor="middle"
            dominantBaseline="middle"
            fill="rgba(255,255,255,0.6)"
            fontSize="11"
            fontFamily="'SF Pro Text', Helvetica, sans-serif"
          >
            {label}
          </text>
        );
      })}
    </svg>
  );
}

// ── Score bar chart ───────────────────────────────────────────────────────────
function ScoreBarChart({ items }: { items: VideoData[] }) {
  const metrics: Array<{ key: keyof VideoData; label: string; max: number }> = [
    { key: "score",            label: "Quality",    max: 100 },
    { key: "tv_score",         label: "TV Score",   max: 100 },
    { key: "confidence_score", label: "Confidence", max: 100 },
    { key: "audio_score",      label: "Audio",      max: 10  },
  ];

  return (
    <div className="score-bar-chart">
      {metrics.map(({ key, label, max }) => (
        <div key={key} className="sbc-metric">
          <span className="sbc-metric-label">{label}</span>
          <div className="sbc-tracks">
            {items.map((item, fi) => {
              const val  = (item[key] as number) ?? 0;
              const pct  = Math.min((val / max) * 100, 100);
              const color = FILE_COLORS[fi % FILE_COLORS.length];
              return (
                <div key={item.path} className="sbc-track-row">
                  <span className="sbc-file-dot" style={{ background: color }} />
                  <div className="sbc-track">
                    <div
                      className="sbc-fill"
                      style={{ width: `${pct}%`, background: color }}
                    />
                  </div>
                  <span className="sbc-value" style={{ color }}>
                    {key === "audio_score" ? `${val}/10` : val}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Bitrate comparison ────────────────────────────────────────────────────────
function BitrateChart({ items }: { items: VideoData[] }) {
  const maxBr = Math.max(...items.map((i) => i.bitrate_mbps ?? 0), 1);
  return (
    <div className="bitrate-chart">
      {items.map((item, fi) => {
        const pct   = ((item.bitrate_mbps ?? 0) / maxBr) * 100;
        const color = FILE_COLORS[fi % FILE_COLORS.length];
        return (
          <div key={item.path} className="bc-row">
            <span className="bc-label" style={{ color }}>
              {item.file.length > 22 ? item.file.slice(0, 22) + "…" : item.file}
            </span>
            <div className="bc-track">
              <div className="bc-fill" style={{ width: `${pct}%`, background: color }} />
            </div>
            <span className="bc-value" style={{ color }}>
              {(item.bitrate_mbps ?? 0).toFixed(2)} Mbps
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── TV compatibility panel ───────────────────────────────────────────────────
function TVPanel({ items }: { items: VideoData[] }) {
  const dvProfiles = [
    { profile: "8.1",  support: "Yes",     label: "Best native target" },
    { profile: "8.4",  support: "Yes",     label: "HLG-base — excellent" },
    { profile: "5",    support: "Yes",     label: "Single-layer streaming" },
    { profile: "8.2",  support: "Yes",     label: "SDR-compat base" },
    { profile: "8.x",  support: "Yes",     label: "Generic Profile 8" },
    { profile: "7",    support: "Partial", label: "BL+RPU used; EL ignored" },
    { profile: "4",    support: "Limited", label: "Unreliable" },
    { profile: "None", support: "No",      label: "No Dolby Vision" },
  ];

  return (
    <div className="tv-panel">
      <div className="panel-head">
        <p className="panel-kicker">Sony Bravia 8 Mark II</p>
        <h3>Dolby Vision Profile Support</h3>
      </div>

      <div className="tv-dv-grid">
        {dvProfiles.map((row) => {
          const matchedFiles = items.filter(
            (item) => item.dv_profile === row.profile
          );
          return (
            <div key={row.profile} className={`tv-dv-row support-${row.support.toLowerCase()}`}>
              <span className="tv-dv-profile">Profile {row.profile}</span>
              <span className={`tv-dv-support support-badge-${row.support.toLowerCase()}`}>
                {row.support}
              </span>
              <span className="tv-dv-label">{row.label}</span>
              {matchedFiles.length > 0 && (
                <span className="tv-dv-match">
                  ← {matchedFiles.map((f) => f.file.split(".")[0]).join(", ")}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {items.length > 0 && (
        <div className="tv-scores-row">
          {items.map((item, fi) => (
            <div key={item.path} className="tv-score-chip">
              <span
                className="tv-score-dot"
                style={{ background: FILE_COLORS[fi % FILE_COLORS.length] }}
              />
              <div>
                <p className="tv-sc-file">
                  {item.file.length > 20 ? item.file.slice(0, 20) + "…" : item.file}
                </p>
                <p
                  className="tv-sc-score"
                  style={{ color: FILE_COLORS[fi % FILE_COLORS.length] }}
                >
                  {item.tv_score ?? "–"}{" "}
                  <span className="tv-sc-label">{item.tv_label ?? ""}</span>
                </p>
                <p className="tv-sc-note">{item.tv_dv_note ?? ""}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── USB panel ────────────────────────────────────────────────────────────────
function USBPanel({ items }: { items: VideoData[] }) {
  return (
    <div className="usb-panel">
      <div className="panel-head">
        <p className="panel-kicker">USB Playback</p>
        <h3>Drive &amp; File Compatibility</h3>
      </div>

      <div className="usb-spec-grid">
        <div className="usb-spec-block">
          <h4>Drive Format</h4>
          {BRAVIA_8_II_SPEC.usb_fs.map((fs) => (
            <p key={fs} className={fs.includes("recommended") ? "usb-rec" : "usb-ok"}>
              {fs}
            </p>
          ))}
        </div>
        <div className="usb-spec-block">
          <h4>Video Codecs</h4>
          {BRAVIA_8_II_SPEC.usb_video.map((v) => (
            <p key={v} className="usb-ok">{v}</p>
          ))}
        </div>
        <div className="usb-spec-block">
          <h4>Audio Codecs</h4>
          {BRAVIA_8_II_SPEC.usb_audio.map((a) => (
            <p key={a} className="usb-ok">{a}</p>
          ))}
        </div>
        <div className="usb-spec-block">
          <h4>Containers</h4>
          {BRAVIA_8_II_SPEC.usb_containers.map((c) => (
            <p key={c} className="usb-ok">{c}</p>
          ))}
        </div>
      </div>

      {items.length > 0 && (
        <div className="usb-file-compat">
          <h4>Per-file compatibility</h4>
          {items.map((item, fi) => (
            <div key={item.path} className={`usb-file-row ${item.usb_compatible ? "usb-compat-ok" : "usb-compat-fail"}`}>
              <span
                className="usb-dot"
                style={{ background: FILE_COLORS[fi % FILE_COLORS.length] }}
              />
              <div className="usb-file-info">
                <p className="usb-file-name">{item.file}</p>
                <p className="usb-file-status">
                  {item.usb_compatible
                    ? "✓ Compatible with USB playback"
                    : "✗ Has issues for USB playback"}
                </p>
                {(item.usb_issues ?? []).map((issue, i) => (
                  <p key={i} className="usb-issue">⚠ {issue}</p>
                ))}
                {(item.usb_warnings ?? []).map((warn, i) => (
                  <p key={i} className="usb-warning">ⓘ {warn}</p>
                ))}
              </div>
              <span className="usb-size">
                {item.file_size_gb ? `${item.file_size_gb.toFixed(1)} GB` : ""}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="usb-tips">
        <h4>Tips for your Bravia 8 II</h4>
        {BRAVIA_8_II_SPEC.usb_notes.map((note, i) => (
          <p key={i} className="usb-tip">• {note}</p>
        ))}
      </div>
    </div>
  );
}

// ── Legend ───────────────────────────────────────────────────────────────────
function FileLegend({ items }: { items: VideoData[] }) {
  if (items.length <= 1) return null;
  return (
    <div className="file-legend">
      {items.map((item, fi) => (
        <div key={item.path} className="legend-item">
          <span className="legend-dot" style={{ background: FILE_COLORS[fi % FILE_COLORS.length] }} />
          <span className="legend-name">
            {item.file.length > 28 ? item.file.slice(0, 28) + "…" : item.file}
          </span>
          <span className="legend-rank">#{item.batch_rank ?? fi + 1}</span>
        </div>
      ))}
    </div>
  );
}

// ── Main App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [path,             setPath]             = useState("");
  const [data,             setData]             = useState<VideoData[]>([]);
  const [error,            setError]            = useState("");
  const [isLoading,        setIsLoading]        = useState(false);
  const [selectedFiles,    setSelectedFiles]    = useState<File[]>([]);
  const [dragActive,       setDragActive]       = useState(false);
  const [fastMode,         setFastMode]         = useState(true);
  const [isLightMode,      setIsLightMode]      = useState(() => {
    return localStorage.getItem("theme") === "light";
  });
  useEffect(() => {
  localStorage.setItem("theme", isLightMode ? "light" : "dark");
  }, [isLightMode]);
  const [progress,         setProgress]         = useState("");
  const [jobId,       setJobId]       = useState<string | null>(null);
  const [progressMsg, setProgressMsg] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // ── File selection ─────────────────────────────────────────────────────────
  const addFiles = (incoming: FileList | File[]) => {
    const valid = Array.from(incoming).filter((f) =>
      VIDEO_EXTS.some((ext) => f.name.toLowerCase().endsWith(ext))
    );
    if (!valid.length) return;
    setSelectedFiles((prev) => {
      const names = new Set(prev.map((f) => f.name));
      return [...prev, ...valid.filter((f) => !names.has(f.name))];
    });
    setError("");
    setPath("");
  };

  const removeFile = (name: string) => {
    setSelectedFiles((prev) => prev.filter((f) => f.name !== name));
  };

  const handleFileInput = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) addFiles(e.target.files);
    e.target.value = "";
  };

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragActive(true);
  };

  const handleDragLeave = () => setDragActive(false);

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragActive(false);
    if (e.dataTransfer.files) addFiles(e.dataTransfer.files);
  };

  // ── Analysis ───────────────────────────────────────────────────────────────
  const analyzeSelection = async () => {
    const trimmedPath = path.trim();
    if (!selectedFiles.length && !trimmedPath) {
      setError("Drag & drop files, choose with Browse, or paste a path.");
      setData([]);
      return;
    }

    setIsLoading(true);
    setError("");

    try {
      let results: VideoData[] = [];

      if (trimmedPath) {
        // Use background job + SSE for pasted paths as well (file or folder on server).
        const res = await fetch(
          `${API}/analyze-path/?path=${encodeURIComponent(trimmedPath)}&fast=${fastMode}`
        );
        if (!res.ok) {
          const payload = await res.json().catch(() => ({})) as { detail?: string };
          const prefix = res.status === 507 ? "🖴 Disk full: " : "";
          throw new Error(prefix + (payload.detail ?? "Analysis request failed."));
        }

        const payload = await res.json().catch(() => ({})) as { job_id?: string };
        const jobId = payload.job_id as string | undefined;
        if (!jobId) throw new Error("No job_id returned from server.");

        setProgress("Queued");

        const poll = async (jobId: string) => {
          const r = await fetch(`${API}/job/${jobId}`);
          const job = await r.json();

          setProgress(`${job.current} — ${job.progress}`);

          if (job.status === "done") {
            setData(job.results);
            setIsLoading(false);
          } else {
            setTimeout(() => void poll(jobId), 600);
          }
        };

        // Try SSE first, fall back to polling if unavailable or on error.
        if (typeof EventSource !== "undefined") {
          let es: EventSource | null = null;
          try {
            es = new EventSource(`${API}/progress/${jobId}`);
            es.onmessage = (ev) => {
              try {
                const pkt = JSON.parse(ev.data as string);
                if (pkt === "__done__" || pkt.msg === "__done__") {
                  es?.close();
                  void poll(jobId);
                  return;
                }
                const msg = pkt.msg ?? pkt.message ?? pkt;
                const ts = pkt.ts ?? pkt.timestamp ?? "";
                setProgress(`${msg} ${ts ? ` — ${ts}` : ""}`);
              } catch (_) {
                // ignore malformed SSE messages
              }
            };
            es.onerror = () => {
              es?.close();
              void poll(jobId);
            };
          } catch (e) {
            void poll(jobId);
          }
        } else {
          void poll(jobId);
        }

        results = [];
      } else if (selectedFiles.length > 0) {
        const formData = new FormData();
        selectedFiles.forEach((f) => formData.append("files", f));
        const res = await fetch(`${API}/analyze-multiple/?fast=${fastMode}`, {
          method: "POST", body: formData,
        });
        if (!res.ok) {
          const payload = await res.json().catch(() => ({})) as { detail?: string };
          const prefix = res.status === 507 ? "🖴 Disk full: "
                       : res.status === 413 ? "📦 File too large: " : "";
          throw new Error(prefix + (payload.detail ?? "Multi-file analysis failed."));
        }
        const { job_id } = await res.json() as { job_id: string; total: number };
        setJobId(job_id);

        // Poll /job/{job_id} every 600ms until done
        await new Promise<void>((resolve, reject) => {
          const poll = async () => {
            try {
              const jobRes  = await fetch(`${API}/job/${job_id}`);
              const job     = await jobRes.json() as {
                status: string; progress: string; current: string;
                results: VideoData[]; error: string | null;
              };
              setProgressMsg(`${job.current || "..."} (${job.progress})`);
              if (job.status === "done") {
                results = job.results;
                resolve();
              } else if (job.status === "error") {
                reject(new Error(job.error ?? "Analysis failed."));
              } else {
                setTimeout(poll, 600);
              }
            } catch (e) { reject(e); }
          };
          poll();
        });
        setJobId(null);
        setProgressMsg("");

        results = [];
      }

      results = results
        .filter(Boolean)
        .sort((a, b) =>
          ((b.tv_score ?? 0) + (b.confidence_score ?? 0)) -
          ((a.tv_score ?? 0) + (a.confidence_score ?? 0))
          );

      setData(results);
    } catch (err: unknown) {
      let message = "Analysis failed.";
      if (axios.isAxiosError(err)) {
        const status = err.response?.status;
        const detail = err.response?.data?.detail;
        if (status === 507) {
          message = `🖴 Server disk full: ${detail ?? "Free up space on the server and retry."}`;
        } else {
          message =
            (typeof detail === "string" && detail) || err.message || message;
        }
      } else if (err instanceof Error) {
        const text = err.message;
        message = text.includes("507")
          ? "🖴 Server disk full — free up space in the uploads/ folder and retry."
          : text;
      }
      setError(message);
      setData([]);
    } finally {
      setIsLoading(false);
    }
  };

  const clearAll = () => {
    setPath("");
    setSelectedFiles([]);
    setError("");
  };

  const bestPath = data[0]?.path;
  const showDashboard = data.length > 0;

  return (
    <div className={`app-shell ${isLightMode ? "theme-light" : "theme-dark"}`}>
      {/* ── Nav ─────────────────────────────────────────────────────────── */}
      <nav className="top-nav">
        <div className="nav-links">
          <a href="#analyze">Analyze</a>
          {showDashboard && <a href="#dashboard">Dashboard</a>}
          {showDashboard && <a href="#tv-usb">TV &amp; USB</a>}
          <a href="#hierarchy">Hierarchy</a>
        </div>
        <Switch isLightMode={isLightMode} setIsLightMode={setIsLightMode} />
      </nav>

      {/* ── Hero ────────────────────────────────────────────────────────── */}
      <section id="analyze" className="hero-section">
        <div className="hero-copy">
          <p className="eyebrow">Multi-file HDR &amp; Dolby Vision inspector</p>
          <h1 className="app-title">
            <span>Video Metadata</span>
            <span>Analyzer</span>
          </h1>
          <p className="app-subtitle">
            Compare multiple files at once using MediaInfo, ffprobe, ffmpeg, and dovi_tool.
            Scores are calibrated for your Sony Bravia 8 Mark II via USB.
          </p>
        </div>

        <div className="hero-panel">
          {/* ── Drag zone ─────────────────────────────────────────────── */}
          <div
            className={`drop-zone ${dragActive ? "drop-zone-active" : ""}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="17 8 12 3 7 8"/>
              <line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
            <p className="drop-main">Drop video files here</p>
            <p className="drop-sub">MKV · MP4 · TS · M2TS · HEVC · H265</p>
          </div>

          {/* ── Selected file list ─────────────────────────────────────── */}
          {selectedFiles.length > 0 && (
            <div className="file-chip-list">
              {selectedFiles.map((f, i) => (
                <div
                  key={f.name}
                  className="file-chip"
                  style={{ borderColor: FILE_COLORS[i % FILE_COLORS.length] }}
                >
                  <span
                    className="chip-dot"
                    style={{ background: FILE_COLORS[i % FILE_COLORS.length] }}
                  />
                  <span className="chip-name">
                    {f.name.length > 28 ? f.name.slice(0, 28) + "…" : f.name}
                  </span>
                  <button
                    className="chip-remove"
                    type="button"
                    onClick={(e) => { e.stopPropagation(); removeFile(f.name); }}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="control-grid">
            {/* ── Path row ─────────────────────────────────────────────── */}
            <div className="path-row">
              <label className="input-label" htmlFor="video-path">
                Or paste a file / folder path
              </label>
              <form
                className="search-form"
                onSubmit={(e) => { e.preventDefault(); void analyzeSelection(); }}
              >
                <button type="submit" className="search-icon-button" aria-label="Analyze">
                  <svg width={17} height={16} fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M7.667 12.667A5.333 5.333 0 107.667 2a5.333 5.333 0 000 10.667zM14.334 14l-2.9-2.9"
                      stroke="currentColor" strokeWidth="1.333" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </button>
                <input
                  id="video-path"
                  className="search-input"
                  placeholder="C:\Movies\  or  /mnt/media/"
                  value={path}
                  onChange={(e) => {
                    setPath(e.target.value);
                    if (selectedFiles.length) setSelectedFiles([]);
                  }}
                  type="text"
                />
                <button
                  className="search-reset"
                  type="button"
                  aria-label="Clear"
                  onClick={clearAll}
                  disabled={!path && !selectedFiles.length}
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="reset-icon" fill="none"
                    viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12"/>
                  </svg>
                </button>
              </form>
            </div>

            {/* ── Browse + fast mode row ────────────────────────────────── */}
            <div className="choose-row">
              <span className="input-label">Options</span>
              <div className="options-row">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isLoading}
                >
                  Browse Files
                </button>

                <label className="fast-toggle">
                  <input
                    type="checkbox"
                    checked={fastMode}
                    onChange={(e) => setFastMode(e.target.checked)}
                  />
                  <span>Fast mode</span>
                  <span className="fast-hint">(skip RPU deep scan)</span>
                </label>
              </div>
            </div>
          </div>

          {jobId && progressMsg && (
            <div style={{
              margin: "12px 0 0", padding: "10px 14px",
              borderRadius: 10, background: "rgba(41,151,255,0.1)",
              border: "1px solid rgba(41,151,255,0.25)",
              fontSize: 13, color: "var(--accent-bright)",
            }}>
              ⏳ Analyzing: {progressMsg}
            </div>
          )}
          <div className="analyze-row">
            <button
              onClick={() => void analyzeSelection()}
              disabled={isLoading}
              className="primary-button"
            >
              {isLoading
                ? "Analyzing…"
                : selectedFiles.length > 1
                ? `Analyze ${selectedFiles.length} Files`
                : "Analyze"}
            </button>
          </div>
        </div>
      </section>

      <input
        ref={fileInputRef}
        type="file"
        accept=".mkv,.mp4,.ts,.m2ts,.hevc,.h265,video/*"
        multiple
        hidden
        onChange={handleFileInput}
      />

      {error && <div className="error-box">{error}</div>}
      {progress && <div className="error-box">{progress}</div>}

      {/* ── Dashboard ───────────────────────────────────────────────────── */}
      {showDashboard && (
        <section id="dashboard" className="dashboard-section">
          <div className="dashboard-inner">
            <div className="dash-header">
              <h2>
                {data.length === 1
                  ? "Analysis Result"
                  : `Comparing ${data.length} Files`}
              </h2>
              <p className="dash-sub">
                Ranked by TV score for Sony Bravia 8 Mark II USB playback
              </p>
            </div>

            <FileLegend items={data} />

            {/* ── Leaderboard ─────────────────────────────────────────── */}
            {data.length > 1 && (
              <div className="leaderboard">
                {data.map((item, fi) => {
                  const isBest = item.path === bestPath;
                  const color  = FILE_COLORS[fi % FILE_COLORS.length];
                  return (
                    <div key={item.path} className={`lb-row ${isBest ? "lb-best" : ""}`}>
                      <span className="lb-rank" style={{ color }}># {item.batch_rank ?? fi + 1}</span>
                      <div className="lb-file">
                        <p className="lb-name">{item.file}</p>
                        <p className="lb-meta">
                          DV {item.dv_profile} · {item.source} · {(item.bitrate_mbps ?? 0).toFixed(1)} Mbps
                        </p>
                      </div>
                      <div className="lb-scores">
                        <span className="lb-score-tv" style={{ color }}>
                          TV {item.tv_score ?? "–"}
                        </span>
                        <span className="lb-score-q">Q {item.score}</span>
                        <span className="lb-verdict">{getVerdict(item, isBest)}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* ── Charts grid ─────────────────────────────────────────── */}
            <div className="charts-grid">
              {data.length >= 2 && (
                <div className="chart-card">
                  <h3>Multi-dimension Radar</h3>
                  <p className="chart-sub">Quality · TV Score · Bitrate · Audio · Confidence</p>
                  <div className="radar-wrap">
                    <RadarChart items={data} size={260} />
                  </div>
                </div>
              )}

              <div className="chart-card">
                <h3>Score Comparison</h3>
                <p className="chart-sub">All scoring dimensions side by side</p>
                <ScoreBarChart items={data} />
              </div>

              <div className="chart-card">
                <h3>Bitrate</h3>
                <p className="chart-sub">Video stream bitrate in Mbps</p>
                <BitrateChart items={data} />
              </div>
            </div>
          </div>
        </section>
      )}

      {/* ── TV & USB panels ─────────────────────────────────────────────── */}
      {showDashboard && (
        <section id="tv-usb" className="tv-usb-section">
          <div className="tv-usb-inner">
            <TVPanel items={data} />
            <USBPanel items={data} />
          </div>
        </section>
      )}

      {/* ── Per-file result cards ────────────────────────────────────────── */}
      <div className="results-grid">
        {data.map((item, idx) => {
          const isBest      = item.path === bestPath;
          const color       = FILE_COLORS[idx % FILE_COLORS.length];
          const scoreWidth  = Math.min(item.score ?? 0, 100);
          const confWidth   = Math.min(item.confidence_score ?? 0, 100);
          const tvWidth     = Math.min(item.tv_score ?? 0, 100);

          return (
            <article key={item.path || idx} className={`result-card ${isBest ? "result-card-best" : ""}`}
              style={{ "--card-accent": color } as React.CSSProperties}>
              <div className="result-header">
                <div className="result-header-copy">
                  <p className="result-kicker" style={{ color }}>
                    #{item.batch_rank ?? idx + 1} · {item.source}
                  </p>
                  <h2 className="result-file">{item.file}</h2>
                  <p className="result-path">{item.path}</p>
                </div>

                <div className="score-panel" style={{ borderColor: color + "44" }}>
                  <span className="score-label">Quality</span>
                  <span className="score-value" style={{ color: scoreColor(item.score) }}>
                    {item.score}
                  </span>
                  <span className="score-label" style={{ marginTop: 6 }}>TV Score</span>
                  <span className="score-tv" style={{ color }}>
                    {item.tv_score ?? "–"}
                  </span>
                  <span className="meta-line">Conf. {item.confidence_score ?? 0}/100</span>
                </div>
              </div>

              <div className="tag-row">
                <span className="tag tag-dv">DV {item.dv_profile}</span>
                <span className="tag tag-source">{item.audio ?? "?"}</span>
                <span className="tag tag-bitrate" style={{ background: color }}>
                  {(item.bitrate_mbps ?? 0).toFixed(2)} Mbps
                </span>
                <span className="tag tag-runtime">{(item.duration_min ?? 0).toFixed(1)} min</span>
                <span className={`tag tag-usb ${item.usb_compatible ? "tag-usb-ok" : "tag-usb-fail"}`}>
                  USB {item.usb_compatible ? "✓" : "✗"}
                </span>
                <span className="tag tag-source">{item.confidence_label ?? "?"}</span>
              </div>

              {item.quick_summary  && <div className="summary-line">{item.quick_summary}</div>}
              {item.recommendation && <div className="detail-line">{item.recommendation}</div>}
              {item.insights       && <div className="insight-line">{item.insights}</div>}

              <div className="result-submeta">
                <span>Analyzer: {item.dv_tool ?? "unknown"}</span>
                <span>BL {item.bl ?? "?"} / EL {item.el ?? "?"} / RPU {item.rpu ?? "?"}</span>
                <span>TV: {item.tv_playback ?? "?"}</span>
              </div>

              {/* Quality bars */}
              <div className="quality-bars">
                {[
                  { label: "Quality",    pct: scoreWidth,  cls: "" },
                  { label: "TV Score",   pct: tvWidth,     cls: "tv" },
                  { label: "Confidence", pct: confWidth,   cls: "secondary" },
                ].map(({ label, pct, cls }) => (
                  <div key={label} className="bar">
                    <span>{label}</span>
                    <div className="bar-track">
                      <div className={`bar-fill ${cls}`} style={{ width: `${pct}%` }} />
                    </div>
                    <span className="bar-val">{pct}</span>
                  </div>
                ))}
              </div>

              {/* Fact panels */}
              <div className="result-sections">
                <section className="fact-panel">
                  <div className="fact-panel-head">
                    <p className="fact-kicker">Dolby Vision</p>
                    <h3>Signal readout</h3>
                  </div>
                  <dl className="fact-list">
                    {(item.signal_facts ?? []).map((fact) => (
                      <div className="fact-row" key={fact.label}>
                        <dt>{fact.label}</dt>
                        <dd>{fact.value}</dd>
                      </div>
                    ))}
                  </dl>
                </section>

                <section className="fact-panel">
                  <div className="fact-panel-head">
                    <p className="fact-kicker">Media</p>
                    <h3>Container &amp; stream facts</h3>
                  </div>
                  <dl className="fact-list">
                    {(item.media_facts ?? []).map((fact) => (
                      <div className="fact-row" key={fact.label}>
                        <dt>{fact.label}</dt>
                        <dd>{fact.value}</dd>
                      </div>
                    ))}
                  </dl>
                </section>
              </div>

              {/* Toolchain */}
              <section className="toolchain-section">
                <div className="fact-panel-head">
                  <p className="fact-kicker">Toolchain</p>
                  <h3>What each tool contributed</h3>
                </div>
                <div className="tool-grid">
                  {(item.tool_reports ?? []).map((report) => (
                    <article
                      key={report.name}
                      className={`tool-card tool-card-${report.status}`}
                    >
                      <div className="tool-card-head">
                        <h4>{report.name}</h4>
                        <span className={`tool-status tool-status-${report.status}`}>
                          {getToolStatusLabel(report.status)}
                        </span>
                      </div>
                      <p className="tool-headline">{report.headline}</p>
                      {report.details.length > 0 && (
                        <ul className="tool-detail-list">
                          {report.details.map((d, di) => (
                            <li key={di}>{d}</li>
                          ))}
                        </ul>
                      )}
                    </article>
                  ))}
                </div>
              </section>

              {isBest && data.length > 1 && (
                <div className="best-badge" style={{ color }}>
                  ★ Top score in this batch
                </div>
              )}

              <a
                href={encodeURI(`file:///${item.path.replace(/\\/g, "/")}`)}
                className="open-link"
              >
                Open File
              </a>
              <div className="meta-line">Verdict: {getVerdict(item, isBest)}</div>
            </article>
          );
        })}
      </div>

      {/* ── Info sections ────────────────────────────────────────────────── */}
      <section id="hierarchy" className="info-section info-section-light">
        <div className="info-copy">
          <p className="section-kicker">Hierarchy</p>
          <h2>Dolby Vision and HDR from best to worst.</h2>
          <p className="quick-ranking">
            <b>Quick ranking:</b>{" "}
            <span className="rank r1">P7 FEL</span> {">"}
            <span className="rank r2"> P7 MEL</span> {">"}
            <span className="rank r3"> P8.1 <span className="approx">≈</span> P5 </span> {">"}
            <span className="rank r3"> P8.4</span> {">"}
            <span className="rank r5"> P8.2</span> {">"}
            <span className="rank r6"> P4</span> {">"}
            <span className="rank r7"> HDR10+</span> {">"}
            <span className="rank r9"> HDR10</span> {">"}
            <span className="rank r10"> HLG</span>
          </p>
        </div>

        <div className="hierarchy-grid">
          <div className="hierarchy-card">
            <h3>Dolby Vision</h3>
            <ol className="hierarchy-list">
              {DV_HIERARCHY.map((item) => (
                <li key={item.label}>
                  <div className="hierarchy-item-head">
                    <span>{item.label}</span>
                    <span className={item.cls}>{item.verdict}</span>
                  </div>
                  <p>{item.notes}</p>
                </li>
              ))}
            </ol>
          </div>

          <div className="hierarchy-card">
            <h3>HDR (non-DV)</h3>
            <ol className="hierarchy-list">
              {HDR_HIERARCHY.map((item) => (
                <li key={item.label}>
                  <div className="hierarchy-item-head">
                    <span>{item.label}</span>
                    <span className={item.cls}>{item.verdict}</span>
                  </div>
                  <p>{item.notes}</p>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </section>
    </div>
  );
}