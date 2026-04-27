import { type ChangeEvent, useRef, useState } from "react";
import axios from "axios";
import type { VideoData } from "./types";
import "./App.css";

const dolbyVisionHierarchy = [
  {
    label: "Profile 7 FEL (Full Enhancement Layer)",
    verdict: "Best",
    notes:
      "Full dual-layer Dolby Vision with BL, EL, and RPU. Highest fidelity and closest to the studio master when playback fully supports FEL.",
  },
  {
    label: "Profile 7 MEL (Minimal Enhancement Layer)",
    verdict: "Excellent",
    notes:
      "Dual-layer Dolby Vision with reduced enhancement-layer data. Still disc-grade quality and only slightly behind FEL.",
  },
  {
    label: "Profile 8.1",
    verdict: "Very Good",
    notes: "Single-layer Dolby Vision with HDR10-compatible base layer.",
  },
  {
    label: "Profile 8.4",
    verdict: "Very Good (niche/broadcast hybrid)",
    notes: "Single-layer Dolby Vision built on an HLG base.",
  },
  {
    label: "Profile 5",
    verdict: "Very Good",
    notes: "Single-layer Dolby Vision with no HDR10 fallback.",
  },
];

const hdrHierarchy = [
  {
    label: "HDR10+",
    verdict: "Very Good",
    notes: "Dynamic metadata HDR.",
  },
  {
    label: "HDR10",
    verdict: "Decent",
    notes: "Static metadata HDR.",
  },
  {
    label: "HLG",
    verdict: "Basic",
    notes: "Broadcast HDR.",
  },
  {
    label: "SDR",
    verdict: "Worst",
    notes: "No HDR.",
  },
];

const getToolStatusLabel = (status: string) => {
  if (status === "partial") return "Bounded";
  if (status === "ok") return "Ready";
  if (status === "error") return "Issue";
  return "Missing";
};

function toArray<T>(value: T | T[]): T[] {
  return Array.isArray(value) ? value : [value];
}

export default function App() {
  const [path, setPath] = useState("");
  const [data, setData] = useState<VideoData[]>([]);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [selectedFileName, setSelectedFileName] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isLightMode, setIsLightMode] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const browseFile = () => {
    fileInputRef.current?.click();
  };

  const handleFileSelect = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setError("");
    setPath("");
    setSelectedFile(file);
    setSelectedFileName(file.name);
    event.target.value = "";
  };

  const analyzeSelection = async () => {
    const trimmedPath = path.trim();

    if (!selectedFile && !trimmedPath) {
      setError("Paste a file path or choose a file with Browse.");
      setData([]);
      return;
    }

    setIsLoading(true);
    setError("");

    try {
      let results: VideoData[] = [];

      if (trimmedPath) {
        try {
          const res = await axios.get<VideoData[]>(
            `http://127.0.0.1:8000/scan-folder/?path=${encodeURIComponent(trimmedPath)}`
          );
          results = toArray(res.data);
        } catch {
          const res = await axios.get<VideoData[]>(
            `http://127.0.0.1:8000/analyze-path/?path=${encodeURIComponent(trimmedPath)}`
          );
          results = toArray(res.data);
        }
      } else if (selectedFile) {
        const formData = new FormData();
        formData.append("file", selectedFile);

        const res = await fetch("http://127.0.0.1:8000/analysis/", {
          method: "POST",
          body: formData,
        });

        if (!res.ok) {
          let detail = "Unable to analyze the selected file.";
          try {
            const payload = (await res.json()) as { detail?: unknown };
            if (payload?.detail) {
              detail = String(payload.detail);
            }
          } catch {
            // keep fallback
          }
          throw new Error(detail);
        }

        const payload = (await res.json()) as VideoData[] | VideoData;
        results = toArray(payload);
      }

      results = results
        .filter(Boolean)
        .sort((a, b) => {
          const scoreA = (a.score || 0) + (a.confidence_score || 0);
          const scoreB = (b.score || 0) + (b.confidence_score || 0);
          return scoreB - scoreA;
        });

      setData(results);
    } catch (err: unknown) {
      let message = "Failed to analyze.";

      if (axios.isAxiosError(err)) {
        message =
          (typeof err.response?.data?.detail === "string" && err.response.data.detail) ||
          err.message ||
          message;
      } else if (err instanceof Error) {
        message = err.message;
      }

      setError(message);
      setData([]);
    } finally {
      setIsLoading(false);
    }
  };

  const clearPath = () => {
    setPath("");
    setSelectedFile(null);
    setSelectedFileName("");
    setError("");
  };

  const bestCandidatePath = data[0]?.path;

  const getColor = (score: number) => {
    if (score >= 70) return "text-green-400";
    if (score >= 40) return "text-yellow-400";
    return "text-red-400";
  };

  const getVerdict = (item: VideoData, isBest: boolean) => {
    if (isBest) return "BEST CHOICE";
    if ((item.score || 0) >= 75 && (item.confidence_score || 0) >= 60) return "SAFE DV";
    if ((item.score || 0) < 40) return "AVOID";
    if ((item.confidence_score || 0) < 40) return "LOW CONFIDENCE";
    return "OK";
  };

  const combinedScore = (item: VideoData) => (item.score || 0) + (item.confidence_score || 0);

  return (
    <div className={`app-shell ${isLightMode ? "theme-light" : "theme-dark"}`}>
      <nav className="top-nav">
        <div className="nav-links">
          <a href="#hdr">HDR</a>
          <a href="#dolby-vision">Dolby Vision</a>
          <a href="#hierarchy">Hierarchy</a>
        </div>

        <button
          type="button"
          className="secondary-button"
          onClick={() => setIsLightMode((value) => !value)}
        >
          {isLightMode ? "Dark Mode" : "Light Mode"}
        </button>
      </nav>

      <section className="hero-section">
        <div className="hero-copy">
          <p className="eyebrow">Single-file inspection for HDR and Dolby Vision</p>
          <h1 className="app-title">
            <span>Video Metadata</span>
            <span>Analyzer</span>
          </h1>
          <p className="app-subtitle">
            Fast local inspection using MediaInfo, ffprobe, ffmpeg, and dovi_tool to rank the best file for playback.
          </p>
        </div>

        <div className="hero-panel">
          <div className="control-grid">
            <div className="path-row">
              <label className="input-label" htmlFor="video-path">
                Path
              </label>
              <form
                className="search-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void analyzeSelection();
                }}
              >
                <button type="submit" className="search-icon-button" aria-label="Analyze path">
                  <svg width={17} height={16} fill="none" xmlns="http://www.w3.org/2000/svg" role="img">
                    <path
                      d="M7.667 12.667A5.333 5.333 0 107.667 2a5.333 5.333 0 000 10.667zM14.334 14l-2.9-2.9"
                      stroke="currentColor"
                      strokeWidth="1.333"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </button>
                <input
                  id="video-path"
                  className="search-input"
                  placeholder="Type your path"
                  value={path}
                  onChange={(event) => {
                    setPath(event.target.value);
                    if (selectedFile) {
                      setSelectedFile(null);
                      setSelectedFileName("");
                    }
                  }}
                  type="text"
                />
                <button
                  className="search-reset"
                  type="button"
                  aria-label="Clear path"
                  onClick={clearPath}
                  disabled={!path && !selectedFileName}
                >
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    className="reset-icon"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </form>
            </div>

            <div className="choose-row">
              <span className="input-label">File</span>
              <div className="file-picker-row">
                <button onClick={browseFile} disabled={isLoading} className="secondary-button">
                  Choose File
                </button>
                <span className="helper-text">{selectedFileName || "No file chosen"}</span>
              </div>
            </div>
          </div>

          <div className="analyze-row">
            <button
              onClick={() => {
                void analyzeSelection();
              }}
              disabled={isLoading}
              className="primary-button"
            >
              {isLoading ? "Analyzing..." : "Analyze"}
            </button>
          </div>
        </div>
      </section>

      <input
        ref={fileInputRef}
        type="file"
        accept=".mkv,.mp4,.ts,.m2ts,.hevc,.h265,video/*"
        hidden
        onChange={handleFileSelect}
      />

      {error && <div className="error-box">{error}</div>}

      {data.length > 1 && (
        <section className="leaderboard">
          <h2>Best File Ranking</h2>
          <div className="leaderboard-list">
            {data.map((item, index) => (
              <div key={item.path} className="leaderboard-item">
                <span className="rank">#{item.batch_rank ?? index + 1}</span>
                <span className="name">{item.file}</span>
                <span className="score">
                  {item.score} / {item.confidence_score || 0} • {item.confidence_label || "Unknown"}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section id="hdr" className="info-section info-section-light">
        <div className="info-copy">
          <p className="section-kicker">HDR</p>
          <h2>Quick read on HDR delivery and compatibility.</h2>
          <p>
            The analyzer inspects HDR signaling, mastering data, stream bitrate, and compatibility hints without
            requiring you to decode raw metadata manually.
          </p>
        </div>
      </section>

      <section id="dolby-vision" className="info-section info-section-dark">
        <div className="info-copy">
          <p className="section-kicker">Dolby Vision</p>
          <h2>BL, EL, RPU, profile, and source clues in one pass.</h2>
          <p>
            The analyzer turns Dolby Vision metadata into readable signals so you can judge whether a file looks
            disc-grade, streaming-grade, or weakly compressed.
          </p>
        </div>
      </section>

      <section id="hierarchy" className="info-section info-section-light">
        <div className="info-copy">
          <p className="section-kicker">Hierarchy</p>
          <h2>Dolby Vision and HDR hierarchy from best to worst.</h2>
          <p className="quick-ranking">
            <b>Quick ranking:</b>{" "}
            <span className="rank r1">Profile 7 FEL</span> &gt;
            <span className="rank r2"> Profile 7 MEL</span> &gt;
            <span className="rank r3"> Profile 8.1</span> ≈
            <span className="rank r3"> Profile 5</span> &gt;
            <span className="rank r4"> Profile 8.4</span> &gt;
            <span className="rank r5"> Profile 8.2</span> &gt;
            <span className="rank r6"> Profile 4</span> &gt;
            <span className="rank r7"> HDR10+</span> &gt;
            <span className="rank r8"> legacy Profiles (3, 2, 1)</span> &gt;
            <span className="rank r9"> HDR10</span> &gt;
            <span className="rank r10"> HLG</span> &gt;
            <span className="rank r11"> SDR</span>
          </p>
        </div>

        <div className="hierarchy-grid">
          <div className="hierarchy-card">
            <h3>Dolby Vision</h3>
            <ol className="hierarchy-list">
              {dolbyVisionHierarchy.map((item) => (
                <li key={item.label}>
                  <div className="hierarchy-item-head">
                    <span>{item.label}</span>
                    <span>{item.verdict}</span>
                  </div>
                  <p>{item.notes}</p>
                </li>
              ))}
            </ol>
          </div>

          <div className="hierarchy-card">
            <h3>HDR</h3>
            <ol className="hierarchy-list">
              {hdrHierarchy.map((item) => (
                <li key={item.label}>
                  <div className="hierarchy-item-head">
                    <span>{item.label}</span>
                    <span>{item.verdict}</span>
                  </div>
                  <p>{item.notes}</p>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </section>

      <div className="results-grid">
        {data.map((item, idx) => {
          const isBest = item.path === bestCandidatePath;
          const scoreWidth = Math.max(0, Math.min(100, item.score || 0));
          const confidenceWidth = Math.max(0, Math.min(100, item.confidence_score || 0));

          return (
            <article key={item.path || idx} className={`result-card ${isBest ? "result-card-best" : ""}`}>
              <div className="result-header">
                <div className="result-header-copy">
                  <p className="result-kicker">{item.source}</p>
                  <h2 className="result-file">{item.file}</h2>
                  <p className="result-path">{item.path}</p>
                </div>

                <div className="score-panel">
                  <span className="score-label">Score</span>
                  <span className={`score-value ${getColor(item.score)}`}>{item.score}</span>
                  <span className="meta-line">Confidence {item.confidence_score || 0}/100</span>
                </div>
              </div>

              <div className="tag-row">
                <span className="tag tag-dv">DV {item.dv_profile}</span>
                <span className="tag tag-source">{item.audio ?? "Audio Unknown"}</span>
                <span className="tag tag-bitrate">{item.bitrate_mbps.toFixed(2)} Mbps</span>
                <span className="tag tag-runtime">{item.duration_min?.toFixed(1) ?? "?"} min</span>
                <span className="tag tag-source">{item.confidence_label || "Unknown confidence"}</span>
              </div>

              {item.quick_summary && <div className="summary-line">{item.quick_summary}</div>}
              {item.recommendation && <div className="detail-line">{item.recommendation}</div>}
              {item.insights && <div className="insight-line">{item.insights}</div>}

              <div className="result-submeta">
                <span>Analyzer chain: {item.dv_tool ?? "unknown"}</span>
                <span>
                  BL {item.bl ?? "Unknown"} / EL {item.el ?? "Unknown"} / RPU {item.rpu ?? "Unknown"}
                </span>
                <span>TV: {item.tv_playback ?? "Unknown"}</span>
              </div>

              <div className="quality-bars">
                <div className="bar">
                  <span>Quality</span>
                  <div className="bar-track">
                    <div className="bar-fill" style={{ width: `${scoreWidth}%` }} />
                  </div>
                </div>

                <div className="bar">
                  <span>Confidence</span>
                  <div className="bar-track">
                    <div className="bar-fill secondary" style={{ width: `${confidenceWidth}%` }} />
                  </div>
                </div>
              </div>

              <div className="result-sections">
                <section className="fact-panel">
                  <div className="fact-panel-head">
                    <p className="fact-kicker">Dolby Vision</p>
                    <h3>Signal readout</h3>
                  </div>
                  <dl className="fact-list">
                    {(item.signal_facts ?? []).map((fact) => (
                      <div className="fact-row" key={`${item.file}-signal-${fact.label}`}>
                        <dt>{fact.label}</dt>
                        <dd>{fact.value}</dd>
                      </div>
                    ))}
                  </dl>
                </section>

                <section className="fact-panel">
                  <div className="fact-panel-head">
                    <p className="fact-kicker">Media</p>
                    <h3>Container and stream facts</h3>
                  </div>
                  <dl className="fact-list">
                    {(item.media_facts ?? []).map((fact) => (
                      <div className="fact-row" key={`${item.file}-media-${fact.label}`}>
                        <dt>{fact.label}</dt>
                        <dd>{fact.value}</dd>
                      </div>
                    ))}
                  </dl>
                </section>
              </div>

              <section className="toolchain-section">
                <div className="fact-panel-head">
                  <p className="fact-kicker">Toolchain</p>
                  <h3>What each tool contributed</h3>
                </div>

                <div className="tool-grid">
                  {(item.tool_reports ?? []).map((report) => (
                    <article key={`${item.file}-${report.name}`} className={`tool-card tool-card-${report.status}`}>
                      <div className="tool-card-head">
                        <h4>{report.name}</h4>
                        <span className={`tool-status tool-status-${report.status}`}>
                          {getToolStatusLabel(report.status)}
                        </span>
                      </div>
                      <p className="tool-headline">{report.headline}</p>
                      {report.details.length > 0 && (
                        <ul className="tool-detail-list">
                          {report.details.map((detail, detailIndex) => (
                            <li key={`${report.name}-${detailIndex}`}>{detail}</li>
                          ))}
                        </ul>
                      )}
                    </article>
                  ))}
                </div>
              </section>

              {data.length > 1 && isBest && <div className="best-badge">Top score in this batch</div>}

              <a href={encodeURI(`file:///${item.path.replace(/\\/g, "/")}`)} className="open-link">
                Open File
              </a>

              <div className="meta-line">Verdict: {getVerdict(item, isBest)}</div>
              <div className="meta-line">Combined rank score: {combinedScore(item)}</div>
            </article>
          );
        })}
      </div>
    </div>
  );
}