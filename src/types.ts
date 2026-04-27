export interface FactItem {
  label: string;
  value: string;
}

export interface ToolReport {
  name: string;
  status: "ok" | "partial" | "unavailable" | "error";
  headline: string;
  details: string[];
}

export interface VideoData {
  path: string;
  file: string;

  dv_profile: string;
  layer_variant?: string;
  layer_reason?: string;

  el: string;
  bl?: string;
  rpu?: string;
  dv_tool?: string;

  hdr: string;
  bitrate_mbps: number;
  bit_depth?: number | null;
  color_range?: string;
  duration_min?: number;

  source: string;
  audio?: string;
  audio_details?: string;
  audio_score?: number;

  score: number;
  confidence_score?: number;
  confidence_label?: string;

  tv_profile_supported?: string;
  tv_el_usable?: string;
  tv_container_compatibility?: string;
  tv_playback_note?: string;
  tv_playback?: string;

  quick_summary?: string;
  insights?: string;
  recommendation?: string;

  batch_rank?: number;

  signal_facts: FactItem[];
  media_facts: FactItem[];
  tool_reports: ToolReport[];
}