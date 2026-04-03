# mega_code/client/enhancement_viewer.py
"""HTTP server-based review viewer for skill-enhance results.

Serves a self-contained HTML page and handles feedback POSTs from the browser
so the user doesn't need to manually copy files.

Usage::

    python -m mega_code.client.enhancement_viewer <iteration-dir> \\
        --skill-name my-skill \\
        [--benchmark <path>] \\
        [--previous-workspace <path>] \\
        [--port 3117]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

_VIEWER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Skill Enhancement Review</title>
  <style>
    :root {
      --bg: #020307;
      --bg-soft: #080b14;
      --surface: rgba(63, 63, 70, 0.12);
      --surface-strong: rgba(63, 63, 70, 0.2);
      --surface-muted: rgba(63, 63, 70, 0.08);
      --border: #3f3f46;
      --border-soft: rgba(119, 160, 255, 0.2);
      --text: #ffffff;
      --text-secondary: #c1c1cc;
      --text-muted: #a1a1aa;
      --text-faint: #71717a;
      --accent: #77a0ff;
      --accent-warm: #fff3b4;
      --success: #77a0ff;
      --danger: #f87171;
      --pass-bg: rgba(119, 160, 255, 0.16);
      --fail-bg: rgba(248, 113, 113, 0.14);
      --radius: 18px;
      --radius-sm: 12px;
      --shadow: 0 18px 60px rgba(0, 0, 0, 0.28);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { min-height: 100%; }
    body {
      font-family: Inter, 'Segoe UI', sans-serif;
      background:
        radial-gradient(circle at top, rgba(119,160,255,0.09) 0%, transparent 38%),
        linear-gradient(180deg, #05070d 0%, #020307 100%);
      color: var(--text);
    }
    a { color: inherit; }
    button, textarea { font: inherit; }
    .page {
      width: min(1280px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }
    .hero {
      position: relative;
      overflow: hidden;
      border: 1px solid var(--border-soft);
      border-radius: 28px;
      background:
        linear-gradient(135deg, rgba(255,243,180,0.08), rgba(255,255,255,0.03) 44%, rgba(119,160,255,0.12)),
        rgba(2, 3, 7, 0.92);
      box-shadow: var(--shadow);
      padding: 28px;
      margin-bottom: 18px;
    }
    .hero::before {
      content: "";
      position: absolute;
      inset: auto auto -160px -120px;
      width: 420px;
      height: 420px;
      background: radial-gradient(circle, rgba(119,160,255,0.14) 0%, transparent 70%);
      pointer-events: none;
    }
    .hero::after {
      content: "";
      position: absolute;
      top: -180px;
      right: -120px;
      width: 420px;
      height: 420px;
      background: radial-gradient(circle, rgba(255,243,180,0.09) 0%, transparent 68%);
      pointer-events: none;
    }
    .hero-top {
      position: relative;
      z-index: 1;
      display: flex;
      gap: 18px;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 24px;
      flex-wrap: wrap;
    }
    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid rgba(119,160,255,0.45);
      background: rgba(2,3,7,0.32);
      border-radius: 999px;
      padding: 6px 14px;
      color: var(--accent);
      font-size: 12px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      margin-bottom: 14px;
    }
    .eyebrow::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: linear-gradient(135deg, var(--accent-warm), var(--accent));
      box-shadow: 0 0 14px rgba(119,160,255,0.45);
    }
    .hero h1 {
      font-size: clamp(1.8rem, 2.6vw, 3rem);
      line-height: 1.05;
      font-weight: 800;
      margin-bottom: 10px;
      max-width: 760px;
    }
    .hero-subtitle {
      color: var(--text-secondary);
      font-size: 0.98rem;
      line-height: 1.7;
      max-width: 760px;
    }
    .hero-meta {
      display: grid;
      grid-template-columns: repeat(2, minmax(140px, 1fr));
      gap: 12px;
      min-width: 280px;
    }
    .meta-card {
      border: 1px solid rgba(119,160,255,0.22);
      background: rgba(2,3,7,0.42);
      border-radius: 16px;
      padding: 14px 16px;
    }
    .meta-label {
      color: var(--text-muted);
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 6px;
    }
    .meta-value {
      font-size: 1rem;
      color: var(--text);
      font-weight: 700;
    }
    .summary-grid {
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }
    .summary-card {
      border: 1px solid rgba(63,63,70,0.88);
      background: rgba(63,63,70,0.12);
      border-radius: 18px;
      padding: 18px;
      min-height: 110px;
    }
    .summary-card.highlight {
      background:
        linear-gradient(135deg, rgba(255,243,180,0.08), rgba(255,255,255,0.02) 50%, rgba(119,160,255,0.14)),
        rgba(63,63,70,0.14);
      border-color: rgba(119,160,255,0.32);
    }
    .summary-label {
      color: var(--text-muted);
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 10px;
    }
    .summary-value {
      font-size: 1.8rem;
      font-weight: 800;
      line-height: 1;
      margin-bottom: 10px;
    }
    .summary-value.gradient {
      background-image: linear-gradient(to right, #fff3b4, #ffffff 50%, #77a0ff);
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
    }
    .summary-detail {
      color: var(--text-secondary);
      font-size: 0.86rem;
      line-height: 1.5;
    }
    .tabbar {
      display: flex;
      gap: 12px;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }
    .tab {
      border: 1px solid var(--border);
      background: rgba(63,63,70,0.08);
      color: var(--text-muted);
      border-radius: 999px;
      padding: 10px 16px;
      cursor: pointer;
      font-size: 0.88rem;
      transition: 120ms ease;
    }
    .tab.active {
      border-color: rgba(119,160,255,0.45);
      background: rgba(119,160,255,0.16);
      color: var(--text);
      box-shadow: inset 0 0 0 1px rgba(119,160,255,0.12);
    }
    .tab:hover { color: var(--text); }
    .panel { display: none; }
    .panel.active { display: block; }
    .section {
      border: 1px solid var(--border);
      background: var(--surface);
      border-radius: var(--radius);
      margin-bottom: 18px;
      overflow: hidden;
    }
    .section-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 14px 18px;
      border-bottom: 1px solid rgba(63,63,70,0.72);
      background: rgba(63,63,70,0.06);
      color: var(--text-secondary);
      font-size: 0.76rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      font-weight: 700;
    }
    .section-body { padding: 18px; }
    .prompt-text {
      white-space: pre-wrap;
      color: var(--text);
      line-height: 1.7;
      font-size: 0.98rem;
    }
    .case-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }
    .case-index {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      color: var(--text-secondary);
      font-size: 0.92rem;
    }
    .case-index strong {
      color: var(--text);
      font-size: 1.08rem;
    }
    .case-pills {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid rgba(63,63,70,0.9);
      background: rgba(63,63,70,0.1);
      color: var(--text-secondary);
      font-size: 0.82rem;
    }
    .pill strong { color: var(--text); font-weight: 700; }
    .comparison-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
      margin-bottom: 18px;
    }
    .output-card {
      border: 1px solid var(--border);
      background:
        linear-gradient(180deg, rgba(63,63,70,0.12), rgba(63,63,70,0.08)),
        rgba(2,3,7,0.3);
      border-radius: 20px;
      padding: 18px;
      min-width: 0;
    }
    .output-card.skill { border-color: rgba(119,160,255,0.34); }
    .output-card.baseline { border-color: rgba(255,243,180,0.2); }
    .output-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 16px;
    }
    .output-title {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .output-title h3 {
      font-size: 1rem;
      font-weight: 700;
      color: var(--text);
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 10px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      width: fit-content;
    }
    .badge-skill {
      color: var(--accent);
      border: 1px solid rgba(119,160,255,0.35);
      background: rgba(119,160,255,0.13);
    }
    .badge-baseline {
      color: var(--accent-warm);
      border: 1px solid rgba(255,243,180,0.24);
      background: rgba(255,243,180,0.08);
    }
    .badge-pass {
      color: var(--accent);
      background: var(--pass-bg);
      border: 1px solid rgba(119,160,255,0.22);
    }
    .badge-fail {
      color: var(--danger);
      background: var(--fail-bg);
      border: 1px solid rgba(248,113,113,0.22);
    }
    .score-card {
      min-width: 124px;
      border-radius: 16px;
      padding: 12px 14px;
      background: rgba(2,3,7,0.42);
      border: 1px solid rgba(63,63,70,0.88);
      text-align: right;
    }
    .score-label {
      color: var(--text-muted);
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 6px;
    }
    .score-value {
      color: var(--text);
      font-size: 1.25rem;
      font-weight: 800;
    }
    .score-sub {
      color: var(--text-faint);
      font-size: 0.78rem;
      margin-top: 3px;
    }
    .metric-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .metric-chip {
      border: 1px solid rgba(63,63,70,0.85);
      background: rgba(63,63,70,0.08);
      border-radius: 14px;
      padding: 10px 12px;
    }
    .metric-chip .label {
      color: var(--text-muted);
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 4px;
    }
    .metric-chip .value {
      color: var(--text);
      font-size: 0.94rem;
      font-weight: 700;
    }
    .output-text {
      white-space: pre-wrap;
      word-break: break-word;
      color: var(--text-secondary);
      font-size: 0.84rem;
      line-height: 1.6;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      max-height: 360px;
      overflow-y: auto;
      padding: 14px;
      background: rgba(2,3,7,0.6);
      border: 1px solid rgba(63,63,70,0.9);
      border-radius: 14px;
    }
    .grading-wrap { margin-top: 14px; }
    .collapsible-toggle {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      color: var(--text-secondary);
      font-size: 0.84rem;
      font-weight: 600;
      user-select: none;
    }
    .collapsible-toggle:hover { color: var(--text); }
    .arrow {
      transition: transform 120ms ease;
      color: var(--accent);
    }
    .arrow.open { transform: rotate(90deg); }
    .collapsible-content { display: none; margin-top: 12px; }
    .collapsible-content.open { display: block; }
    .grading-item {
      display: flex;
      gap: 10px;
      align-items: flex-start;
      padding: 10px 0;
      border-bottom: 1px solid rgba(63,63,70,0.55);
    }
    .grading-item:last-child { border-bottom: none; }
    .grading-expectation {
      flex: 1;
      color: var(--text);
      font-size: 0.88rem;
      line-height: 1.5;
    }
    .grading-evidence {
      color: var(--text-muted);
      font-size: 0.8rem;
      line-height: 1.5;
      margin-top: 4px;
    }
    .feedback-grid {
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 18px;
      margin-bottom: 18px;
    }
    .feedback-textarea {
      width: 100%;
      min-height: 180px;
      resize: vertical;
      border: 1px solid rgba(119,160,255,0.22);
      border-radius: 16px;
      background: rgba(2,3,7,0.55);
      color: var(--text);
      padding: 14px 16px;
      line-height: 1.6;
    }
    .feedback-textarea:focus {
      outline: none;
      border-color: rgba(119,160,255,0.42);
      box-shadow: 0 0 0 3px rgba(119,160,255,0.08);
    }
    .side-note {
      border: 1px solid rgba(63,63,70,0.9);
      background: rgba(63,63,70,0.08);
      border-radius: 16px;
      padding: 16px;
      margin-bottom: 14px;
    }
    .side-note:last-child { margin-bottom: 0; }
    .side-note-label {
      color: var(--text-muted);
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }
    .side-note-body {
      color: var(--text-secondary);
      font-size: 0.86rem;
      line-height: 1.6;
      white-space: pre-wrap;
    }
    .benchmark-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 18px;
    }
    .overview-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }
    .overview-card {
      border: 1px solid rgba(63,63,70,0.9);
      background: rgba(63,63,70,0.09);
      border-radius: 18px;
      padding: 18px;
    }
    .overview-card .label {
      color: var(--text-muted);
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }
    .overview-card .value {
      color: var(--text);
      font-size: 1.55rem;
      font-weight: 800;
      line-height: 1.05;
      margin-bottom: 8px;
    }
    .overview-card .sub {
      color: var(--text-secondary);
      font-size: 0.86rem;
      line-height: 1.5;
    }
    .benchmark-list {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .bench-row {
      border: 1px solid rgba(63,63,70,0.9);
      background: rgba(63,63,70,0.08);
      border-radius: 18px;
      padding: 16px;
    }
    .bench-row-top {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 12px;
      align-items: flex-start;
    }
    .bench-title {
      color: var(--text);
      font-weight: 700;
      margin-bottom: 4px;
      line-height: 1.45;
    }
    .bench-subtitle {
      color: var(--text-faint);
      font-size: 0.82rem;
    }
    .delta-pill {
      flex-shrink: 0;
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 0.84rem;
      font-weight: 700;
      border: 1px solid rgba(63,63,70,0.9);
      background: rgba(63,63,70,0.08);
    }
    .delta-positive {
      color: var(--accent);
      border-color: rgba(119,160,255,0.28);
      background: rgba(119,160,255,0.12);
    }
    .delta-negative {
      color: var(--danger);
      border-color: rgba(248,113,113,0.25);
      background: rgba(248,113,113,0.12);
    }
    .delta-neutral { color: var(--text-muted); }
    .bench-bars {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .bench-metric {
      border-radius: 14px;
      background: rgba(2,3,7,0.44);
      border: 1px solid rgba(63,63,70,0.86);
      padding: 12px;
    }
    .bench-metric-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
      color: var(--text-secondary);
      font-size: 0.82rem;
    }
    .bench-track {
      width: 100%;
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(63,63,70,0.35);
    }
    .bench-fill {
      height: 100%;
      border-radius: inherit;
    }
    .fill-skill {
      background: linear-gradient(to right, #fff3b4, #ffffff 50%, #77a0ff);
    }
    .fill-baseline {
      background: #52525b;
    }
    .nav {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      flex-wrap: wrap;
      margin: 22px 0 18px;
    }
    .nav-cluster {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .nav-btn, .submit-btn {
      border-radius: 999px;
      cursor: pointer;
      transition: 120ms ease;
    }
    .nav-btn {
      padding: 10px 18px;
      border: 1px solid rgba(63,63,70,0.95);
      background: rgba(63,63,70,0.09);
      color: var(--text);
    }
    .nav-btn:hover { border-color: rgba(119,160,255,0.24); }
    .nav-btn:disabled {
      opacity: 0.42;
      cursor: default;
    }
    .submit-btn {
      border: none;
      padding: 12px 22px;
      color: #18181b;
      font-weight: 800;
      background: linear-gradient(to right, #fff3b4, #ffffff 50%, #77a0ff);
      box-shadow: 0 8px 24px rgba(119,160,255,0.16);
    }
    .submit-btn:hover { filter: brightness(0.98); }
    .submit-btn:disabled {
      opacity: 0.48;
      cursor: default;
      box-shadow: none;
      filter: none;
    }
    .submit-status {
      color: var(--accent);
      display: none;
      font-size: 0.86rem;
    }
    .empty-state {
      border: 1px dashed rgba(63,63,70,0.9);
      border-radius: 20px;
      padding: 44px 20px;
      text-align: center;
      color: var(--text-muted);
      background: rgba(63,63,70,0.05);
    }
    @media (max-width: 1080px) {
      .summary-grid, .overview-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .comparison-grid, .feedback-grid, .bench-bars { grid-template-columns: 1fr; }
    }
    @media (max-width: 760px) {
      .page { width: min(100vw - 20px, 1280px); padding-top: 14px; }
      .hero { padding: 22px 18px; border-radius: 22px; }
      .hero-meta { grid-template-columns: 1fr 1fr; min-width: 0; width: 100%; }
      .summary-grid, .overview-grid { grid-template-columns: 1fr; }
      .section-body, .section-header { padding-left: 14px; padding-right: 14px; }
    }
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <div class="hero-top">
        <div>
          <div class="eyebrow">Enhancement Review</div>
          <h1 id="skill-title"></h1>
          <p class="hero-subtitle">
            Review case-by-case output quality, compare with baseline, and capture
            revision feedback before submitting the full pass.
          </p>
        </div>
        <div class="hero-meta">
          <div class="meta-card">
            <div class="meta-label">Progress</div>
            <div class="meta-value" id="progress-text">0 / 0</div>
          </div>
          <div class="meta-card">
            <div class="meta-label">Iteration</div>
            <div class="meta-value" id="iteration-text">Iteration 1</div>
          </div>
        </div>
      </div>
      <div class="summary-grid" id="summary-grid"></div>
    </section>

    <div class="tabbar">
      <button class="tab active" data-panel="outputs">Case Review</button>
      <button class="tab" data-panel="benchmark">Benchmark Summary</button>
    </div>

    <main>
      <section class="panel active" id="panel-outputs">
        <div id="test-case-content"></div>
        <div class="nav">
          <div class="nav-cluster">
            <button class="nav-btn" id="prev-btn" onclick="navigate(-1)">&larr; Previous</button>
            <button class="nav-btn" id="next-btn" onclick="navigate(1)">Next &rarr;</button>
          </div>
          <div class="nav-cluster">
            <span id="nav-counter" class="pill"></span>
            <button class="submit-btn" id="submit-btn" onclick="submitFeedback()" disabled>Submit</button>
            <span class="submit-status" id="submit-status"></span>
          </div>
        </div>
      </section>

      <section class="panel" id="panel-benchmark">
        <div id="benchmark-content"></div>
      </section>
    </main>
  </div>

  <script>
    /*__EMBEDDED_DATA__*/

    let currentIndex = 0;
    const feedbackMap = {};
    const data = EMBEDDED_DATA;
    const testCases = data.test_cases || [];
    const abOutputs = data.ab_outputs || [];
    const gradings = data.gradings || [];
    const previousData = data.previous || null;
    const visitedCases = new Set(testCases.length > 0 ? [0] : []);

    document.getElementById('skill-title').textContent = data.skill_name || 'Unknown Skill';
    document.getElementById('iteration-text').textContent =
      `Iteration ${data.iteration || 1}`;

    document.querySelectorAll('.tab').forEach(tab => {
      tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('panel-' + tab.dataset.panel).classList.add('active');
      });
    });

    document.addEventListener('keydown', e => {
      if (e.target.tagName === 'TEXTAREA') return;
      if (e.key === 'ArrowLeft') navigate(-1);
      if (e.key === 'ArrowRight') navigate(1);
    });

    function navigate(delta) {
      saveFeedbackState();
      currentIndex = Math.max(0, Math.min(testCases.length - 1, currentIndex + delta));
      visitedCases.add(currentIndex);
      renderTestCase();
    }

    function saveFeedbackState() {
      const ta = document.getElementById('feedback-input');
      if (ta) feedbackMap[currentIndex] = ta.value;
    }

    function escapeHtml(s) {
      const div = document.createElement('div');
      div.textContent = s == null ? '' : String(s);
      return div.innerHTML;
    }

    function formatPercent(n) {
      const sign = n > 0 ? '+' : '';
      return `${sign}${n}%`;
    }

    function formatTokens(n) {
      const value = Number(n || 0);
      if (!value) return '0';
      if (value >= 1000000) return `${(value / 1000000).toFixed(2).replace(/\.00$/, '')}M`;
      if (value >= 1000) return `${Math.round(value / 100) / 10}K`.replace('.0K', 'K');
      return String(value);
    }

    function getCaseMetrics(index) {
      const tc = testCases[index] || {};
      const gr = gradings[index] || {};
      const ab = abOutputs[index] || {};
      const withGr = gr.with_skill_gradings || [];
      const baseGr = gr.baseline_gradings || [];
      const expectationCount = Math.max(
        (tc.expectations || []).length,
        withGr.length,
        baseGr.length,
        1,
      );
      const withPassed = withGr.filter(g => g.passed).length;
      const basePassed = baseGr.filter(g => g.passed).length;
      const withPct = Math.round((withPassed / expectationCount) * 100);
      const basePct = Math.round((basePassed / expectationCount) * 100);
      const delta = withPct - basePct;
      const withTokens = Number(ab.with_skill_tokens || 0);
      const baseTokens = Number(ab.baseline_tokens || 0);
      const tokenDeltaPct = baseTokens > 0
        ? Math.round((1 - withTokens / baseTokens) * 100)
        : 0;
      return {
        expectationCount,
        withPassed,
        basePassed,
        withPct,
        basePct,
        delta,
        withTokens,
        baseTokens,
        tokenDeltaPct,
      };
    }

    function computeOverallMetrics() {
      if (testCases.length === 0) {
        return {
          totalCases: 0,
          avgWith: 0,
          avgBase: 0,
          perfIncrease: 0,
          tokenSavings: 0,
          totalWithTokens: 0,
          totalBaseTokens: 0,
          reviewedCount: 0,
        };
      }

      let totalWith = 0;
      let totalBase = 0;
      let totalWithTokens = 0;
      let totalBaseTokens = 0;
      let reviewedCount = 0;

      for (let i = 0; i < testCases.length; i++) {
        const metrics = getCaseMetrics(i);
        totalWith += metrics.withPct;
        totalBase += metrics.basePct;
        totalWithTokens += metrics.withTokens;
        totalBaseTokens += metrics.baseTokens;
        if ((feedbackMap[i] || '').trim()) reviewedCount += 1;
      }

      const avgWith = Math.round(totalWith / testCases.length);
      const avgBase = Math.round(totalBase / testCases.length);
      return {
        totalCases: testCases.length,
        avgWith,
        avgBase,
        perfIncrease: avgWith - avgBase,
        tokenSavings: totalBaseTokens > 0
          ? Math.round((1 - totalWithTokens / totalBaseTokens) * 100)
          : 0,
        totalWithTokens,
        totalBaseTokens,
        reviewedCount,
      };
    }

    function getDeltaClass(delta) {
      if (delta > 0) return 'delta-positive';
      if (delta < 0) return 'delta-negative';
      return 'delta-neutral';
    }

    function renderGradings(gradingsList, label) {
      if (!gradingsList || gradingsList.length === 0) return '';
      const passed = gradingsList.filter(g => g.passed).length;
      const total = gradingsList.length;
      let html = '<div class="grading-wrap">';
      html += `<div class="collapsible-toggle" onclick="this.nextElementSibling.classList.toggle('open');this.querySelector('.arrow').classList.toggle('open')">`;
      html += `<span class="arrow">&#9654;</span>`;
      html += `<span>${escapeHtml(label)}: ${passed}/${total} passed</span>`;
      html += '</div>';
      html += '<div class="collapsible-content">';
      for (const g of gradingsList) {
        html += '<div class="grading-item">';
        html += `<span class="badge ${g.passed ? 'badge-pass' : 'badge-fail'}">${g.passed ? 'Pass' : 'Fail'}</span>`;
        html += `<div class="grading-expectation">${escapeHtml(g.expectation || g.text || '')}`;
        if (g.evidence) {
          html += `<div class="grading-evidence">${escapeHtml(g.evidence)}</div>`;
        }
        html += '</div></div>';
      }
      html += '</div></div>';
      return html;
    }

    function renderSummary() {
      const metrics = computeOverallMetrics();
      const completion = metrics.totalCases > 0
        ? Math.round((metrics.reviewedCount / metrics.totalCases) * 100)
        : 0;

      document.getElementById('summary-grid').innerHTML = `
        <div class="summary-card highlight">
          <div class="summary-label">Performance Increase</div>
          <div class="summary-value gradient">${formatPercent(metrics.perfIncrease)}</div>
          <div class="summary-detail">With skill ${metrics.avgWith}% vs baseline ${metrics.avgBase}% across all reviewed cases.</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">Token Savings</div>
          <div class="summary-value">${formatPercent(metrics.tokenSavings)}</div>
          <div class="summary-detail">${formatTokens(metrics.totalWithTokens)} with skill vs ${formatTokens(metrics.totalBaseTokens)} baseline tokens.</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">Coverage</div>
          <div class="summary-value">${metrics.totalCases}</div>
          <div class="summary-detail">Total benchmark cases in this enhancement pass.</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">Feedback Completion</div>
          <div class="summary-value">${completion}%</div>
          <div class="summary-detail">${metrics.reviewedCount} of ${metrics.totalCases} cases currently have review notes.</div>
        </div>
      `;
    }

    function updateSubmitState() {
      const submitBtn = document.getElementById('submit-btn');
      if (!submitBtn) return;
      const allCasesVisited = testCases.length > 0 && visitedCases.size >= testCases.length;
      const onLastCase = currentIndex === testCases.length - 1;
      const canSubmit = allCasesVisited && onLastCase;
      submitBtn.disabled = !canSubmit;
      submitBtn.title = canSubmit
        ? 'Submit the full review pass'
        : 'Use Next to review every case before submitting';
    }

    function renderTestCase() {
      const container = document.getElementById('test-case-content');
      if (testCases.length === 0) {
        container.innerHTML = '<div class="empty-state">No test cases found.</div>';
        document.getElementById('progress-text').textContent = '0 / 0';
        return;
      }

      const tc = testCases[currentIndex];
      const ab = abOutputs[currentIndex] || {};
      const gr = gradings[currentIndex] || {};
      const fb = feedbackMap[currentIndex] || '';
      const metrics = getCaseMetrics(currentIndex);
      const prevAb = previousData && previousData.ab_outputs
        ? previousData.ab_outputs[currentIndex]
        : null;
      const prevFeedback = previousData && previousData.feedback
        ? previousData.feedback[currentIndex]
        : null;

      let html = '';
      html += '<div class="case-toolbar">';
      html += `<div class="case-index"><strong>Case ${currentIndex + 1}</strong><span>of ${testCases.length}</span></div>`;
      html += '<div class="case-pills">';
      html += `<span class="pill">Score delta <strong>${formatPercent(metrics.delta)}</strong></span>`;
      html += `<span class="pill">With skill <strong>${metrics.withPassed}/${metrics.expectationCount}</strong></span>`;
      html += `<span class="pill">Baseline <strong>${metrics.basePassed}/${metrics.expectationCount}</strong></span>`;
      html += `<span class="pill">Tokens <strong>${formatPercent(metrics.tokenDeltaPct)}</strong></span>`;
      html += '</div></div>';

      html += '<div class="section">';
      html += '<div class="section-header"><span>Prompt</span><span>Task under enhancement review</span></div>';
      html += `<div class="section-body"><div class="prompt-text">${escapeHtml(tc.task || ab.task || '')}</div></div>`;
      html += '</div>';

      html += '<div class="comparison-grid">';

      html += '<div class="output-card skill">';
      html += '<div class="output-head">';
      html += '<div class="output-title">';
      html += '<span class="badge badge-skill">With Skill</span>';
      html += '<h3>Generated Output</h3>';
      html += '</div>';
      html += `<div class="score-card"><div class="score-label">Pass Rate</div><div class="score-value">${metrics.withPct}%</div><div class="score-sub">${metrics.withPassed}/${metrics.expectationCount} expectations</div></div>`;
      html += '</div>';
      html += '<div class="metric-row">';
      html += `<div class="metric-chip"><div class="label">Token Use</div><div class="value">${formatTokens(metrics.withTokens)}</div></div>`;
      html += `<div class="metric-chip"><div class="label">Performance Increase</div><div class="value">${formatPercent(metrics.delta)}</div></div>`;
      html += '</div>';
      html += `<div class="output-text">${escapeHtml(ab.with_skill_output || '(no output)')}</div>`;
      html += renderGradings(gr.with_skill_gradings, 'With-skill grading');
      html += '</div>';

      html += '<div class="output-card baseline">';
      html += '<div class="output-head">';
      html += '<div class="output-title">';
      html += '<span class="badge badge-baseline">Baseline</span>';
      html += '<h3>Reference Output</h3>';
      html += '</div>';
      html += `<div class="score-card"><div class="score-label">Pass Rate</div><div class="score-value">${metrics.basePct}%</div><div class="score-sub">${metrics.basePassed}/${metrics.expectationCount} expectations</div></div>`;
      html += '</div>';
      html += '<div class="metric-row">';
      html += `<div class="metric-chip"><div class="label">Token Use</div><div class="value">${formatTokens(metrics.baseTokens)}</div></div>`;
      html += `<div class="metric-chip"><div class="label">Performance Gap</div><div class="value">${formatPercent(-metrics.delta)}</div></div>`;
      html += '</div>';
      html += `<div class="output-text">${escapeHtml(ab.baseline_output || '(no output)')}</div>`;
      html += renderGradings(gr.baseline_gradings, 'Baseline grading');
      html += '</div>';

      html += '</div>';

      html += '<div class="feedback-grid">';
      html += '<div class="section">';
      html += '<div class="section-header"><span>Your Feedback (Optional)</span><span>Saved locally on submit</span></div>';
      html += '<div class="section-body">';
      html += `<textarea class="feedback-textarea" id="feedback-input" placeholder="Call out regressions, weak evidence, prompt issues, or concrete edits for the next iteration...">${escapeHtml(fb)}</textarea>`;
      html += '</div></div>';

      html += '<div>';
      if (prevAb) {
        html += '<div class="side-note">';
        html += '<div class="side-note-label">Previous Iteration Output</div>';
        html += '<div class="collapsible-toggle" onclick="this.nextElementSibling.classList.toggle(\'open\');this.querySelector(\'.arrow\').classList.toggle(\'open\')">';
        html += '<span class="arrow">&#9654;</span><span>Show previous with-skill output</span></div>';
        html += `<div class="collapsible-content"><div class="output-text">${escapeHtml(prevAb.with_skill_output || '(no output)')}</div></div>`;
        html += '</div>';
      }
      if (prevFeedback) {
        html += '<div class="side-note">';
        html += '<div class="side-note-label">Previous Feedback</div>';
        html += `<div class="side-note-body">${escapeHtml(prevFeedback)}</div>`;
        html += '</div>';
      }
      if (!prevAb && !prevFeedback) {
        html += '<div class="side-note">';
        html += '<div class="side-note-label">Iteration Context</div>';
        html += '<div class="side-note-body">No previous iteration data is attached for this case.</div>';
        html += '</div>';
      }
      html += '</div></div>';

      container.innerHTML = html;
      document.getElementById('nav-counter').textContent = `${currentIndex + 1} / ${testCases.length}`;
      document.getElementById('prev-btn').disabled = currentIndex === 0;
      document.getElementById('next-btn').disabled = currentIndex === testCases.length - 1;
      document.getElementById('progress-text').textContent = `Case ${currentIndex + 1} / ${testCases.length}`;
      updateSubmitState();
      renderSummary();
    }

    function renderBenchmark() {
      const container = document.getElementById('benchmark-content');
      if (testCases.length === 0) {
        container.innerHTML = '<div class="empty-state">No benchmark data.</div>';
        return;
      }

      const metrics = computeOverallMetrics();
      let html = '<div class="benchmark-grid">';
      html += '<div class="overview-grid">';
      html += `<div class="overview-card"><div class="label">With Skill Average</div><div class="value">${metrics.avgWith}%</div><div class="sub">Aggregate pass rate across all benchmark cases.</div></div>`;
      html += `<div class="overview-card"><div class="label">Baseline Average</div><div class="value">${metrics.avgBase}%</div><div class="sub">Reference pass rate without the generated skill.</div></div>`;
      html += `<div class="overview-card"><div class="label">Performance Increase</div><div class="value">${formatPercent(metrics.perfIncrease)}</div><div class="sub">Net score delta between with-skill and baseline runs.</div></div>`;
      html += `<div class="overview-card"><div class="label">Token Savings</div><div class="value">${formatPercent(metrics.tokenSavings)}</div><div class="sub">${formatTokens(metrics.totalWithTokens)} vs ${formatTokens(metrics.totalBaseTokens)} total tokens.</div></div>`;
      html += '</div>';

      html += '<div class="section">';
      html += '<div class="section-header"><span>Per-Case Results</span><span>Score and token comparison</span></div>';
      html += '<div class="section-body"><div class="benchmark-list">';
      for (let i = 0; i < testCases.length; i++) {
        const tc = testCases[i];
        const caseMetrics = getCaseMetrics(i);
        html += '<div class="bench-row">';
        html += '<div class="bench-row-top">';
        html += `<div><div class="bench-title">${i + 1}. ${escapeHtml(tc.task || '(untitled task)')}</div>`;
        html += `<div class="bench-subtitle">${caseMetrics.expectationCount} expectation${caseMetrics.expectationCount === 1 ? '' : 's'} in this enhancement pass</div></div>`;
        html += `<div class="delta-pill ${getDeltaClass(caseMetrics.delta)}">${formatPercent(caseMetrics.delta)}</div>`;
        html += '</div>';
        html += '<div class="bench-bars">';
        html += '<div class="bench-metric">';
        html += `<div class="bench-metric-head"><span>With Skill</span><span>${caseMetrics.withPassed}/${caseMetrics.expectationCount} • ${caseMetrics.withPct}%</span></div>`;
        html += `<div class="bench-track"><div class="bench-fill fill-skill" style="width:${caseMetrics.withPct}%"></div></div>`;
        html += '</div>';
        html += '<div class="bench-metric">';
        html += `<div class="bench-metric-head"><span>Baseline</span><span>${caseMetrics.basePassed}/${caseMetrics.expectationCount} • ${caseMetrics.basePct}%</span></div>`;
        html += `<div class="bench-track"><div class="bench-fill fill-baseline" style="width:${caseMetrics.basePct}%"></div></div>`;
        html += '</div>';
        html += '</div>';
        html += '</div>';
      }
      html += '</div></div></div>';
      html += '</div>';

      container.innerHTML = html;
    }

    function submitFeedback() {
      const submitBtn = document.getElementById('submit-btn');
      if (submitBtn && submitBtn.disabled) {
        return;
      }
      saveFeedbackState();
      const reviews = [];
      for (let i = 0; i < testCases.length; i++) {
        reviews.push({
          test_index: i,
          task: (testCases[i].task || '').substring(0, 100),
          feedback: feedbackMap[i] || '',
          timestamp: new Date().toISOString(),
        });
      }
      const result = {
        reviews,
        status: 'complete',
        skill_name: data.skill_name,
        iteration: data.iteration,
      };

      fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(result),
      })
      .then(r => r.json())
      .then(d => {
        const el = document.getElementById('submit-status');
        el.textContent = d.ok ? 'Feedback saved.' : 'Error saving feedback.';
        el.style.color = d.ok ? 'var(--accent)' : 'var(--danger)';
        el.style.display = 'inline';
        renderSummary();
        setTimeout(() => { el.style.display = 'none'; }, 3000);
      })
      .catch(() => {
        const el = document.getElementById('submit-status');
        el.textContent = 'Network error.';
        el.style.color = 'var(--danger)';
        el.style.display = 'inline';
      });
    }

    renderSummary();
    renderTestCase();
    renderBenchmark();
  </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------


def generate_review_html(
    eval_data: dict,
    skill_name: str,
    iteration: int,
    previous_data: dict | None = None,
) -> str:
    """Generate a self-contained HTML review page.

    Args:
        eval_data: Combined eval data dict with test_cases, ab_outputs, gradings.
        skill_name: Name of the skill being evaluated.
        iteration: Current iteration number.
        previous_data: Previous iteration's eval data for comparison (optional).

    Returns:
        Complete HTML string.
    """
    embedded = {
        "skill_name": skill_name,
        "iteration": iteration,
        "test_cases": eval_data.get("test_cases", []),
        "ab_outputs": eval_data.get("ab_outputs", []),
        "gradings": eval_data.get("gradings", []),
    }

    if previous_data:
        embedded["previous"] = {
            "ab_outputs": previous_data.get("ab_outputs", []),
            "feedback": previous_data.get("feedback", {}),
        }

    data_json = json.dumps(embedded, ensure_ascii=False)
    # Escape angle brackets to prevent </script> in LLM outputs from
    # breaking out of the script block (XSS).
    data_json = data_json.replace("<", "\\u003c").replace(">", "\\u003e")
    return _VIEWER_HTML.replace(
        "/*__EMBEDDED_DATA__*/",
        f"const EMBEDDED_DATA = {data_json};",
    )


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------


def _kill_port(port: int, iter_path: Path | None = None) -> None:
    """Kill any previous viewer process and ensure *port* is free.

    Uses a PID-file first (no external tools needed), then checks the port
    with a socket probe.  Falls back to ``lsof`` only as a best-effort
    last resort — the function works correctly even when ``lsof`` and
    ``fuser`` are absent.
    """
    # 1. PID-file cleanup (works everywhere, no external tools)
    if iter_path is not None:
        pid_file = iter_path / "viewer.pid"
        if pid_file.exists():
            try:
                old_pid = int(pid_file.read_text().strip())
                if old_pid == os.getpid():
                    pid_file.unlink(missing_ok=True)
                else:
                    os.kill(old_pid, signal.SIGTERM)
                    time.sleep(0.5)
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                pass

    # 2. Socket probe — if nothing is listening we're done
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sock.connect_ex(("127.0.0.1", port)) != 0:
            return  # port is free
    finally:
        sock.close()

    # 3. Port still occupied — try lsof as best-effort fallback
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for pid_str in result.stdout.strip().split("\n"):
            if pid_str.strip():
                try:
                    pid = int(pid_str.strip())
                    if pid != os.getpid():
                        os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, ValueError):
                    pass
        if result.stdout.strip():
            time.sleep(0.5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


class ReviewHandler(BaseHTTPRequestHandler):
    """Serves the review HTML and handles feedback saves.

    Regenerates the HTML on each page load so that refreshing the browser
    picks up new eval outputs without restarting the server.
    """

    def __init__(
        self,
        eval_data_path: Path,
        skill_name: str,
        iteration: int,
        feedback_path: Path,
        previous_data: dict | None,
        exit_on_feedback: bool,
        stop_info: dict,
        *args,
        **kwargs,
    ):
        self.eval_data_path = eval_data_path
        self.skill_name = skill_name
        self.iteration = iteration
        self.feedback_path = feedback_path
        self.previous_data = previous_data
        self.exit_on_feedback = exit_on_feedback
        self.stop_info = stop_info
        super().__init__(*args, **kwargs)

    def _load_eval_data(self) -> dict:
        """Re-read eval data from disk on each request."""
        if self.eval_data_path.exists():
            return json.loads(self.eval_data_path.read_text(encoding="utf-8"))
        return {"test_cases": [], "ab_outputs": [], "gradings": []}

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            eval_data = self._load_eval_data()
            html = generate_review_html(
                eval_data,
                self.skill_name,
                self.iteration,
                self.previous_data,
            )
            content = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/api/feedback":
            data = b"{}"
            if self.feedback_path.exists():
                data = self.feedback_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/api/feedback":
            MAX_BODY = 5 * 1024 * 1024  # 5 MB
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                self.send_error(413, "Request body too large")
                return
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                if not isinstance(data, dict) or "reviews" not in data:
                    raise ValueError("Expected JSON object with 'reviews' key")
                self.feedback_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                resp = b'{"ok":true}'
                self.send_response(200)
            except (json.JSONDecodeError, OSError, ValueError) as e:
                resp = json.dumps({"error": str(e)}).encode()
                self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            self.wfile.flush()
            if resp == b'{"ok":true}' and self.exit_on_feedback:
                self.stop_info.update(
                    {"trigger": "feedback_submission", "reason": "user_submitted_feedback"}
                )
                threading.Timer(0.5, self.server.shutdown).start()
        else:
            self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        pass


def start_review_server(
    iteration_dir: str | Path,
    skill_name: str,
    iteration: int,
    previous_data: dict | None = None,
    port: int = 3117,
    no_browser: bool = False,
    exit_on_feedback: bool = False,
) -> int:
    """Start the review HTTP server and optionally open in browser.

    Args:
        iteration_dir: Path to the iteration-N directory containing eval-full.json.
        skill_name: Name of the skill being evaluated.
        iteration: Current iteration number.
        previous_data: Previous iteration's eval data (optional).
        port: Server port (default 3117). Falls back to auto-assign on conflict.
        no_browser: If True, skip opening the browser automatically.

    Returns:
        The port the server is listening on.
    """
    iter_path = Path(iteration_dir)
    eval_data_path = iter_path / "eval-full.json"
    feedback_path = iter_path / "feedback.json"

    _kill_port(port, iter_path)

    _stop_info: dict = {}  # populated before shutdown; read in finally block

    handler = partial(
        ReviewHandler,
        eval_data_path,
        skill_name,
        iteration,
        feedback_path,
        previous_data,
        exit_on_feedback,
        _stop_info,
    )
    requested_port = port
    try:
        server = HTTPServer(("127.0.0.1", port), handler)
    except OSError:
        server = HTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        logger.warning(
            "Port %d unavailable, fell back to auto-assigned port %d",
            requested_port,
            port,
        )

    # Write the actual port so launch-viewer.sh can health-check it
    port_file = iter_path / "viewer.port"
    port_file.write_text(str(port), encoding="utf-8")

    # Write our own PID so stop-viewer.sh / future launch-viewer.sh can find us
    pid_file = iter_path / "viewer.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    stop_context_path = iter_path / "stop-context.json"

    def _handle_sigterm(signum: int, frame: object) -> None:
        if _stop_info:
            return  # idempotent: ignore repeated signals
        _stop_info["trigger"] = "sigterm"
        _stop_info["signal"] = signum
        # server.shutdown() blocks until serve_forever() exits, so it must not
        # be called on the main thread (where serve_forever() is running).
        threading.Thread(target=server.shutdown, daemon=True, name="sigterm-shutdown").start()

    signal.signal(signal.SIGTERM, _handle_sigterm)

    url = f"http://localhost:{port}"
    logger.info("Server bound to %s", url)
    print("\n  Enhancement Viewer")
    print("  ─────────────────────────────────")
    print(f"  URL:       {url}")
    print(f"  Workspace: {iter_path}")
    print(f"  Feedback:  {feedback_path}")
    print("\n  Press Ctrl+C to stop.\n")

    if not no_browser:
        threading.Timer(0.5, webbrowser.open, args=[url]).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if _stop_info:
            from mega_code.client.utils.tracing import get_current_span

            span = get_current_span()
            trigger = _stop_info.get("trigger", "unknown")
            span.set_attribute("stop_trigger", trigger)
            if trigger == "sigterm":
                span.add_event("sigterm_received", {"signal": _stop_info.get("signal", 0)})
                try:
                    ctx = json.loads(stop_context_path.read_text(encoding="utf-8"))
                    span.set_attribute("stop_reason", str(ctx.get("reason", "unknown")))
                    span.set_attribute("stopper_pid", str(ctx.get("stopper_pid", "")))
                except Exception:
                    span.set_attribute("stop_reason", "sigterm_no_context_file")
                finally:
                    stop_context_path.unlink(missing_ok=True)
            elif trigger == "feedback_submission":
                span.set_attribute("stop_reason", str(_stop_info.get("reason", "unknown")))
        server.server_close()
        port_file.unlink(missing_ok=True)
        pid_file.unlink(missing_ok=True)

    return port


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mega_code.client.enhancement_viewer",
        description="Serve HTML review page for skill-enhance results.",
    )
    parser.add_argument(
        "iteration_dir",
        type=Path,
        help="Path to iteration directory containing eval-full.json.",
    )
    parser.add_argument("--skill-name", required=True, help="Name of the skill.")
    parser.add_argument("--iteration", type=int, default=1, help="Iteration number.")
    parser.add_argument(
        "--previous-workspace",
        type=Path,
        default=None,
        help="Path to previous iteration directory for comparison.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3117,
        help="Server port (default: 3117).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        default=False,
        help="Do not open the browser automatically.",
    )
    parser.add_argument(
        "--exit-on-feedback",
        action="store_true",
        default=False,
        help="Shut down the server automatically after feedback is submitted.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Setup tracing (reuses parent trace when TRACEPARENT is set)
    from mega_code.client.utils.tracing import get_span_writer, get_tracer, setup_tracing

    session_id = os.environ.get("MEGA_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    setup_tracing(service_name="mega-code-enhancement-viewer", session_id=session_id)
    tracer = get_tracer(__name__)

    iter_dir = args.iteration_dir.resolve()

    try:
        with tracer.start_as_current_span("enhancement_viewer") as span:
            span.set_attribute("skill_name", args.skill_name)
            span.set_attribute("iteration", args.iteration)
            span.set_attribute("iteration_dir", str(iter_dir))
            span.set_attribute("port", args.port)

            if not iter_dir.is_dir():
                span.set_attribute("error", "iteration_dir_missing")
                logger.error("Iteration directory does not exist: %s", iter_dir)
                sys.exit(1)

            eval_data_path = iter_dir / "eval-full.json"
            if not eval_data_path.exists():
                span.set_attribute("error", "eval_full_json_missing")
                logger.error("eval-full.json not found in %s", iter_dir)
                sys.exit(1)

            # Load previous iteration data
            previous_data = None
            if args.previous_workspace:
                prev_eval = args.previous_workspace / "eval-full.json"
                span.set_attribute("previous_workspace", str(args.previous_workspace))
                if prev_eval.exists():
                    previous_data = json.loads(prev_eval.read_text(encoding="utf-8"))
                    prev_feedback = args.previous_workspace / "feedback.json"
                    if prev_feedback.exists():
                        fb = json.loads(prev_feedback.read_text(encoding="utf-8"))
                        feedback_map = {}
                        for review in fb.get("reviews", []):
                            idx = review.get("test_index")
                            if idx is not None and review.get("feedback", "").strip():
                                feedback_map[idx] = review["feedback"]
                        previous_data["feedback"] = feedback_map
                else:
                    span.set_attribute("warning", "previous_eval_missing")
                    logger.warning("Previous eval-full.json not found at %s", prev_eval)

            span.set_attribute("status", "starting_server")
            actual_port = start_review_server(
                iter_dir,
                args.skill_name,
                args.iteration,
                previous_data=previous_data,
                port=args.port,
                no_browser=args.no_browser,
                exit_on_feedback=args.exit_on_feedback,
            )
            if actual_port != args.port:
                span.set_attribute("actual_port", actual_port)
                span.set_attribute("port_fallback", True)
    except SystemExit:
        raise
    except Exception as exc:
        span.record_exception(exc)  # pyright: ignore[reportPossiblyUnboundVariable]
        logger.exception("Viewer failed to start")
        sys.exit(1)
    finally:
        from mega_code.client.utils.ndjson_tracing import export_traces

        export_traces(writer=get_span_writer())


if __name__ == "__main__":
    main()
