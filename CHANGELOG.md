# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Placeholder section for upcoming fixes and features.

## [0.1.1] - 2025-09-28 20:16 UTC (rev 0.1.1)

### Changed
- Documented the outstanding UI tasks in `TODO.md` as an actionable checklist.
- Added revision metadata to this changelog to reflect the documented release history.

## [0.1.0] - 2025-09-28 19:52 UTC (rev f865698)

### Added
- End-to-end Mini-DVR stack with FastAPI backend, MJPEG preview via uStreamer, and segmented MP4 recording through FFmpeg.
- Web interface with live zoom, ROI minimap, pan controls, and SURGICAM watermark for surgical streaming.
- Camera control drawer backed by `/api/controls`, including reset-to-default functionality and caching options.
- WebSocket coordination for start/stop recording, snapshot capture, and real-time media gallery updates.
- Media gallery with download/delete actions and configurable storage paths for photos and videos.
- Installation script that provisions system dependencies, Python virtual environment, and directory permissions.
- Systemd unit for unattended service management plus cleanup script for pruning outdated recordings.
